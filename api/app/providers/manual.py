"""Provedor manual — o padrão, e o único que nunca falha.

O organizador cadastra competição, times e jogos pela interface (ou importa um
CSV) e lança os placares. Sem rede, sem chave, sem cota, sem custo. Um bolão do
Brasileirão com 38 rodadas dá umas duas horas de digitação, ou trinta segundos
com o CSV.

É deliberadamente o padrão porque um bolão entre amigos não pode parar no
domingo à noite porque uma API gratuita atingiu a cota diária.
"""

from __future__ import annotations

import csv
import io
from collections.abc import Sequence
from datetime import UTC, date, datetime, timedelta

from slugify import slugify

from app.models.enums import CompetitionType, FixtureStatus
from app.providers.base import (
    FootballDataProvider,
    ProviderCompetition,
    ProviderFixture,
    ProviderRound,
    ProviderSeason,
    ProviderTeam,
)

CSV_COLUMNS = (
    "rodada",
    "data",
    "mandante",
    "visitante",
    "gols_mandante",
    "gols_visitante",
)

CSV_TEMPLATE = """rodada,data,mandante,visitante,gols_mandante,gols_visitante
1,2026-04-11 16:00,Time A,Time B,,
1,2026-04-11 18:30,Time C,Time D,,
2,2026-04-18 16:00,Time B,Time C,,
"""


class ManualCsvError(ValueError):
    """CSV malformado. A mensagem vai direto para a tela do organizador."""


class ManualProvider(FootballDataProvider):
    """Não sincroniza nada: os dados já estão no banco, postos por uma pessoa."""

    slug = "manual"
    name = "Cadastro manual"
    supports_sync = False

    async def list_competitions(self) -> Sequence[ProviderCompetition]:
        return ()

    async def list_seasons(self, competition_external_id: str) -> Sequence[ProviderSeason]:
        return ()

    async def list_teams(self, competition_external_id: str, year: int) -> Sequence[ProviderTeam]:
        return ()

    async def list_fixtures(
        self, competition_external_id: str, year: int
    ) -> Sequence[ProviderFixture]:
        return ()

    async def get_fixture(self, external_id: str) -> ProviderFixture | None:
        return None


def parse_fixtures_csv(
    content: str,
    *,
    competition_slug: str,
    year: int,
    timezone_offset_hours: int = -3,
) -> tuple[list[ProviderTeam], list[ProviderFixture]]:
    """Converte o CSV do organizador em DTOs de provedor.

    A coluna ``data`` é lida no **horário local** (Brasília por padrão) porque é
    assim que a pessoa lê a tabela do campeonato, e convertida para UTC aqui —
    a conversão acontece uma vez, na borda, e nunca mais.

    Placares em branco significam jogo ainda não realizado.
    """
    reader = csv.DictReader(io.StringIO(content.strip()))
    if reader.fieldnames is None:
        raise ManualCsvError("CSV vazio")

    header = [name.strip().lower() for name in reader.fieldnames]
    missing = [column for column in CSV_COLUMNS if column not in header]
    if missing:
        raise ManualCsvError(
            f"faltam colunas no CSV: {', '.join(missing)}. Esperado: {', '.join(CSV_COLUMNS)}"
        )

    teams: dict[str, ProviderTeam] = {}
    fixtures: list[ProviderFixture] = []

    for line_number, raw in enumerate(reader, start=2):
        row = {key.strip().lower(): (value or "").strip() for key, value in raw.items() if key}
        if not any(row.values()):
            continue

        home_name = row["mandante"]
        away_name = row["visitante"]
        if not home_name or not away_name:
            raise ManualCsvError(f"linha {line_number}: mandante e visitante são obrigatórios")
        if home_name == away_name:
            raise ManualCsvError(f"linha {line_number}: um time não joga contra si mesmo")

        for name in (home_name, away_name):
            slug = slugify(name)
            teams.setdefault(
                slug,
                ProviderTeam(external_id=slug, name=name, slug=slug, country="Brasil"),
            )

        kickoff = _parse_local_datetime(row["data"], line_number, timezone_offset_hours)
        home_goals = _parse_goals(row["gols_mandante"], line_number, "gols_mandante")
        away_goals = _parse_goals(row["gols_visitante"], line_number, "gols_visitante")

        played = home_goals is not None and away_goals is not None
        round_number = _parse_round(row["rodada"], line_number)

        fixtures.append(
            ProviderFixture(
                external_id=(
                    f"{competition_slug}-{year}-r{round_number}-"
                    f"{slugify(home_name)}-{slugify(away_name)}"
                ),
                home_team_external_id=slugify(home_name),
                away_team_external_id=slugify(away_name),
                kickoff_at=kickoff,
                status=FixtureStatus.FINISHED if played else FixtureStatus.SCHEDULED,
                round=ProviderRound(name=f"Rodada {round_number}", number=round_number),
                home_ft=home_goals,
                away_ft=away_goals,
            )
        )

    if not fixtures:
        raise ManualCsvError("nenhum jogo encontrado no CSV")

    return list(teams.values()), fixtures


def _parse_local_datetime(value: str, line_number: int, offset_hours: int) -> datetime:
    if not value:
        raise ManualCsvError(f"linha {line_number}: data é obrigatória")

    for pattern in ("%Y-%m-%d %H:%M", "%d/%m/%Y %H:%M", "%Y-%m-%dT%H:%M"):
        try:
            naive = datetime.strptime(value, pattern)  # noqa: DTZ007 - local por definição
        except ValueError:
            continue
        # O horário informado é local; UTC = local - offset.
        return (naive - _offset_delta(offset_hours)).replace(tzinfo=UTC)

    raise ManualCsvError(
        f"linha {line_number}: data '{value}' não reconhecida. "
        "Use AAAA-MM-DD HH:MM ou DD/MM/AAAA HH:MM"
    )


def _offset_delta(offset_hours: int) -> timedelta:
    return timedelta(hours=offset_hours)


def _parse_goals(value: str, line_number: int, column: str) -> int | None:
    if value == "":
        return None
    try:
        goals = int(value)
    except ValueError as exc:
        raise ManualCsvError(f"linha {line_number}: {column} '{value}' não é um número") from exc
    if goals < 0:
        raise ManualCsvError(f"linha {line_number}: {column} não pode ser negativo")
    return goals


def _parse_round(value: str, line_number: int) -> int:
    try:
        return int(value)
    except ValueError as exc:
        raise ManualCsvError(f"linha {line_number}: rodada '{value}' não é um número") from exc


def build_competition(name: str, year: int) -> tuple[ProviderCompetition, ProviderSeason]:
    """Competição e temporada sintéticas para um cadastro manual."""
    slug = slugify(name)
    competition = ProviderCompetition(
        external_id=slug,
        slug=slug,
        name=name,
        country="Brasil",
        type=CompetitionType.LEAGUE,
    )
    season = ProviderSeason(
        external_id=f"{slug}-{year}",
        competition_external_id=slug,
        year=year,
        start_date=date(year, 1, 1),
        end_date=date(year, 12, 31),
        is_current=True,
    )
    return competition, season
