"""Rodadas montadas pelo organizador."""

from __future__ import annotations

from datetime import UTC, datetime

from fastapi import APIRouter, HTTPException, Path, status
from pydantic import BaseModel, Field
from sqlalchemy import select
from sqlalchemy.orm import selectinload

from app.api.deps import CurrentUser, ManagerDep, MembershipDep, PoolDep, SessionDep
from app.models import Fixture, Matchday, PoolKind
from app.schemas.common import FixtureOut, Message
from app.schemas.pool import MyPrediction, OtherPrediction
from app.services import matchdays as matchday_service
from app.services import predictions as prediction_service

router = APIRouter(prefix="/pools/{slug}/matchdays", tags=["rodadas do bolão"])


class MatchdayIn(BaseModel):
    name: str | None = Field(default=None, max_length=120)
    multiplier: int = Field(default=1, ge=1, le=10)


class MatchdayFixturesIn(BaseModel):
    fixture_ids: list[int] = Field(default_factory=list, max_length=100)


class MatchdayOut(BaseModel):
    id: int
    number: int
    name: str
    multiplier: int
    fixtures: int
    settled: int
    opens_at: datetime | None
    is_open: bool
    """Ainda dá para palpitar em pelo menos um jogo."""


async def _serialize(session, matchday: Matchday) -> MatchdayOut:  # type: ignore[no-untyped-def]
    ids = await matchday_service.matchday_fixture_ids(session, matchday.id)
    jogos = (await session.scalars(select(Fixture).where(Fixture.id.in_(ids)))).all() if ids else []
    agora = datetime.now(UTC)
    abertos = [jogo for jogo in jogos if not prediction_service.is_locked(jogo, agora)]

    return MatchdayOut(
        id=matchday.id,
        number=matchday.number,
        name=matchday.name,
        multiplier=matchday.multiplier,
        fixtures=len(jogos),
        settled=sum(1 for jogo in jogos if jogo.settled_at is not None),
        opens_at=min((jogo.kickoff_at for jogo in jogos), default=None),
        is_open=bool(abertos),
    )


def _exige_personalizado(pool) -> None:  # type: ignore[no-untyped-def]
    if pool.kind is not PoolKind.CUSTOM:
        raise HTTPException(
            status_code=status.HTTP_409_CONFLICT,
            detail="este bolão segue as rodadas do campeonato; use a tela de Jogos",
        )


@router.get("", response_model=list[MatchdayOut])
async def list_matchdays(
    pool: PoolDep, session: SessionDep, membership: MembershipDep
) -> list[MatchdayOut]:
    rodadas = await matchday_service.list_matchdays(session, pool.id)
    return [await _serialize(session, rodada) for rodada in rodadas]


@router.post("", response_model=MatchdayOut, status_code=status.HTTP_201_CREATED)
async def create_matchday(
    payload: MatchdayIn,
    pool: PoolDep,
    session: SessionDep,
    user: CurrentUser,
    _: ManagerDep,
) -> MatchdayOut:
    _exige_personalizado(pool)
    try:
        rodada = await matchday_service.create_matchday(
            session, pool=pool, actor=user, name=payload.name, multiplier=payload.multiplier
        )
    except matchday_service.MatchdayError as exc:
        raise HTTPException(status_code=status.HTTP_400_BAD_REQUEST, detail=str(exc)) from exc

    await session.commit()
    return await _serialize(session, rodada)


@router.patch("/{matchday_id}", response_model=MatchdayOut)
async def update_matchday(
    payload: MatchdayIn,
    pool: PoolDep,
    session: SessionDep,
    user: CurrentUser,
    _: ManagerDep,
    matchday_id: int = Path(),
) -> MatchdayOut:
    rodada = await matchday_service.get_matchday(session, pool.id, matchday_id)
    if rodada is None:
        raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail="rodada não encontrada")

    try:
        await matchday_service.rename_matchday(
            session,
            pool=pool,
            actor=user,
            matchday=rodada,
            name=payload.name,
            multiplier=payload.multiplier,
        )
    except matchday_service.MatchdayError as exc:
        raise HTTPException(status_code=status.HTTP_400_BAD_REQUEST, detail=str(exc)) from exc

    await session.commit()
    return await _serialize(session, rodada)


@router.delete("/{matchday_id}", response_model=Message)
async def delete_matchday(
    pool: PoolDep,
    session: SessionDep,
    user: CurrentUser,
    _: ManagerDep,
    matchday_id: int = Path(),
) -> Message:
    rodada = await matchday_service.get_matchday(session, pool.id, matchday_id)
    if rodada is None:
        raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail="rodada não encontrada")

    await matchday_service.delete_matchday(session, pool=pool, actor=user, matchday=rodada)
    await session.commit()
    return Message(detail="rodada apagada, junto com os palpites dela")


@router.get("/{matchday_id}/fixtures", response_model=list[FixtureOut])
async def matchday_fixtures(
    pool: PoolDep,
    session: SessionDep,
    membership: MembershipDep,
    matchday_id: int = Path(),
) -> list[FixtureOut]:
    rodada = await matchday_service.get_matchday(session, pool.id, matchday_id)
    if rodada is None:
        raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail="rodada não encontrada")

    ids = await matchday_service.matchday_fixture_ids(session, rodada.id)
    if not ids:
        return []

    jogos = (
        await session.scalars(
            select(Fixture)
            .where(Fixture.id.in_(ids))
            .options(
                selectinload(Fixture.home_team),
                selectinload(Fixture.away_team),
                selectinload(Fixture.round),
            )
            .order_by(Fixture.kickoff_at)
        )
    ).all()

    agora = datetime.now(UTC)
    saida = []
    for jogo in jogos:
        item = FixtureOut.model_validate(jogo)
        item.is_locked = prediction_service.is_locked(jogo, agora)
        item.is_included = True
        saida.append(item)

    from app.api.pools import _com_campeonato, _com_retrospecto

    # O campeonato importa MAIS aqui do que na rodada de campeonato: é aqui que
    # o organizador junta o clássico do Brasileirão com a volta da
    # Libertadores, e os dois aparecem lado a lado sem nada os distinguindo.
    return await _com_campeonato(
        session, jogos, await _com_retrospecto(session, jogos, saida)
    )


@router.get("/{matchday_id}/my-predictions", response_model=list[MyPrediction])
async def my_matchday_predictions(
    pool: PoolDep,
    session: SessionDep,
    membership: MembershipDep,
    matchday_id: int = Path(),
) -> list[MyPrediction]:
    ids = await matchday_service.matchday_fixture_ids(session, matchday_id)
    mine = await prediction_service.my_predictions(
        session, membership_id=membership.id, fixture_ids=ids
    )
    return [
        MyPrediction(
            fixture_id=fixture_id,
            home_goals=palpite.home_goals,
            away_goals=palpite.away_goals,
            updated_at=palpite.updated_at,
        )
        for fixture_id, palpite in mine.items()
    ]


@router.get("/{matchday_id}/predictions", response_model=list[OtherPrediction])
async def matchday_predictions(
    pool: PoolDep,
    session: SessionDep,
    membership: MembershipDep,
    matchday_id: int = Path(),
) -> list[OtherPrediction]:
    """Palpites da rodada montada. **Blindado**, igual ao resto.

    Passa pelo mesmo ``visible_predictions`` — é o que garante que um endpoint
    novo não vire a brecha.
    """
    ids = await matchday_service.matchday_fixture_ids(session, matchday_id)
    visiveis = await prediction_service.visible_predictions(
        session,
        pool_id=pool.id,
        fixture_ids=ids,
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
        for item in visiveis
    ]


@router.get("/{matchday_id}/standings", response_model=list[dict])
async def matchday_standings(
    pool: PoolDep,
    session: SessionDep,
    membership: MembershipDep,
    matchday_id: int = Path(),
) -> list[dict]:
    from app.services import settlement as settlement_service

    return await settlement_service.round_standings(session, pool.id, matchday_id=matchday_id)


@router.put("/{matchday_id}/fixtures", response_model=dict)
async def set_matchday_fixtures(
    payload: MatchdayFixturesIn,
    pool: PoolDep,
    session: SessionDep,
    user: CurrentUser,
    _: ManagerDep,
    matchday_id: int = Path(),
) -> dict:
    """Define os jogos da rodada. Substitui a lista inteira."""
    _exige_personalizado(pool)
    rodada = await matchday_service.get_matchday(session, pool.id, matchday_id)
    if rodada is None:
        raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail="rodada não encontrada")

    try:
        mudanca = await matchday_service.set_fixtures(
            session, pool=pool, actor=user, matchday=rodada, fixture_ids=payload.fixture_ids
        )
    except matchday_service.PoolError as exc:
        raise HTTPException(status_code=status.HTTP_400_BAD_REQUEST, detail=str(exc)) from exc

    await session.commit()
    return {
        "added": mudanca.added,
        "removed": mudanca.removed,
        "rejected": mudanca.rejected,
        "total": len(await matchday_service.matchday_fixture_ids(session, rodada.id)),
    }
