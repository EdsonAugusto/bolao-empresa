"""Fábricas de dados para os testes.

Deliberadamente diretas: criam o mínimo para o cenário existir, sem esconder o
que está sendo testado atrás de camadas de mágica.
"""

from __future__ import annotations

from datetime import UTC, datetime, timedelta

from sqlalchemy.ext.asyncio import AsyncSession

from app.core.security import hash_password
from app.models import (
    Competition,
    Fixture,
    FixtureStatus,
    Membership,
    MembershipRole,
    Pool,
    PoolRound,
    Provider,
    Round,
    ScoringConfig,
    Season,
    Team,
    User,
)
from app.scoring import CLASSIC, ScoringMode


async def make_user(
    session: AsyncSession,
    email: str = "jogador@teste.local",
    display_name: str = "Jogador",
    password: str = "senha-de-teste",
) -> User:
    user = User(
        email=email,
        password_hash=hash_password(password),
        display_name=display_name,
    )
    session.add(user)
    await session.flush()
    return user


async def make_season(session: AsyncSession, year: int = 2026) -> Season:
    provider = Provider(slug=f"teste-{year}", name="Provedor de teste")
    session.add(provider)
    await session.flush()

    competition = Competition(
        provider_id=provider.id,
        external_id=f"comp-{year}",
        slug=f"campeonato-teste-{year}",
        name="Campeonato de Teste",
    )
    session.add(competition)
    await session.flush()

    season = Season(competition_id=competition.id, year=year, is_current=True)
    session.add(season)
    await session.flush()
    return season


async def make_round(session: AsyncSession, season: Season, number: int = 1) -> Round:
    round_obj = Round(
        season_id=season.id,
        number=number,
        name=f"Rodada {number}",
        starts_at=datetime.now(UTC) + timedelta(days=1),
    )
    session.add(round_obj)
    await session.flush()
    return round_obj


async def make_team(session: AsyncSession, season: Season, name: str) -> Team:
    competition = await session.get(Competition, season.competition_id)
    assert competition is not None
    team = Team(
        provider_id=competition.provider_id,
        external_id=f"team-{name.lower().replace(' ', '-')}-{season.year}",
        slug=name.lower().replace(" ", "-"),
        name=name,
    )
    session.add(team)
    await session.flush()
    return team


async def make_fixture(
    session: AsyncSession,
    season: Season,
    round_obj: Round,
    home: Team,
    away: Team,
    *,
    kickoff_in: timedelta = timedelta(days=1),
    status: FixtureStatus = FixtureStatus.SCHEDULED,
    home_ft: int | None = None,
    away_ft: int | None = None,
) -> Fixture:
    competition = await session.get(Competition, season.competition_id)
    assert competition is not None
    fixture = Fixture(
        provider_id=competition.provider_id,
        external_id=f"fx-{home.slug}-{away.slug}-{round_obj.number}-{season.year}",
        season_id=season.id,
        round_id=round_obj.id,
        home_team_id=home.id,
        away_team_id=away.id,
        kickoff_at=datetime.now(UTC) + kickoff_in,
        status=status,
        home_ft=home_ft,
        away_ft=away_ft,
    )
    session.add(fixture)
    await session.flush()
    return fixture


async def make_pool(
    session: AsyncSession,
    owner: User,
    season: Season,
    *,
    name: str = "Bolão de Teste",
    rounds: list[Round] | None = None,
    multiplier: int = 1,
) -> Pool:
    pool = Pool(
        owner_id=owner.id,
        season_id=season.id,
        name=name,
        slug=f"bolao-teste-{owner.id}",
        invite_code=f"TESTE{owner.id:03d}",
    )
    session.add(pool)
    await session.flush()

    session.add(
        ScoringConfig(
            pool_id=pool.id,
            version=1,
            mode=ScoringMode.CLASSIC,
            criteria=CLASSIC.to_payload(),
        )
    )
    for round_obj in rounds or []:
        session.add(PoolRound(pool_id=pool.id, round_id=round_obj.id, multiplier=multiplier))

    session.add(
        Membership(
            pool_id=pool.id,
            user_id=owner.id,
            display_name=owner.display_name,
            role=MembershipRole.OWNER,
        )
    )
    await session.flush()
    return pool


async def add_member(
    session: AsyncSession, pool: Pool, user: User, display_name: str | None = None
) -> Membership:
    membership = Membership(
        pool_id=pool.id,
        user_id=user.id,
        display_name=display_name or user.display_name,
        role=MembershipRole.PLAYER,
    )
    session.add(membership)
    await session.flush()
    return membership
