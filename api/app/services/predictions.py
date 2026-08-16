"""Palpites e blindagem.

Duas garantias diferentes, em camadas diferentes, de propósito:

- **Trava de escrita** (não se palpita depois do apito): trigger no Postgres.
  Vale para qualquer caminho que escreva na tabela, hoje e daqui a um ano.
- **Blindagem de leitura** (ninguém vê palpite alheio antes do início): aqui,
  em ``visible_predictions``. Depende do relógio no momento da leitura, então
  não dá para materializar no banco.

Todo endpoint que devolve palpite passa por ``visible_predictions``. Nenhum
monta a resposta por conta própria.
"""

from __future__ import annotations

from collections.abc import Sequence
from dataclasses import dataclass
from datetime import UTC, datetime

from sqlalchemy import func, select
from sqlalchemy.exc import DBAPIError, IntegrityError
from sqlalchemy.ext.asyncio import AsyncSession

from app.models import (
    Fixture,
    FixtureStatus,
    Membership,
    MembershipStatus,
    Prediction,
)


class PredictionError(Exception):
    pass


class PredictionLocked(PredictionError):
    """O jogo já começou."""


class FixtureNotInPool(PredictionError):
    pass


@dataclass(frozen=True, slots=True)
class VisiblePrediction:
    """Palpite pronto para serialização, já filtrado pela blindagem."""

    membership_id: int
    display_name: str
    fixture_id: int
    home_goals: int | None
    away_goals: int | None
    is_hidden: bool
    """Existe palpite, mas está blindado — a tela mostra "aguardando o apito"."""

    has_prediction: bool


def is_locked(fixture: Fixture, now: datetime) -> bool:
    """O palpite está fechado para este jogo?

    Duas condições, e o **status vem primeiro**:

    - Jogo adiado reabre. A nova data ainda não chegou e seria injusto manter
      travado um palpite que ninguém mais pode revisar.
    - Jogo que saiu de ``SCHEDULED`` está fechado, mesmo que o relógio ainda
      não tenha chegado no ``kickoff_at``. Isso acontece de verdade: no modo
      manual o organizador lança o placar antes da hora, ou a data veio errada
      no CSV. Sem esta regra o jogo ficaria pontuado e blindado ao mesmo tempo —
      o ranking mexe e ninguém consegue ver por quê.

    Mesma regra da trigger do banco. As duas precisam concordar.
    """
    if fixture.status is FixtureStatus.POSTPONED:
        return False
    if fixture.status is not FixtureStatus.SCHEDULED:
        return True
    return now >= fixture.kickoff_at


async def upsert_prediction(
    session: AsyncSession,
    *,
    membership: Membership,
    fixture: Fixture,
    home_goals: int,
    away_goals: int,
    now: datetime | None = None,
) -> Prediction:
    now = now or datetime.now(UTC)

    if membership.status is not MembershipStatus.ACTIVE:
        raise PredictionError("participação inativa neste bolão")
    if home_goals < 0 or away_goals < 0:
        raise PredictionError("placar não pode ser negativo")
    if home_goals > 99 or away_goals > 99:
        raise PredictionError("placar implausível")
    if is_locked(fixture, now):
        raise PredictionLocked("o jogo já começou; o palpite está fechado")

    existing = await session.scalar(
        select(Prediction).where(
            Prediction.membership_id == membership.id, Prediction.fixture_id == fixture.id
        )
    )

    try:
        if existing is None:
            prediction = Prediction(
                membership_id=membership.id,
                fixture_id=fixture.id,
                home_goals=home_goals,
                away_goals=away_goals,
            )
            session.add(prediction)
        else:
            prediction = existing
            prediction.home_goals = home_goals
            prediction.away_goals = away_goals
        await session.flush()
    except IntegrityError as exc:
        await session.rollback()
        raise PredictionError("palpite duplicado para este jogo") from exc
    except DBAPIError as exc:
        await session.rollback()
        # A trigger recusou. Chega aqui quando o relógio da aplicação e o do
        # banco divergem, ou quando o jogo começou entre a validação e o commit.
        if "palpite fechado" in str(exc.orig):
            raise PredictionLocked("o jogo já começou; o palpite está fechado") from exc
        raise

    return prediction


async def bulk_upsert(
    session: AsyncSession,
    *,
    membership: Membership,
    entries: Sequence[tuple[Fixture, int, int]],
    now: datetime | None = None,
) -> tuple[list[Prediction], list[dict[str, object]]]:
    """Grava vários palpites de uma rodada.

    Jogo travado no meio da lista não invalida os outros: quem preencheu a
    rodada inteira e demorou não pode perder tudo porque o primeiro jogo
    começou. Os recusados voltam descritos, para a tela marcar quais foram.
    """
    now = now or datetime.now(UTC)
    saved: list[Prediction] = []
    rejected: list[dict[str, object]] = []

    for fixture, home_goals, away_goals in entries:
        try:
            saved.append(
                await upsert_prediction(
                    session,
                    membership=membership,
                    fixture=fixture,
                    home_goals=home_goals,
                    away_goals=away_goals,
                    now=now,
                )
            )
        except PredictionError as exc:
            rejected.append({"fixture_id": fixture.id, "reason": str(exc)})

    return saved, rejected


async def visible_predictions(
    session: AsyncSession,
    *,
    pool_id: int,
    fixture_ids: Sequence[int],
    viewer_membership_id: int,
    now: datetime | None = None,
) -> list[VisiblePrediction]:
    """**Ponto único de blindagem.**

    O palpite de terceiro só aparece a partir do apito inicial daquele jogo. O
    do próprio usuário aparece sempre — ele acabou de digitar.

    Repare que a blindagem é por jogo, não por rodada: um jogo de sábado abre
    enquanto o de domingo continua fechado.
    """
    if not fixture_ids:
        return []

    now = now or datetime.now(UTC)

    rows = (
        await session.execute(
            select(Prediction, Membership, Fixture)
            .join(Membership, Prediction.membership_id == Membership.id)
            .join(Fixture, Prediction.fixture_id == Fixture.id)
            .where(
                Membership.pool_id == pool_id,
                Prediction.fixture_id.in_(fixture_ids),
                Membership.status == MembershipStatus.ACTIVE,
            )
        )
    ).all()

    result: list[VisiblePrediction] = []
    for prediction, membership, fixture in rows:
        is_own = membership.id == viewer_membership_id
        revealed = is_own or is_locked(fixture, now)

        result.append(
            VisiblePrediction(
                membership_id=membership.id,
                display_name=membership.display_name,
                fixture_id=fixture.id,
                home_goals=prediction.home_goals if revealed else None,
                away_goals=prediction.away_goals if revealed else None,
                is_hidden=not revealed,
                has_prediction=True,
            )
        )

    return result


async def my_predictions(
    session: AsyncSession, *, membership_id: int, fixture_ids: Sequence[int]
) -> dict[int, Prediction]:
    if not fixture_ids:
        return {}
    rows = (
        await session.scalars(
            select(Prediction).where(
                Prediction.membership_id == membership_id,
                Prediction.fixture_id.in_(fixture_ids),
            )
        )
    ).all()
    return {prediction.fixture_id: prediction for prediction in rows}


async def members_without_prediction(
    session: AsyncSession, *, pool_id: int, fixture_ids: Sequence[int]
) -> list[Membership]:
    """Quem ainda não palpitou — é o público do lembrete que gera retenção."""
    if not fixture_ids:
        return []

    members = (
        await session.scalars(
            select(Membership).where(
                Membership.pool_id == pool_id,
                Membership.status == MembershipStatus.ACTIVE,
            )
        )
    ).all()

    # Só está "em dia" quem palpitou em TODOS os jogos da lista. Quem
    # preencheu metade da rodada continua merecendo o lembrete.
    complete = set(
        (
            await session.scalars(
                select(Prediction.membership_id)
                .join(Membership, Prediction.membership_id == Membership.id)
                .where(
                    Membership.pool_id == pool_id,
                    Prediction.fixture_id.in_(fixture_ids),
                )
                .group_by(Prediction.membership_id)
                .having(func.count(Prediction.id) == len(fixture_ids))
            )
        ).all()
    )

    return [member for member in members if member.id not in complete]


async def prediction_coverage(
    session: AsyncSession, *, pool_id: int, fixture_ids: Sequence[int]
) -> dict[str, int]:
    """Taxa de preenchimento de uma rodada, para o relatório do organizador."""
    total_members = len(
        (
            await session.scalars(
                select(Membership.id).where(
                    Membership.pool_id == pool_id,
                    Membership.status == MembershipStatus.ACTIVE,
                )
            )
        ).all()
    )
    made = len(
        (
            await session.scalars(
                select(Prediction.id)
                .join(Membership, Prediction.membership_id == Membership.id)
                .where(Membership.pool_id == pool_id, Prediction.fixture_id.in_(fixture_ids))
            )
        ).all()
    )
    expected = total_members * len(fixture_ids)
    return {
        "members": total_members,
        "fixtures": len(fixture_ids),
        "predictions": made,
        "expected": expected,
        "percent": round(100 * made / expected) if expected else 0,
    }
