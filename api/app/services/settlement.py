"""Apuração e ranking.

Propriedade central: **reprocessar produz exatamente o mesmo resultado**.
Apurar um jogo apaga os ``prediction_scores`` daquele jogo e recalcula do zero.
Isso é o que permite corrigir um placar de VAR ou de W.O. sem ninguém precisar
mexer no banco à mão — e vai acontecer, mais do que parece.

Fluxo de ``settle_fixture``:

    apaga os scores do jogo
      → recalcula com a config vigente de cada bolão
      → aplica o multiplicador da rodada
      → grava
      → recomputa o ranking do bolão
      → tira a foto da rodada, se ela acabou
      → enfileira notificação de quem mudou de posição
"""

from __future__ import annotations

from collections.abc import Sequence
from dataclasses import dataclass, field
from datetime import UTC, datetime

from sqlalchemy import delete, func, select
from sqlalchemy.ext.asyncio import AsyncSession

from app.core.logging import get_logger
from app.models import (
    AuditAction,
    AuditLog,
    Fixture,
    Membership,
    MembershipStatus,
    Pool,
    PoolKind,
    Prediction,
    PredictionScore,
    Standing,
    StandingsSnapshot,
)
from app.scoring import (
    CriterionKey,
    RankEntry,
    Score,
    apply_multiplier,
    rank,
    score,
)
from app.services import bonuses as bonus_service
from app.services import matchdays as matchday_service
from app.services import pools as pool_service

log = get_logger(__name__)


@dataclass
class SettlementResult:
    fixture_id: int
    pools_touched: list[int] = field(default_factory=list)
    predictions_scored: int = 0
    position_changes: list[dict[str, object]] = field(default_factory=list)
    skipped_reason: str | None = None

    @property
    def settled(self) -> bool:
        return self.skipped_reason is None


async def settle_fixture(
    session: AsyncSession,
    fixture_id: int,
    *,
    now: datetime | None = None,
    recomputar: bool = True,
) -> SettlementResult:
    """Apura um jogo em todos os bolões que o incluem. Idempotente.

    ``recomputar=False`` grava a pontuação e deixa o ranking para quem chamou.
    Serve para o laço de ``recompute_pool``, onde refazer o ranking inteiro a
    cada um dos 380 jogos custa milhares de consultas para produzir estados
    intermediários que são descartados na linha seguinte.
    """
    now = now or datetime.now(UTC)
    result = SettlementResult(fixture_id=fixture_id)

    fixture = await session.get(Fixture, fixture_id)
    if fixture is None:
        result.skipped_reason = "jogo não encontrado"
        return result

    # Jogo cancelado ou abandonado: ninguém pontua e ele sai do denominador do
    # ranking. Apagar os scores é o que o tira da conta.
    if fixture.status.is_void:
        await session.execute(
            delete(PredictionScore).where(PredictionScore.fixture_id == fixture_id)
        )
        fixture.settled_at = now
        fixture.settlement_dirty = False
        await session.flush()
        for pool_id in await _pools_with_fixture(session, fixture):
            await recompute_standings(session, pool_id, now=now)
        result.skipped_reason = f"jogo {fixture.status}: ninguém pontua"
        return result

    if not fixture.is_scoreable:
        result.skipped_reason = "jogo ainda não terminou ou está sem placar"
        return result

    actual_home, actual_away = fixture.effective_score  # type: ignore[misc]
    actual = Score(actual_home, actual_away)

    # Recomputar do zero é o que torna a operação idempotente.
    await session.execute(delete(PredictionScore).where(PredictionScore.fixture_id == fixture_id))

    pool_ids = await _pools_with_fixture(session, fixture)

    for pool_id in pool_ids:
        if not await pool_service.fixture_is_included(session, pool_id, fixture):
            continue

        config_row = await pool_service.active_config(session, pool_id)
        engine_config = pool_service.to_engine_config(config_row)

        # A rodada e o multiplicador vêm de lugares diferentes conforme o tipo
        # de bolão — mas daqui para baixo o código é o mesmo.
        matchday = await matchday_service.matchday_of_fixture(session, pool_id, fixture_id)
        if matchday is not None:
            multiplier = matchday.multiplier
            matchday_id: int | None = matchday.id
            round_id: int | None = None
        else:
            multiplier = await pool_service.multiplier_for_round(session, pool_id, fixture.round_id)
            matchday_id = None
            round_id = fixture.round_id

        predictions = (
            await session.execute(
                select(Prediction, Membership)
                .join(Membership, Prediction.membership_id == Membership.id)
                .where(
                    Membership.pool_id == pool_id,
                    Prediction.fixture_id == fixture_id,
                    Membership.status == MembershipStatus.ACTIVE,
                )
            )
        ).all()

        for prediction, membership in predictions:
            outcome = score(
                Score(prediction.home_goals, prediction.away_goals), actual, engine_config
            )
            final_points = apply_multiplier(outcome.base_points, multiplier)

            session.add(
                PredictionScore(
                    prediction_id=prediction.id,
                    membership_id=membership.id,
                    pool_id=pool_id,
                    fixture_id=fixture_id,
                    round_id=round_id,
                    matchday_id=matchday_id,
                    criterion_key=CriterionKey(str(outcome.criterion_key)),
                    base_points=outcome.base_points,
                    multiplier=multiplier,
                    final_points=final_points,
                    scoring_version=config_row.version,
                    computed_at=now,
                )
            )
            result.predictions_scored += 1

        # A configuração congela no primeiro palpite pontuado: a partir daqui,
        # mudar a regra cria versão nova em vez de reescrever o passado.
        if predictions and config_row.frozen_at is None:
            config_row.frozen_at = now

        # Bônus de mata-mata do mesmo jogo. Sai de graça aqui: o jogo acabou de
        # ser apurado e já sabemos quem avançou.
        await bonus_service.settle_knockout(session, fixture_id, pool_id, now=now)

        result.pools_touched.append(pool_id)

    await session.flush()

    was_resettle = fixture.settled_at is not None
    fixture.settled_at = now
    fixture.settlement_dirty = False

    if recomputar:
        for pool_id in result.pools_touched:
            changes = await recompute_standings(session, pool_id, now=now)
            result.position_changes.extend(changes)
            await _snapshot_round_if_complete(session, pool_id, fixture, now=now)

    session.add(
        AuditLog(
            action=AuditAction.FIXTURE_RESETTLED if was_resettle else AuditAction.FIXTURE_SETTLED,
            entity="fixture",
            entity_id=fixture_id,
            payload={
                "score": f"{actual_home}x{actual_away}",
                "pools": result.pools_touched,
                "predictions": result.predictions_scored,
                "position_changes": result.position_changes,
            },
        )
    )
    await session.flush()

    log.info(
        "settle.ok",
        fixture=fixture_id,
        pools=len(result.pools_touched),
        palpites=result.predictions_scored,
        reapuracao=was_resettle,
    )
    return result


async def _pools_with_fixture(session: AsyncSession, fixture: Fixture) -> list[int]:
    """Bolões que podem pontuar este jogo.

    São dois caminhos, e ignorar o segundo faria a rodada personalizada nunca
    apurar:

    - bolão de campeonato: roda a temporada do jogo;
    - bolão personalizado: o organizador colocou o jogo em alguma rodada.
    """
    por_temporada = (
        await session.scalars(
            select(Pool.id).where(Pool.season_id == fixture.season_id, Pool.kind == PoolKind.SEASON)
        )
    ).all()

    personalizados = await matchday_service.custom_pools_with_fixture(session, fixture.id)

    # dict.fromkeys preserva a ordem e remove repetição.
    return list(dict.fromkeys([*por_temporada, *personalizados]))


async def recompute_standings(
    session: AsyncSession, pool_id: int, *, now: datetime | None = None
) -> list[dict[str, object]]:
    """Recalcula o ranking inteiro do bolão a partir de ``prediction_scores``.

    Tabela derivada: apagar ``standings`` e chamar isto reconstrói tudo. É por
    isso que uma correção de placar não deixa resíduo.
    """
    now = now or datetime.now(UTC)

    previous = {
        row.membership_id: row.position
        for row in (
            await session.scalars(select(Standing).where(Standing.pool_id == pool_id))
        ).all()
    }

    members = (
        await session.scalars(
            select(Membership)
            .where(Membership.pool_id == pool_id, Membership.status == MembershipStatus.ACTIVE)
            .order_by(Membership.joined_at, Membership.id)
        )
    ).all()
    if not members:
        return []

    totals = {
        row.membership_id: row
        for row in (
            await session.execute(
                select(
                    PredictionScore.membership_id.label("membership_id"),
                    func.coalesce(func.sum(PredictionScore.final_points), 0).label("points"),
                    func.count(PredictionScore.id).label("settled"),
                )
                .where(PredictionScore.pool_id == pool_id)
                .group_by(PredictionScore.membership_id)
            )
        ).all()
    }

    hits_rows = (
        await session.execute(
            select(
                PredictionScore.membership_id,
                PredictionScore.criterion_key,
                func.count(PredictionScore.id),
            )
            .where(PredictionScore.pool_id == pool_id)
            .group_by(PredictionScore.membership_id, PredictionScore.criterion_key)
        )
    ).all()

    hits: dict[int, dict[CriterionKey, int]] = {}
    for membership_id, criterion_key, count in hits_rows:
        if str(criterion_key) == "miss":
            continue
        hits.setdefault(membership_id, {})[CriterionKey(str(criterion_key))] = count

    made: dict[int, int] = {
        row[0]: row[1]
        for row in (
            await session.execute(
                select(Prediction.membership_id, func.count(Prediction.id))
                .join(Membership, Prediction.membership_id == Membership.id)
                .where(Membership.pool_id == pool_id)
                .group_by(Prediction.membership_id)
            )
        ).all()
    }

    # Bônus (mata-mata e temporada) somam ao total, mas não contam como acerto
    # de critério — o desempate continua sendo sobre os palpites de placar.
    bonus = await bonus_service.bonus_points_by_membership(session, pool_id)

    entries = [
        RankEntry(
            membership_id=member.id,
            display_name=member.display_name,
            points=int(getattr(totals.get(member.id), "points", 0) or 0) + bonus.get(member.id, 0),
            criterion_hits=hits.get(member.id, {}),
            joined_order=index,
            predictions_made=made.get(member.id, 0),
            fixtures_settled=int(getattr(totals.get(member.id), "settled", 0) or 0),
        )
        for index, member in enumerate(members)
    ]

    engine_config = pool_service.to_engine_config(
        await pool_service.active_config(session, pool_id)
    )
    ranked = rank(entries, engine_config, previous_positions=previous)

    existing = {
        row.membership_id: row
        for row in (
            await session.scalars(select(Standing).where(Standing.pool_id == pool_id))
        ).all()
    }

    changes: list[dict[str, object]] = []
    for item in ranked:
        standing = existing.get(item.entry.membership_id)
        if standing is None:
            standing = Standing(pool_id=pool_id, membership_id=item.entry.membership_id)
            session.add(standing)

        standing.points = item.entry.points
        standing.predictions_made = item.entry.predictions_made
        standing.fixtures_settled = item.entry.fixtures_settled
        standing.criterion_hits = {
            str(key): value for key, value in item.entry.criterion_hits.items()
        }
        standing.previous_position = item.previous_position
        standing.position = item.position
        standing.computed_at = now

        if item.previous_position is not None and item.movement != 0:
            changes.append(
                {
                    "membership_id": item.entry.membership_id,
                    "display_name": item.entry.display_name,
                    "from": item.previous_position,
                    "to": item.position,
                    "movement": item.movement,
                }
            )

    await session.flush()
    return changes


async def _snapshot_round_if_complete(
    session: AsyncSession, pool_id: int, fixture: Fixture, *, now: datetime
) -> None:
    """Fotografa o ranking quando a última partida da rodada é apurada.

    Só para bolão de campeonato: a foto é indexada por ``round_id``, que a
    rodada personalizada não tem. O histórico do personalizado sai do ranking
    por rodada, que já funciona por ``matchday_id``.
    """
    if fixture.round_id is None:
        return

    pool = await session.get(Pool, pool_id)
    if pool is None or pool.kind is not PoolKind.SEASON:
        return

    pending = await session.scalar(
        select(func.count())
        .select_from(Fixture)
        .where(
            Fixture.round_id == fixture.round_id,
            Fixture.settled_at.is_(None),
            Fixture.status.notin_(["CANCELLED", "ABANDONED"]),
        )
    )
    if pending:
        return

    round_points: dict[int, int] = {
        row[0]: row[1]
        for row in (
            await session.execute(
                select(
                    PredictionScore.membership_id,
                    func.coalesce(func.sum(PredictionScore.final_points), 0),
                )
                .where(
                    PredictionScore.pool_id == pool_id,
                    PredictionScore.round_id == fixture.round_id,
                )
                .group_by(PredictionScore.membership_id)
            )
        ).all()
    }

    standings = (
        await session.scalars(
            select(Standing).where(Standing.pool_id == pool_id).order_by(Standing.position)
        )
    ).all()

    payload = [
        {
            "membership_id": standing.membership_id,
            "position": standing.position,
            "points": standing.points,
            "round_points": round_points.get(standing.membership_id, 0),
        }
        for standing in standings
    ]

    existing = await session.scalar(
        select(StandingsSnapshot).where(
            StandingsSnapshot.pool_id == pool_id,
            StandingsSnapshot.round_id == fixture.round_id,
        )
    )
    if existing is None:
        session.add(
            StandingsSnapshot(
                pool_id=pool_id,
                round_id=fixture.round_id,
                payload=payload,
                computed_at=now,
            )
        )
    else:
        # Reapuração da rodada: a foto tem que refletir o estado corrigido.
        existing.payload = payload
        existing.computed_at = now

    await session.flush()


async def settle_pending(
    session: AsyncSession, *, limit: int = 200, now: datetime | None = None
) -> list[SettlementResult]:
    """Apura tudo que está pendente ou marcado como sujo.

    Cada jogo é apurado e commitado por conta própria. Antes, um único jogo
    problemático — configuração de bolão inconsistente, mata-mata sem chave,
    tempo de statement estourado — abortava os outros 199 e descartava o que já
    tinha sido calculado. É a mesma contenção por item que a coleta já fazia por
    competição; ela só nunca tinha chegado até aqui.
    """
    from app.services.catalog import fixtures_needing_settlement

    # Os ids são materializados ANTES do laço, de propósito. `rollback()` expira
    # todos os objetos da sessão — inclusive com `expire_on_commit=False` — e
    # ler `fixture.id` depois dispararia um SELECT de recarga síncrono, que numa
    # sessão assíncrona vira `MissingGreenlet`. A exceção nasceria dentro do
    # `except`, ninguém a pegaria, e o lote inteiro morreria justamente no
    # caminho escrito para conter falha de um jogo só.
    pendentes = [jogo.id for jogo in await fixtures_needing_settlement(session)][:limit]

    results = []
    for fixture_id in pendentes:
        try:
            resultado = await settle_fixture(session, fixture_id, now=now)
            await session.commit()
        except Exception as exc:  # noqa: BLE001
            await session.rollback()
            log.warning(
                "apuracao.jogo_falhou",
                fixture_id=fixture_id,
                erro=str(exc),
                tipo=type(exc).__name__,
            )
            continue
        results.append(resultado)
    return results


async def standings_for(session: AsyncSession, pool_id: int) -> Sequence[Standing]:
    return (
        await session.scalars(
            select(Standing).where(Standing.pool_id == pool_id).order_by(Standing.position)
        )
    ).all()


async def round_standings(
    session: AsyncSession,
    pool_id: int,
    round_id: int | None = None,
    *,
    matchday_id: int | None = None,
) -> list[dict[str, object]]:
    """Ranking de uma rodada isolada.

    Serve aos dois tipos de bolão: ``round_id`` para o de campeonato,
    ``matchday_id`` para a rodada montada pelo organizador.
    """
    if matchday_id is not None:
        filtro = PredictionScore.matchday_id == matchday_id
    elif round_id is not None:
        filtro = PredictionScore.round_id == round_id
    else:
        raise ValueError("informe round_id ou matchday_id")

    rows = (
        await session.execute(
            select(
                PredictionScore.membership_id,
                Membership.display_name,
                func.coalesce(func.sum(PredictionScore.final_points), 0).label("points"),
                func.count(PredictionScore.id).label("scored"),
            )
            .join(Membership, PredictionScore.membership_id == Membership.id)
            .where(PredictionScore.pool_id == pool_id, filtro)
            .group_by(PredictionScore.membership_id, Membership.display_name)
            .order_by(func.coalesce(func.sum(PredictionScore.final_points), 0).desc())
        )
    ).all()

    return [
        {
            "membership_id": membership_id,
            "display_name": display_name,
            "points": int(points),
            "fixtures_scored": int(scored),
            "position": index,
        }
        for index, (membership_id, display_name, points, scored) in enumerate(rows, start=1)
    ]


async def recompute_pool(
    session: AsyncSession, pool_id: int, *, now: datetime | None = None
) -> dict[str, object]:
    """Reapura o bolão inteiro. Botão de emergência do organizador.

    Existe porque um dia alguém vai mudar a configuração, corrigir um placar
    antigo ou importar uma rodada errada — e a alternativa a este botão é
    alguém abrir o psql em produção.
    """
    now = now or datetime.now(UTC)
    pool = await session.get(Pool, pool_id)
    if pool is None:
        raise ValueError("bolão não encontrado")

    if pool.kind is PoolKind.CUSTOM:
        # Só os jogos que o organizador colocou em alguma rodada.
        fixture_ids = sorted(
            {
                fixture_id
                for rodada in await matchday_service.list_matchdays(session, pool_id)
                for fixture_id in await matchday_service.matchday_fixture_ids(session, rodada.id)
            }
        )
    else:
        todos = list(
            (
                await session.scalars(select(Fixture.id).where(Fixture.season_id == pool.season_id))
            ).all()
        )
        fixture_ids = sorted(await pool_service.included_fixture_ids(session, pool_id, todos))

    # `recomputar=False`: o ranking sai uma vez só, logo abaixo. Com ele ligado,
    # um Brasileirão de 380 jogos disparava 380 recomputações completas — cada
    # uma com sete agregações — só para que a última sobrescrevesse todas.
    settled = 0
    ultimo_de_cada_rodada: dict[int, Fixture] = {}
    for fixture_id in fixture_ids:
        outcome = await settle_fixture(session, fixture_id, now=now, recomputar=False)
        if outcome.settled:
            settled += 1
            jogo = await session.get(Fixture, fixture_id)
            if jogo is not None and jogo.round_id is not None:
                ultimo_de_cada_rodada[jogo.round_id] = jogo

    await recompute_standings(session, pool_id, now=now)

    # O retrato de rodada precisa de um jogo por rodada para saber qual fechar.
    for jogo in ultimo_de_cada_rodada.values():
        await _snapshot_round_if_complete(session, pool_id, jogo, now=now)
    session.add(
        AuditLog(
            action=AuditAction.STANDINGS_RECOMPUTED,
            pool_id=pool_id,
            entity="pool",
            entity_id=pool_id,
            payload={"fixtures": len(fixture_ids), "settled": settled},
        )
    )
    await session.flush()
    return {"fixtures": len(fixture_ids), "settled": settled}
