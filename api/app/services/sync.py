"""Coleta de resultado, por competição.

O erro que isto conserta
------------------------
Havia **um** provedor global (``settings.football_provider``), e ele vinha
``manual`` — que não sincroniza nada. Resultado: os jobs ``sync_fixtures``,
``sync_live`` e ``reconcile`` saíam na primeira linha, todo dia, e nenhum placar
jamais entrava sozinho. O jogo passava, virava ``LIVE`` pela rede de segurança
do relógio, e ficava ``LIVE`` para sempre: sem placar, sem apuração, sem
ranking. A rodada nunca concluía.

Um provedor global nunca poderia funcionar aqui, aliás: o Brasileirão vem do
GE, as ligas europeias de CSV e o mata-mata da Wikipédia — ao mesmo tempo, no
mesmo banco. Quem sabe coletar uma competição é a própria competição, e é o que
``competitions.provider_config`` guarda.

Duas velocidades, porque as fontes são diferentes
-------------------------------------------------
- **Ao vivo** (``supports_live``): só o GE publica placar enquanto a bola rola,
  e o faz por rodada — uma requisição cobre os dez jogos da rodada. Dá para
  bater de dois em dois minutos sem abusar.
- **Depois do apito**: o CSV do fixturedownload e a Wikipédia publicam o
  resultado mais tarde, não ao vivo. Bater neles de dois em dois minutos seria
  gastar requisição para reler o mesmo arquivo. Eles entram na varredura lenta.

Chamar isso de "tempo real" para as duas seria mentira, então a plataforma
mostra de onde veio e quando foi a última coleta.
"""

from __future__ import annotations

from dataclasses import dataclass, field
from datetime import UTC, datetime, timedelta

from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from app.core.logging import get_logger
from app.models import Competition, Fixture, FixtureStatus, Provider, Round, Season
from app.providers.base import FootballDataProvider, ProviderError, ProviderSnapshot
from app.services import catalog as catalog_service

log = get_logger(__name__)

#: Quanto tempo depois do apito inicial um jogo ainda é "de hoje". Cobre 90
#: minutos, intervalo, acréscimos e um atraso razoável da fonte.
DURACAO_MAXIMA = timedelta(hours=3)

#: Antes do apito já vale começar a olhar: fonte costuma publicar escalação e
#: mudar o estado do jogo alguns minutos antes.
ANTECEDENCIA = timedelta(minutes=10)

#: Sem placar por tanto tempo, a fonte não vai mais publicar sozinha e o jogo
#: precisa de intervenção do organizador.
PACIENCIA_SEM_PLACAR = timedelta(hours=24)


class SyncError(Exception):
    """Não deu para montar o coletor desta competição."""


@dataclass
class ResultadoDaColeta:
    competicoes: int = 0
    jogos_tocados: int = 0
    placares_mudados: list[int] = field(default_factory=list)
    falhas: list[str] = field(default_factory=list)

    def as_dict(self) -> dict[str, object]:
        return {
            "competitions": self.competicoes,
            "fixtures": self.jogos_tocados,
            "scores_changed": self.placares_mudados,
            "failures": self.falhas,
        }


def provider_para(competition: Competition, provider_slug: str) -> FootballDataProvider:
    """Reconstrói o coletor que importou esta competição.

    A configuração vem de ``competitions.provider_config``, gravada na
    importação. Competição antiga sem configuração cai no ``external_id``, que
    basta para fixturedownload e Wikipédia mas **não** para o GE — ali falta a
    fase, e sem ela não há coleta.
    """
    config = dict(competition.provider_config or {})

    if provider_slug == "globo":
        from app.providers.globo import GloboProvider

        tabela = config.get("table_id")
        fase = config.get("phase")
        if not tabela or not fase:
            raise SyncError(
                f"{competition.name}: falta tabela ou fase em provider_config. "
                "Reimporte o campeonato pela tela de Campeonatos para gravá-los."
            )
        return GloboProvider(
            tabela_id=str(tabela),
            phase=str(fase),
            rounds=int(config.get("rounds") or 38),
            competition_name=competition.name,
            # Sem isto o coletor se apresentaria com o UUID da tabela e o
            # ingest criaria uma competição paralela.
            competition_external_id=competition.external_id,
            country=competition.country,
        )

    if provider_slug == "fixturedownload":
        from app.providers.fixturedownload import FixtureDownloadProvider

        return FixtureDownloadProvider()

    if provider_slug == "wikipedia":
        from app.providers.wikipedia import WikipediaProvider

        artigo = config.get("article") or competition.external_id
        return WikipediaProvider(
            artigo=str(artigo),
            competition_name=competition.name,
            country=competition.country,
        )

    raise SyncError(f"{competition.name}: provedor '{provider_slug}' não coleta sozinho")


def _classe_do_provedor(provider_slug: str) -> type[FootballDataProvider]:
    """A classe do coletor, para consultar capacidade sem construir nada.

    ``supports_live`` e ``supports_partial`` são atributos de classe. Perguntar
    "esta fonte publica ao vivo?" não pode exigir montar um cliente HTTP nem
    depender de a configuração estar completa.
    """
    if provider_slug == "globo":
        from app.providers.globo import GloboProvider

        return GloboProvider
    if provider_slug == "fixturedownload":
        from app.providers.fixturedownload import FixtureDownloadProvider

        return FixtureDownloadProvider
    if provider_slug == "wikipedia":
        from app.providers.wikipedia import WikipediaProvider

        return WikipediaProvider

    from app.providers.manual import ManualProvider

    return ManualProvider


def _identificador_para_coleta(competition: Competition, provider_slug: str) -> str:
    """O que ``import_season`` espera como primeiro argumento, por provedor."""
    config = dict(competition.provider_config or {})
    if provider_slug == "fixturedownload":
        return str(config.get("league") or competition.external_id)
    if provider_slug == "wikipedia":
        return str(config.get("article") or competition.external_id)
    return competition.external_id


def _ano_do_arquivo(competition: Competition, provider_slug: str, season: Season) -> int:
    """Ano que o coletor espera, que nem sempre é o ano da temporada.

    O fixturedownload nomeia o arquivo pelo ano em que a temporada **começa**
    (``epl-2026`` é a 2026-27), enquanto o banco guarda o ano em que ela
    termina. Pedir 2027 baixaria a temporada seguinte, que ainda não existe.
    """
    if provider_slug != "fixturedownload":
        return season.year

    from app.data.ligas import LIGAS_POR_SLUG

    liga = LIGAS_POR_SLUG.get(str((competition.provider_config or {}).get("league") or ""))
    if liga is not None and liga.virada:
        return season.year - 1
    return season.year


#: Provedores que sabem coletar sozinhos. Competição de outro provedor — o
#: manual, por exemplo — é pulada em silêncio: não é falha, é desenho.
PROVEDORES_COLETAVEIS = frozenset({"globo", "fixturedownload", "wikipedia"})

#: Janela de relevância de uma temporada. Para trás cobre resultado que ainda
#: não chegou; para frente, calendário que muda.
JANELA_ATRAS = timedelta(days=14)
JANELA_FRENTE = timedelta(days=60)


async def temporadas_ativas(
    session: AsyncSession, *, now: datetime | None = None
) -> list[tuple[Competition, Season, str]]:
    """Competição, temporada e provedor de tudo que tem jogo perto de agora.

    **Não usa ``seasons.is_current``**, e isso não é preferência. A coluna é
    escrita pelo provedor, e o do GE a deriva do ano civil
    (``year == datetime.now().year``). Com liga de virada — a Premier 2026-27 é
    gravada como 2027, pela armadilha 10 — isso dá ``False`` já na importação, e
    a temporada nasce fora de toda coleta. Pior: em 1º de janeiro a recoleta do
    Brasileirão 2026 calcularia ``2026 == 2027`` e gravaria ``False``, apagando
    do radar uma temporada que ainda tem rodada para jogar.

    Como o único escritor da coluna é a própria coleta, seria uma porta de mão
    única: a coleta que reescreveria o campo é justamente a que deixou de
    acontecer. Os jogos ficariam ``LIVE`` sem placar para sempre, e a rodada
    nunca fecharia — o exato estado que este módulo existe para acabar.

    O critério certo é o que a tela já usa (``_jogos_por_vir``): **ter jogo por
    perto**. Isso vem dos dados, não de convenção de ano.
    """
    agora = now or datetime.now(UTC)

    com_jogo = select(Fixture.season_id).where(
        Fixture.kickoff_at.between(agora - JANELA_ATRAS, agora + JANELA_FRENTE)
    )

    linhas = (
        await session.execute(
            select(Competition, Season, Provider.slug)
            .select_from(Season)
            .join(Competition, Competition.id == Season.competition_id)
            .join(Provider, Provider.id == Competition.provider_id)
            .where(
                Provider.slug.in_(PROVEDORES_COLETAVEIS),
                Season.id.in_(com_jogo),
            )
        )
    ).all()
    return [(competition, season, slug) for competition, season, slug in linhas]


async def jogos_na_janela(session: AsyncSession, *, now: datetime | None = None) -> list[Fixture]:
    """Jogos que estão em campo agora, ou prestes a começar.

    A janela é generosa nas duas pontas de propósito: dez minutos antes do
    apito para não perder o começo, e três horas depois para cobrir acréscimo,
    prorrogação e atraso da fonte. Estreitar isso economiza requisição e perde
    resultado — a troca não compensa.
    """
    agora = now or datetime.now(UTC)
    return list(
        (
            await session.scalars(
                select(Fixture).where(
                    Fixture.status.in_(
                        [FixtureStatus.SCHEDULED, FixtureStatus.LIVE, FixtureStatus.HT]
                    ),
                    Fixture.kickoff_at <= agora + ANTECEDENCIA,
                    Fixture.kickoff_at >= agora - DURACAO_MAXIMA,
                )
            )
        ).all()
    )


async def rodadas_em_jogo(session: AsyncSession, jogos: list[Fixture]) -> dict[int, set[int]]:
    """Números de rodada com jogo em campo, por temporada.

    É o que permite ao GE coletar só o que interessa: uma requisição por rodada
    em andamento, em vez das trinta e oito da temporada inteira.
    """
    ids = {jogo.round_id for jogo in jogos if jogo.round_id is not None}
    if not ids:
        return {}

    saida: dict[int, set[int]] = {}
    for rodada in (await session.scalars(select(Round).where(Round.id.in_(ids)))).all():
        if rodada.number is not None:
            saida.setdefault(rodada.season_id, set()).add(rodada.number)
    return saida


async def coletar_competicao(
    session: AsyncSession,
    competition: Competition,
    season: Season,
    provider_slug: str,
    *,
    rodadas: set[int] | None = None,
) -> tuple[int, list[int]]:
    """Coleta uma competição e grava o que veio.

    ``rodadas`` restringe a coleta às rodadas informadas, quando o provedor
    sabe fazer isso. É o caminho barato do ao vivo.

    Devolve ``(jogos tocados, ids com placar corrigido)``.
    """
    provider = provider_para(competition, provider_slug)
    try:
        snapshot = await _coletar(provider, competition, season, provider_slug, rodadas)
        _, stats = await catalog_service.ingest_snapshot(
            session, provider.slug, provider.name, snapshot
        )
    finally:
        await provider.aclose()

    return stats.fixtures_created + stats.fixtures_updated, list(stats.scores_changed)


async def _coletar(
    provider: FootballDataProvider,
    competition: Competition,
    season: Season,
    provider_slug: str,
    rodadas: set[int] | None,
) -> ProviderSnapshot:
    identificador = _identificador_para_coleta(competition, provider_slug)
    ano = _ano_do_arquivo(competition, provider_slug, season)

    if rodadas and provider.supports_partial:
        return await provider.import_rounds(identificador, ano, sorted(rodadas))
    return await provider.import_season(identificador, ano)


async def coletar_ao_vivo(
    session: AsyncSession, *, now: datetime | None = None
) -> ResultadoDaColeta:
    """Atualiza o placar do que está em campo agora.

    Só toca em competição cujo provedor publica durante o jogo. As outras
    ficam para a varredura lenta — bater num CSV de temporada inteira de dois
    em dois minutos seria reler o mesmo arquivo às centenas.
    """
    resultado = ResultadoDaColeta()
    jogos = await jogos_na_janela(session, now=now)
    if not jogos:
        return resultado

    por_temporada = await rodadas_em_jogo(session, jogos)
    temporadas_com_jogo = {jogo.season_id for jogo in jogos}

    for competition, season, slug in await temporadas_ativas(session, now=now):
        if season.id not in temporadas_com_jogo:
            continue

        # `supports_live` é atributo de classe: perguntar não deve custar
        # instanciar um coletor, e instanciar pode falhar. Uma competição mal
        # configurada não pode derrubar a coleta das outras — antes desta
        # linha, `provider_para` estourava fora do try e a passada inteira
        # morria a cada dois minutos.
        if not _classe_do_provedor(slug).supports_live:
            continue

        try:
            tocados, mudados = await coletar_competicao(
                session, competition, season, slug, rodadas=por_temporada.get(season.id)
            )
        except (ProviderError, SyncError) as exc:
            # Falha da fonte: nada foi escrito, a sessão segue limpa.
            resultado.falhas.append(f"{competition.name}: {exc}")
            log.warning("sync.ao_vivo_falhou", competicao=competition.name, erro=str(exc))
            continue
        except Exception as exc:  # noqa: BLE001 - contenção deliberada
            # Erro de banco: a sessão fica inutilizável até o rollback, e sem
            # ele as competições seguintes falhariam em cascata. Uma violação de
            # chave única aqui derrubava a passada inteira, em silêncio, e foi o
            # que deixou o Brasileirão oito dias sem placar.
            await session.rollback()
            resultado.falhas.append(f"{competition.name}: {exc}")
            log.warning(
                "sync.ao_vivo_falhou",
                competicao=competition.name,
                erro=str(exc),
                tipo=type(exc).__name__,
            )
            continue

        # Grava competição a competição: uma falha lá na frente não pode
        # descartar o placar que já entrou.
        await session.commit()

        resultado.competicoes += 1
        resultado.jogos_tocados += tocados
        resultado.placares_mudados.extend(mudados)

    return resultado


async def jogos_sem_placar(
    session: AsyncSession, *, now: datetime | None = None, dias: int = 3
) -> list[Fixture]:
    """Jogos que já acabaram e continuam sem placar.

    É a lista que a sobreposição de placar tenta resolver depois do apito —
    o que a fonte do calendário não publicou, e o que uma coleta ao vivo que
    falhou deixou para trás.
    """
    agora = now or datetime.now(UTC)
    return list(
        (
            await session.scalars(
                select(Fixture).where(
                    Fixture.status.in_(
                        [FixtureStatus.SCHEDULED, FixtureStatus.LIVE, FixtureStatus.HT]
                    ),
                    Fixture.home_ft.is_(None),
                    Fixture.kickoff_at < agora,
                    Fixture.kickoff_at > agora - timedelta(days=dias),
                )
            )
        ).all()
    )


async def fechar_palpites(session: AsyncSession, *, now: datetime | None = None) -> int:
    """Fecha o palpite de todo jogo cujo horário já passou.

    Rede de segurança: a garantia de verdade é a trigger no banco. Isto existe
    para a interface parar de oferecer o campo de palpite mesmo quando a coleta
    está atrasada — o que acontece, e não pode deixar ninguém palpitar num jogo
    que já começou.
    """
    agora = now or datetime.now(UTC)
    jogos = (
        await session.scalars(
            select(Fixture).where(
                Fixture.status == FixtureStatus.SCHEDULED,
                Fixture.kickoff_at <= agora,
            )
        )
    ).all()

    for jogo in jogos:
        jogo.status = FixtureStatus.LIVE
    return len(jogos)


@dataclass
class Destravamento:
    encerrados: int = 0
    sem_placar: list[int] = field(default_factory=list)

    def as_dict(self) -> dict[str, object]:
        return {"finished": self.encerrados, "awaiting_score": self.sem_placar}


async def encerrar_parados(session: AsyncSession, *, now: datetime | None = None) -> Destravamento:
    """Fecha o que já acabou, e denuncia o que empacou.

    Um jogo fica preso em ``LIVE`` quando a fonte para de responder no meio da
    tarde. Preso assim ele trava a rodada inteira: a rodada nunca conclui, o
    ranking nunca fecha, e a tela mostra um jogo "em andamento" de ontem.

    São dois casos, e eles não se resolvem igual:

    **Tem placar e o tempo passou** — a fonte trouxe o resultado mas não disse
    que encerrou (o GE não manda campo de status). Aqui dá para fechar com
    honestidade: vira ``FINISHED`` e a apuração pega na sequência.

    **Não tem placar** — aqui as duas saídas fáceis quebram coisas piores.
    ``FINISHED`` sem placar entra na fila de apuração e nunca sai dela.
    ``POSTPONED`` **reabre a trava de palpite**, e um jogo de duas semanas
    atrás voltaria a aceitar palpite de quem já sabe o resultado. Então o jogo
    fica ``LIVE`` — travado, sem pontuar — e é reportado para o organizador
    lançar o placar à mão, que é o caminho que já existe.
    """
    agora = now or datetime.now(UTC)
    resultado = Destravamento()

    parados = (
        await session.scalars(
            select(Fixture).where(
                Fixture.status.in_([FixtureStatus.LIVE, FixtureStatus.HT]),
                Fixture.kickoff_at < agora - DURACAO_MAXIMA,
            )
        )
    ).all()

    for jogo in parados:
        if jogo.effective_score is not None and _placar_e_final(jogo, agora):
            jogo.status = FixtureStatus.FINISHED
            resultado.encerrados += 1
            continue

        if jogo.kickoff_at < agora - PACIENCIA_SEM_PLACAR:
            resultado.sem_placar.append(jogo.id)
            log.warning(
                "fixture.sem_placar",
                fixture=jogo.id,
                kickoff=jogo.kickoff_at.isoformat(),
                sincronizado_em=jogo.synced_at.isoformat() if jogo.synced_at else None,
                detalhe="segue travado para palpite; precisa de placar à mão",
            )

    return resultado


def _placar_e_final(jogo: Fixture, agora: datetime) -> bool:
    """O placar que está aí é o resultado, ou é a foto do minuto 20?

    Ter placar não basta. A coleta ao vivo grava ``home_ft``/``away_ft`` a cada
    dois minutos **durante** a partida — é o que faz o placar aparecer na tela.
    Se a fonte cair no primeiro tempo com 1x0, três horas depois esse 1x0
    continua no banco, e promovê-lo a resultado final apuraria a rodada inteira
    em cima de um jogo que terminou 1x3.

    A evidência de que o placar é final é a **hora da última coleta**: se a
    fonte confirmou aquele placar depois de o jogo certamente ter acabado, é o
    resultado. Se a última confirmação foi durante a partida, não é — e o jogo
    fica esperando alguém lançar.
    """
    confirmado_em = jogo.provider_updated_at or jogo.synced_at
    if confirmado_em is None:
        # Placar posto à mão, sem coleta. Quem lançou sabia o que estava fazendo.
        return True
    return confirmado_em >= jogo.kickoff_at + DURACAO_MAXIMA


async def coletar_resultados(
    session: AsyncSession, *, now: datetime | None = None, limite_competicoes: int = 12
) -> ResultadoDaColeta:
    """Varredura lenta: busca resultado de jogo que já acabou e ainda não tem placar.

    Serve às fontes que não publicam ao vivo, e é também a rede de segurança de
    quem publica: se a coleta ao vivo falhou a tarde inteira, é aqui que o
    placar entra.

    Só coleta competição que **precisa** — uma com todos os jogos apurados não
    gasta requisição nenhuma.
    """
    agora = now or datetime.now(UTC)
    resultado = ResultadoDaColeta()

    # Só estados que a recoleta consegue resolver. `!= FINISHED` pegaria
    # também CANCELLED, ABANDONED e POSTPONED — que a fonte nunca vai
    # transformar em resultado — e o LIVE-sem-placar que `encerrar_parados`
    # deixa travado de propósito. Qualquer um deles manteria a competição
    # eternamente "pendente", e o GE seria recoletado inteiro (38 requisições)
    # de meia em meia hora, para sempre, por causa de um jogo adiado.
    pendentes = (
        await session.execute(
            select(Fixture.season_id, Fixture.round_id)
            .where(
                Fixture.status.in_([FixtureStatus.SCHEDULED, FixtureStatus.LIVE, FixtureStatus.HT]),
                Fixture.home_ft.is_(None),
                Fixture.kickoff_at < agora - DURACAO_MAXIMA,
                Fixture.kickoff_at > agora - JANELA_ATRAS,
            )
            .distinct()
        )
    ).all()

    if not pendentes:
        return resultado

    ids_de_rodada = {round_id for _, round_id in pendentes if round_id is not None}
    rodadas_por_temporada: dict[int, set[int]] = {}
    if ids_de_rodada:
        for rodada in (
            await session.scalars(select(Round).where(Round.id.in_(ids_de_rodada)))
        ).all():
            if rodada.number is not None:
                rodadas_por_temporada.setdefault(rodada.season_id, set()).add(rodada.number)

    alvo = {season_id for season_id, _ in pendentes}
    # Ordem estável: sem ela, com mais competições do que o teto, sempre as
    # mesmas ficariam de fora e nunca receberiam resultado.
    ativas = sorted(await temporadas_ativas(session, now=now), key=lambda item: item[1].id)

    for competition, season, slug in ativas:
        if season.id not in alvo or resultado.competicoes >= limite_competicoes:
            continue
        try:
            # Quem sabe coletar por rodada paga 1 requisição em vez de 38.
            tocados, mudados = await coletar_competicao(
                session,
                competition,
                season,
                slug,
                rodadas=rodadas_por_temporada.get(season.id),
            )
        except (ProviderError, SyncError) as exc:
            # Falha da fonte: nada foi escrito, a sessão segue limpa.
            resultado.falhas.append(f"{competition.name}: {exc}")
            log.warning("sync.resultados_falhou", competicao=competition.name, erro=str(exc))
            continue
        except Exception as exc:  # noqa: BLE001 - contenção deliberada
            # Erro de banco: a sessão fica inutilizável até o rollback, e sem
            # ele as competições seguintes falhariam em cascata. Uma violação de
            # chave única aqui derrubava a passada inteira, em silêncio, e foi o
            # que deixou o Brasileirão oito dias sem placar.
            await session.rollback()
            resultado.falhas.append(f"{competition.name}: {exc}")
            log.warning(
                "sync.resultados_falhou",
                competicao=competition.name,
                erro=str(exc),
                tipo=type(exc).__name__,
            )
            continue

        # Grava competição a competição: uma falha lá na frente não pode
        # descartar o placar que já entrou.
        await session.commit()

        resultado.competicoes += 1
        resultado.jogos_tocados += tocados
        resultado.placares_mudados.extend(mudados)

    return resultado
