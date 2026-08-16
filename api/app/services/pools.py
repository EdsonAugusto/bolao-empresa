"""Bolões: criação, convite, participação e configuração de pontuação."""

from __future__ import annotations

from collections.abc import Sequence
from datetime import UTC, datetime
from typing import Any

from slugify import slugify
from sqlalchemy import delete, func, select
from sqlalchemy.ext.asyncio import AsyncSession
from sqlalchemy.orm import selectinload

from app.core.security import generate_invite_code
from app.models import (
    AuditAction,
    AuditLog,
    Competition,
    Fixture,
    Matchday,
    MatchdayFixture,
    Membership,
    MembershipRole,
    MembershipStatus,
    Pool,
    PoolCompetition,
    PoolFixture,
    PoolKind,
    PoolRound,
    PoolStatus,
    PoolVisibility,
    Round,
    ScoringConfig,
    Season,
    User,
)
from app.models.enums import ScoringMode as ModelScoringMode
from app.scoring import ScoringConfig as EngineConfig
from app.scoring import ScoringMode, preset_for


def _model_mode(mode: ScoringMode) -> ModelScoringMode:
    """Converte o modo do motor para o do banco.

    São dois enums com os mesmos valores de propósito: ``app/scoring`` é puro e
    não pode importar nada de ``app/models``. A conversão acontece só aqui, na
    fronteira entre as duas camadas.
    """
    return ModelScoringMode(str(mode))


class PoolError(Exception):
    pass


class PoolFull(PoolError):
    pass


class NotAMember(PoolError):
    pass


class NotAuthorized(PoolError):
    pass


class ConfigFrozen(PoolError):
    """A configuração já pontuou palpite; só resta versionar."""


async def create_pool(
    session: AsyncSession,
    *,
    owner: User,
    name: str,
    season_id: int | None = None,
    kind: PoolKind = PoolKind.SEASON,
    mode: ScoringMode = ScoringMode.CLASSIC,
    criteria: list[dict[str, Any]] | None = None,
    description: str | None = None,
    visibility: PoolVisibility = PoolVisibility.PRIVATE,
    max_participants: int | None = None,
    round_ids: Sequence[int] | None = None,
    competition_ids: Sequence[int] | None = None,
    multipliers: dict[int, int] | None = None,
    knockout_advance_points: int = 0,
    champion_points: int = 0,
    top4_points: int = 0,
    relegated_points: int = 0,
) -> Pool:
    if kind is PoolKind.SEASON:
        if season_id is None:
            raise PoolError("bolão de campeonato precisa de uma temporada")
        if await session.get(Season, season_id) is None:
            raise PoolError("temporada não encontrada")
    else:
        # Rodada personalizada não pertence a campeonato nenhum — é justamente
        # isso que permite misturar Brasileirão com Premier League.
        season_id = None

    pool = Pool(
        owner_id=owner.id,
        kind=kind,
        season_id=season_id,
        name=name.strip(),
        description=description,
        slug=await _unique_pool_slug(session, name),
        invite_code=await _unique_invite_code(session),
        status=PoolStatus.OPEN,
        visibility=visibility,
        max_participants=max_participants,
    )
    session.add(pool)
    await session.flush()

    engine_config = EngineConfig.from_payload(criteria, mode=mode) if criteria else preset_for(mode)
    session.add(
        ScoringConfig(
            pool_id=pool.id,
            version=1,
            mode=_model_mode(mode),
            criteria=engine_config.to_payload(),
            knockout_advance_points=knockout_advance_points,
            champion_points=champion_points,
            top4_points=top4_points,
            relegated_points=relegated_points,
        )
    )

    if kind is PoolKind.SEASON:
        await _set_pool_rounds(session, pool, round_ids, multipliers)
    elif competition_ids:
        await set_pool_competitions(session, pool=pool, competition_ids=competition_ids)

    session.add(
        Membership(
            pool_id=pool.id,
            user_id=owner.id,
            display_name=owner.display_name,
            role=MembershipRole.OWNER,
        )
    )
    session.add(
        AuditLog(
            action=AuditAction.POOL_CREATED,
            actor_user_id=owner.id,
            pool_id=pool.id,
            entity="pool",
            entity_id=pool.id,
            payload={"name": pool.name, "mode": str(mode)},
        )
    )
    await session.flush()
    return pool


async def pool_competition_ids(session: AsyncSession, pool_id: int) -> list[int]:
    """Campeonatos que alimentam a rodada. Vazio significa "todos"."""
    linhas = await session.scalars(
        select(PoolCompetition.competition_id).where(PoolCompetition.pool_id == pool_id)
    )
    return list(linhas.all())


async def set_pool_competitions(
    session: AsyncSession,
    *,
    pool: Pool,
    competition_ids: Sequence[int],
) -> list[int]:
    """Define de quais campeonatos a agenda da rodada é garimpada.

    Lista vazia volta ao padrão: a agenda inteira. Isso é escolha de produto —
    quem não quer filtrar não deveria precisar marcar dezenove caixas.

    **Não mexe em rodada já montada.** Tirar um campeonato daqui não remove
    jogo dele de uma rodada existente: a rodada é o que o organizador marcou, e
    apagar palpite de gente por causa de um filtro seria surpresa ruim.
    """
    if pool.kind is not PoolKind.CUSTOM:
        raise PoolError("só bolão de rodada montada filtra campeonatos")

    pedidos = list(dict.fromkeys(competition_ids))
    if pedidos:
        existentes = set(
            (await session.scalars(select(Competition.id).where(Competition.id.in_(pedidos)))).all()
        )
        desconhecidos = [item for item in pedidos if item not in existentes]
        if desconhecidos:
            raise PoolError(f"campeonato não encontrado: {desconhecidos[0]}")

    await session.execute(delete(PoolCompetition).where(PoolCompetition.pool_id == pool.id))
    for competition_id in pedidos:
        session.add(PoolCompetition(pool_id=pool.id, competition_id=competition_id))

    await session.flush()
    return pedidos


async def delete_pool(session: AsyncSession, *, pool: Pool, actor: User) -> None:
    """Apaga o bolão e tudo que pende dele.

    Só o dono. Administrador de bolão pode gerenciar rodada e participante, mas
    não desfazer o bolão de outra pessoa — quem entrou e palpitou perde tudo, e
    isso não pode estar a um clique de quem não criou.

    O apagamento é físico e em cascata (as chaves estrangeiras já mandam
    ``ON DELETE CASCADE``): palpites, pontuações, ranking, rodadas e convites
    somem junto. Não há lixeira, e o aviso na tela diz isso.
    """
    if pool.owner_id != actor.id:
        raise PoolError("só o dono pode excluir o bolão")

    # Registrado antes de apagar: o log tem `pool_id` com cascata, então a
    # linha morre junto. O que sobrevive é o log sem vínculo, de propósito.
    session.add(
        AuditLog(
            action=AuditAction.POOL_DELETED,
            actor_user_id=actor.id,
            pool_id=None,
            entity="pool",
            entity_id=pool.id,
            payload={"name": pool.name, "slug": pool.slug},
        )
    )
    await session.delete(pool)
    await session.flush()


async def _set_pool_rounds(
    session: AsyncSession,
    pool: Pool,
    round_ids: Sequence[int] | None,
    multipliers: dict[int, int] | None,
) -> None:
    """Sem lista explícita, entram todas as rodadas da temporada."""
    if round_ids is None:
        round_ids = (
            await session.scalars(select(Round.id).where(Round.season_id == pool.season_id))
        ).all()

    multipliers = multipliers or {}
    for round_id in round_ids:
        session.add(
            PoolRound(
                pool_id=pool.id,
                round_id=round_id,
                multiplier=max(1, multipliers.get(round_id, 1)),
                included=True,
            )
        )


async def get_pool_by_slug(session: AsyncSession, slug: str) -> Pool | None:
    pool: Pool | None = await session.scalar(
        select(Pool).where(Pool.slug == slug).options(selectinload(Pool.scoring_configs))
    )
    return pool


async def get_pool_by_invite(session: AsyncSession, invite_code: str) -> Pool | None:
    pool: Pool | None = await session.scalar(
        select(Pool).where(Pool.invite_code == invite_code.strip().upper())
    )
    return pool


async def get_membership(session: AsyncSession, pool_id: int, user_id: int) -> Membership | None:
    membership: Membership | None = await session.scalar(
        select(Membership).where(Membership.pool_id == pool_id, Membership.user_id == user_id)
    )
    return membership


async def organiza_bolao_com_jogo(session: AsyncSession, *, user_id: int, fixture_id: int) -> bool:
    """Esta pessoa organiza algum bolão em que este jogo vale?

    Serve para autorizar o lançamento de placar. Quem organiza um bolão precisa
    poder destravar um jogo que terminou e ficou sem resultado — é o único
    caminho de saída daquele estado, e o organizador raramente é a primeira
    conta da instalação, que é quem tem ``is_superuser``.

    Os dois tipos de bolão chegam ao jogo por caminhos diferentes: o de
    campeonato pela rodada incluída, o montado à mão pela rodada montada.
    """
    fixture = await session.get(Fixture, fixture_id)
    if fixture is None:
        return False

    organizados = (
        select(Membership.pool_id)
        .where(
            Membership.user_id == user_id,
            Membership.role.in_((MembershipRole.OWNER, MembershipRole.ADMIN)),
            Membership.status == MembershipStatus.ACTIVE,
        )
        .scalar_subquery()
    )

    # Bolão de campeonato: o jogo está numa rodada que o bolão incluiu.
    if fixture.round_id is not None:
        pela_rodada = await session.scalar(
            select(PoolRound.pool_id)
            .where(
                PoolRound.pool_id.in_(organizados),
                PoolRound.round_id == fixture.round_id,
                PoolRound.included.is_(True),
            )
            .limit(1)
        )
        if pela_rodada is not None:
            return True

    # Bolão montado à mão: o jogo foi escolhido para uma rodada.
    pela_montada = await session.scalar(
        select(Matchday.pool_id)
        .join(MatchdayFixture, MatchdayFixture.matchday_id == Matchday.id)
        .where(Matchday.pool_id.in_(organizados), MatchdayFixture.fixture_id == fixture_id)
        .limit(1)
    )
    return pela_montada is not None


async def require_membership(session: AsyncSession, pool_id: int, user_id: int) -> Membership:
    membership = await get_membership(session, pool_id, user_id)
    if membership is None or not membership.is_active:
        raise NotAMember("você não participa deste bolão")
    return membership


async def require_manager(session: AsyncSession, pool_id: int, user_id: int) -> Membership:
    membership = await require_membership(session, pool_id, user_id)
    if not membership.role.can_manage:
        raise NotAuthorized("só o organizador pode fazer isso")
    return membership


async def join_pool(
    session: AsyncSession, *, pool: Pool, user: User, display_name: str | None = None
) -> Membership:
    existing = await get_membership(session, pool.id, user.id)
    if existing is not None:
        # Reentrada de quem tinha saído: mantém o histórico de palpites.
        if not existing.is_active:
            existing.status = MembershipStatus.ACTIVE
            existing.joined_at = datetime.now(UTC)
        return existing

    if pool.status in {PoolStatus.FINISHED, PoolStatus.ARCHIVED}:
        raise PoolError("este bolão já terminou")

    if pool.max_participants is not None:
        active = await count_active_members(session, pool.id)
        if active >= pool.max_participants:
            raise PoolFull(f"o bolão está cheio ({pool.max_participants} participantes)")

    membership = Membership(
        pool_id=pool.id,
        user_id=user.id,
        display_name=(display_name or user.display_name).strip(),
        role=MembershipRole.PLAYER,
    )
    session.add(membership)
    session.add(
        AuditLog(
            action=AuditAction.MEMBER_JOINED,
            actor_user_id=user.id,
            pool_id=pool.id,
            entity="membership",
            payload={"display_name": membership.display_name},
        )
    )
    await session.flush()
    return membership


async def count_active_members(session: AsyncSession, pool_id: int) -> int:
    return (
        await session.scalar(
            select(func.count())
            .select_from(Membership)
            .where(Membership.pool_id == pool_id, Membership.status == MembershipStatus.ACTIVE)
        )
    ) or 0


async def leave_pool(session: AsyncSession, membership: Membership) -> None:
    if membership.role is MembershipRole.OWNER:
        raise PoolError("o dono não pode sair; transfira o bolão antes")
    membership.status = MembershipStatus.LEFT


async def remove_member(
    session: AsyncSession, *, pool: Pool, actor: User, membership: Membership
) -> None:
    await require_manager(session, pool.id, actor.id)
    if membership.role is MembershipRole.OWNER:
        raise NotAuthorized("o dono não pode ser removido")
    membership.status = MembershipStatus.REMOVED
    session.add(
        AuditLog(
            action=AuditAction.MEMBER_REMOVED,
            actor_user_id=actor.id,
            pool_id=pool.id,
            entity="membership",
            entity_id=membership.id,
            payload={"display_name": membership.display_name},
        )
    )


async def set_member_role(
    session: AsyncSession, *, pool: Pool, actor: User, membership: Membership, role: MembershipRole
) -> None:
    await require_manager(session, pool.id, actor.id)
    if role is MembershipRole.OWNER:
        raise NotAuthorized("use a transferência de propriedade")
    membership.role = role


async def active_config(session: AsyncSession, pool_id: int) -> ScoringConfig:
    config = await session.scalar(
        select(ScoringConfig)
        .where(ScoringConfig.pool_id == pool_id)
        .order_by(ScoringConfig.version.desc())
        .limit(1)
    )
    if config is None:
        raise PoolError("bolão sem configuração de pontuação")
    return config


def to_engine_config(config: ScoringConfig) -> EngineConfig:
    return EngineConfig.from_payload(
        config.criteria, mode=ScoringMode(config.mode), version=config.version
    )


async def update_scoring_config(
    session: AsyncSession,
    *,
    pool: Pool,
    actor: User,
    mode: ScoringMode,
    criteria: list[dict[str, Any]],
    knockout_advance_points: int = 0,
    champion_points: int = 0,
    top4_points: int = 0,
    relegated_points: int = 0,
) -> ScoringConfig:
    """Altera a pontuação do bolão.

    Enquanto nenhum palpite tiver sido pontuado, edita no lugar. Depois disso a
    configuração está congelada e nasce uma **versão nova** — o histórico
    anterior não é reescrito, e cada ``prediction_score`` continua sabendo com
    que regra foi calculado.
    """
    await require_manager(session, pool.id, actor.id)
    current = await active_config(session, pool.id)

    # Valida antes de gravar: configuração inválida derruba a apuração inteira.
    EngineConfig.from_payload(criteria, mode=mode)

    if not current.is_frozen:
        current.mode = _model_mode(mode)
        current.criteria = criteria
        current.knockout_advance_points = knockout_advance_points
        current.champion_points = champion_points
        current.top4_points = top4_points
        current.relegated_points = relegated_points
        await session.flush()
        return current

    new_config = ScoringConfig(
        pool_id=pool.id,
        version=current.version + 1,
        mode=_model_mode(mode),
        criteria=criteria,
        knockout_advance_points=knockout_advance_points,
        champion_points=champion_points,
        top4_points=top4_points,
        relegated_points=relegated_points,
    )
    session.add(new_config)
    session.add(
        AuditLog(
            action=AuditAction.SCORING_CONFIG_VERSIONED,
            actor_user_id=actor.id,
            pool_id=pool.id,
            entity="scoring_config",
            payload={"from_version": current.version, "to_version": new_config.version},
        )
    )
    await session.flush()
    return new_config


async def set_round_multiplier(
    session: AsyncSession, *, pool: Pool, actor: User, round_id: int, multiplier: int
) -> PoolRound:
    await require_manager(session, pool.id, actor.id)
    if multiplier < 1:
        raise PoolError("o multiplicador precisa ser pelo menos 1")

    pool_round = await session.scalar(
        select(PoolRound).where(PoolRound.pool_id == pool.id, PoolRound.round_id == round_id)
    )
    if pool_round is None:
        pool_round = PoolRound(pool_id=pool.id, round_id=round_id)
        session.add(pool_round)
    pool_round.multiplier = multiplier
    await session.flush()
    return pool_round


async def multiplier_for_round(session: AsyncSession, pool_id: int, round_id: int | None) -> int:
    if round_id is None:
        return 1
    value = await session.scalar(
        select(PoolRound.multiplier).where(
            PoolRound.pool_id == pool_id,
            PoolRound.round_id == round_id,
            PoolRound.included.is_(True),
        )
    )
    return value or 1


async def round_is_included(session: AsyncSession, pool_id: int, round_id: int | None) -> bool:
    if round_id is None:
        return True
    return bool(
        await session.scalar(
            select(PoolRound.id).where(
                PoolRound.pool_id == pool_id,
                PoolRound.round_id == round_id,
                PoolRound.included.is_(True),
            )
        )
    )


async def fixture_is_included(session: AsyncSession, pool_id: int, fixture: Fixture) -> bool:
    """Este jogo vale neste bolão?

    **Ponto único da regra.** A apuração e o CRUD de palpite chamam esta
    função; nenhum dos dois refaz a lógica. Se refizesse, um jogo excluído
    poderia aceitar palpite e não pontuar — ou o contrário.

    Bolão personalizado: vale se o organizador colocou o jogo em alguma rodada.
    Bolão de campeonato: vale a rodada da temporada, salvo exceção por jogo.
    """
    pool = await session.get(Pool, pool_id)
    if pool is None:
        return False

    if pool.kind is PoolKind.CUSTOM:
        return bool(
            await session.scalar(
                select(MatchdayFixture.id)
                .join(Matchday, Matchday.id == MatchdayFixture.matchday_id)
                .where(Matchday.pool_id == pool_id, MatchdayFixture.fixture_id == fixture.id)
                .limit(1)
            )
        )

    excecao = await session.scalar(
        select(PoolFixture.included).where(
            PoolFixture.pool_id == pool_id, PoolFixture.fixture_id == fixture.id
        )
    )
    if excecao is not None:
        return bool(excecao)
    return await round_is_included(session, pool_id, fixture.round_id)


async def included_fixture_ids(
    session: AsyncSession, pool_id: int, fixture_ids: Sequence[int]
) -> set[int]:
    """Versão em lote, para telas que listam uma rodada inteira."""
    if not fixture_ids:
        return set()

    pool = await session.get(Pool, pool_id)
    if pool is not None and pool.kind is PoolKind.CUSTOM:
        return set(
            (
                await session.scalars(
                    select(MatchdayFixture.fixture_id)
                    .join(Matchday, Matchday.id == MatchdayFixture.matchday_id)
                    .where(
                        Matchday.pool_id == pool_id,
                        MatchdayFixture.fixture_id.in_(fixture_ids),
                    )
                )
            ).all()
        )

    linhas = (
        await session.execute(
            select(Fixture.id, Fixture.round_id).where(Fixture.id.in_(fixture_ids))
        )
    ).all()

    excecoes: dict[int, bool] = {
        linha[0]: linha[1]
        for linha in (
            await session.execute(
                select(PoolFixture.fixture_id, PoolFixture.included).where(
                    PoolFixture.pool_id == pool_id,
                    PoolFixture.fixture_id.in_(fixture_ids),
                )
            )
        ).all()
    }

    rodadas_incluidas = set(
        (
            await session.scalars(
                select(PoolRound.round_id).where(
                    PoolRound.pool_id == pool_id, PoolRound.included.is_(True)
                )
            )
        ).all()
    )

    incluidos: set[int] = set()
    for fixture_id, round_id in linhas:
        if fixture_id in excecoes:
            if excecoes[fixture_id]:
                incluidos.add(fixture_id)
        elif round_id is None or round_id in rodadas_incluidas:
            incluidos.add(fixture_id)
    return incluidos


async def set_fixture_inclusion(
    session: AsyncSession,
    *,
    pool: Pool,
    actor: User,
    fixture_ids: Sequence[int],
    included: bool,
) -> int:
    """Liga ou desliga jogos específicos. Devolve quantos foram afetados."""
    await require_manager(session, pool.id, actor.id)
    if not fixture_ids:
        return 0

    existentes = {
        linha.fixture_id: linha
        for linha in (
            await session.scalars(
                select(PoolFixture).where(
                    PoolFixture.pool_id == pool.id,
                    PoolFixture.fixture_id.in_(fixture_ids),
                )
            )
        ).all()
    }

    for fixture_id in fixture_ids:
        linha = existentes.get(fixture_id)
        if linha is None:
            session.add(PoolFixture(pool_id=pool.id, fixture_id=fixture_id, included=included))
        else:
            linha.included = included

    await session.flush()
    session.add(
        AuditLog(
            action=AuditAction.POOL_UPDATED,
            actor_user_id=actor.id,
            pool_id=pool.id,
            entity="pool_fixtures",
            payload={"fixtures": list(fixture_ids), "included": included},
        )
    )
    return len(fixture_ids)


async def set_round_inclusion(
    session: AsyncSession, *, pool: Pool, actor: User, round_id: int, included: bool
) -> None:
    """Liga ou desliga uma rodada inteira.

    Limpa as exceções daquela rodada: se o organizador acabou de dizer
    "a rodada 12 inteira entra", uma exclusão individual esquecida lá atrás
    contradiz o que ele acabou de pedir.
    """
    await require_manager(session, pool.id, actor.id)

    pool_round = await session.scalar(
        select(PoolRound).where(PoolRound.pool_id == pool.id, PoolRound.round_id == round_id)
    )
    if pool_round is None:
        pool_round = PoolRound(pool_id=pool.id, round_id=round_id)
        session.add(pool_round)
    pool_round.included = included

    fixture_ids = list(
        (await session.scalars(select(Fixture.id).where(Fixture.round_id == round_id))).all()
    )
    if fixture_ids:
        await session.execute(
            delete(PoolFixture).where(
                PoolFixture.pool_id == pool.id, PoolFixture.fixture_id.in_(fixture_ids)
            )
        )
    await session.flush()


async def included_fixture_count(session: AsyncSession, pool_id: int, season_id: int | None) -> int:
    """Quantos jogos valem neste bolão. Vai para o cabeçalho e para as regras."""
    if season_id is None:
        # Bolão personalizado: o total é a soma das rodadas montadas.
        total = await session.scalar(
            select(func.count(MatchdayFixture.id))
            .join(Matchday, Matchday.id == MatchdayFixture.matchday_id)
            .where(Matchday.pool_id == pool_id)
        )
        return total or 0

    fixture_ids = list(
        (await session.scalars(select(Fixture.id).where(Fixture.season_id == season_id))).all()
    )
    return len(await included_fixture_ids(session, pool_id, fixture_ids))


async def _unique_pool_slug(session: AsyncSession, name: str) -> str:
    base = slugify(name)[:100] or "bolao"
    slug = base
    suffix = 2
    while await session.scalar(select(Pool.id).where(Pool.slug == slug)):
        slug = f"{base}-{suffix}"
        suffix += 1
    return slug


async def _unique_invite_code(session: AsyncSession) -> str:
    for _ in range(20):
        code = generate_invite_code()
        if not await session.scalar(select(Pool.id).where(Pool.invite_code == code)):
            return code
    raise PoolError("não foi possível gerar um código de convite único")
