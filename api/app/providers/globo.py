"""Coletor do GE Globo.

Traz o calendário e os resultados reais do Brasileirão a partir da API que
alimenta o site do ge.globo.com. Fica atrás de ``FootballDataProvider`` como
qualquer outro provedor — nenhum código de domínio sabe que existe scraping.

O que você precisa saber antes de usar
--------------------------------------
1. **Não é uma API pública.** É o endpoint interno do site. Os termos de uso do
   Globo não autorizam coleta automatizada. Para um bolão entre amigos numa
   máquina de casa o risco prático é baixo, mas a decisão é sua e ela não é
   tecnicamente neutra.
2. **Vai quebrar um dia.** É endpoint interno: muda sem aviso e sem versão. Por
   isso este módulo falha **alto e específico** quando o formato muda, em vez
   de importar dado pela metade — um bolão com meia rodada errada é pior do que
   um bolão sem a rodada.
3. **Escudos** vêm apontando para o CDN do Globo. São imagens de terceiro: se o
   link cair, a plataforma volta sozinha para o escudo desenhado.

O caminho legal e estável continua sendo um provedor com API contratada. Este
existe porque o gratuito não cobre o Brasileirão.
"""

from __future__ import annotations

import asyncio
import re
from collections.abc import Sequence
from dataclasses import dataclass
from datetime import UTC, date, datetime, timedelta
from typing import Any

import httpx
from slugify import slugify

from app.core.logging import get_logger
from app.models.enums import CompetitionType, FixtureStatus
from app.providers.base import (
    FootballDataProvider,
    ProviderCompetition,
    ProviderError,
    ProviderFixture,
    ProviderRound,
    ProviderSeason,
    ProviderSnapshot,
    ProviderTeam,
)

log = get_logger(__name__)

BASE_URL = "https://api.globoesporte.globo.com"

#: Identificador da tabela do Brasileirão Série A no GE.
TABELA_SERIE_A = "d1a37fa4-e948-43a6-ba53-ab24ab3a45b1"

TOTAL_RODADAS = 38

#: Horário de Brasília. O GE devolve data e hora locais, sem fuso.
BRASILIA_OFFSET = timedelta(hours=-3)

#: Traduções de status, para o caso de o GE voltar a mandar o campo. Hoje ele
#: não manda: a rodada inteira vem com `jogo_status` ausente, e quem informa é
#: `jogo_ja_comecou` mais a presença de placar.
STATUS_MAP: dict[str, FixtureStatus] = {
    "pre-jogo": FixtureStatus.SCHEDULED,
    "pre_jogo": FixtureStatus.SCHEDULED,
    "agendado": FixtureStatus.SCHEDULED,
    "em-andamento": FixtureStatus.LIVE,
    "em_andamento": FixtureStatus.LIVE,
    "intervalo": FixtureStatus.HT,
    "encerrado": FixtureStatus.FINISHED,
    "final": FixtureStatus.FINISHED,
    "adiado": FixtureStatus.POSTPONED,
    "cancelado": FixtureStatus.CANCELLED,
    "suspenso": FixtureStatus.SUSPENDED,
}

#: Depois disso, um jogo que começou certamente acabou. Serve para separar
#: "em andamento" de "encerrado" sem campo de status.
DURACAO_MAXIMA = timedelta(hours=3)


class SemDataDefinida(Exception):
    """Jogo publicado sem data. Acontece e não é erro de formato."""


class GloboScrapeError(ProviderError):
    """O formato do GE mudou, ou a coleta foi bloqueada."""


@dataclass
class Coleta:
    """Resultado de uma passada pelas 38 rodadas."""

    teams: dict[str, ProviderTeam]
    fixtures: list[ProviderFixture]
    empty_rounds: list[int]
    undated: list[str]
    """Jogos publicados sem data marcada. Pulados, não inventados."""


UUID_RE = re.compile(r"[0-9a-f]{8}-[0-9a-f]{4}-[0-9a-f]{4}-[0-9a-f]{4}-[0-9a-f]{12}")
FASE_RE = re.compile(r"fase[a-z0-9\-]*-\d{4}(?:-\d{4})?")

NAVEGADOR = {
    "User-Agent": (
        "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 "
        "(KHTML, like Gecko) Chrome/126 Safari/537.36"
    ),
    "Accept": "text/html,application/xhtml+xml",
}


@dataclass(frozen=True, slots=True)
class Descoberta:
    """Um par (tabela, fase) que devolveu jogos de verdade.

    Os números vêm de uma **amostra** das primeiras rodadas, não da temporada
    inteira — a descoberta testa poucas rodadas para não fazer dezenas de
    requisições por candidato. O total real aparece depois de importar.
    """

    table_id: str
    phase: str
    fixtures: int
    """Jogos encontrados na amostra."""

    upcoming: int
    """Quantos deles ainda vão acontecer. Zero não significa temporada
    encerrada: as primeiras rodadas costumam estar no passado."""

    rounds_found: int
    sample: list[str]


async def descobrir_competicao(
    url: str, *, client: httpx.AsyncClient | None = None, max_rodadas: int = 6
) -> list[Descoberta]:
    """Acha tabela e fase a partir do endereço da página do campeonato no GE.

    O GE não tem catálogo público: o identificador da tabela é um UUID embutido
    no HTML, misturado com dezenas de ids de matéria. Esta função extrai todos
    os candidatos, testa cada um contra o endpoint de jogos e devolve os que
    responderam — testar é a única forma de saber qual UUID é a tabela.

    Também tenta as variantes de ano: liga europeia usa ``-2025-2026``, liga de
    ano civil usa ``-2026``, e a página quase sempre mostra só uma das duas.
    """
    fechar = client is None
    client = client or httpx.AsyncClient(timeout=httpx.Timeout(20.0), follow_redirects=True)

    try:
        try:
            pagina = await client.get(url, headers=NAVEGADOR)
        except httpx.HTTPError as exc:
            raise GloboScrapeError(f"não consegui abrir {url}: {exc}") from exc

        if pagina.status_code != 200:
            raise GloboScrapeError(f"{url} devolveu HTTP {pagina.status_code}. Confira o endereço.")

        uuids = sorted(set(UUID_RE.findall(pagina.text)))
        fases_cruas = sorted(set(FASE_RE.findall(pagina.text)))

        if not uuids:
            raise GloboScrapeError(
                "nenhum identificador encontrado nessa página. Ela provavelmente "
                "não é a página de um campeonato do ge.globo.com."
            )
        if not fases_cruas:
            raise GloboScrapeError(
                "achei identificadores mas nenhuma fase. Essa página não expõe a "
                "tabela — tente a aba de tabela ou jogos do campeonato."
            )

        fases: list[str] = []
        for fase in fases_cruas[:6]:
            fases.append(fase)
            curto = re.match(r"^(.*)-(\d{4})$", fase)
            if curto:
                raiz, ano = curto.group(1), int(curto.group(2))
                fases.append(f"{raiz}-{ano - 1}-{ano}")
                fases.append(f"{raiz}-{ano}-{ano + 1}")

        agora = datetime.now(UTC)
        achados: list[Descoberta] = []

        for fase in dict.fromkeys(fases):
            for tabela in uuids:
                provider = GloboProvider(tabela_id=tabela, phase=fase, client=client)
                total = futuros = rodadas_com_jogo = 0
                amostra: list[str] = []

                for rodada in range(1, max_rodadas + 1):
                    try:
                        jogos = await provider._get_round(0, rodada)
                    except GloboScrapeError:
                        break
                    if not jogos:
                        continue
                    rodadas_com_jogo += 1
                    total += len(jogos)
                    for jogo in jogos:
                        equipes = jogo.get("equipes") or {}
                        casa = (equipes.get("mandante") or {}).get("nome_popular")
                        fora = (equipes.get("visitante") or {}).get("nome_popular")
                        if casa and fora and len(amostra) < 3:
                            amostra.append(f"{casa} x {fora}")
                        crua = jogo.get("data_realizacao")
                        if not crua:
                            continue
                        try:
                            quando = datetime.fromisoformat(str(crua)).replace(tzinfo=UTC)
                        except ValueError:
                            continue
                        if quando > agora:
                            futuros += 1

                if total:
                    achados.append(
                        Descoberta(
                            table_id=tabela,
                            phase=fase,
                            fixtures=total,
                            upcoming=futuros,
                            rounds_found=rodadas_com_jogo,
                            sample=amostra,
                        )
                    )
                    break  # A tabela é uma só; achou, passa para a próxima fase.

        if not achados:
            raise GloboScrapeError(
                f"testei {len(uuids)} identificadores contra {len(set(fases))} fases e "
                "nenhum devolveu jogos. Essa competição pode usar fases por etapa "
                "(oitavas, quartas) que não aparecem na página inicial."
            )

        # Quem tem jogo futuro primeiro: é o que serve para montar rodada.
        return sorted(achados, key=lambda item: (-item.upcoming, -item.fixtures))
    finally:
        if fechar:
            await client.aclose()


class GloboProvider(FootballDataProvider):
    slug = "globo"
    name = "GE Globo (coleta)"

    #: O GE publica `placar_oficial` enquanto o jogo acontece — é o mesmo
    #: endpoint que alimenta o placar do site deles.
    supports_live = True

    #: E publica por rodada: uma requisição cobre os dez jogos de uma vez, o
    #: que torna a coleta de dois em dois minutos barata.
    supports_partial = True

    #: Última coleta, para a CLI reportar o que foi pulado.
    last_collection: Coleta | None = None

    def __init__(
        self,
        *,
        tabela_id: str = TABELA_SERIE_A,
        base_url: str = BASE_URL,
        delay_seconds: float = 1.0,
        client: httpx.AsyncClient | None = None,
        competition_name: str = "Brasileirão Série A",
        competition_external_id: str | None = None,
        country: str | None = "Brasil",
        phase: str | None = None,
        rounds: int = TOTAL_RODADAS,
    ) -> None:
        self._tabela = tabela_id
        # Identidade da competição para o upsert, que **não** é sempre a tabela.
        # O Brasileirão foi importado pela CLI e ficou gravado com o slug; se a
        # recoleta se apresentasse com o UUID, o ingest não encontraria a
        # competição, criaria outra, e o jogo iria para uma temporada nova —
        # onde o INSERT bate na chave única `(provider, external_id)`. Foi
        # exatamente isso que derrubou toda coleta ao vivo do Brasileirão.
        self._external_id = competition_external_id or tabela_id
        # O GE não diz de que país é o campeonato — quem sabe é quem manda
        # importar. Sem isso a Premier League entra cadastrada como brasileira.
        self._country = country
        # A fase é parte da identidade do campeonato no GE e muda de ano: ligas
        # europeias usam "-2025-2026", as de ano civil usam "-2026". Por isso
        # ela é informada, não derivada.
        self._phase = phase
        self._competition_name = competition_name
        self._rounds = rounds
        self._base_url = base_url.rstrip("/")
        # Uma requisição por segundo. São 38 chamadas para a temporada inteira;
        # não há razão para ir mais rápido e atrair bloqueio.
        self._delay = delay_seconds
        self._client = client or httpx.AsyncClient(
            timeout=httpx.Timeout(20.0),
            follow_redirects=True,
            headers={
                # Cabeçalho honesto: identifica o que está chamando em vez de
                # se passar por navegador.
                "User-Agent": "bolao-platform/0.1 (uso pessoal, rede local)",
                "Accept": "application/json",
            },
        )

    # -- infraestrutura ----------------------------------------------------

    def _phase_for(self, year: int) -> str:
        return self._phase or f"fase-unica-campeonato-brasileiro-{year}"

    def _round_url(self, year: int, rodada: int) -> str:
        return (
            f"{self._base_url}/tabela/{self._tabela}"
            f"/fase/{self._phase_for(year)}"
            f"/rodada/{rodada}/jogos/"
        )

    async def _get_round(self, year: int, rodada: int) -> list[dict[str, Any]]:
        url = self._round_url(year, rodada)
        try:
            response = await self._client.get(url)
        except httpx.HTTPError as exc:
            raise GloboScrapeError(
                f"não foi possível alcançar o GE na rodada {rodada}: {exc}. "
                "Verifique a conexão da máquina que roda a plataforma."
            ) from exc

        if response.status_code == 404:
            # Rodada ainda não publicada. Normal em temporada em andamento.
            return []
        if response.status_code in (401, 403, 429):
            raise GloboScrapeError(
                f"o GE recusou a requisição (HTTP {response.status_code}) na rodada "
                f"{rodada}. Coleta automatizada não é autorizada por eles e pode "
                "ser bloqueada a qualquer momento."
            )
        if response.status_code >= 400:
            raise GloboScrapeError(f"rodada {rodada} devolveu HTTP {response.status_code}")

        try:
            payload = response.json()
        except ValueError as exc:
            raise GloboScrapeError(
                f"a rodada {rodada} não veio em JSON. O endpoint interno do GE "
                "provavelmente mudou — este coletor precisa ser atualizado."
            ) from exc

        if not isinstance(payload, list):
            raise GloboScrapeError(
                f"a rodada {rodada} veio como {type(payload).__name__}, esperava uma "
                "lista de jogos. Formato do GE mudou."
            )
        return payload

    async def aclose(self) -> None:
        await self._client.aclose()

    # -- tradução ----------------------------------------------------------

    def _team(self, raw: dict[str, Any] | None, lado: str, rodada: int) -> ProviderTeam:
        if not raw:
            raise GloboScrapeError(
                f"jogo da rodada {rodada} veio sem o time {lado}. Formato do GE mudou."
            )

        nome = raw.get("nome_popular") or raw.get("nome")
        if not nome:
            raise GloboScrapeError(
                f"time {lado} da rodada {rodada} veio sem nome. Formato do GE mudou."
            )

        externo = str(raw.get("id") or slugify(str(nome)))
        return ProviderTeam(
            external_id=externo,
            name=str(nome),
            slug=slugify(str(nome)),
            short_name=raw.get("sigla"),
            crest_url=raw.get("escudo"),
            # O GE não manda o país do clube. Herdar o do campeonato acerta em
            # liga nacional; em torneio continental é a região, não o país.
            country=self._country,
        )

    @classmethod
    def _kickoff(cls, jogo: dict[str, Any], rodada: int) -> datetime:
        """Converte o horário local do GE para UTC.

        O GE manda ``data_realizacao`` como ISO **sem fuso**, em horário de
        Brasília — hoje com a hora junto (``2026-01-28T19:00``), mas já mandou
        só a data. Os dois casos são aceitos, e ``hora_realizacao`` entra
        quando a data vem sem hora.

        Levanta ``SemDataDefinida`` quando o jogo ainda não tem data marcada.
        Isso é comum: a CBF publica a rodada antes de fechar todos os horários.
        """
        crua = jogo.get("data_realizacao")
        if not crua:
            raise SemDataDefinida

        texto = str(crua)
        try:
            if "T" in texto:
                local = datetime.fromisoformat(texto).replace(tzinfo=None)
            else:
                dia = date.fromisoformat(texto[:10])
                hora, minuto = cls._parse_hora(jogo.get("hora_realizacao"))
                # Ingênuo de propósito: é horário de Brasília, e o fuso é
                # aplicado na conversão para UTC logo abaixo.
                local = datetime(dia.year, dia.month, dia.day, hora, minuto)  # noqa: DTZ001
        except ValueError as exc:
            raise GloboScrapeError(
                f"data '{crua}' na rodada {rodada} não é ISO. Formato do GE mudou."
            ) from exc

        return (local - BRASILIA_OFFSET).replace(tzinfo=UTC)

    @staticmethod
    def _parse_hora(bruto: Any) -> tuple[int, int]:
        """Hora vinda como ``19:00`` ou ``19:00:00``. Sem hora, 16h."""
        try:
            partes = [int(parte) for parte in str(bruto).split(":")[:2]]
        except (ValueError, TypeError):
            return 16, 0
        if not partes:
            return 16, 0
        return partes[0], (partes[1] if len(partes) > 1 else 0)

    @classmethod
    def _status(cls, jogo: dict[str, Any], kickoff: datetime, now: datetime) -> FixtureStatus:
        """Deduz o estado do jogo.

        O GE **não manda campo de status** nesta rota — a rodada inteira vem
        sem ele. O que existe é ``jogo_ja_comecou`` e a presença de placar.

        Separar "em andamento" de "encerrado" importa: marcar como encerrado um
        jogo que ainda rola apuraria a pontuação no intervalo e revelaria os
        palpites antes da hora.
        """
        crua = str(jogo.get("jogo_status") or jogo.get("status") or "").lower().strip()
        if crua:
            traduzido = STATUS_MAP.get(crua)
            if traduzido is not None:
                return traduzido
            log.warning("globo.status_desconhecido", status=crua)

        tem_placar = (
            jogo.get("placar_oficial_mandante") is not None
            and jogo.get("placar_oficial_visitante") is not None
        )
        comecou = bool(jogo.get("jogo_ja_comecou")) or now >= kickoff

        if comecou and now < kickoff + DURACAO_MAXIMA:
            return FixtureStatus.LIVE
        if tem_placar:
            return FixtureStatus.FINISHED
        return FixtureStatus.SCHEDULED

    def _fixture(
        self,
        jogo: dict[str, Any],
        year: int,
        rodada: int,
        now: datetime | None = None,
    ) -> ProviderFixture:
        now = now or datetime.now(UTC)
        equipes = jogo.get("equipes") or {}
        mandante = self._team(equipes.get("mandante"), "mandante", rodada)
        visitante = self._team(equipes.get("visitante"), "visitante", rodada)

        externo = str(jogo.get("id") or jogo.get("jogo_id") or "")
        if not externo:
            # Sem id do GE, monta uma chave estável a partir do confronto —
            # o upsert depende dela para não duplicar na próxima coleta.
            externo = f"ge-{year}-r{rodada:02d}-{mandante.slug}-{visitante.slug}"

        kickoff = self._kickoff(jogo, rodada)
        sede = jogo.get("sede") or {}

        return ProviderFixture(
            external_id=externo,
            home_team_external_id=mandante.external_id,
            away_team_external_id=visitante.external_id,
            kickoff_at=kickoff,
            status=self._status(jogo, kickoff, now),
            round=ProviderRound(name=f"Rodada {rodada}", number=rodada),
            venue=sede.get("nome_popular") or sede.get("nome"),
            home_ft=jogo.get("placar_oficial_mandante"),
            away_ft=jogo.get("placar_oficial_visitante"),
        )

    # -- contrato ----------------------------------------------------------

    async def list_competitions(self) -> Sequence[ProviderCompetition]:
        # A identidade externa é a que o banco já guarda para esta competição
        # (por padrão, a tabela do GE). É ela que faz o upsert cair na linha
        # certa em vez de criar uma competição paralela.
        return (
            ProviderCompetition(
                external_id=self._external_id,
                slug=slugify(self._competition_name),
                name=self._competition_name,
                country=self._country,
                type=CompetitionType.LEAGUE,
                # É o que permite ao worker voltar aqui sozinho: sem a fase não
                # há recoleta, e a fase só existe neste instante.
                config={
                    "table_id": self._tabela,
                    "phase": self._phase_for(datetime.now(UTC).year),
                    "rounds": self._rounds,
                },
            ),
        )

    async def list_seasons(self, competition_external_id: str) -> Sequence[ProviderSeason]:
        agora = datetime.now(UTC)
        return tuple(
            ProviderSeason(
                external_id=str(ano),
                competition_external_id=competition_external_id,
                year=ano,
                is_current=ano == agora.year,
            )
            for ano in range(2020, agora.year + 1)
        )

    async def _collect(self, year: int, rodadas: Sequence[int] | None = None) -> Coleta:
        """Percorre as 38 rodadas.

        Jogo sem data marcada é **pulado**, não inventado: sem horário não há
        como travar o palpite na hora certa, e chutar uma data faria o bolão
        fechar antes ou depois do apito. Como o upsert é idempotente, a próxima
        coleta traz o jogo assim que a CBF confirmar o horário.
        """
        times: dict[str, ProviderTeam] = {}
        jogos: list[ProviderFixture] = []
        vazias: list[int] = []
        sem_data: list[str] = []
        agora = datetime.now(UTC)

        alvo = sorted(rodadas) if rodadas else list(range(1, self._rounds + 1))

        for indice, rodada in enumerate(alvo):
            bruto = await self._get_round(year, rodada)
            if not bruto:
                vazias.append(rodada)

            for item in bruto:
                equipes = item.get("equipes") or {}
                mandante = self._team(equipes.get("mandante"), "mandante", rodada)
                visitante = self._team(equipes.get("visitante"), "visitante", rodada)
                times.setdefault(mandante.external_id, mandante)
                times.setdefault(visitante.external_id, visitante)

                try:
                    jogos.append(self._fixture(item, year, rodada, now=agora))
                except SemDataDefinida:
                    sem_data.append(f"R{rodada}: {mandante.name} x {visitante.name}")

            if indice < len(alvo) - 1:
                await asyncio.sleep(self._delay)

        if not jogos:
            raise GloboScrapeError(
                f"nenhum jogo encontrado para {year}"
                + (f" nas rodadas {alvo}" if rodadas else "")
                + ". Ou a temporada não existe no GE com esse ano, ou o "
                "endereço do endpoint mudou."
            )

        return Coleta(teams=times, fixtures=jogos, empty_rounds=vazias, undated=sem_data)

    async def list_teams(self, competition_external_id: str, year: int) -> Sequence[ProviderTeam]:
        return tuple((await self._collect(year)).teams.values())

    async def list_fixtures(
        self, competition_external_id: str, year: int
    ) -> Sequence[ProviderFixture]:
        return tuple((await self._collect(year)).fixtures)

    async def get_fixture(self, external_id: str) -> ProviderFixture | None:
        # O GE não expõe jogo avulso por id nesta rota; a atualização vem da
        # coleta da temporada, que é barata o bastante (38 requisições).
        return None

    async def import_rounds(
        self, competition_external_id: str, year: int, rounds: Sequence[int]
    ) -> ProviderSnapshot:
        """Retrato só das rodadas pedidas — o caminho barato da coleta ao vivo.

        Uma requisição por rodada, em vez das trinta e oito da temporada. Com
        uma rodada em campo isso é 1/38 do custo, e é o que permite bater de
        dois em dois minutos sem abusar de um endpoint que não é público.
        """
        return await self._montar(year, await self._collect(year, rounds), parcial=True)

    async def import_season(self, competition_external_id: str, year: int) -> ProviderSnapshot:
        """Retrato completo em uma passada — evita coletar 38 rodadas três vezes."""
        return await self._montar(year, await self._collect(year))

    async def _montar(
        self, year: int, coleta: Coleta, *, parcial: bool = False
    ) -> ProviderSnapshot:
        """Empacota uma coleta em retrato, seja da temporada ou de uma rodada.

        ``parcial`` muda uma coisa só, e ela importa: a coleta de uma rodada
        não sabe quando a temporada começa nem quando acaba, e dizer que sabe
        encolheria o intervalo da temporada para os dois dias daquela rodada —
        de dois em dois minutos, durante o jogo.
        """
        self.last_collection = coleta

        if coleta.empty_rounds:
            log.info(
                "globo.rodadas_sem_jogos",
                ano=year,
                rodadas=coleta.empty_rounds,
                detalhe="normal em temporada cuja tabela ainda não saiu inteira",
            )
        if coleta.undated:
            log.warning(
                "globo.jogos_sem_data",
                ano=year,
                total=len(coleta.undated),
                exemplos=coleta.undated[:5],
                detalhe="pulados; voltam na próxima coleta quando a CBF marcar o horário",
            )

        competicao = (await self.list_competitions())[0]
        primeiro = min(jogo.kickoff_at for jogo in coleta.fixtures)
        ultimo = max(jogo.kickoff_at for jogo in coleta.fixtures)

        log.info(
            "globo.coleta",
            ano=year,
            times=len(coleta.teams),
            jogos=len(coleta.fixtures),
            sem_data=len(coleta.undated),
        )

        return ProviderSnapshot(
            competition=competicao,
            season=ProviderSeason(
                external_id=str(year),
                competition_external_id=competicao.external_id,
                year=year,
                start_date=None if parcial else primeiro.date(),
                end_date=None if parcial else ultimo.date(),
                is_current=year == datetime.now(UTC).year,
            ),
            teams=tuple(coleta.teams.values()),
            fixtures=tuple(coleta.fixtures),
        )
