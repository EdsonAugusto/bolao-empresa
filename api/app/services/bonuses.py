"""Palpites que não são de placar.

Dois tipos, com naturezas diferentes:

- **Mata-mata** (``knockout_advance``): quem passa de fase, independente do
  placar. Trava no apito do jogo e é apurado quando ele termina. É o único
  lugar do produto em que os pênaltis decidem alguma coisa.
- **Temporada** (``champion``, ``top4``, ``relegated``): travam no início e são
  apurados no fim, contra o desfecho declarado pelo organizador.

Como no motor de pontuação, a apuração é **idempotente**: reprocessar produz o
mesmo resultado.
"""

from __future__ import annotations

from collections.abc import Sequence
from dataclasses import dataclass
from datetime import UTC, datetime

from sqlalchemy import select
from sqlalchemy.exc import DBAPIError
from sqlalchemy.ext.asyncio import AsyncSession

from app.core.logging import get_logger
from app.models import (
    BonusKind,
    BonusPrediction,
    Fixture,
    Membership,
    MembershipStatus,
    Pool,
    Round,
    ScoringConfig,
    Season,
    Stage,
)

log = get_logger(__name__)

SEASON_KINDS = (BonusKind.CHAMPION, BonusKind.TOP4, BonusKind.RELEGATED)

#: Quantos times cada tipo de palpite de temporada aceita.
EXPECTED_TEAMS: dict[BonusKind, int | None] = {
    BonusKind.CHAMPION: 1,
    BonusKind.TOP4: 4,
    BonusKind.RELEGATED: None,  # varia por campeonato
    BonusKind.KNOCKOUT_ADVANCE: 1,
}


class BonusError(Exception):
    pass


class BonusLocked(BonusError):
    """Passou do prazo."""


@dataclass
class BonusSettlement:
    settled: int = 0
    points_awarded: int = 0
    skipped_reason: str | None = None


# ---------------------------------------------------------------------------
# Escrita
# ---------------------------------------------------------------------------


async def save_bonus(
    session: AsyncSession,
    *,
    membership: Membership,
    kind: BonusKind,
    team_ids: Sequence[int],
    reference_id: int = 0,
    locks_at: datetime,
) -> BonusPrediction:
    """Grava um palpite de bônus. A trava é garantida por trigger no banco."""
    if membership.status is not MembershipStatus.ACTIVE:
        raise BonusError("participação inativa neste bolão")

    expected = EXPECTED_TEAMS[kind]
    if expected is not None and len(team_ids) != expected:
        raise BonusError(f"{kind} espera {expected} time(s), recebeu {len(team_ids)}")
    if len(set(team_ids)) != len(team_ids):
        raise BonusError("time repetido no palpite")
    if not team_ids:
        raise BonusError("informe pelo menos um time")

    existing = await session.scalar(
        select(BonusPrediction).where(
            BonusPrediction.membership_id == membership.id,
            BonusPrediction.kind == kind,
            BonusPrediction.reference_id == reference_id,
        )
    )

    try:
        if existing is None:
            bonus = BonusPrediction(
                membership_id=membership.id,
                kind=kind,
                reference_id=reference_id,
                payload={"team_ids": list(team_ids)},
                locks_at=locks_at,
            )
            session.add(bonus)
        else:
            bonus = existing
            bonus.payload = {"team_ids": list(team_ids)}
            bonus.locks_at = locks_at
        await session.flush()
    except DBAPIError as exc:
        await session.rollback()
        if "bonus fechado" in str(exc.orig):
            raise BonusLocked("o prazo deste palpite já passou") from exc
        raise

    return bonus


async def knockout_lock_for(session: AsyncSession, fixture_id: int) -> datetime:
    """O palpite de mata-mata trava no apito do jogo, como o de placar."""
    fixture = await session.get(Fixture, fixture_id)
    if fixture is None:
        raise BonusError("jogo não encontrado")
    return fixture.kickoff_at


async def season_lock_for(session: AsyncSession, pool: Pool) -> datetime:
    """Palpite de temporada trava no início do campeonato.

    Sem data configurada, cai no primeiro jogo da temporada — que é a definição
    natural de "começou".
    """
    if pool.season_bonus_locks_at is not None:
        return pool.season_bonus_locks_at

    first = await session.scalar(
        select(Fixture.kickoff_at)
        .where(Fixture.season_id == pool.season_id)
        .order_by(Fixture.kickoff_at)
        .limit(1)
    )
    if first is None:
        raise BonusError("a temporada não tem jogos ainda")
    return first


# ---------------------------------------------------------------------------
# Apuração
# ---------------------------------------------------------------------------


async def settle_knockout(
    session: AsyncSession, fixture_id: int, pool_id: int, *, now: datetime | None = None
) -> BonusSettlement:
    """Apura o bônus "quem avança" de um confronto.

    Repare que quem avança pode ser diferente de quem venceu: nos pênaltis,
    ``advancing_team_id`` aponta para o classificado enquanto o jogo, para
    efeito de pontuação de placar, terminou empatado.
    """
    now = now or datetime.now(UTC)
    result = BonusSettlement()

    fixture = await session.get(Fixture, fixture_id)
    if fixture is None or not fixture.status.is_final:
        result.skipped_reason = "jogo não terminou"
        return result
    if fixture.advancing_team_id is None:
        result.skipped_reason = "o confronto não define quem avança"
        return result

    config = await session.scalar(
        select(ScoringConfig)
        .where(ScoringConfig.pool_id == pool_id)
        .order_by(ScoringConfig.version.desc())
        .limit(1)
    )
    if config is None or config.knockout_advance_points <= 0:
        result.skipped_reason = "este bolão não pontua mata-mata"
        return result

    palpites = (
        await session.scalars(
            select(BonusPrediction)
            .join(Membership, BonusPrediction.membership_id == Membership.id)
            .where(
                Membership.pool_id == pool_id,
                Membership.status == MembershipStatus.ACTIVE,
                BonusPrediction.kind == BonusKind.KNOCKOUT_ADVANCE,
                BonusPrediction.reference_id == fixture_id,
            )
        )
    ).all()

    for palpite in palpites:
        escolhidos = list(palpite.payload.get("team_ids", []))
        acertou = bool(escolhidos) and escolhidos[0] == fixture.advancing_team_id
        palpite.points_awarded = config.knockout_advance_points if acertou else 0
        palpite.settled_at = now
        result.settled += 1
        result.points_awarded += palpite.points_awarded

    await session.flush()
    return result


async def settle_season_bonuses(
    session: AsyncSession, pool_id: int, *, now: datetime | None = None
) -> BonusSettlement:
    """Apura campeão, G-4 e rebaixados contra o desfecho declarado.

    O G-4 e os rebaixados pontuam **por acerto**: quem cravou três dos quatro
    leva três vezes os pontos. Tudo ou nada tornaria o palpite quase inútil num
    grupo de amigos.
    """
    now = now or datetime.now(UTC)
    result = BonusSettlement()

    pool = await session.get(Pool, pool_id)
    if pool is None:
        result.skipped_reason = "bolão não encontrado"
        return result

    season = await session.get(Season, pool.season_id)
    outcome = dict(season.outcome or {}) if season else {}
    if not outcome:
        result.skipped_reason = "o desfecho da temporada ainda não foi declarado"
        return result

    config = await session.scalar(
        select(ScoringConfig)
        .where(ScoringConfig.pool_id == pool_id)
        .order_by(ScoringConfig.version.desc())
        .limit(1)
    )
    if config is None:
        result.skipped_reason = "bolão sem configuração de pontuação"
        return result

    gabarito: dict[BonusKind, tuple[set[int], int]] = {
        BonusKind.CHAMPION: (
            {int(outcome["champion_team_id"])} if outcome.get("champion_team_id") else set(),
            config.champion_points,
        ),
        BonusKind.TOP4: (
            {int(team) for team in outcome.get("top4_team_ids", [])},
            config.top4_points,
        ),
        BonusKind.RELEGATED: (
            {int(team) for team in outcome.get("relegated_team_ids", [])},
            config.relegated_points,
        ),
    }

    palpites = (
        await session.scalars(
            select(BonusPrediction)
            .join(Membership, BonusPrediction.membership_id == Membership.id)
            .where(
                Membership.pool_id == pool_id,
                Membership.status == MembershipStatus.ACTIVE,
                BonusPrediction.kind.in_(SEASON_KINDS),
            )
        )
    ).all()

    for palpite in palpites:
        certos, por_acerto = gabarito.get(BonusKind(palpite.kind), (set(), 0))
        if not certos or por_acerto <= 0:
            palpite.points_awarded = 0
            palpite.settled_at = now
            result.settled += 1
            continue

        escolhidos = {int(team) for team in palpite.payload.get("team_ids", [])}
        acertos = len(escolhidos & certos)
        palpite.points_awarded = acertos * por_acerto
        palpite.settled_at = now
        result.settled += 1
        result.points_awarded += palpite.points_awarded

    await session.flush()
    log.info("bonus.apurado", pool=pool_id, palpites=result.settled)
    return result


async def bonus_points_by_membership(session: AsyncSession, pool_id: int) -> dict[int, int]:
    """Pontos de bônus por participante, para somar no ranking."""
    rows = (
        await session.execute(
            select(BonusPrediction.membership_id, BonusPrediction.points_awarded)
            .join(Membership, BonusPrediction.membership_id == Membership.id)
            .where(
                Membership.pool_id == pool_id,
                BonusPrediction.points_awarded.isnot(None),
            )
        )
    ).all()

    total: dict[int, int] = {}
    for membership_id, points in rows:
        total[membership_id] = total.get(membership_id, 0) + int(points or 0)
    return total


async def knockout_fixtures(session: AsyncSession, season_id: int) -> Sequence[Fixture]:
    """Jogos de fase eliminatória da temporada."""
    return (
        await session.scalars(
            select(Fixture)
            .join(Round, Fixture.round_id == Round.id)
            .join(Stage, Round.stage_id == Stage.id)
            .where(Fixture.season_id == season_id, Stage.is_knockout.is_(True))
            .order_by(Fixture.kickoff_at)
        )
    ).all()


async def my_bonuses(session: AsyncSession, membership_id: int) -> Sequence[BonusPrediction]:
    return (
        await session.scalars(
            select(BonusPrediction).where(BonusPrediction.membership_id == membership_id)
        )
    ).all()
