"""Coletor de mata-mata pela Wikipédia.

Por que existe
--------------
Liga de pontos corridos tem calendário publicado em CSV; mata-mata não. O GE
expõe a fase de grupos da Libertadores e devolve lista vazia para as oitavas —
testei as oito fases contra os vinte identificadores da página e nenhuma
combinação traz jogo. A CONMEBOL não publica formato aberto.

A Wikipédia publica, e publica bem: cada jogo é um ``{{Footballbox}}`` com data,
hora **e fuso explícito** (``{{UTZ|21:30|-3}}``), que é justamente o que costuma
sair errado numa importação de torneio continental — times em cinco fusos
diferentes, cada jogo no horário local do mandante.

O que este coletor assume
-------------------------
1. **A fonte é uma enciclopédia, não um placar ao vivo.** Serve para trazer o
   calendário e o chaveamento; o placar chega depois, pela edição do jogo ou
   por outra fonte. Placar preenchido no artigo é aproveitado.
2. **O ano vem de quem chama.** ``|data = 11 de agosto`` não tem ano. Pegar do
   artigo é o certo, e é por isso que o ano é parâmetro e não adivinhação.
3. **Falha alto.** Artigo sem nenhum ``Footballbox`` é erro com nome, não uma
   importação vazia que passa despercebida.
"""

from __future__ import annotations

import re
from collections.abc import Sequence
from dataclasses import dataclass
from datetime import UTC, datetime, timedelta

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
    build_external_id,
)

log = get_logger(__name__)

API_URL = "https://pt.wikipedia.org/w/api.php"

MESES = {
    "janeiro": 1,
    "fevereiro": 2,
    "março": 3,
    "marco": 3,
    "abril": 4,
    "maio": 5,
    "junho": 6,
    "julho": 7,
    "agosto": 8,
    "setembro": 9,
    "outubro": 10,
    "novembro": 11,
    "dezembro": 12,
}

CAIXA_RE = re.compile(r"\{\{\s*Footballbox(.*?)\n\}\}", re.IGNORECASE | re.DOTALL)
SECAO_RE = re.compile(r"^==+\s*(.+?)\s*==+\s*$", re.MULTILINE)
TIME_RE = re.compile(r"\{\{\s*Futebol\s+([^|}]+?)\s*(?:\|[^}]*)?\}\}")
HORA_RE = re.compile(r"\{\{\s*UTZ\s*\|\s*(\d{1,2}):(\d{2})\s*\|\s*([+-]?\d{1,2})\s*\}\}")
DATA_RE = re.compile(r"(\d{1,2})\s+de\s+([a-zçã]+)", re.IGNORECASE)
# A Wikipedia separa os gols com travessao U+2013, nao com hifen. Escrito em
# escape porque os dois caracteres sao identicos na tela.
PLACAR_RE = re.compile("(\\d+)\\s*[–-]\\s*(\\d+)")  # noqa: RUF001
LOCAL_RE = re.compile(r"\[\[([^\]|]+)(?:\|[^\]]+)?\]\]")


class WikipediaScrapeError(ProviderError):
    """O artigo mudou de formato, ou não é um artigo de tabela."""


@dataclass(frozen=True, slots=True)
class Jogo:
    """Um ``Footballbox`` já traduzido. Público para o teste de extração."""

    casa: str
    fora: str
    kickoff: datetime
    fase: str
    local: str | None
    gols_casa: int | None
    gols_fora: int | None


def _campos(bloco: str) -> dict[str, str]:
    """Quebra o corpo de um ``Footballbox`` em ``campo -> valor``.

    Feito na mão em vez de por regex de linha porque valor de campo pode ter
    quebra de linha (nota de rodapé do estádio, por exemplo).
    """
    saida: dict[str, str] = {}
    for pedaco in bloco.split("\n|"):
        if "=" not in pedaco:
            continue
        chave, _, valor = pedaco.partition("=")
        saida[chave.strip().lstrip("|").lower()] = valor.strip()
    return saida


def _time(bruto: str) -> str | None:
    achado = TIME_RE.search(bruto)
    if achado:
        return achado.group(1).strip()
    limpo = LOCAL_RE.sub(r"\1", bruto).strip()
    return limpo or None


def _kickoff(campos: dict[str, str], ano: int) -> datetime | None:
    """Converte ``11 de agosto`` + ``{{UTZ|21:30|-3}}`` em UTC.

    Sem hora não dá para travar o palpite na hora certa — devolve ``None`` e o
    jogo é pulado, igual aos outros coletores.
    """
    data = DATA_RE.search(campos.get("data", ""))
    hora = HORA_RE.search(campos.get("hora", ""))
    if data is None or hora is None:
        return None

    mes = MESES.get(data.group(2).lower())
    if mes is None:
        return None

    dia = int(data.group(1))
    offset = timedelta(hours=int(hora.group(3)))
    local = datetime(ano, mes, dia, int(hora.group(1)), int(hora.group(2)))  # noqa: DTZ001
    return (local - offset).replace(tzinfo=UTC)


def _placar(bruto: str) -> tuple[int | None, int | None]:
    achado = PLACAR_RE.search(bruto)
    if achado is None:
        return None, None
    return int(achado.group(1)), int(achado.group(2))


def _fases_por_posicao(texto: str) -> list[tuple[int, str]]:
    """Onde cada seção começa, para dar nome à rodada de cada jogo."""
    return [(achado.start(), achado.group(1).strip()) for achado in SECAO_RE.finditer(texto)]


def _fase_de(posicao: int, secoes: list[tuple[int, str]]) -> str:
    nome = "Fase final"
    for inicio, titulo in secoes:
        if inicio > posicao:
            break
        # "Chave A" é subdivisão de "Oitavas de final"; o que interessa para a
        # rodada é a fase, não a chave.
        if not titulo.lower().startswith("chave"):
            nome = titulo
    return nome


def extrair_jogos(wikitexto: str, ano: int) -> list[Jogo]:
    secoes = _fases_por_posicao(wikitexto)
    jogos: list[Jogo] = []

    for caixa in CAIXA_RE.finditer(wikitexto):
        campos = _campos(caixa.group(1))
        casa = _time(campos.get("time1", ""))
        fora = _time(campos.get("time2", ""))
        if not casa or not fora or casa == fora:
            continue

        kickoff = _kickoff(campos, ano)
        if kickoff is None:
            continue

        gols_casa, gols_fora = _placar(campos.get("placar", ""))
        estadio = campos.get("estadio", "")
        local = LOCAL_RE.sub(r"\1", estadio).split(",")[0].strip() or None

        jogos.append(
            Jogo(
                casa=casa,
                fora=fora,
                kickoff=kickoff,
                fase=_fase_de(caixa.start(), secoes),
                local=local,
                gols_casa=gols_casa,
                gols_fora=gols_fora,
            )
        )

    return jogos


class WikipediaProvider(FootballDataProvider):
    slug = "wikipedia"
    name = "Wikipédia (mata-mata)"

    def __init__(
        self,
        *,
        artigo: str,
        competition_name: str,
        country: str | None = None,
        api_url: str = API_URL,
        client: httpx.AsyncClient | None = None,
    ) -> None:
        self._artigo = artigo
        self._competition_name = competition_name
        self._country = country
        self._api_url = api_url
        self._client = client or httpx.AsyncClient(
            timeout=httpx.Timeout(30.0),
            follow_redirects=True,
            headers={"User-Agent": "bolao-platform/0.1 (uso pessoal, rede local)"},
        )

    async def _wikitexto(self) -> str:
        try:
            resposta = await self._client.get(
                self._api_url,
                params={
                    "action": "parse",
                    "page": self._artigo,
                    "prop": "wikitext",
                    "format": "json",
                    "formatversion": "2",
                },
            )
        except httpx.HTTPError as exc:
            raise WikipediaScrapeError(f"não consegui abrir o artigo: {exc}") from exc

        if resposta.status_code != 200:
            raise WikipediaScrapeError(f"a Wikipédia devolveu HTTP {resposta.status_code}")

        dados = resposta.json()
        if "error" in dados:
            raise WikipediaScrapeError(
                f"artigo '{self._artigo}' não existe na Wikipédia em português "
                f"({dados['error'].get('code')})"
            )
        return str(dados["parse"]["wikitext"])

    async def aclose(self) -> None:
        await self._client.aclose()

    # -- contrato ----------------------------------------------------------

    async def list_competitions(self) -> Sequence[ProviderCompetition]:
        return (
            ProviderCompetition(
                external_id=self._artigo,
                slug=slugify(self._competition_name),
                name=self._competition_name,
                country=self._country,
                type=CompetitionType.CUP,
            ),
        )

    async def list_seasons(self, competition_external_id: str) -> Sequence[ProviderSeason]:
        return ()

    async def list_teams(self, competition_external_id: str, year: int) -> Sequence[ProviderTeam]:
        snapshot = await self.import_season(competition_external_id, year)
        return snapshot.teams

    async def list_fixtures(
        self, competition_external_id: str, year: int
    ) -> Sequence[ProviderFixture]:
        snapshot = await self.import_season(competition_external_id, year)
        return snapshot.fixtures

    async def get_fixture(self, external_id: str) -> ProviderFixture | None:
        return None

    async def import_season(self, competition_external_id: str, year: int) -> ProviderSnapshot:
        jogos = extrair_jogos(await self._wikitexto(), year)
        if not jogos:
            raise WikipediaScrapeError(
                f"nenhum jogo em '{self._artigo}'. Ou o artigo ainda não tem a "
                "tabela, ou o formato dele mudou."
            )

        agora = datetime.now(UTC)
        times: dict[str, ProviderTeam] = {}
        fixtures: list[ProviderFixture] = []
        # Numera as fases na ordem em que aparecem: oitavas antes de quartas,
        # quartas antes de semifinal. É o que faz a rodada sair na ordem certa.
        ordem: dict[str, int] = {}

        for jogo in jogos:
            for nome in (jogo.casa, jogo.fora):
                chave = slugify(nome)
                times.setdefault(
                    chave,
                    ProviderTeam(external_id=chave, name=nome, slug=chave, country=self._country),
                )

            numero = ordem.setdefault(jogo.fase, len(ordem) + 1)
            jogado = jogo.gols_casa is not None and jogo.gols_fora is not None

            fixtures.append(
                ProviderFixture(
                    external_id=build_external_id(
                        str(year),
                        slugify(jogo.fase),
                        slugify(jogo.casa),
                        slugify(jogo.fora),
                    ),
                    home_team_external_id=slugify(jogo.casa),
                    away_team_external_id=slugify(jogo.fora),
                    kickoff_at=jogo.kickoff,
                    # Placar com jogo no futuro seria data errada no artigo, e
                    # travaria o palpite antes do apito.
                    status=(
                        FixtureStatus.FINISHED
                        if jogado and agora >= jogo.kickoff
                        else FixtureStatus.SCHEDULED
                    ),
                    round=ProviderRound(
                        name=jogo.fase, number=numero, stage_name=jogo.fase, is_knockout=True
                    ),
                    venue=jogo.local,
                    home_ft=jogo.gols_casa,
                    away_ft=jogo.gols_fora,
                )
            )

        return ProviderSnapshot(
            competition=ProviderCompetition(
                external_id=self._artigo,
                slug=slugify(self._competition_name),
                name=self._competition_name,
                country=self._country,
                type=CompetitionType.CUP,
                config={"article": self._artigo},
            ),
            season=ProviderSeason(
                external_id=str(year),
                competition_external_id=self._artigo,
                year=year,
                is_current=True,
            ),
            teams=tuple(times.values()),
            fixtures=tuple(fixtures),
        )
