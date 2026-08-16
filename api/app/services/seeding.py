"""Criação da temporada do Brasileirão.

Passa pelo **mesmo caminho da ingestão de qualquer provedor** — monta os DTOs e
chama ``ingest_snapshot``. Não existe atalho escrevendo direto nas tabelas: se
existisse, o seed divergiria da importação real na primeira mudança de schema,
e o bug só apareceria em produção.

Como é upsert por ``(provider_id, external_id)``, rodar de novo atualiza em vez
de duplicar. Isso importa: quando o calendário oficial sair, o organizador
reimporta por cima.
"""

from __future__ import annotations

from datetime import UTC, datetime, timedelta
from typing import NamedTuple

from sqlalchemy.ext.asyncio import AsyncSession

from app.core.logging import get_logger
from app.data.brasileirao import CLUBS, Club, generate_season, resolve_first_round
from app.data.crests import crest_path
from app.models.enums import CompetitionType, FixtureStatus
from app.providers.base import (
    ProviderCompetition,
    ProviderFixture,
    ProviderRound,
    ProviderSeason,
    ProviderSnapshot,
    ProviderTeam,
)
from app.services.catalog import IngestStats, ingest_snapshot

log = get_logger(__name__)

COMPETITION_SLUG = "brasileirao-serie-a"
COMPETITION_NAME = "Brasileirão Série A"
PROVIDER_SLUG = "manual"
PROVIDER_NAME = "Cadastro manual"


class SeededSeason(NamedTuple):
    season_id: int
    teams: int
    rounds: int
    fixtures: int
    first_round_at: datetime
    stats: IngestStats


def build_snapshot(
    year: int,
    clubs: tuple[Club, ...] = CLUBS,
    *,
    first_round: datetime | None = None,
) -> ProviderSnapshot:
    """Monta a temporada inteira como se tivesse vindo de um provedor.

    ``first_round`` decide quando a rodada 1 cai. O padrão é abril, ou o
    próximo sábado se abril já passou — quem cria o campeonato do ano corrente
    em agosto não quer metade das rodadas nascendo fechada.
    """
    if first_round is None:
        first_round = resolve_first_round(year, datetime.now(UTC))
    competition = ProviderCompetition(
        external_id=f"{COMPETITION_SLUG}-{year}",
        slug=f"{COMPETITION_SLUG}-{year}",
        name=f"{COMPETITION_NAME} {year}",
        country="Brasil",
        type=CompetitionType.LEAGUE,
    )

    ultimo = first_round + timedelta(weeks=37, days=1)
    season = ProviderSeason(
        external_id=f"{COMPETITION_SLUG}-{year}",
        competition_external_id=competition.external_id,
        year=year,
        start_date=first_round.date(),
        end_date=ultimo.date(),
        is_current=True,
    )

    teams = tuple(
        ProviderTeam(
            external_id=club.slug,
            name=club.name,
            slug=club.slug,
            short_name=club.code,
            crest_url=crest_path(club.slug),
            country="Brasil",
        )
        for club in clubs
    )

    fixtures = tuple(
        ProviderFixture(
            external_id=(
                f"{COMPETITION_SLUG}-{year}-r{partida.round_number:02d}"
                f"-{partida.home_slug}-{partida.away_slug}"
            ),
            home_team_external_id=partida.home_slug,
            away_team_external_id=partida.away_slug,
            kickoff_at=partida.kickoff_at,
            status=FixtureStatus.SCHEDULED,
            round=ProviderRound(
                name=f"Rodada {partida.round_number}",
                number=partida.round_number,
            ),
        )
        for partida in generate_season(year, clubs=clubs, first_round_saturday=first_round)
    )

    return ProviderSnapshot(competition=competition, season=season, teams=teams, fixtures=fixtures)


async def seed_brasileirao(
    session: AsyncSession,
    year: int,
    *,
    clubs: tuple[Club, ...] = CLUBS,
    first_round: datetime | None = None,
) -> SeededSeason:
    """Cria (ou atualiza) a temporada completa no banco."""
    inicio = datetime.now(UTC)
    snapshot = build_snapshot(year, clubs, first_round=first_round)
    season, stats = await ingest_snapshot(session, PROVIDER_SLUG, PROVIDER_NAME, snapshot)

    rodadas = len({fixture.round.name for fixture in snapshot.fixtures if fixture.round})

    log.info(
        "seed.brasileirao",
        ano=year,
        times=len(snapshot.teams),
        rodadas=rodadas,
        jogos=len(snapshot.fixtures),
        segundos=round((datetime.now(UTC) - inicio).total_seconds(), 1),
    )

    return SeededSeason(
        season_id=season.id,
        teams=len(snapshot.teams),
        rounds=rodadas,
        fixtures=len(snapshot.fixtures),
        first_round_at=min(fixture.kickoff_at for fixture in snapshot.fixtures),
        stats=stats,
    )
