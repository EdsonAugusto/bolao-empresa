"""Catálogo: competições, temporadas, rodadas — e o cadastro manual.

O cadastro manual é o coração do modo sem custo: o organizador cola um CSV com
a tabela do campeonato e a plataforma funciona sem depender de ninguém.
"""

from __future__ import annotations

from dataclasses import replace
from datetime import UTC, date, datetime, time, timedelta
from urllib.parse import urlparse

from fastapi import APIRouter, HTTPException, Query, Response, status
from pydantic import BaseModel, Field, field_validator
from sqlalchemy import func, select
from sqlalchemy.orm import selectinload

from app.api.deps import CurrentUser, Requer, SessionDep, SuperUser
from app.core import names
from app.core.config import settings
from app.core.logging import get_logger
from app.core.permissoes import Permissao
from app.core.redis import enqueue_job
from app.data.competitions import BY_SLUG, COMO_DESCOBRIR, GE_COMPETITIONS
from app.data.crests import crest_svg
from app.data.ligas import (
    COPAS,
    COPAS_POR_SLUG,
    LIGAS,
    LIGAS_POR_SLUG,
    MATA_MATAS,
    MATA_MATAS_POR_SLUG,
    TEMPORADA,
)
from app.models import Competition, Fixture, FixtureStatus, Provider, Round, Season, Team
from app.providers.base import ProviderError, ProviderSnapshot
from app.providers.factory import available_providers, build_provider
from app.providers.manual import CSV_TEMPLATE, ManualCsvError, build_competition, parse_fixtures_csv
from app.schemas.common import Message
from app.services import catalog as catalog_service
from app.services import crests as crest_service
from app.services import permissoes as permissao_service
from app.services import pools as pool_service
from app.services import predictions as prediction_service
from app.services import seeding
from app.services import settlement as settlement_service

log = get_logger(__name__)

router = APIRouter(prefix="/catalog", tags=["catálogo"])

# A pergunta certa aqui não é "administra a plataforma?" e sim "pode importar?".
# É o que separa o amigo que organiza o bolão da firma — e precisa trazer o
# campeonato — de quem administra as contas de todo mundo.
PodeImportar = Requer(Permissao.CAMPEONATOS_IMPORTAR)
PodeLancarPlacar = Requer(Permissao.CAMPEONATOS_PLACAR)


class CompetitionOut(BaseModel):
    id: int
    slug: str
    name: str
    country: str | None
    type: str
    logo_url: str | None
    seasons: list[dict]


class SeasonOut(BaseModel):
    id: int
    year: int
    label: str
    """Como a temporada se chama para uma pessoa: ``2026`` ou ``2026-27``.

    Sai das datas dos jogos, não de convenção: a Premier 2026-27 é gravada com
    ano 2027 (o ano em que acaba) e chamá-la de "Premier League 2027" na tela
    seria confuso.
    """

    competition: str
    competition_slug: str
    is_current: bool
    rounds: int
    fixtures: int
    teams: int
    upcoming: int
    """Jogos ainda por acontecer. Zero = temporada encerrada."""

    next_fixture_at: datetime | None


class ManualImportRequest(BaseModel):
    competition_name: str = Field(min_length=2, max_length=120)
    year: int = Field(ge=1900, le=2200)
    csv: str = Field(min_length=10)
    timezone_offset_hours: int = Field(
        default=-3,
        ge=-12,
        le=14,
        description="Fuso em que as datas do CSV foram escritas. -3 = Brasília.",
    )


class ImportSeasonRequest(BaseModel):
    provider: str = "football_data_org"
    competition_external_id: str
    year: int = Field(ge=1900, le=2200)


class ScoreUpdate(BaseModel):
    home_goals: int = Field(ge=0, le=99)
    away_goals: int = Field(ge=0, le=99)
    home_pen: int | None = Field(default=None, ge=0, le=99)
    away_pen: int | None = Field(default=None, ge=0, le=99)
    finished: bool = True


class SeedBrasileiraoRequest(BaseModel):
    year: int = Field(default=2026, ge=2000, le=2100)


@router.get("/providers")
async def providers(user: CurrentUser) -> list[dict]:
    return available_providers()


@router.get(
    "/teams/{slug}/crest.svg",
    response_class=Response,
    responses={200: {"content": {"image/svg+xml": {}}}},
)
async def team_crest(slug: str) -> Response:
    """Escudo do clube, desenhado pela plataforma.

    Público de propósito: é imagem, entra em ``<img src>`` sem cabeçalho de
    autenticação. Cache longo porque o desenho só muda se o código mudar.
    """
    return Response(
        content=crest_svg(slug),
        media_type="image/svg+xml",
        headers={"Cache-Control": "public, max-age=604800, immutable"},
    )


@router.post("/brasileirao", status_code=status.HTTP_201_CREATED)
async def seed_brasileirao(
    payload: SeedBrasileiraoRequest, session: SessionDep, user: PodeImportar
) -> dict:
    """Cria a temporada completa do Brasileirão: 20 clubes, 38 rodadas, 380 jogos.

    O calendário é gerado (turno e returno completos, datas plausíveis), não é o
    oficial da CBF — que não existe em formato aberto. Quando o oficial sair,
    reimporte por CSV: a ingestão é idempotente e atualiza em vez de duplicar.
    """
    resultado = await seeding.seed_brasileirao(session, payload.year)
    await session.commit()
    return {
        "season_id": resultado.season_id,
        "year": payload.year,
        "teams": resultado.teams,
        "rounds": resultado.rounds,
        "fixtures": resultado.fixtures,
        "first_round_at": resultado.first_round_at,
        "stats": resultado.stats.as_dict(),
        "note": (
            "Calendário gerado com turno e returno completos. As datas são "
            "plausíveis, não oficiais — ajuste reimportando o CSV quando a "
            "tabela da CBF sair."
        ),
    }


async def _jogos_por_vir(session: SessionDep) -> dict[int, tuple[int, datetime | None]]:
    """Quantos jogos cada temporada ainda tem pela frente, e quando é o próximo.

    "Em andamento" pelo ano é mentira: a Premier 2025-26 tem ano 2026 e acabou
    em maio. O que vale é ter jogo pela frente — e esta é a única contagem, para
    que dois endpoints não respondam coisas diferentes sobre a mesma temporada.
    """
    agora = datetime.now(UTC)
    linhas = await session.execute(
        select(
            Fixture.season_id,
            func.count(),
            func.min(Fixture.kickoff_at),
        )
        .where(Fixture.kickoff_at > agora, Fixture.status == FixtureStatus.SCHEDULED)
        .group_by(Fixture.season_id)
    )
    return {season_id: (total, proximo) for season_id, total, proximo in linhas}


async def _rotulos(session: SessionDep) -> dict[int, str]:
    """Nome da temporada para gente ler: ``2026`` ou ``2026-27``.

    Derivado das datas dos jogos porque é o único jeito de acertar sem uma
    tabela de convenção por liga: se o primeiro e o último jogo caem em anos
    diferentes, a temporada é de virada.
    """
    linhas = await session.execute(
        select(
            Fixture.season_id,
            func.min(func.extract("year", Fixture.kickoff_at)),
            func.max(func.extract("year", Fixture.kickoff_at)),
        ).group_by(Fixture.season_id)
    )
    return {
        int(season_id): (
            str(int(inicio)) if inicio == fim else f"{int(inicio)}-{int(fim) % 100:02d}"
        )
        for season_id, inicio, fim in linhas
    }


@router.get("/competitions", response_model=list[CompetitionOut])
async def list_competitions(session: SessionDep, user: CurrentUser) -> list[CompetitionOut]:
    competitions = (await session.scalars(select(Competition).order_by(Competition.name))).all()
    por_vir = await _jogos_por_vir(session)

    result = []
    for competition in competitions:
        seasons = (
            await session.scalars(
                select(Season)
                .where(Season.competition_id == competition.id)
                .order_by(Season.year.desc())
            )
        ).all()
        result.append(
            CompetitionOut(
                id=competition.id,
                slug=competition.slug,
                name=competition.name,
                country=competition.country,
                type=str(competition.type),
                logo_url=competition.logo_url,
                seasons=[
                    {
                        "id": season.id,
                        "year": season.year,
                        "is_current": bool(por_vir.get(season.id, (0, None))[0]),
                        "upcoming": por_vir.get(season.id, (0, None))[0],
                    }
                    for season in seasons
                ],
            )
        )
    return result


@router.get("/seasons", response_model=list[SeasonOut])
async def list_seasons(session: SessionDep, user: CurrentUser) -> list[SeasonOut]:
    seasons = (await session.scalars(select(Season).order_by(Season.year.desc()))).all()
    por_vir = await _jogos_por_vir(session)
    rotulos = await _rotulos(session)

    result = []
    for season in seasons:
        competition = await session.get(Competition, season.competition_id)
        rounds = await session.scalar(
            select(func.count()).select_from(Round).where(Round.season_id == season.id)
        )
        fixtures = await session.scalar(
            select(func.count()).select_from(Fixture).where(Fixture.season_id == season.id)
        )
        teams = await session.scalar(
            select(func.count(func.distinct(Fixture.home_team_id))).where(
                Fixture.season_id == season.id
            )
        )

        upcoming, proximo = por_vir.get(season.id, (0, None))

        result.append(
            SeasonOut(
                id=season.id,
                year=season.year,
                label=rotulos.get(season.id, str(season.year)),
                competition=competition.name,
                competition_slug=competition.slug,
                is_current=bool(upcoming),
                rounds=rounds or 0,
                fixtures=fixtures or 0,
                teams=teams or 0,
                upcoming=upcoming or 0,
                next_fixture_at=proximo,
            )
        )
    return result


class AgendaWindow(BaseModel):
    """Onde estão os jogos, para a tela não abrir num buraco de calendário."""

    next_fixture_at: datetime | None
    suggested_from: date | None
    suggested_to: date | None
    total_upcoming: int
    competitions: list[str]


@router.get("/agenda/janela", response_model=AgendaWindow)
async def agenda_window(
    session: SessionDep,
    user: CurrentUser,
    dias: int = Query(default=7, ge=1, le=90, description="Tamanho da janela sugerida"),
) -> AgendaWindow:
    """Sugere a janela de datas que contém jogos.

    Existe porque "hoje + 7 dias" é uma escolha ruim: campeonato tem pausa, e
    a janela pode cair exatamente entre duas rodadas. A tela abria vazia e
    parecia quebrada — quando o próximo jogo era só três dias depois.
    """
    agora = datetime.now(UTC)

    futuros = (
        await session.scalars(
            select(Fixture)
            .where(Fixture.kickoff_at > agora, Fixture.status == FixtureStatus.SCHEDULED)
            .order_by(Fixture.kickoff_at)
            .limit(500)
        )
    ).all()

    if not futuros:
        return AgendaWindow(
            next_fixture_at=None,
            suggested_from=None,
            suggested_to=None,
            total_upcoming=0,
            competitions=[],
        )

    primeiro = futuros[0]
    inicio = min(agora.date(), primeiro.kickoff_at.date())
    fim = primeiro.kickoff_at.date() + timedelta(days=dias)

    nomes: set[str] = set()
    for jogo in futuros:
        season = await session.get(Season, jogo.season_id)
        if season is None:
            continue
        competition = await session.get(Competition, season.competition_id)
        if competition is not None:
            nomes.add(competition.name)

    return AgendaWindow(
        next_fixture_at=primeiro.kickoff_at,
        suggested_from=inicio,
        suggested_to=fim,
        total_upcoming=len(futuros),
        competitions=sorted(nomes),
    )


@router.get("/agenda", response_model=list[dict])
async def agenda(
    session: SessionDep,
    user: CurrentUser,
    de: date | None = Query(default=None, description="Primeiro dia (padrão: hoje)"),
    ate: date | None = Query(default=None, description="Último dia (padrão: 7 dias)"),
    competicao: int | None = Query(default=None, description="Filtra por competição"),
    competicoes: str | None = Query(
        default=None,
        description="Várias competições, separadas por vírgula. Vazio = todas.",
    ),
    apenas_abertos: bool = Query(default=True, description="Só jogos que ainda não começaram"),
) -> list[dict]:
    """Calendário de jogos, de todos os campeonatos importados.

    É a lista de onde o organizador monta a rodada da semana. Agrupada por
    competição, com os horários já resolvidos para o fuso de apresentação pelo
    frontend.
    """
    agora = datetime.now(UTC)
    inicio = datetime.combine(de or agora.date(), time.min, tzinfo=UTC)
    fim = datetime.combine(ate or (agora.date() + timedelta(days=7)), time.max, tzinfo=UTC)

    # Conjunto vazio significa "todas", não "nenhuma": um filtro que some com
    # tudo por engano é pior do que um filtro que não filtra.
    permitidas: set[int] = set()
    if competicao is not None:
        permitidas.add(competicao)
    if competicoes:
        permitidas.update(
            int(pedaco) for pedaco in competicoes.split(",") if pedaco.strip().isdigit()
        )

    consulta = (
        select(Fixture)
        .where(Fixture.kickoff_at.between(inicio, fim))
        .options(
            selectinload(Fixture.home_team),
            selectinload(Fixture.away_team),
            selectinload(Fixture.round),
        )
        .order_by(Fixture.kickoff_at)
        .limit(500)
    )
    jogos = (await session.scalars(consulta)).all()

    if apenas_abertos:
        # Pelo relógio **e** pelo status. Um jogo pode estar encerrado com a
        # data ainda no futuro — placar lançado antes da hora, ou data errada
        # na origem. Oferecê-lo para montar rodada entregaria um jogo que
        # ninguém consegue palpitar.
        jogos = [jogo for jogo in jogos if not prediction_service.is_locked(jogo, agora)]

    # Competição de cada jogo, para agrupar na tela.
    season_ids = {jogo.season_id for jogo in jogos}
    competicoes: dict[int, tuple[int, str, int]] = {}
    for season_id in season_ids:
        season = await session.get(Season, season_id)
        if season is None:
            continue
        competition = await session.get(Competition, season.competition_id)
        if competition is None:
            continue
        competicoes[season_id] = (competition.id, competition.name, season.year)

    saida = []
    for jogo in jogos:
        dados = competicoes.get(jogo.season_id)
        if dados is None:
            continue
        competition_id, competition_name, year = dados
        if permitidas and competition_id not in permitidas:
            continue

        saida.append(
            {
                "id": jogo.id,
                "kickoff_at": jogo.kickoff_at,
                "status": str(jogo.status),
                "competition_id": competition_id,
                "competition": competition_name,
                "season_year": year,
                "round": jogo.round.name if jogo.round else None,
                "home_team": {
                    "id": jogo.home_team.id,
                    "name": jogo.home_team.name,
                    "short_name": jogo.home_team.short_name,
                    "crest_url": jogo.home_team.crest_url,
                },
                "away_team": {
                    "id": jogo.away_team.id,
                    "name": jogo.away_team.name,
                    "short_name": jogo.away_team.short_name,
                    "crest_url": jogo.away_team.crest_url,
                },
            }
        )
    return saida


@router.get("/ge-competitions")
async def ge_competitions(user: CurrentUser) -> dict:
    """Campeonatos que a plataforma sabe coletar do GE.

    Os presets foram verificados contra a API; qualquer outro pode ser
    acrescentado informando tabela e fase — não existe API de descoberta no GE,
    então essa é a única forma de crescer a lista sem mexer no código.
    """
    return {
        "presets": [
            {
                "slug": item.slug,
                "name": item.name,
                "country": item.country,
                "rounds": item.rounds,
                "season_year": item.season_year,
                "verified_at": item.verificado_em,
            }
            for item in GE_COMPETITIONS
        ],
        "how_to_find": COMO_DESCOBRIR,
    }


@router.get("/ligas")
async def ligas_do_mundo(session: SessionDep, user: CurrentUser) -> list[dict]:
    """Ligas com calendário aberto, e se cada uma já está no banco.

    O GE não publica a temporada europeia nova antes de ela começar — foi o que
    fez a plataforma dizer "temporada encerrada" para a Premier League enquanto
    a primeira rodada de 2026-27 já estava marcada. Estas vêm de CSV público.
    """
    # Casa por (nome, ano) e não por slug: quando a Premier chega por duas
    # fontes, a segunda competição nasce com slug `premier-league-2` e a
    # comparação por slug diria "não importado" para algo que está no banco.
    ja_tem = {
        (names.chave(nome), ano)
        for nome, ano in await session.execute(
            select(Competition.name, Season.year).join(
                Season, Season.competition_id == Competition.id
            )
        )
    }
    return [
        {
            "slug": liga.slug,
            "name": liga.name,
            "country": liga.country,
            "rounds": liga.rounds,
            "fixtures": liga.fixtures,
            "first_round": liga.first_round,
            "season_year": TEMPORADA,
            "verified_at": liga.verificado_em,
            "imported": (names.chave(liga.name), liga.season_year(TEMPORADA)) in ja_tem,
        }
        for liga in LIGAS
    ]


class LigaImportRequest(BaseModel):
    slug: str = Field(description="Identificador da liga (ex.: epl, la-liga)")
    year: int | None = Field(default=None, ge=2000, le=2100)


@router.post("/ligas/import", status_code=status.HTTP_201_CREATED)
async def importar_liga(
    payload: LigaImportRequest, session: SessionDep, user: PodeImportar
) -> dict:
    """Importa a temporada inteira de uma liga a partir do CSV público.

    Uma requisição HTTP, sem chave e sem cota. As datas do arquivo já vêm em
    UTC, então não há fuso para adivinhar. Reimportar não duplica: o upsert é
    por ``(provider, external_id)``.
    """
    from app.providers.fixturedownload import FixtureDownloadError, FixtureDownloadProvider

    liga = LIGAS_POR_SLUG.get(payload.slug)
    if liga is None:
        raise HTTPException(
            status_code=status.HTTP_404_NOT_FOUND,
            detail=f"liga '{payload.slug}' não está no catálogo",
        )

    ano = payload.year or TEMPORADA
    provider = FixtureDownloadProvider()
    iniciou = datetime.now(UTC)
    try:
        snapshot = await provider.import_season(liga.slug, ano)
    except FixtureDownloadError as exc:
        await catalog_service.record_sync_run(
            session, kind="import_season", status="failed", started_at=iniciou, error=str(exc)
        )
        await session.commit()
        raise HTTPException(status_code=status.HTTP_502_BAD_GATEWAY, detail=str(exc)) from exc
    finally:
        await provider.aclose()

    season, stats = await catalog_service.ingest_snapshot(
        session, provider.slug, provider.name, snapshot
    )
    await catalog_service.record_sync_run(
        session, kind="import_season", status="success", started_at=iniciou, stats=stats.as_dict()
    )
    await session.commit()

    jogados = sum(1 for item in snapshot.fixtures if item.status is FixtureStatus.FINISHED)
    return {
        "season_id": season.id,
        "competition": liga.name,
        # `season.year` e não `ano`: liga de virada é gravada pelo ano em que
        # termina, e reportar o ano do arquivo confundiria quem lê a resposta.
        "year": season.year,
        "teams": len(snapshot.teams),
        "fixtures": len(snapshot.fixtures),
        "played": jogados,
        "skipped_without_date": provider.ultimas_sem_data,
        "note": (
            "Sem escudo oficial nesta fonte: os clubes usam o escudo desenhado pela plataforma."
        ),
    }


class EscudosRequest(BaseModel):
    remote: bool = Field(
        default=True,
        description="Buscar fora quando o escudo não estiver no banco (é lento sem chave)",
    )
    limit: int | None = Field(
        default=None,
        ge=1,
        le=400,
        description="Teto de buscas externas nesta passada",
    )


@router.get("/escudos")
async def escudos_status(session: SessionDep, user: CurrentUser) -> dict:
    """Quantos clubes ainda estão sem escudo, e por campeonato."""
    faltando, indice = await crest_service.times_por_escudo(session)
    por_campeonato: dict[str, int] = {}
    for item in faltando:
        por_campeonato[item.competition] = por_campeonato.get(item.competition, 0) + 1

    tem_chave = bool(settings.football_data_org_key)
    return {
        "with_crest": len(indice),
        "missing": len(faltando),
        "by_competition": por_campeonato,
        "fast_path": tem_chave,
        "hint": (
            "Chave do football-data.org configurada: a busca pega a liga inteira em uma requisição."
            if tem_chave
            else "Sem FOOTBALL_DATA_ORG_KEY a busca externa vai clube a clube, "
            "devagar, para não ser bloqueada. A chave é gratuita."
        ),
    }


@router.post("/escudos")
async def preencher_escudos(
    payload: EscudosRequest, session: SessionDep, user: PodeImportar
) -> dict:
    """Preenche o escudo de quem está sem.

    Três fontes, nessa ordem: o que já está no banco (escudo oficial do GE, de
    graça e sem rede), a liga inteira pelo football-data.org quando há chave, e
    clube a clube no TheSportsDB quando não há.

    Idempotente: não mexe em escudo que já existe. Se um veio errado, apague-o
    e rode de novo.
    """
    buscador: crest_service.Buscador | None = None
    fechar: list[object] = []

    if payload.remote and settings.football_data_org_key:
        from app.providers.football_data_org import CODIGOS_GRATUITOS, FootballDataOrgProvider

        provider = FootballDataOrgProvider(
            settings.football_data_org_key,
            requests_per_minute=settings.football_data_org_rate_limit_per_minute,
        )
        fechar.append(provider)
        cache: dict[str, dict[str, str]] = {}

        async def buscar_em_bloco(nome: str, competicao: str) -> str | None:
            codigo = CODIGOS_GRATUITOS.get(names.chave(competicao))
            if codigo is None:
                return None
            if codigo not in cache:
                try:
                    cache[codigo] = await provider.escudos_da_competicao(codigo)
                except ProviderError as exc:
                    log.warning("escudos.fdorg_falhou", codigo=codigo, motivo=str(exc))
                    cache[codigo] = {}
            return cache[codigo].get(names.chave(nome))

        buscador = buscar_em_bloco

    elif payload.remote:
        # Sem chave, a busca é clube a clube com seis segundos entre uma e
        # outra — cem clubes dão dez minutos, e uma requisição HTTP não fica
        # dez minutos aberta. Faz a passada local agora e joga o resto no
        # worker, que é retomável.
        resultado = await crest_service.preencher(session, buscador=None)
        await session.commit()
        enfileirado = await enqueue_job("fill_crests", limit=payload.limit)
        return {
            **resultado.as_dict(),
            "queued": enfileirado,
            "note": (
                "A busca externa foi para a fila: são 6 segundos entre clubes "
                "para não ser bloqueada. Recarregue a tela daqui a alguns "
                "minutos. Com FOOTBALL_DATA_ORG_KEY seria instantâneo."
            ),
        }

    try:
        resultado = await crest_service.preencher(
            session, buscador=buscador, limite_remoto=payload.limit
        )
    finally:
        for item in fechar:
            await item.aclose()  # type: ignore[attr-defined]

    await session.commit()

    # O que o football-data.org não cobre ainda precisa de alguém.
    #
    # O plano gratuito conhece oito ligas. Para qualquer outra — EFL League
    # One, Süper Lig, mata-mata — `buscar_em_bloco` devolve None, e antes disso
    # era o fim da linha: com a chave configurada, esses clubes não tinham
    # caminho nenhum e ficariam sem escudo para sempre. O paradoxo era que
    # configurar a chave PIORAVA o resultado deles, porque desviava do ramo que
    # usa o TheSportsDB.
    #
    # Agora o resto vai para a fila, exatamente como no caminho sem chave: a
    # parte rápida sai na hora e a lenta continua sozinha, seis segundos por
    # clube, sem ninguém esperando na frente da tela.
    faltando, _ = await crest_service.times_por_escudo(session)
    if faltando:
        enfileirado = await enqueue_job("fill_crests")
        return {
            **resultado.as_dict(),
            "queued": enfileirado,
            "still_missing": len(faltando),
            "note": (
                f"{len(faltando)} clube(s) fora das ligas do plano gratuito foram "
                "para a fila, onde a busca é mais lenta. Recarregue a tela daqui "
                "a alguns minutos."
            ),
        }

    return resultado.as_dict()


@router.get("/copas")
async def copas(session: SessionDep, user: CurrentUser) -> list[dict]:
    """Copas eliminatórias que a plataforma sabe coletar, e se já estão no banco.

    Separadas das ligas porque a fonte é outra: mata-mata brasileiro não sai em
    CSV, e o chaveamento da Wikipédia não traz horário de jogo.
    """
    ja_tem = {
        (names.chave(nome), ano)
        for nome, ano in await session.execute(
            select(Competition.name, Season.year).join(
                Season, Season.competition_id == Competition.id
            )
        )
    }
    return [
        {
            "slug": item.slug,
            "name": item.name,
            "country": item.country,
            "year": item.year,
            "label": item.rotulo,
            "verified_at": item.verificado_em,
            "imported": (names.chave(item.name), item.year) in ja_tem,
        }
        for item in COPAS
    ]


class CopaImportRequest(BaseModel):
    slug: str = Field(description="Slug da copa, ex.: copa-do-brasil")
    year: int | None = Field(default=None, ge=2000, le=2100)


@router.post("/copas/import", status_code=status.HTTP_201_CREATED)
async def importar_copa(
    payload: CopaImportRequest, session: SessionDep, user: PodeImportar
) -> dict:
    """Importa uma copa eliminatória pela ESPN.

    São doze requisições, uma por mês: a API trunca janela longa, e pedir o ano
    inteiro de uma vez devolvia jogos só até abril enquanto as oitavas de agosto
    existiam.
    """
    from app.providers.espn import EspnCalendario, EspnError

    copa = COPAS_POR_SLUG.get(payload.slug)
    if copa is None:
        raise HTTPException(
            status_code=status.HTTP_422_UNPROCESSABLE_ENTITY,
            detail=f"não conheço a copa '{payload.slug}'",
        )

    ano = payload.year or copa.year
    provider = EspnCalendario()
    iniciou = datetime.now(UTC)
    try:
        snapshot = await provider.import_season(
            copa.espn_leagues, ano, virada=copa.virada, identidade=copa.slug
        )
    except EspnError as exc:
        await catalog_service.record_sync_run(
            session, kind="import_season", status="failed", started_at=iniciou, error=str(exc)
        )
        await session.commit()
        raise HTTPException(status_code=status.HTTP_502_BAD_GATEWAY, detail=str(exc)) from exc
    finally:
        await provider.aclose()

    # O snapshot vem com o código da ESPN como nome da competição. Trocamos
    # pelo nome de gente antes de gravar: é o que aparece na tela do bolão.
    snapshot = replace(
        snapshot,
        competition=replace(snapshot.competition, name=copa.name, slug=copa.slug),
    )

    season, stats = await catalog_service.ingest_snapshot(
        session, provider.slug, provider.name, snapshot
    )

    # Guarda a liga da ESPN para o placar ao vivo achar esta competição depois
    # sem ninguém precisar configurar nada.
    competicao = await session.get(Competition, season.competition_id)
    if competicao is not None:
        config = dict(competicao.provider_config or {})
        # TODAS as ligas do torneio, não só a principal.
        #
        # Guardar só `uefa.champions` fazia o placar dos jogos de agosto nunca
        # ser encontrado: eles são de qualificação, vivem em
        # `uefa.champions_qual`, e a consulta ia para a liga certa do torneio e
        # errada do jogo. Voltava vazia, e o jogo ficava 0 a 0 em campo para
        # sempre — sem erro, porque não achar jogo não é falha.
        #
        # O singular fica junto, apontando para a principal, para quem ainda
        # ler a chave antiga.
        config["espn_leagues"] = list(copa.espn_leagues)
        config["espn_league"] = copa.espn_leagues[-1]
        competicao.provider_config = config

    await catalog_service.record_sync_run(
        session, kind="import_season", status="success", started_at=iniciou, stats=stats.as_dict()
    )
    await session.commit()

    fases = [
        nome
        for _, nome in sorted(
            {
                (item.round.number or 999, item.round.name)
                for item in snapshot.fixtures
                if item.round
            }
        )
    ]
    return {
        "season_id": season.id,
        "competition": copa.name,
        "year": ano,
        "label": copa.rotulo,
        "teams": len(snapshot.teams),
        "fixtures": len(snapshot.fixtures),
        "phases": fases,
        "note": (
            "Copa é sorteada por fase: as próximas aparecem conforme os "
            "confrontos são definidos. Reimportar traz o que faltava sem "
            "duplicar o que já está aqui."
        ),
    }


@router.get("/mata-matas")
async def mata_matas(session: SessionDep, user: CurrentUser) -> list[dict]:
    """Torneios eliminatórios, coletados da Wikipédia.

    Mata-mata não tem calendário em CSV — o chaveamento só existe depois do
    sorteio — e o GE devolve lista vazia para as fases eliminatórias.
    """
    ja_tem = {
        (names.chave(nome), ano)
        for nome, ano in await session.execute(
            select(Competition.name, Season.year).join(
                Season, Season.competition_id == Competition.id
            )
        )
    }
    return [
        {
            "slug": item.slug,
            "name": item.name,
            "country": item.country,
            "article": item.artigo,
            "year": item.year,
            "verified_at": item.verificado_em,
            "imported": (names.chave(item.name), item.year) in ja_tem,
        }
        for item in MATA_MATAS
    ]


class MataMataImportRequest(BaseModel):
    slug: str | None = Field(default=None, description="Preset conhecido")
    article: str | None = Field(default=None, max_length=200)
    name: str | None = Field(default=None, max_length=120)
    country: str | None = Field(default=None, max_length=80)
    year: int | None = Field(default=None, ge=2000, le=2100)


@router.post("/mata-matas/import", status_code=status.HTTP_201_CREATED)
async def importar_mata_mata(
    payload: MataMataImportRequest, session: SessionDep, user: PodeImportar
) -> dict:
    """Importa um mata-mata a partir de um artigo da Wikipédia em português.

    Funciona para qualquer artigo com caixas ``Footballbox`` — Libertadores,
    Copa do Brasil, Champions, Copa do Mundo. O artigo traz o fuso de cada jogo
    explícito, que é onde importação de torneio continental costuma errar.
    """
    from app.providers.wikipedia import WikipediaProvider, WikipediaScrapeError

    if payload.slug and payload.slug in MATA_MATAS_POR_SLUG:
        preset = MATA_MATAS_POR_SLUG[payload.slug]
        artigo, nome, pais, ano = preset.artigo, preset.name, preset.country, preset.year
    elif payload.article:
        artigo = payload.article
        nome = payload.name or payload.article
        pais = payload.country
        ano = payload.year or datetime.now(UTC).year
    else:
        raise HTTPException(
            status_code=status.HTTP_422_UNPROCESSABLE_ENTITY,
            detail="informe um preset conhecido, ou o título do artigo",
        )

    provider = WikipediaProvider(artigo=artigo, competition_name=nome, country=pais)
    iniciou = datetime.now(UTC)
    try:
        snapshot = await provider.import_season(artigo, ano)
    except WikipediaScrapeError as exc:
        await catalog_service.record_sync_run(
            session, kind="import_season", status="failed", started_at=iniciou, error=str(exc)
        )
        await session.commit()
        raise HTTPException(status_code=status.HTTP_502_BAD_GATEWAY, detail=str(exc)) from exc
    finally:
        await provider.aclose()

    season, stats = await catalog_service.ingest_snapshot(
        session, provider.slug, provider.name, snapshot
    )
    await catalog_service.record_sync_run(
        session, kind="import_season", status="success", started_at=iniciou, stats=stats.as_dict()
    )
    await session.commit()

    fases = sorted({item.round.name for item in snapshot.fixtures if item.round})
    return {
        "season_id": season.id,
        "competition": nome,
        "year": ano,
        "teams": len(snapshot.teams),
        "fixtures": len(snapshot.fixtures),
        "phases": fases,
        "note": (
            "Fonte enciclopédica: serve para o calendário e o chaveamento. O "
            "placar entra pela edição do jogo quando a rodada acabar."
        ),
    }


#: Único destino que este endpoint pode buscar. Sem esta lista ele é um proxy:
#: quem chama escolhe a URL, o servidor busca e devolve o que achou — inclusive
#: `http://169.254.169.254/` (metadados da nuvem) ou qualquer serviço que só
#: exista dentro da rede do Compose. Numa instalação em rede local isso era
#: contido pela própria rede; num servidor público, não é.
GLOBO_PERMITIDOS = ("ge.globo.com", "globoesporte.globo.com")


class GeDiscoverRequest(BaseModel):
    url: str = Field(
        min_length=10,
        description="Endereço da página do campeonato no ge.globo.com",
    )

    @field_validator("url")
    @classmethod
    def _so_o_ge(cls, valor: str) -> str:
        endereco = urlparse(valor.strip())
        if endereco.scheme != "https":
            raise ValueError("o endereço precisa começar com https://")
        hospedeiro = (endereco.hostname or "").lower()
        if hospedeiro not in GLOBO_PERMITIDOS:
            permitidos = " ou ".join(GLOBO_PERMITIDOS)
            raise ValueError(f"só endereços de {permitidos} são aceitos aqui")
        return valor.strip()


class GeDiscoverResult(BaseModel):
    table_id: str
    phase: str
    fixtures: int
    upcoming: int
    rounds_found: int
    sample: list[str]


@router.post("/ge-discover", response_model=list[GeDiscoverResult])
async def ge_discover(payload: GeDiscoverRequest, user: PodeImportar) -> list[GeDiscoverResult]:
    """Descobre tabela e fase a partir do endereço da página do campeonato.

    Existe porque o GE não tem catálogo público: o identificador é um UUID
    embutido no HTML, misturado com dezenas de ids de matéria, e a única forma
    de saber qual é o certo é testar cada um. Fazer isso à mão é inviável —
    aqui a plataforma testa e devolve o que funcionou, com quantos jogos e
    quantos ainda vão acontecer.
    """
    from app.providers.globo import GloboScrapeError, descobrir_competicao

    try:
        achados = await descobrir_competicao(payload.url)
    except GloboScrapeError as exc:
        raise HTTPException(
            status_code=status.HTTP_422_UNPROCESSABLE_ENTITY, detail=str(exc)
        ) from exc

    return [
        GeDiscoverResult(
            table_id=item.table_id,
            phase=item.phase,
            fixtures=item.fixtures,
            upcoming=item.upcoming,
            rounds_found=item.rounds_found,
            sample=item.sample,
        )
        for item in achados
    ]


class GeImportRequest(BaseModel):
    slug: str | None = Field(default=None, description="Preset conhecido")
    name: str | None = Field(default=None, max_length=120)
    table_id: str | None = Field(default=None, description="UUID da tabela no GE")
    phase: str | None = Field(default=None, description="Slug da fase no GE")
    country: str | None = Field(default=None, max_length=80)
    rounds: int = Field(default=38, ge=1, le=60)
    year: int | None = Field(default=None, ge=2000, le=2100)


@router.post("/ge-import", status_code=status.HTTP_201_CREATED)
async def ge_import(payload: GeImportRequest, session: SessionDep, user: PodeImportar) -> dict:
    """Importa um campeonato do GE — preset conhecido ou informado à mão."""
    from app.providers.globo import GloboProvider, GloboScrapeError

    if payload.slug and payload.slug in BY_SLUG:
        preset = BY_SLUG[payload.slug]
        tabela, fase, rodadas = preset.table_id, preset.phase, preset.rounds
        nome, ano, pais = preset.name, preset.season_year, preset.country
    elif payload.table_id and payload.phase:
        tabela, fase, rodadas = payload.table_id, payload.phase, payload.rounds
        nome = payload.name or "Campeonato"
        ano = payload.year or datetime.now(UTC).year
        # Sem país informado fica em branco: chutar "Brasil" cadastraria a
        # Premier League como campeonato brasileiro.
        pais = payload.country
    else:
        raise HTTPException(
            status_code=status.HTTP_422_UNPROCESSABLE_ENTITY,
            detail="informe um preset conhecido, ou tabela e fase",
        )

    provider = GloboProvider(
        tabela_id=tabela,
        competition_name=nome,
        country=pais,
        phase=fase,
        rounds=rodadas,
    )
    iniciou = datetime.now(UTC)
    try:
        snapshot = await provider.import_season(nome, ano)
    except GloboScrapeError as exc:
        await catalog_service.record_sync_run(
            session, kind="import_season", status="failed", started_at=iniciou, error=str(exc)
        )
        await session.commit()
        raise HTTPException(status_code=status.HTTP_502_BAD_GATEWAY, detail=str(exc)) from exc
    finally:
        await provider.aclose()

    season, stats = await catalog_service.ingest_snapshot(
        session, provider.slug, provider.name, snapshot
    )
    await catalog_service.record_sync_run(
        session, kind="import_season", status="success", started_at=iniciou, stats=stats.as_dict()
    )
    await session.commit()

    coleta = provider.last_collection
    return {
        "season_id": season.id,
        "competition": nome,
        "year": ano,
        "teams": len(snapshot.teams),
        "fixtures": len(snapshot.fixtures),
        "played": sum(1 for jogo in snapshot.fixtures if jogo.home_ft is not None),
        "undated": list(coleta.undated) if coleta else [],
    }


@router.get("/manual/template")
async def csv_template(user: CurrentUser) -> dict:
    """Modelo de CSV, com explicação — é o que o organizador vai preencher."""
    return {
        "columns": ["rodada", "data", "mandante", "visitante", "gols_mandante", "gols_visitante"],
        "template": CSV_TEMPLATE,
        "notes": [
            "A data é no horário local (Brasília por padrão) e vira UTC na importação.",
            "Formatos aceitos: AAAA-MM-DD HH:MM ou DD/MM/AAAA HH:MM.",
            "Deixe os gols em branco para jogo que ainda não aconteceu.",
            "Importar de novo o mesmo CSV atualiza os jogos em vez de duplicar.",
        ],
    }


@router.post("/manual/import", status_code=status.HTTP_201_CREATED)
async def manual_import(
    payload: ManualImportRequest, session: SessionDep, user: PodeImportar
) -> dict:
    """Importa uma temporada inteira a partir de um CSV. Idempotente."""
    try:
        teams, fixtures = parse_fixtures_csv(
            payload.csv,
            competition_slug=payload.competition_name,
            year=payload.year,
            timezone_offset_hours=payload.timezone_offset_hours,
        )
    except ManualCsvError as exc:
        raise HTTPException(
            status_code=status.HTTP_422_UNPROCESSABLE_ENTITY, detail=str(exc)
        ) from exc

    competition, season_dto = build_competition(payload.competition_name, payload.year)
    snapshot = ProviderSnapshot(
        competition=competition, season=season_dto, teams=teams, fixtures=fixtures
    )

    started = datetime.now(UTC)
    season, stats = await catalog_service.ingest_snapshot(
        session, "manual", "Cadastro manual", snapshot
    )
    await catalog_service.record_sync_run(
        session,
        kind="import_season",
        status="success",
        started_at=started,
        stats=stats.as_dict(),
    )
    await session.commit()

    # Jogos que já vieram com placar no CSV são apurados na hora.
    results = await settlement_service.settle_pending(session)
    await session.commit()

    return {
        "season_id": season.id,
        "competition": competition.name,
        "year": payload.year,
        "teams": len(teams),
        "fixtures": len(fixtures),
        "settled_now": sum(1 for item in results if item.settled),
        "stats": stats.as_dict(),
    }


@router.post("/brasileirao/importar-real", status_code=status.HTTP_201_CREATED)
async def import_brasileirao_real(
    payload: SeedBrasileiraoRequest, session: SessionDep, user: PodeImportar
) -> dict:
    """Importa o Brasileirão real coletando do site do GE Globo.

    São 38 requisições espaçadas em um segundo — a chamada leva cerca de 40s.
    Traz o calendário com datas, os placares dos jogos já realizados e os
    escudos oficiais.

    **Não é uma API pública.** Os termos de uso do Globo não autorizam coleta
    automatizada, e o endpoint interno muda sem aviso. Quando mudar, este
    endpoint devolve o motivo em vez de importar dado pela metade.
    """
    from app.providers.globo import GloboProvider, GloboScrapeError

    provider = GloboProvider()
    iniciou = datetime.now(UTC)

    try:
        snapshot = await provider.import_season("brasileirao-serie-a", payload.year)
    except GloboScrapeError as exc:
        await catalog_service.record_sync_run(
            session, kind="import_season", status="failed", started_at=iniciou, error=str(exc)
        )
        await session.commit()
        raise HTTPException(status_code=status.HTTP_502_BAD_GATEWAY, detail=str(exc)) from exc
    finally:
        await provider.aclose()

    season, stats = await catalog_service.ingest_snapshot(
        session, provider.slug, provider.name, snapshot
    )
    await catalog_service.record_sync_run(
        session, kind="import_season", status="success", started_at=iniciou, stats=stats.as_dict()
    )
    await session.commit()

    realizados = sum(1 for jogo in snapshot.fixtures if jogo.home_ft is not None)
    coleta = provider.last_collection
    return {
        "season_id": season.id,
        "year": payload.year,
        "teams": len(snapshot.teams),
        "fixtures": len(snapshot.fixtures),
        "played": realizados,
        "undated": list(coleta.undated) if coleta else [],
        "stats": stats.as_dict(),
    }


@router.post("/import-season", status_code=status.HTTP_201_CREATED)
async def import_season(
    payload: ImportSeasonRequest, session: SessionDep, user: PodeImportar
) -> dict:
    """Importa uma temporada de um provedor externo."""
    provider = build_provider(payload.provider)
    started = datetime.now(UTC)
    try:
        snapshot = await provider.import_season(payload.competition_external_id, payload.year)
    except Exception as exc:
        await catalog_service.record_sync_run(
            session, kind="import_season", status="failed", started_at=started, error=str(exc)
        )
        await session.commit()
        raise HTTPException(
            status_code=status.HTTP_502_BAD_GATEWAY, detail=f"provedor falhou: {exc}"
        ) from exc
    finally:
        await provider.aclose()

    season, stats = await catalog_service.ingest_snapshot(
        session, provider.slug, provider.name, snapshot
    )
    await catalog_service.record_sync_run(
        session, kind="import_season", status="success", started_at=started, stats=stats.as_dict()
    )
    await session.commit()
    return {"season_id": season.id, "stats": stats.as_dict()}


@router.put("/fixtures/{fixture_id}/score", response_model=Message)
async def set_score(
    fixture_id: int, payload: ScoreUpdate, session: SessionDep, user: CurrentUser
) -> Message:
    """Lança ou corrige o placar de um jogo (modo manual).

    Corrigir um placar já apurado é caso de uso normal — VAR, W.O., tribunal.
    A reapuração é disparada aqui e é idempotente.

    **Quem pode**: quem tem a permissão ``campeonatos.placar`` na plataforma,
    **ou** quem organiza um bolão que inclui este jogo.

    São dois caminhos de propósito, e não um só. Exigir a permissão fecharia o
    único jeito de destravar um jogo que terminou sem placar para quem organiza
    o bolão da firma e não administra nada. Exigir a organização impediria quem
    cuida do catálogo de corrigir um placar errado num bolão que não é dele.
    """
    fixture = await session.get(Fixture, fixture_id)
    if fixture is None:
        raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail="jogo não encontrado")

    autorizado = await permissao_service.pode(
        session, user, Permissao.CAMPEONATOS_PLACAR
    ) or await pool_service.organiza_bolao_com_jogo(session, user_id=user.id, fixture_id=fixture_id)
    if not autorizado:
        raise HTTPException(
            status_code=status.HTTP_403_FORBIDDEN,
            detail=(
                'para lançar placar é preciso a permissão "Lançar placar", '
                "ou organizar um bolão que inclua este jogo"
            ),
        )

    from app.models import FixtureStatus

    fixture.home_ft = payload.home_goals
    fixture.away_ft = payload.away_goals
    fixture.home_pen = payload.home_pen
    fixture.away_pen = payload.away_pen
    fixture.status = FixtureStatus.FINISHED if payload.finished else FixtureStatus.LIVE
    fixture.settlement_dirty = fixture.settled_at is not None

    home = await session.get(Team, fixture.home_team_id)
    away = await session.get(Team, fixture.away_team_id)
    if home and away:
        fixture.winner_team_id = catalog_service.resolve_winner(fixture, home, away)
        fixture.advancing_team_id = catalog_service.resolve_advancing(fixture, home, away)

    await session.flush()

    if payload.finished:
        await settlement_service.settle_fixture(session, fixture_id)

    await session.commit()
    return Message(detail="placar lançado e ranking recalculado")


@router.get("/sync-runs")
async def sync_runs(session: SessionDep, user: SuperUser, limit: int = 25) -> list[dict]:
    from app.models import SyncRun

    runs = (
        await session.scalars(select(SyncRun).order_by(SyncRun.started_at.desc()).limit(limit))
    ).all()
    providers_by_id = {
        provider.id: provider.slug for provider in (await session.scalars(select(Provider))).all()
    }
    return [
        {
            "id": run.id,
            "kind": run.kind,
            "status": run.status,
            "provider": providers_by_id.get(run.provider_id or 0, "—"),
            "started_at": run.started_at,
            "finished_at": run.finished_at,
            "stats": run.stats,
            "error": run.error,
        }
        for run in runs
    ]


@router.get("/times")
async def times_para_escolher(
    session: SessionDep,
    user: CurrentUser,
    busca: str = Query(default="", max_length=60, description="Filtra pelo nome"),
) -> list[dict]:
    """Times já importados, para a pessoa escolher o do coração no perfil.

    Só os que estão no banco: sem catálogo mundial, sem digitar nome livre. O
    campo é enfeite de perfil e não vale a pena virar porta de entrada de texto
    arbitrário exibido na tela dos outros.
    """
    consulta = select(Team).order_by(Team.name)
    if busca.strip():
        consulta = consulta.where(Team.name.ilike(f"%{busca.strip()}%"))

    return [
        {
            "id": time.id,
            "name": time.name,
            "short_name": time.short_name,
            "crest_url": time.crest_url,
        }
        # Um catálogo grande não cabe num seletor: quem não achar, filtra.
        for time in (await session.scalars(consulta.limit(60))).all()
    ]
