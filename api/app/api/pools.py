"""Endpoints de bolão: criação, convite, palpites e ranking.

**Todo endpoint que devolve palpite de terceiro passa por
``predictions.visible_predictions``.** Não existe caminho alternativo, e é
assim que a blindagem se sustenta quando um endpoint novo for criado.
"""

from __future__ import annotations

from collections.abc import Sequence
from datetime import UTC, datetime

from fastapi import APIRouter, HTTPException, Query, status
from pydantic import BaseModel, Field
from sqlalchemy import func, select
from sqlalchemy.ext.asyncio import AsyncSession
from sqlalchemy.orm import selectinload

from app.api.deps import (
    CurrentUser,
    ManagerDep,
    MembershipDep,
    PoolDep,
    SessionDep,
)
from app.models import (
    Competition,
    Fixture,
    FixtureStatus,
    Matchday,
    Membership,
    MembershipRole,
    MembershipStatus,
    Pool,
    PoolKind,
    PoolRound,
    PoolStatus,
    PoolVisibility,
    Prediction,
    PredictionScore,
    Round,
    Season,
    Standing,
    StandingsSnapshot,
)
from app.schemas.common import FixtureOut, Message
from app.schemas.pool import (
    CriterionOut,
    FixtureSelectionIn,
    JoinRequest,
    MemberOut,
    MultiplierIn,
    MyPrediction,
    OtherPrediction,
    PoolCreate,
    PoolDetail,
    PoolSummary,
    PoolUpdate,
    PredictionBatch,
    PredictionResult,
    RoundStandingOut,
    RulesOut,
    ScoreBreakdown,
    ScoringConfigIn,
    ScoringConfigOut,
    SnapshotOut,
    StandingOut,
)
from app.scoring import Score, ScoringConfig, ScoringMode, explain, tiebreak_order
from app.services import form as form_service
from app.services import matchdays as matchday_service
from app.services import notifications as notify_service
from app.services import pools as pool_service
from app.services import predictions as prediction_service
from app.services import settlement as settlement_service

router = APIRouter(prefix="/pools", tags=["bolões"])

CRITERION_LABELS: dict[str, tuple[str, str]] = {
    "exact": ("Placar exato", "Cravou o resultado."),
    "winner_and_one_score": (
        "Vencedor + 1 placar",
        "Acertou quem ganhou e quantos gols um dos times fez.",
    ),
    "winner_and_goal_diff": (
        "Vencedor + saldo",
        "Acertou quem ganhou e por quantos gols de diferença.",
    ),
    "draw": ("Empate", "Acertou que ia empatar, mas não o placar."),
    "winner_only": ("Vencedor", "Acertou só quem ganhou."),
    "one_score_only": (
        "1 placar certo",
        "Acertou os gols de um dos times, mas errou o vencedor.",
    ),
}


# ---------------------------------------------------------------------------
# Serialização
# ---------------------------------------------------------------------------


def _config_out(config_row, engine: ScoringConfig) -> ScoringConfigOut:  # type: ignore[no-untyped-def]
    return ScoringConfigOut(
        version=config_row.version,
        mode=str(config_row.mode),
        criteria=[
            CriterionOut(
                key=str(criterion.key),
                enabled=criterion.enabled,
                points=criterion.points,
                label=CRITERION_LABELS.get(str(criterion.key), (str(criterion.key), ""))[0],
                description=CRITERION_LABELS.get(str(criterion.key), ("", ""))[1],
            )
            for criterion in engine.criteria
        ],
        evaluation_order=[str(item.key) for item in engine.evaluation_order],
        tiebreak_order=[str(key) for key in tiebreak_order(engine)],
        max_points=engine.max_points,
        is_frozen=config_row.is_frozen,
        knockout_advance_points=config_row.knockout_advance_points,
        champion_points=config_row.champion_points,
        top4_points=config_row.top4_points,
        relegated_points=config_row.relegated_points,
    )


def _fixture_out(fixture: Fixture, now: datetime) -> FixtureOut:
    data = FixtureOut.model_validate(fixture)
    data.is_locked = prediction_service.is_locked(fixture, now)
    return data


async def _com_campeonato(
    session: AsyncSession, jogos: Sequence[Fixture], saida: list[FixtureOut]
) -> list[FixtureOut]:
    """Diz de que campeonato é cada jogo.

    Numa consulta só para a lista inteira, e não uma por jogo — mesmo motivo do
    retrospecto: uma rodada tem dez jogos e o N+1 apareceria na tela mais
    aberta do bolão. Como `Fixture` não tem relação declarada com `Season`, um
    `selectinload` não resolveria de qualquer forma.

    Numa rodada de campeonato todos os jogos são do mesmo, e a informação é
    redundante. Numa rodada montada à mão não: o organizador junta o clássico
    do Brasileirão com a volta da Libertadores, e aí saber a qual torneio cada
    jogo pertence muda o palpite.
    """
    if not saida:
        return saida

    temporadas = {jogo.season_id for jogo in jogos}
    nomes = dict(
        (
            await session.execute(
                select(Season.id, Competition.name)
                .join(Competition, Competition.id == Season.competition_id)
                .where(Season.id.in_(temporadas))
            )
        ).all()
    )

    por_id = {jogo.id: jogo for jogo in jogos}
    for item in saida:
        jogo = por_id.get(item.id)
        if jogo is not None:
            item.competition = nomes.get(jogo.season_id)
    return saida


async def _com_retrospecto(
    session: AsyncSession, jogos: Sequence[Fixture], saida: list[FixtureOut]
) -> list[FixtureOut]:
    """Preenche o retrospecto dos dois times de cada jogo.

    Numa consulta só para a lista inteira, e não uma por jogo: uma rodada tem
    vinte times e o N+1 apareceria justo na tela mais aberta do bolão.

    Não vaza palpite: sai daqui só resultado de jogo já encerrado, que é
    público para todo mundo.
    """
    if not saida:
        return saida

    ids = {jogo.home_team_id for jogo in jogos} | {jogo.away_team_id for jogo in jogos}

    # Corta no primeiro jogo da lista: numa rodada antiga o retrospecto tem que
    # mostrar como os times chegaram **naquele** dia. Sem o corte, a tela de uma
    # rodada de julho exibiria resultados de agosto — inclusive o resultado do
    # próprio jogo que está ali na tela, ao lado do escudo dele.
    corte = min((jogo.kickoff_at for jogo in jogos), default=None)
    achados = await form_service.retrospectos(session, ids, ate=corte)

    por_id = {jogo.id: jogo for jogo in jogos}
    for item in saida:
        jogo = por_id.get(item.id)
        if jogo is None:
            continue
        casa = achados.get(jogo.home_team_id)
        fora = achados.get(jogo.away_team_id)
        item.home_form = casa.resumo if casa else ""
        item.away_form = fora.resumo if fora else ""
    return saida


async def _summary(
    session,
    pool: Pool,
    user_id: int | None,  # type: ignore[no-untyped-def]
) -> PoolSummary:
    # Bolão de rodada personalizada não tem temporada: os jogos vêm de
    # campeonatos diferentes, e é justamente esse o ponto.
    season = await session.get(Season, pool.season_id) if pool.season_id else None
    competition = (
        await session.get(Competition, season.competition_id) if season is not None else None
    )
    members = await pool_service.count_active_members(session, pool.id)

    my_role = my_position = my_points = None
    if user_id is not None:
        membership = await pool_service.get_membership(session, pool.id, user_id)
        if membership is not None:
            my_role = str(membership.role)
            standing = await session.scalar(
                select(Standing).where(
                    Standing.pool_id == pool.id, Standing.membership_id == membership.id
                )
            )
            if standing is not None:
                my_position, my_points = standing.position, standing.points

    return PoolSummary(
        id=pool.id,
        slug=pool.slug,
        name=pool.name,
        description=pool.description,
        status=str(pool.status),
        visibility=str(pool.visibility),
        kind=str(pool.kind),
        season=str(season.year) if season else "—",
        competition=competition.name if competition else "Vários campeonatos",
        members=members,
        max_participants=pool.max_participants,
        my_role=my_role,
        my_position=my_position,
        my_points=my_points,
        created_at=pool.created_at,
    )


# ---------------------------------------------------------------------------
# Bolões
# ---------------------------------------------------------------------------


@router.get("", response_model=list[PoolSummary])
async def list_pools(
    session: SessionDep,
    user: CurrentUser,
    mine: bool = Query(default=True, description="Só os bolões de que eu participo."),
) -> list[PoolSummary]:
    if mine:
        pool_ids = (
            await session.scalars(
                select(Membership.pool_id).where(
                    Membership.user_id == user.id,
                    Membership.status == MembershipStatus.ACTIVE,
                )
            )
        ).all()
        pools = (
            (await session.scalars(select(Pool).where(Pool.id.in_(pool_ids)))).all()
            if pool_ids
            else []
        )
    else:
        pools = (
            await session.scalars(select(Pool).where(Pool.visibility == PoolVisibility.PUBLIC))
        ).all()

    return [await _summary(session, pool, user.id) for pool in pools]


@router.post("", response_model=PoolDetail, status_code=status.HTTP_201_CREATED)
async def create_pool(payload: PoolCreate, session: SessionDep, user: CurrentUser) -> PoolDetail:
    criteria = (
        [item.model_dump(mode="json") for item in payload.scoring.criteria]
        if payload.scoring.criteria
        else None
    )
    try:
        pool = await pool_service.create_pool(
            session,
            owner=user,
            name=payload.name,
            kind=PoolKind(payload.kind),
            season_id=payload.season_id,
            mode=payload.scoring.mode,
            criteria=criteria,
            description=payload.description,
            visibility=PoolVisibility(payload.visibility),
            max_participants=payload.max_participants,
            round_ids=payload.round_ids,
            competition_ids=payload.competition_ids,
            multipliers=payload.multipliers,
            knockout_advance_points=payload.scoring.knockout_advance_points,
            champion_points=payload.scoring.champion_points,
            top4_points=payload.scoring.top4_points,
            relegated_points=payload.scoring.relegated_points,
        )
    except (pool_service.PoolError, ValueError) as exc:
        raise HTTPException(status_code=status.HTTP_400_BAD_REQUEST, detail=str(exc)) from exc

    await session.commit()
    return await _detail(session, pool, user.id)


async def _detail(session, pool: Pool, user_id: int) -> PoolDetail:  # type: ignore[no-untyped-def]
    summary = await _summary(session, pool, user_id)
    config_row = await pool_service.active_config(session, pool.id)
    engine = pool_service.to_engine_config(config_row)
    membership = await pool_service.get_membership(session, pool.id, user_id)
    is_manager = membership is not None and membership.role.can_manage

    owner_name = (
        await session.scalar(
            select(Membership.display_name).where(
                Membership.pool_id == pool.id, Membership.role == MembershipRole.OWNER
            )
        )
        or "—"
    )

    if pool.kind is PoolKind.CUSTOM:
        # No personalizado a "rodada" é a que o organizador montou.
        rounds_included = (
            await session.scalar(
                select(func.count()).select_from(Matchday).where(Matchday.pool_id == pool.id)
            )
        ) or 0
    else:
        rounds_included = (
            await session.scalar(
                select(func.count())
                .select_from(PoolRound)
                .where(PoolRound.pool_id == pool.id, PoolRound.included.is_(True))
            )
        ) or 0

    return PoolDetail(
        **summary.model_dump(),
        invite_code=pool.invite_code if is_manager else None,
        scoring=_config_out(config_row, engine),
        branding=pool.branding,
        rounds_included=rounds_included,
        owner_name=owner_name,
        competition_ids=await pool_service.pool_competition_ids(session, pool.id),
        is_owner=pool.owner_id == user_id,
    )


@router.get("/{slug}", response_model=PoolDetail)
async def get_pool_detail(pool: PoolDep, session: SessionDep, user: CurrentUser) -> PoolDetail:
    membership = await pool_service.get_membership(session, pool.id, user.id)
    if membership is None and pool.visibility is PoolVisibility.PRIVATE:
        raise HTTPException(
            status_code=status.HTTP_403_FORBIDDEN,
            detail="bolão privado; use o código de convite para entrar",
        )
    return await _detail(session, pool, user.id)


class CompetitionsIn(BaseModel):
    competition_ids: list[int] = Field(
        default_factory=list, max_length=50, description="Vazio = todos os importados"
    )


@router.put("/{slug}/competitions", response_model=Message)
async def set_pool_competitions(
    payload: CompetitionsIn,
    pool: PoolDep,
    session: SessionDep,
    user: CurrentUser,
    _: ManagerDep,
) -> Message:
    """Define de quais campeonatos a rodada é garimpada.

    Não mexe em rodada já montada: tirar um campeonato daqui encurta a lista de
    escolha, não apaga jogo que já está numa rodada — nem o palpite de ninguém.
    """
    try:
        escolhidos = await pool_service.set_pool_competitions(
            session, pool=pool, competition_ids=payload.competition_ids
        )
    except pool_service.PoolError as exc:
        raise HTTPException(status_code=status.HTTP_400_BAD_REQUEST, detail=str(exc)) from exc

    await session.commit()
    return Message(
        detail=(
            f"{len(escolhidos)} campeonato(s) no filtro."
            if escolhidos
            else "Sem filtro: a rodada pode puxar jogo de qualquer campeonato importado."
        )
    )


@router.delete("/{slug}", response_model=Message)
async def delete_pool(pool: PoolDep, session: SessionDep, user: CurrentUser) -> Message:
    """Exclui o bolão. Só o dono, e não tem volta.

    Some tudo que pende dele: palpites, pontuações, ranking, rodadas montadas e
    o código de convite. Não há lixeira — a tela avisa isso antes.
    """
    nome = pool.name
    try:
        await pool_service.delete_pool(session, pool=pool, actor=user)
    except pool_service.PoolError as exc:
        raise HTTPException(status_code=status.HTTP_403_FORBIDDEN, detail=str(exc)) from exc

    await session.commit()
    return Message(detail=f"Bolão “{nome}” excluído.")


@router.patch("/{slug}", response_model=PoolDetail)
async def update_pool(
    payload: PoolUpdate,
    pool: PoolDep,
    session: SessionDep,
    user: CurrentUser,
    _: ManagerDep,
) -> PoolDetail:
    data = payload.model_dump(exclude_unset=True)
    if "visibility" in data:
        pool.visibility = PoolVisibility(data.pop("visibility"))
    if "status" in data:
        pool.status = PoolStatus(data.pop("status"))
    for field, value in data.items():
        setattr(pool, field, value)
    await session.commit()
    return await _detail(session, pool, user.id)


@router.post("/join", response_model=PoolDetail)
async def join_by_code(payload: JoinRequest, session: SessionDep, user: CurrentUser) -> PoolDetail:
    pool = await pool_service.get_pool_by_invite(session, payload.invite_code)
    if pool is None:
        raise HTTPException(
            status_code=status.HTTP_404_NOT_FOUND, detail="código de convite inválido"
        )
    try:
        membership = await pool_service.join_pool(
            session, pool=pool, user=user, display_name=payload.display_name
        )
    except pool_service.PoolFull as exc:
        raise HTTPException(status_code=status.HTTP_409_CONFLICT, detail=str(exc)) from exc
    except pool_service.PoolError as exc:
        raise HTTPException(status_code=status.HTTP_400_BAD_REQUEST, detail=str(exc)) from exc

    managers = (
        await session.scalars(
            select(Membership.id).where(
                Membership.pool_id == pool.id,
                Membership.role.in_([MembershipRole.OWNER, MembershipRole.ADMIN]),
                Membership.status == MembershipStatus.ACTIVE,
            )
        )
    ).all()
    await notify_service.enqueue_many(
        session,
        membership_ids=list(managers),
        template="member_joined",
        reference_id=membership.id,
        payload={"name": membership.display_name, "pool": pool.name},
    )

    await session.commit()
    return await _detail(session, pool, user.id)


@router.post("/{slug}/leave", response_model=Message)
async def leave(pool: PoolDep, membership: MembershipDep, session: SessionDep) -> Message:
    try:
        await pool_service.leave_pool(session, membership)
    except pool_service.PoolError as exc:
        raise HTTPException(status_code=status.HTTP_400_BAD_REQUEST, detail=str(exc)) from exc
    await session.commit()
    return Message(detail="você saiu do bolão")


@router.get("/{slug}/members", response_model=list[MemberOut])
async def list_members(
    pool: PoolDep, session: SessionDep, membership: MembershipDep
) -> list[MemberOut]:
    members = (
        await session.scalars(
            select(Membership)
            .where(Membership.pool_id == pool.id, Membership.status == MembershipStatus.ACTIVE)
            .order_by(Membership.joined_at)
        )
    ).all()
    return [
        MemberOut(
            membership_id=member.id,
            display_name=member.display_name,
            role=str(member.role),
            status=str(member.status),
            joined_at=member.joined_at,
            is_me=member.id == membership.id,
        )
        for member in members
    ]


@router.delete("/{slug}/members/{membership_id}", response_model=Message)
async def remove_member(
    membership_id: int,
    pool: PoolDep,
    session: SessionDep,
    user: CurrentUser,
    _: ManagerDep,
) -> Message:
    target = await session.get(Membership, membership_id)
    if target is None or target.pool_id != pool.id:
        raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail="participante não existe")
    try:
        await pool_service.remove_member(session, pool=pool, actor=user, membership=target)
    except pool_service.PoolError as exc:
        raise HTTPException(status_code=status.HTTP_403_FORBIDDEN, detail=str(exc)) from exc
    await session.commit()
    return Message(detail="participante removido")


# ---------------------------------------------------------------------------
# Pontuação
# ---------------------------------------------------------------------------


@router.get("/{slug}/scoring", response_model=ScoringConfigOut)
async def get_scoring(
    pool: PoolDep, session: SessionDep, membership: MembershipDep
) -> ScoringConfigOut:
    config_row = await pool_service.active_config(session, pool.id)
    return _config_out(config_row, pool_service.to_engine_config(config_row))


@router.put("/{slug}/scoring", response_model=ScoringConfigOut)
async def update_scoring(
    payload: ScoringConfigIn,
    pool: PoolDep,
    session: SessionDep,
    user: CurrentUser,
    _: ManagerDep,
) -> ScoringConfigOut:
    from app.scoring import preset_for

    criteria = (
        [item.model_dump(mode="json") for item in payload.criteria]
        if payload.criteria
        else preset_for(ScoringMode(payload.mode)).to_payload()
    )
    try:
        config_row = await pool_service.update_scoring_config(
            session,
            pool=pool,
            actor=user,
            mode=ScoringMode(payload.mode),
            criteria=criteria,
            knockout_advance_points=payload.knockout_advance_points,
            champion_points=payload.champion_points,
            top4_points=payload.top4_points,
            relegated_points=payload.relegated_points,
        )
    except (pool_service.PoolError, ValueError) as exc:
        raise HTTPException(status_code=status.HTTP_400_BAD_REQUEST, detail=str(exc)) from exc

    await session.commit()
    return _config_out(config_row, pool_service.to_engine_config(config_row))


@router.put("/{slug}/rounds/multiplier", response_model=Message)
async def set_multiplier(
    payload: MultiplierIn,
    pool: PoolDep,
    session: SessionDep,
    user: CurrentUser,
    _: ManagerDep,
) -> Message:
    try:
        await pool_service.set_round_multiplier(
            session,
            pool=pool,
            actor=user,
            round_id=payload.round_id,
            multiplier=payload.multiplier,
        )
    except pool_service.PoolError as exc:
        raise HTTPException(status_code=status.HTTP_400_BAD_REQUEST, detail=str(exc)) from exc
    await session.commit()
    return Message(detail=f"rodada agora vale {payload.multiplier}x")


# ---------------------------------------------------------------------------
# Rodadas e jogos
# ---------------------------------------------------------------------------


@router.get("/{slug}/rounds")
async def list_rounds(pool: PoolDep, session: SessionDep, membership: MembershipDep) -> list[dict]:
    """As rodadas em que se palpita neste bolão.

    Responde pelos dois tipos de bolão de propósito. A tela não precisa saber
    se o bolão segue um campeonato ou é montado à mão para pedir a lista — e
    esse detalhe já custou um bug: a tela decidia o endereço olhando o bolão,
    que ainda não tinha carregado, caía no endpoint de campeonato e mostrava
    "este bolão ainda não tem rodadas" com as rodadas todas montadas no banco.

    Cada item traz o próprio ``kind``, então o link para a rodada sai do item,
    não de outro carregamento que pode não ter chegado.
    """
    if pool.kind is PoolKind.CUSTOM:
        return await _rodadas_montadas(session, pool)

    rows = (
        await session.execute(
            select(Round, PoolRound.multiplier)
            .join(PoolRound, PoolRound.round_id == Round.id)
            .where(PoolRound.pool_id == pool.id, PoolRound.included.is_(True))
            .order_by(Round.starts_at.nulls_last(), Round.number)
        )
    ).all()

    now = datetime.now(UTC)
    # Uma consulta para todas as rodadas, com as quatro colunas que interessam.
    # Antes era uma consulta por rodada hidratando o Fixture inteiro — 38
    # viagens ao banco e 380 objetos ORM para produzir três inteiros por rodada.
    resumos = await _resumo_por_rodada(session, [linha[0].id for linha in rows], now)

    return [
        {
            "id": round_obj.id,
            "kind": "season",
            "number": round_obj.number,
            "name": round_obj.name,
            "starts_at": round_obj.starts_at,
            "ends_at": round_obj.ends_at,
            "multiplier": multiplier,
            **resumos.get(round_obj.id, _RESUMO_VAZIO),
        }
        for round_obj, multiplier in rows
    ]


_RESUMO_VAZIO: dict[str, object] = {"fixtures": 0, "settled": 0, "is_open": False}


async def _resumo_por_rodada(
    session: AsyncSession, round_ids: Sequence[int], now: datetime
) -> dict[int, dict[str, object]]:
    """Quantos jogos, quantos apurados e se ainda dá para palpitar.

    ``is_open`` repete a regra de ``prediction_service.is_locked`` em vez de
    chamá-la: aqui não há objeto ``Fixture``, só as colunas. Se a regra da trava
    mudar, esta função tem que mudar junto — está anotado nos dois lugares.
    """
    if not round_ids:
        return {}

    linhas = await session.execute(
        select(
            Fixture.round_id,
            Fixture.status,
            Fixture.kickoff_at,
            Fixture.settled_at,
        ).where(Fixture.round_id.in_(round_ids))
    )

    resumo: dict[int, dict[str, object]] = {}
    for round_id, status_jogo, kickoff, settled in linhas:
        item = resumo.setdefault(round_id, {"fixtures": 0, "settled": 0, "is_open": False})
        item["fixtures"] = int(item["fixtures"]) + 1  # type: ignore[arg-type]
        if settled is not None:
            item["settled"] = int(item["settled"]) + 1  # type: ignore[arg-type]
        # Espelha `predictions.is_locked`: adiado reabre; fora de SCHEDULED
        # fecha; senão, vale o relógio.
        if status_jogo is FixtureStatus.POSTPONED or (
            status_jogo is FixtureStatus.SCHEDULED and now < kickoff
        ):
            item["is_open"] = True
    return resumo


async def _rodadas_montadas(session: AsyncSession, pool: Pool) -> list[dict]:
    """Rodadas de um bolão montado à mão, no mesmo formato das do campeonato."""
    rodadas = await matchday_service.list_matchdays(session, pool.id)
    now = datetime.now(UTC)

    # Uma consulta traz o vínculo rodada→jogo de todas as rodadas de uma vez;
    # outra traz as colunas dos jogos. Eram duas por rodada.
    ids_por_rodada = await matchday_service.fixture_ids_por_rodada(
        session, [rodada.id for rodada in rodadas]
    )
    todos_ids = {fid for ids in ids_por_rodada.values() for fid in ids}

    colunas: dict[int, tuple[FixtureStatus, datetime, datetime | None]] = {}
    if todos_ids:
        linhas = await session.execute(
            select(Fixture.id, Fixture.status, Fixture.kickoff_at, Fixture.settled_at).where(
                Fixture.id.in_(todos_ids)
            )
        )
        colunas = {fid: (st, kick, settled) for fid, st, kick, settled in linhas}

    resultado = []
    for rodada in rodadas:
        jogos = [colunas[fid] for fid in ids_por_rodada.get(rodada.id, ()) if fid in colunas]
        horarios = [kick for _, kick, _ in jogos]
        resultado.append(
            {
                "id": rodada.id,
                "kind": "custom",
                "number": rodada.number,
                "name": rodada.name,
                "starts_at": min(horarios, default=None),
                "ends_at": max(horarios, default=None),
                "multiplier": rodada.multiplier,
                "fixtures": len(jogos),
                "settled": sum(1 for _, _, settled in jogos if settled is not None),
                # Mesma regra de `predictions.is_locked`, ver `_resumo_por_rodada`.
                "is_open": any(
                    st is FixtureStatus.POSTPONED or (st is FixtureStatus.SCHEDULED and now < kick)
                    for st, kick, _ in jogos
                ),
            }
        )
    return resultado


@router.get("/{slug}/rounds/{round_id}/fixtures", response_model=list[FixtureOut])
async def list_round_fixtures(
    round_id: int,
    pool: PoolDep,
    session: SessionDep,
    membership: MembershipDep,
    todos: bool = Query(
        default=False,
        description="Traz também os jogos que não valem neste bolão (tela do organizador).",
    ),
) -> list[FixtureOut]:
    fixtures = (
        await session.scalars(
            select(Fixture)
            .where(Fixture.round_id == round_id, Fixture.season_id == pool.season_id)
            # Sem o eager load a serialização dispara I/O fora do contexto
            # assíncrono do SQLAlchemy e estoura MissingGreenlet.
            .options(
                selectinload(Fixture.home_team),
                selectinload(Fixture.away_team),
                selectinload(Fixture.round),
            )
            .order_by(Fixture.kickoff_at)
        )
    ).all()

    incluidos = await pool_service.included_fixture_ids(
        session, pool.id, [fixture.id for fixture in fixtures]
    )
    now = datetime.now(UTC)

    saida = []
    for fixture in fixtures:
        if not todos and fixture.id not in incluidos:
            continue
        item = _fixture_out(fixture, now)
        item.is_included = fixture.id in incluidos
        saida.append(item)
    return await _com_campeonato(
        session, fixtures, await _com_retrospecto(session, fixtures, saida)
    )


@router.put("/{slug}/fixtures/selection", response_model=Message)
async def set_fixture_selection(
    payload: FixtureSelectionIn,
    pool: PoolDep,
    session: SessionDep,
    user: CurrentUser,
    _: ManagerDep,
) -> Message:
    """Liga ou desliga jogos específicos deste bolão.

    Jogo desligado some da tela de palpite e não pontua. Como a apuração
    consulta a mesma regra, desligar um jogo já apurado e recalcular tira os
    pontos dele do ranking.
    """
    try:
        if payload.round_id is not None:
            await pool_service.set_round_inclusion(
                session,
                pool=pool,
                actor=user,
                round_id=payload.round_id,
                included=payload.included,
            )
            detalhe = "rodada " + ("incluída" if payload.included else "excluída")
        else:
            quantos = await pool_service.set_fixture_inclusion(
                session,
                pool=pool,
                actor=user,
                fixture_ids=payload.fixture_ids,
                included=payload.included,
            )
            detalhe = f"{quantos} jogo(s) " + ("incluído(s)" if payload.included else "excluído(s)")
    except pool_service.PoolError as exc:
        raise HTTPException(status_code=status.HTTP_400_BAD_REQUEST, detail=str(exc)) from exc

    await session.commit()
    return Message(detail=detalhe)


@router.get("/{slug}/rules", response_model=RulesOut)
async def rules(pool: PoolDep, session: SessionDep, membership: MembershipDep) -> RulesOut:
    """Regras deste bolão, geradas a partir da configuração real.

    Não é texto fixo: sai do mesmo motor que pontua. Se o organizador mudar a
    pontuação, esta página muda junto — regulamento que diverge do código é
    pior do que regulamento nenhum.
    """
    config_row = await pool_service.active_config(session, pool.id)
    engine = pool_service.to_engine_config(config_row)
    jogos = await pool_service.included_fixture_count(session, pool.id, pool.season_id)

    criterios = [
        {
            "key": str(criterio.key),
            "label": CRITERION_LABELS.get(str(criterio.key), (str(criterio.key), ""))[0],
            "description": CRITERION_LABELS.get(str(criterio.key), ("", ""))[1],
            "points": criterio.points,
        }
        for criterio in engine.evaluation_order
    ]

    return RulesOut(
        pool_name=pool.name,
        scoring_version=config_row.version,
        max_points_per_fixture=engine.max_points,
        fixtures_included=jogos,
        max_points_total=engine.max_points * jogos,
        criteria=criterios,
        tiebreak=[
            CRITERION_LABELS.get(str(key), (str(key), ""))[0] for key in tiebreak_order(engine)
        ],
        bonus={
            "knockout_advance": config_row.knockout_advance_points,
            "champion": config_row.champion_points,
            "top4": config_row.top4_points,
            "relegated": config_row.relegated_points,
        },
        notes=[
            "Cada palpite leva o critério de maior valor que ele satisfaz.",
            "A ordem de avaliação é por pontos, do maior para o menor — não é fixa.",
            "Vale o tempo normal mais a prorrogação. Pênaltis não contam para o placar.",
            "Não palpitou antes do apito: zero, sem exceção.",
            "Ninguém vê o palpite de ninguém antes de o jogo começar.",
            "Jogo cancelado ou abandonado não pontua para ninguém e sai da conta.",
            "Se um placar for corrigido, a rodada é reapurada e o ranking se ajusta.",
            "Empate de pontos e de acertos divide a posição; a ordem de exibição "
            "usa quem entrou antes no bolão.",
        ],
    )


# ---------------------------------------------------------------------------
# Palpites
# ---------------------------------------------------------------------------


@router.get("/{slug}/rounds/{round_id}/my-predictions", response_model=list[MyPrediction])
async def get_my_predictions(
    round_id: int, pool: PoolDep, session: SessionDep, membership: MembershipDep
) -> list[MyPrediction]:
    fixture_ids = list(
        (await session.scalars(select(Fixture.id).where(Fixture.round_id == round_id))).all()
    )
    mine = await prediction_service.my_predictions(
        session, membership_id=membership.id, fixture_ids=fixture_ids
    )
    return [
        MyPrediction(
            fixture_id=fixture_id,
            home_goals=prediction.home_goals,
            away_goals=prediction.away_goals,
            updated_at=prediction.updated_at,
        )
        for fixture_id, prediction in mine.items()
    ]


@router.put("/{slug}/predictions", response_model=PredictionResult)
async def save_predictions(
    payload: PredictionBatch,
    pool: PoolDep,
    session: SessionDep,
    membership: MembershipDep,
) -> PredictionResult:
    fixture_ids = [item.fixture_id for item in payload.predictions]
    fixtures = {
        fixture.id: fixture
        for fixture in (
            await session.scalars(
                select(Fixture).where(
                    # Bolão personalizado não tem temporada: quem decide se o
                    # jogo vale é `fixture_is_included`, logo abaixo. Filtrar
                    # por temporada aqui recusaria todos os jogos dele.
                    Fixture.id.in_(fixture_ids),
                    *([Fixture.season_id == pool.season_id] if pool.season_id is not None else []),
                )
            )
        ).all()
    }

    entries = []
    rejected: list[dict] = []
    for item in payload.predictions:
        fixture = fixtures.get(item.fixture_id)
        if fixture is None:
            rejected.append({"fixture_id": item.fixture_id, "reason": "jogo não é deste bolão"})
            continue
        if not await pool_service.fixture_is_included(session, pool.id, fixture):
            rejected.append(
                {"fixture_id": item.fixture_id, "reason": "este jogo não vale neste bolão"}
            )
            continue
        entries.append((fixture, item.home_goals, item.away_goals))

    saved, more_rejected = await prediction_service.bulk_upsert(
        session, membership=membership, entries=entries
    )
    await session.commit()

    return PredictionResult(
        saved=[
            MyPrediction(
                fixture_id=prediction.fixture_id,
                home_goals=prediction.home_goals,
                away_goals=prediction.away_goals,
                updated_at=prediction.updated_at,
            )
            for prediction in saved
        ],
        rejected=rejected + more_rejected,
    )


@router.get("/{slug}/fixtures/{fixture_id}/predictions", response_model=list[OtherPrediction])
async def list_fixture_predictions(
    fixture_id: int, pool: PoolDep, session: SessionDep, membership: MembershipDep
) -> list[OtherPrediction]:
    """Palpites de todo mundo num jogo.

    **Blindado**: antes do apito, o palpite alheio vem sem números. O único
    palpite completo é o do próprio solicitante.
    """
    visible = await prediction_service.visible_predictions(
        session,
        pool_id=pool.id,
        fixture_ids=[fixture_id],
        viewer_membership_id=membership.id,
    )
    return [
        OtherPrediction(
            membership_id=item.membership_id,
            display_name=item.display_name,
            fixture_id=item.fixture_id,
            home_goals=item.home_goals,
            away_goals=item.away_goals,
            is_hidden=item.is_hidden,
        )
        for item in visible
    ]


@router.get("/{slug}/rounds/{round_id}/predictions", response_model=list[OtherPrediction])
async def list_round_predictions(
    round_id: int, pool: PoolDep, session: SessionDep, membership: MembershipDep
) -> list[OtherPrediction]:
    """Grade de palpites da rodada inteira. Mesma blindagem, jogo a jogo."""
    fixture_ids = list(
        (await session.scalars(select(Fixture.id).where(Fixture.round_id == round_id))).all()
    )
    visible = await prediction_service.visible_predictions(
        session,
        pool_id=pool.id,
        fixture_ids=fixture_ids,
        viewer_membership_id=membership.id,
    )
    return [
        OtherPrediction(
            membership_id=item.membership_id,
            display_name=item.display_name,
            fixture_id=item.fixture_id,
            home_goals=item.home_goals,
            away_goals=item.away_goals,
            is_hidden=item.is_hidden,
        )
        for item in visible
    ]


@router.get("/{slug}/fixtures/{fixture_id}/breakdown", response_model=list[ScoreBreakdown])
async def score_breakdown(
    fixture_id: int, pool: PoolDep, session: SessionDep, membership: MembershipDep
) -> list[ScoreBreakdown]:
    """Por que cada um levou o que levou.

    Só faz sentido depois do jogo, e é exatamente a tela que evita a discussão
    de "por que eu levei 7 e ele 10".
    """
    fixture = await session.get(Fixture, fixture_id)
    if fixture is None or not await pool_service.fixture_is_included(session, pool.id, fixture):
        raise HTTPException(
            status_code=status.HTTP_404_NOT_FOUND, detail="jogo não vale neste bolão"
        )

    now = datetime.now(UTC)
    if not prediction_service.is_locked(fixture, now):
        raise HTTPException(
            status_code=status.HTTP_409_CONFLICT,
            detail="o jogo ainda não começou; os palpites continuam blindados",
        )

    # O palpite entra na mesma consulta. Buscá-lo com `session.get` dentro do
    # laço era uma ida ao banco por participante — num bolão de vinte pessoas,
    # vinte consultas para montar uma tabela de vinte linhas.
    #
    # A ordem é por pontos e, no empate, por nome: sem o segundo critério a
    # ordem de quem empatou muda a cada carregamento, e a tabela parece
    # embaralhar sozinha.
    rows = (
        await session.execute(
            select(PredictionScore, Membership, Prediction)
            .join(Membership, PredictionScore.membership_id == Membership.id)
            .join(Prediction, PredictionScore.prediction_id == Prediction.id)
            .where(PredictionScore.pool_id == pool.id, PredictionScore.fixture_id == fixture_id)
            .order_by(PredictionScore.final_points.desc(), Membership.display_name)
        )
    ).all()

    actual = fixture.effective_score
    engine = pool_service.to_engine_config(await pool_service.active_config(session, pool.id))

    result = []
    for prediction_score, member, prediction in rows:
        predicted = Score(prediction.home_goals, prediction.away_goals) if prediction else None
        detail = explain(predicted, Score(*actual), engine) if actual else {"reason": "sem placar"}
        result.append(
            ScoreBreakdown(
                fixture_id=fixture_id,
                membership_id=member.id,
                display_name=member.display_name,
                is_me=member.id == membership.id,
                prediction=str(predicted) if predicted else None,
                actual=f"{actual[0]}x{actual[1]}" if actual else None,
                criterion=str(prediction_score.criterion_key),
                reason=str(detail.get("reason", "")),
                base_points=prediction_score.base_points,
                multiplier=prediction_score.multiplier,
                final_points=prediction_score.final_points,
            )
        )
    return result


# ---------------------------------------------------------------------------
# Ranking
# ---------------------------------------------------------------------------


@router.get("/{slug}/standings", response_model=list[StandingOut])
async def standings(
    pool: PoolDep, session: SessionDep, membership: MembershipDep
) -> list[StandingOut]:
    rows = await settlement_service.standings_for(session, pool.id)
    names = {
        member.id: member.display_name
        for member in (
            await session.scalars(select(Membership).where(Membership.pool_id == pool.id))
        ).all()
    }
    return [
        StandingOut(
            position=row.position,
            previous_position=row.previous_position,
            movement=row.movement,
            membership_id=row.membership_id,
            display_name=names.get(row.membership_id, "—"),
            points=row.points,
            predictions_made=row.predictions_made,
            fixtures_settled=row.fixtures_settled,
            criterion_hits=row.criterion_hits,
            is_me=row.membership_id == membership.id,
        )
        for row in rows
    ]


@router.get("/{slug}/rounds/{round_id}/standings", response_model=list[RoundStandingOut])
async def round_standings(
    round_id: int, pool: PoolDep, session: SessionDep, membership: MembershipDep
) -> list[RoundStandingOut]:
    rows = await settlement_service.round_standings(session, pool.id, round_id)
    return [RoundStandingOut(**row) for row in rows]  # type: ignore[arg-type]


@router.get("/{slug}/history", response_model=list[SnapshotOut])
async def history(
    pool: PoolDep, session: SessionDep, membership: MembershipDep
) -> list[SnapshotOut]:
    """Evolução de posição rodada a rodada."""
    snapshots = (
        await session.scalars(
            select(StandingsSnapshot)
            .where(StandingsSnapshot.pool_id == pool.id)
            .order_by(StandingsSnapshot.computed_at)
        )
    ).all()
    names = {
        round_obj.id: round_obj.name
        for round_obj in (
            await session.scalars(select(Round).where(Round.season_id == pool.season_id))
        ).all()
    }
    return [
        SnapshotOut(
            round_id=snapshot.round_id,
            round_name=names.get(snapshot.round_id, "—"),
            computed_at=snapshot.computed_at,
            payload=snapshot.payload,
        )
        for snapshot in snapshots
    ]


@router.post("/{slug}/recompute", response_model=dict)
async def recompute(pool: PoolDep, session: SessionDep, user: CurrentUser, _: ManagerDep) -> dict:
    """Reapura o bolão inteiro. Idempotente."""
    result = await settlement_service.recompute_pool(session, pool.id)
    await session.commit()
    return result
