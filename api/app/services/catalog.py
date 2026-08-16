"""Ingestão do catálogo.

Contrato: **upsert idempotente por (provider_id, external_id)**. Rodar o sync
dez vezes deixa o banco igual a rodar uma vez. É o que permite reprocessar sem
medo quando algo dá errado no meio.
"""

from __future__ import annotations

from collections.abc import Sequence
from dataclasses import dataclass, field
from datetime import UTC, datetime

from slugify import slugify
from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from app.core.logging import get_logger
from app.models import (
    Competition,
    Fixture,
    FixtureStatus,
    Provider,
    Round,
    Season,
    Stage,
    SyncRun,
    Team,
)
from app.providers.base import (
    ProviderCompetition,
    ProviderFixture,
    ProviderSeason,
    ProviderSnapshot,
    ProviderTeam,
)

log = get_logger(__name__)


@dataclass
class IngestStats:
    teams_created: int = 0
    teams_updated: int = 0
    fixtures_created: int = 0
    fixtures_updated: int = 0
    fixtures_unchanged: int = 0
    rounds_created: int = 0
    scores_changed: list[int] = field(default_factory=list)
    """Fixtures cujo placar mudou depois de já estarem apurados."""

    def as_dict(self) -> dict[str, object]:
        return {
            "teams_created": self.teams_created,
            "teams_updated": self.teams_updated,
            "fixtures_created": self.fixtures_created,
            "fixtures_updated": self.fixtures_updated,
            "fixtures_unchanged": self.fixtures_unchanged,
            "rounds_created": self.rounds_created,
            "scores_changed": self.scores_changed,
        }


async def get_or_create_provider(
    session: AsyncSession, slug: str, name: str, base_url: str | None = None
) -> Provider:
    provider = await session.scalar(select(Provider).where(Provider.slug == slug))
    if provider is None:
        provider = Provider(slug=slug, name=name, base_url=base_url)
        session.add(provider)
        await session.flush()
    return provider


async def upsert_competition(
    session: AsyncSession, provider: Provider, dto: ProviderCompetition
) -> Competition:
    competition = await session.scalar(
        select(Competition).where(
            Competition.provider_id == provider.id, Competition.external_id == dto.external_id
        )
    )
    if competition is None:
        competition = Competition(
            provider_id=provider.id,
            external_id=dto.external_id,
            slug=await _unique_slug(session, dto.slug),
        )
        session.add(competition)

    competition.name = dto.name
    competition.country = dto.country
    competition.type = dto.type
    competition.logo_url = dto.logo_url

    # Mescla, não substitui. O provedor só conhece as **suas** chaves; trocar o
    # dicionário inteiro apaga o que outro subsistema gravou ali — foi assim
    # que a coleta do GE apagou o `espn_league` do Brasileirão minutos depois
    # de a migration gravá-lo, e o placar ao vivo simplesmente não aconteceu.
    #
    # Config vazia também não apaga nada: provedor que não sabe informar não
    # deve desconfigurar quem já estava configurado.
    if dto.config:
        competition.provider_config = {**(competition.provider_config or {}), **dto.config}

    await session.flush()
    return competition


async def upsert_season(
    session: AsyncSession, competition: Competition, dto: ProviderSeason
) -> Season:
    season = await session.scalar(
        select(Season).where(Season.competition_id == competition.id, Season.year == dto.year)
    )
    if season is None:
        season = Season(competition_id=competition.id, year=dto.year)
        session.add(season)

    season.external_id = dto.external_id
    season.is_current = dto.is_current

    # Coleta parcial não sabe as datas da temporada, e diz isso mandando None.
    # Sem esta guarda, um retrato de uma rodada só — que é o caminho da coleta
    # ao vivo — reescreveria o intervalo da temporada inteira para os dois dias
    # daquela rodada, a cada dois minutos.
    if dto.start_date is not None:
        season.start_date = dto.start_date
    if dto.end_date is not None:
        season.end_date = dto.end_date

    await session.flush()
    return season


async def upsert_teams(
    session: AsyncSession, provider: Provider, dtos: Sequence[ProviderTeam], stats: IngestStats
) -> dict[str, Team]:
    existing = {
        team.external_id: team
        for team in (
            await session.scalars(select(Team).where(Team.provider_id == provider.id))
        ).all()
    }

    result: dict[str, Team] = {}
    for dto in dtos:
        team = existing.get(dto.external_id)
        if team is None:
            team = Team(provider_id=provider.id, external_id=dto.external_id)
            session.add(team)
            stats.teams_created += 1
        else:
            stats.teams_updated += 1

        team.name = dto.name
        team.slug = dto.slug or slugify(dto.name)
        team.country = dto.country or team.country

        # Fonte que não traz escudo manda `None`, e a recoleta diária passa por
        # aqui. Sem esta guarda, o CSV apagaria de madrugada todo escudo que a
        # busca preencheu — e a plataforma voltaria ao escudo desenhado sem
        # ninguém entender por quê. Apagar escudo é operação explícita, não
        # efeito colateral de sincronizar calendário.
        team.crest_url = dto.crest_url or team.crest_url
        team.short_name = dto.short_name or team.short_name

        result[dto.external_id] = team

    await session.flush()
    return result


async def upsert_fixtures(
    session: AsyncSession,
    provider: Provider,
    season: Season,
    dtos: Sequence[ProviderFixture],
    teams: dict[str, Team],
    stats: IngestStats,
) -> list[Fixture]:
    # A busca do que já existe tem que usar **a mesma chave da restrição
    # única**: `(provider_id, external_id)`, sem a temporada.
    #
    # Filtrar por temporada aqui parecia inofensivo e não era: se a competição
    # for resolvida para outra linha — porque o coletor se apresentou com um
    # identificador diferente do gravado, por exemplo — o jogo antigo não
    # aparece nesta busca, o código o trata como novo, e o INSERT bate na
    # restrição única e derruba a coleta inteira. Foi assim que toda coleta ao
    # vivo do Brasileirão falhou por oito dias, em silêncio.
    #
    # Procurando pela chave real, o jogo é encontrado e **atualizado** — a
    # temporada dele inclusive é corrigida logo abaixo.
    externos = [dto.external_id for dto in dtos]
    existing = {
        fixture.external_id: fixture
        for fixture in (
            await session.scalars(
                select(Fixture).where(
                    Fixture.provider_id == provider.id, Fixture.external_id.in_(externos)
                )
            )
        ).all()
    }

    round_cache: dict[str, Round] = {}
    touched: list[Fixture] = []
    now = datetime.now(UTC)

    for dto in dtos:
        home = teams.get(dto.home_team_external_id)
        away = teams.get(dto.away_team_external_id)
        if home is None or away is None:
            log.warning("ingest.time_ausente", fixture=dto.external_id)
            continue

        round_obj = None
        if dto.round is not None:
            round_obj = await _get_or_create_round(session, season, dto, round_cache, stats)

        fixture = existing.get(dto.external_id)
        if fixture is None:
            fixture = Fixture(
                provider_id=provider.id,
                external_id=dto.external_id,
                season_id=season.id,
            )
            session.add(fixture)
            stats.fixtures_created += 1
            previous_score = None
            was_settled = False
        else:
            previous_score = fixture.effective_score
            was_settled = fixture.settled_at is not None

        # Corrige a temporada de um jogo que estava pendurado noutra: só
        # acontece quando a resolução de competição mudou, e é a correção que
        # tira o jogo do limbo em vez de duplicá-lo.
        fixture.season_id = season.id
        fixture.round_id = round_obj.id if round_obj else fixture.round_id
        fixture.home_team_id = home.id
        fixture.away_team_id = away.id
        fixture.kickoff_at = dto.kickoff_at
        fixture.status = _status_sem_regressao(fixture, dto, now)
        fixture.minute = dto.minute
        fixture.venue = dto.venue or fixture.venue

        # Placar em branco não apaga placar que já existe. Uma coleta que
        # falhou pela metade, ou uma fonte que ainda não publicou o resultado,
        # devolve `None` — e gravar isso zeraria um jogo já apurado e mandaria
        # a rodada inteira para reapuração com placar vazio.
        if dto.home_ft is not None or dto.away_ft is not None:
            fixture.home_ft = dto.home_ft
            fixture.away_ft = dto.away_ft
            fixture.home_et = dto.home_et
            fixture.away_et = dto.away_et
            fixture.home_pen = dto.home_pen
            fixture.away_pen = dto.away_pen
        fixture.provider_updated_at = dto.provider_updated_at
        fixture.synced_at = now
        fixture.winner_team_id = _winner_id(fixture, home, away)
        fixture.advancing_team_id = _advancing_id(fixture, home, away)

        await session.flush()
        new_score = fixture.effective_score

        # Placar corrigido depois de apurado (VAR, W.O., tribunal). Marca para
        # reapuração em vez de mexer no ranking aqui: apuração é trabalho do
        # settlement, e tem que ser idempotente.
        if was_settled and previous_score != new_score:
            fixture.settlement_dirty = True
            stats.scores_changed.append(fixture.id)
            log.warning(
                "ingest.placar_corrigido",
                fixture=fixture.id,
                de=previous_score,
                para=new_score,
            )
        elif previous_score == new_score and dto.external_id in existing:
            stats.fixtures_unchanged += 1

        touched.append(fixture)

    stats.fixtures_updated = len(touched) - stats.fixtures_created
    return touched


async def _get_or_create_round(
    session: AsyncSession,
    season: Season,
    dto: ProviderFixture,
    cache: dict[str, Round],
    stats: IngestStats,
) -> Round:
    assert dto.round is not None
    key = f"{dto.round.number}:{dto.round.name}"
    if key in cache:
        return cache[key]

    round_obj = await session.scalar(
        select(Round).where(
            Round.season_id == season.id,
            Round.number.is_(dto.round.number)
            if dto.round.number is None
            else Round.number == dto.round.number,
            Round.name == dto.round.name,
        )
    )
    if round_obj is None:
        stage = None
        if dto.round.stage_name:
            stage = await _get_or_create_stage(
                session, season, dto.round.stage_name, dto.round.is_knockout
            )
        round_obj = Round(
            season_id=season.id,
            stage_id=stage.id if stage else None,
            number=dto.round.number,
            name=dto.round.name,
        )
        session.add(round_obj)
        await session.flush()
        stats.rounds_created += 1

    # A rodada começa no primeiro jogo e acaba no último.
    if round_obj.starts_at is None or dto.kickoff_at < round_obj.starts_at:
        round_obj.starts_at = dto.kickoff_at
    if round_obj.ends_at is None or dto.kickoff_at > round_obj.ends_at:
        round_obj.ends_at = dto.kickoff_at

    cache[key] = round_obj
    return round_obj


async def _get_or_create_stage(
    session: AsyncSession, season: Season, name: str, is_knockout: bool
) -> Stage:
    stage = await session.scalar(
        select(Stage).where(Stage.season_id == season.id, Stage.name == name)
    )
    if stage is None:
        count = len(
            (await session.scalars(select(Stage.id).where(Stage.season_id == season.id))).all()
        )
        stage = Stage(season_id=season.id, name=name, order_index=count, is_knockout=is_knockout)
        session.add(stage)
        await session.flush()
    return stage


def resolve_winner(fixture: Fixture, home: Team, away: Team) -> int | None:
    """Quem venceu, considerando tempo normal + prorrogação."""
    return _winner_id(fixture, home, away)


def resolve_advancing(fixture: Fixture, home: Team, away: Team) -> int | None:
    """Quem avança de fase. É o único lugar em que os pênaltis contam."""
    return _advancing_id(fixture, home, away)


def _winner_id(fixture: Fixture, home: Team, away: Team) -> int | None:
    score = fixture.effective_score
    if score is None or not fixture.status.is_final:
        return None
    home_goals, away_goals = score
    if home_goals > away_goals:
        return home.id
    if away_goals > home_goals:
        return away.id
    return None


def _advancing_id(fixture: Fixture, home: Team, away: Team) -> int | None:
    """Quem avança. Só aqui os pênaltis importam."""
    if not fixture.status.is_final:
        return None
    winner = _winner_id(fixture, home, away)
    if winner is not None:
        return winner
    if fixture.home_pen is not None and fixture.away_pen is not None:
        if fixture.home_pen > fixture.away_pen:
            return home.id
        if fixture.away_pen > fixture.home_pen:
            return away.id
    return None


async def ingest_snapshot(
    session: AsyncSession, provider_slug: str, provider_name: str, snapshot: ProviderSnapshot
) -> tuple[Season, IngestStats]:
    """Grava um retrato inteiro de temporada. Idempotente de ponta a ponta."""
    stats = IngestStats()
    provider = await get_or_create_provider(session, provider_slug, provider_name)
    competition = await upsert_competition(session, provider, snapshot.competition)
    season = await upsert_season(session, competition, snapshot.season)
    teams = await upsert_teams(session, provider, snapshot.teams, stats)
    await upsert_fixtures(session, provider, season, snapshot.fixtures, teams, stats)
    return season, stats


async def record_sync_run(
    session: AsyncSession,
    *,
    kind: str,
    status: str,
    started_at: datetime,
    provider_id: int | None = None,
    stats: dict[str, object] | None = None,
    error: str | None = None,
) -> SyncRun:
    run = SyncRun(
        provider_id=provider_id,
        kind=kind,
        status=status,
        started_at=started_at,
        finished_at=datetime.now(UTC),
        stats=stats or {},
        error=error[:1000] if error else None,
    )
    session.add(run)
    await session.flush()
    return run


async def _unique_slug(session: AsyncSession, base: str) -> str:
    slug = base
    suffix = 2
    while await session.scalar(select(Competition.id).where(Competition.slug == slug)):
        slug = f"{base}-{suffix}"
        suffix += 1
    return slug


#: Estados que significam "a bola já rolou".
COMECOU = frozenset({FixtureStatus.LIVE, FixtureStatus.HT, FixtureStatus.FINISHED})


def _status_sem_regressao(fixture: Fixture, dto: ProviderFixture, now: datetime) -> FixtureStatus:
    """Status novo, sem deixar um jogo andar para trás no tempo.

    A coleta roda de minuto em minuto durante a partida, e nem toda fonte sabe
    de tudo. O CSV das ligas europeias, por exemplo, só conhece dois estados:
    tem placar (``FINISHED``) ou não tem (``SCHEDULED``). Enquanto o resultado
    não é publicado — o que leva horas — ele afirma ``SCHEDULED`` sobre um jogo
    que já acabou.

    Aceitar isso produz duas regressões, e as duas já aconteceram aqui:

    - **De estado final para qualquer outro.** O jogo apurado voltaria para a
      fila, o ranking oscilaria, e a trava de palpite reabriria.
    - **De "em campo" para "vai começar".** O jogo fica pulando entre os dois
      a cada passada — a fonte diz `SCHEDULED`, o relógio devolve para `LIVE` no
      minuto seguinte — e a tela mostra "vai começar" num jogo encerrado.

    A regra é assimétrica de propósito. Avançar sempre pode. Voltar para
    ``SCHEDULED`` só se o jogo **de fato** foi remarcado para o futuro: aí o
    provedor manda um ``kickoff_at`` que ainda não chegou, e aí sim é adiamento
    de verdade, não desinformação.
    """
    # Jogo recém-criado ainda não tem status: nada a proteger.
    if fixture.status is None:
        return dto.status

    if fixture.status.is_final:
        if dto.status.is_final:
            return dto.status
        motivo = "jogo encerrado não regride"

    elif fixture.status in COMECOU and dto.status is FixtureStatus.SCHEDULED:
        # Remarcação legítima: o novo horário ainda não chegou.
        if dto.kickoff_at > now:
            return dto.status
        motivo = "fonte diz que não começou, mas o horário já passou"

    else:
        return dto.status

    log.warning(
        "ingest.regressao_de_status_ignorada",
        fixture=fixture.external_id,
        de=str(fixture.status),
        para=str(dto.status),
        motivo=motivo,
    )
    return fixture.status


async def fixtures_needing_settlement(session: AsyncSession) -> Sequence[Fixture]:
    """Jogos terminados ainda não apurados, ou com placar corrigido."""
    return (
        await session.scalars(
            select(Fixture).where(
                Fixture.status == FixtureStatus.FINISHED,
                (Fixture.settled_at.is_(None)) | (Fixture.settlement_dirty.is_(True)),
            )
        )
    ).all()
