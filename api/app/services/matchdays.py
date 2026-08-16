"""Rodadas montadas pelo organizador.

Um bolão personalizado não pertence a campeonato nenhum: o organizador cria uma
rodada por semana e escolhe os jogos, de onde quiser. O clássico do Brasileirão,
o jogo grande da Premier League e a decisão da Libertadores podem estar na mesma.

Duas regras que separam este modo do bolão de campeonato:

- A rodada é **exatamente** o que foi escolhido. Não existe "todos os jogos
  daquele dia menos estes" — a lista é explícita.
- Jogo que já começou **não pode ser adicionado**. Colocá-lo na rodada depois
  do apito seria pedir palpite de resultado conhecido; e como a trava está no
  banco, o palpite seria recusado de qualquer forma, deixando um jogo na rodada
  que ninguém consegue palpitar.
"""

from __future__ import annotations

from collections.abc import Sequence
from dataclasses import dataclass
from datetime import UTC, datetime

from sqlalchemy import delete, func, select
from sqlalchemy.ext.asyncio import AsyncSession

from app.core.logging import get_logger
from app.models import (
    AuditAction,
    AuditLog,
    Fixture,
    Matchday,
    MatchdayFixture,
    Pool,
    PoolKind,
    User,
)
from app.services.pools import NotAuthorized, PoolError, require_manager

log = get_logger(__name__)


class MatchdayError(PoolError):
    pass


@dataclass
class FixtureChange:
    added: list[int]
    removed: list[int]
    rejected: list[dict[str, object]]


async def create_matchday(
    session: AsyncSession,
    *,
    pool: Pool,
    actor: User,
    name: str | None = None,
    multiplier: int = 1,
) -> Matchday:
    """Cria a próxima rodada do bolão."""
    await require_manager(session, pool.id, actor.id)
    if pool.kind is not PoolKind.CUSTOM:
        raise MatchdayError("este bolão segue as rodadas do campeonato")
    if multiplier < 1:
        raise MatchdayError("o multiplicador precisa ser pelo menos 1")

    proximo = (
        await session.scalar(
            select(func.coalesce(func.max(Matchday.number), 0)).where(Matchday.pool_id == pool.id)
        )
    ) or 0
    numero = proximo + 1

    rodada = Matchday(
        pool_id=pool.id,
        number=numero,
        name=(name or "").strip() or f"Rodada {numero}",
        multiplier=multiplier,
    )
    session.add(rodada)
    await session.flush()

    session.add(
        AuditLog(
            action=AuditAction.POOL_UPDATED,
            actor_user_id=actor.id,
            pool_id=pool.id,
            entity="matchday",
            entity_id=rodada.id,
            payload={"name": rodada.name, "number": numero},
        )
    )
    return rodada


async def rename_matchday(
    session: AsyncSession,
    *,
    pool: Pool,
    actor: User,
    matchday: Matchday,
    name: str | None = None,
    multiplier: int | None = None,
) -> Matchday:
    await require_manager(session, pool.id, actor.id)
    if name is not None and name.strip():
        matchday.name = name.strip()
    if multiplier is not None:
        if multiplier < 1:
            raise MatchdayError("o multiplicador precisa ser pelo menos 1")
        matchday.multiplier = multiplier
    await session.flush()
    return matchday


async def delete_matchday(
    session: AsyncSession, *, pool: Pool, actor: User, matchday: Matchday
) -> None:
    """Apaga a rodada. Os palpites vão junto por cascata."""
    await require_manager(session, pool.id, actor.id)
    if matchday.pool_id != pool.id:
        raise NotAuthorized("essa rodada é de outro bolão")
    await session.delete(matchday)
    await session.flush()


async def set_fixtures(
    session: AsyncSession,
    *,
    pool: Pool,
    actor: User,
    matchday: Matchday,
    fixture_ids: Sequence[int],
    now: datetime | None = None,
) -> FixtureChange:
    """Define os jogos da rodada.

    Substitui a lista inteira. Jogos que já começaram são recusados na entrada
    — mas se já estavam na rodada, **permanecem**: tirar um jogo em andamento
    apagaria os palpites de quem já respondeu.
    """
    await require_manager(session, pool.id, actor.id)
    if matchday.pool_id != pool.id:
        raise NotAuthorized("essa rodada é de outro bolão")

    now = now or datetime.now(UTC)
    desejados = set(fixture_ids)

    atuais = {
        linha.fixture_id: linha
        for linha in (
            await session.scalars(
                select(MatchdayFixture).where(MatchdayFixture.matchday_id == matchday.id)
            )
        ).all()
    }

    jogos = {
        fixture.id: fixture
        for fixture in (
            await session.scalars(select(Fixture).where(Fixture.id.in_(desejados | set(atuais))))
        ).all()
    }

    from app.services.predictions import is_locked

    rejeitados: list[dict[str, object]] = []
    adicionados: list[int] = []

    for fixture_id in desejados - set(atuais):
        fixture = jogos.get(fixture_id)
        if fixture is None:
            rejeitados.append({"fixture_id": fixture_id, "reason": "jogo não existe"})
            continue
        if is_locked(fixture, now):
            rejeitados.append(
                {
                    "fixture_id": fixture_id,
                    "reason": "o jogo já começou; ninguém poderia palpitar nele",
                }
            )
            continue
        session.add(MatchdayFixture(matchday_id=matchday.id, fixture_id=fixture_id))
        adicionados.append(fixture_id)

    removidos: list[int] = []
    for fixture_id, linha in atuais.items():
        if fixture_id in desejados:
            continue
        fixture = jogos.get(fixture_id)
        if fixture is not None and is_locked(fixture, now):
            # Já começou e alguém pode ter palpitado. Sai da lista pedida, mas
            # continua na rodada — o histórico é mais importante que a edição.
            rejeitados.append(
                {
                    "fixture_id": fixture_id,
                    "reason": "o jogo já começou e não pode ser tirado da rodada",
                }
            )
            continue
        await session.delete(linha)
        removidos.append(fixture_id)

    await session.flush()

    session.add(
        AuditLog(
            action=AuditAction.POOL_UPDATED,
            actor_user_id=actor.id,
            pool_id=pool.id,
            entity="matchday_fixtures",
            entity_id=matchday.id,
            payload={"added": adicionados, "removed": removidos},
        )
    )
    log.info(
        "matchday.jogos",
        pool=pool.id,
        matchday=matchday.id,
        adicionados=len(adicionados),
        removidos=len(removidos),
        recusados=len(rejeitados),
    )
    return FixtureChange(added=adicionados, removed=removidos, rejected=rejeitados)


async def list_matchdays(session: AsyncSession, pool_id: int) -> Sequence[Matchday]:
    return (
        await session.scalars(
            select(Matchday).where(Matchday.pool_id == pool_id).order_by(Matchday.number)
        )
    ).all()


async def get_matchday(session: AsyncSession, pool_id: int, matchday_id: int) -> Matchday | None:
    rodada: Matchday | None = await session.scalar(
        select(Matchday).where(Matchday.id == matchday_id, Matchday.pool_id == pool_id)
    )
    return rodada


async def matchday_fixture_ids(session: AsyncSession, matchday_id: int) -> list[int]:
    return list(
        (
            await session.scalars(
                select(MatchdayFixture.fixture_id).where(MatchdayFixture.matchday_id == matchday_id)
            )
        ).all()
    )


async def fixture_ids_por_rodada(
    session: AsyncSession, matchday_ids: Sequence[int]
) -> dict[int, list[int]]:
    """Os jogos de várias rodadas de uma vez.

    Existe para a listagem de rodadas, que chamava ``matchday_fixture_ids`` uma
    vez por rodada. Numa temporada com trinta rodadas montadas isso eram trinta
    idas ao banco para montar uma tela só.
    """
    if not matchday_ids:
        return {}

    linhas = await session.execute(
        select(MatchdayFixture.matchday_id, MatchdayFixture.fixture_id).where(
            MatchdayFixture.matchday_id.in_(matchday_ids)
        )
    )
    por_rodada: dict[int, list[int]] = {mid: [] for mid in matchday_ids}
    for matchday_id, fixture_id in linhas:
        por_rodada.setdefault(matchday_id, []).append(fixture_id)
    return por_rodada


async def matchday_of_fixture(
    session: AsyncSession, pool_id: int, fixture_id: int
) -> Matchday | None:
    """Em que rodada do bolão este jogo entrou."""
    rodada: Matchday | None = await session.scalar(
        select(Matchday)
        .join(MatchdayFixture, MatchdayFixture.matchday_id == Matchday.id)
        .where(Matchday.pool_id == pool_id, MatchdayFixture.fixture_id == fixture_id)
        .limit(1)
    )
    return rodada


async def custom_pools_with_fixture(session: AsyncSession, fixture_id: int) -> list[int]:
    """Bolões personalizados que incluem este jogo. Usado pela apuração."""
    return list(
        (
            await session.scalars(
                select(Matchday.pool_id)
                .join(MatchdayFixture, MatchdayFixture.matchday_id == Matchday.id)
                .where(MatchdayFixture.fixture_id == fixture_id)
                .distinct()
            )
        ).all()
    )


async def clear_fixtures(session: AsyncSession, matchday_id: int) -> int:
    """Esvazia a rodada. Devolve quantos jogos saíram."""
    ids = await matchday_fixture_ids(session, matchday_id)
    if ids:
        await session.execute(
            delete(MatchdayFixture).where(MatchdayFixture.matchday_id == matchday_id)
        )
        await session.flush()
    return len(ids)
