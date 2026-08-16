"""Endpoints de palpites de bônus: mata-mata e temporada."""

from __future__ import annotations

from datetime import datetime

from fastapi import APIRouter, HTTPException, status
from pydantic import BaseModel, Field
from sqlalchemy import select

from app.api.deps import CurrentUser, ManagerDep, MembershipDep, PoolDep, SessionDep
from app.models import BonusKind, Fixture, Season, Team
from app.schemas.common import Message
from app.services import bonuses as bonus_service
from app.services import settlement as settlement_service

router = APIRouter(prefix="/pools/{slug}/bonus", tags=["bônus"])


class BonusIn(BaseModel):
    kind: BonusKind
    team_ids: list[int] = Field(min_length=1, max_length=20)
    reference_id: int = Field(
        default=0, description="Id do jogo, para mata-mata. Zero nos de temporada."
    )


class BonusOut(BaseModel):
    kind: str
    reference_id: int
    team_ids: list[int]
    locks_at: datetime
    is_locked: bool
    points_awarded: int | None
    settled_at: datetime | None


class SeasonOutcomeIn(BaseModel):
    champion_team_id: int | None = None
    top4_team_ids: list[int] = Field(default_factory=list, max_length=8)
    relegated_team_ids: list[int] = Field(default_factory=list, max_length=10)


class KnockoutFixtureOut(BaseModel):
    fixture_id: int
    label: str
    kickoff_at: datetime
    is_locked: bool
    home_team_id: int
    away_team_id: int
    advancing_team_id: int | None


@router.get("", response_model=list[BonusOut])
async def my_bonuses(
    pool: PoolDep, session: SessionDep, membership: MembershipDep
) -> list[BonusOut]:
    from datetime import UTC

    agora = datetime.now(UTC)
    itens = await bonus_service.my_bonuses(session, membership.id)
    return [
        BonusOut(
            kind=str(item.kind),
            reference_id=item.reference_id,
            team_ids=list(item.payload.get("team_ids", [])),
            locks_at=item.locks_at,
            is_locked=agora >= item.locks_at,
            points_awarded=item.points_awarded,
            settled_at=item.settled_at,
        )
        for item in itens
    ]


@router.put("", response_model=Message)
async def save_bonus(
    payload: BonusIn, pool: PoolDep, session: SessionDep, membership: MembershipDep
) -> Message:
    try:
        if payload.kind is BonusKind.KNOCKOUT_ADVANCE:
            if not payload.reference_id:
                raise HTTPException(
                    status_code=status.HTTP_422_UNPROCESSABLE_ENTITY,
                    detail="palpite de mata-mata precisa do jogo",
                )
            locks_at = await bonus_service.knockout_lock_for(session, payload.reference_id)
        else:
            locks_at = await bonus_service.season_lock_for(session, pool)

        await bonus_service.save_bonus(
            session,
            membership=membership,
            kind=payload.kind,
            team_ids=payload.team_ids,
            reference_id=payload.reference_id,
            locks_at=locks_at,
        )
    except bonus_service.BonusLocked as exc:
        raise HTTPException(status_code=status.HTTP_409_CONFLICT, detail=str(exc)) from exc
    except bonus_service.BonusError as exc:
        raise HTTPException(
            status_code=status.HTTP_422_UNPROCESSABLE_ENTITY, detail=str(exc)
        ) from exc

    await session.commit()
    return Message(detail="palpite de bônus salvo")


@router.get("/knockout", response_model=list[KnockoutFixtureOut])
async def knockout_fixtures(
    pool: PoolDep, session: SessionDep, membership: MembershipDep
) -> list[KnockoutFixtureOut]:
    from datetime import UTC

    agora = datetime.now(UTC)
    fixtures = await bonus_service.knockout_fixtures(session, pool.season_id)

    nomes = {team.id: team.name for team in (await session.scalars(select(Team))).all()}
    return [
        KnockoutFixtureOut(
            fixture_id=fixture.id,
            # A tela separa mandante e visitante por " x " — o sinal de
            # multiplicação daria ambiguidade de caractere no código-fonte.
            label=(
                f"{nomes.get(fixture.home_team_id, '?')} x {nomes.get(fixture.away_team_id, '?')}"
            ),
            kickoff_at=fixture.kickoff_at,
            is_locked=agora >= fixture.kickoff_at,
            home_team_id=fixture.home_team_id,
            away_team_id=fixture.away_team_id,
            advancing_team_id=fixture.advancing_team_id,
        )
        for fixture in fixtures
    ]


@router.get("/outcome")
async def get_outcome(pool: PoolDep, session: SessionDep, membership: MembershipDep) -> dict:
    if pool.season_id is None:
        # Palpite de temporada não faz sentido num bolão que mistura
        # campeonatos: não existe "o campeão" de uma rodada montada à mão.
        return {"outcome": {}, "teams": []}

    season = await session.get(Season, pool.season_id)
    times = (
        await session.scalars(
            select(Team)
            .join(Fixture, Fixture.home_team_id == Team.id)
            .where(Fixture.season_id == pool.season_id)
            .distinct()
        )
    ).all()
    return {
        "outcome": season.outcome if season else {},
        "teams": [{"id": team.id, "name": team.name} for team in times],
    }


@router.put("/outcome", response_model=Message)
async def set_outcome(
    payload: SeasonOutcomeIn,
    pool: PoolDep,
    session: SessionDep,
    user: CurrentUser,
    _: ManagerDep,
) -> Message:
    """Declara o desfecho da temporada e apura os palpites de temporada.

    Declarado e não calculado: montar a tabela exige o regulamento de cada
    campeonato, e numa plataforma em que o organizador já lança os placares à
    mão, pedir três campos no fim é mais honesto do que reimplementar critérios
    de classificação que mudam por torneio.
    """
    season = await session.get(Season, pool.season_id)
    if season is None:
        raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail="temporada não existe")

    season.outcome = {
        "champion_team_id": payload.champion_team_id,
        "top4_team_ids": payload.top4_team_ids,
        "relegated_team_ids": payload.relegated_team_ids,
    }
    await session.flush()

    resultado = await bonus_service.settle_season_bonuses(session, pool.id)
    await settlement_service.recompute_standings(session, pool.id)
    await session.commit()

    if resultado.skipped_reason:
        return Message(detail=f"desfecho salvo, mas nada foi apurado: {resultado.skipped_reason}")
    return Message(
        detail=f"desfecho salvo; {resultado.settled} palpite(s) apurado(s), "
        f"{resultado.points_awarded} ponto(s) distribuído(s)"
    )
