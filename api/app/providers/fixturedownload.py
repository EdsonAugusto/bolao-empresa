"""Coletor do fixturedownload.com.

Existe por um motivo específico: o GE não publica a temporada europeia nova até
ela começar. Em 31/07/2026 a plataforma dizia "Premier League — temporada
encerrada" enquanto o Google já mostrava Arsenal x Coventry na primeira rodada
de 21/08. O dado existia; a fonte é que não tinha.

O que este provedor traz de diferente
-------------------------------------
1. **CSV público, sem chave e sem cota.** Uma requisição por temporada.
2. **Datas já em UTC.** O arquivo ``-UTC.csv`` dispensa adivinhar fuso — que é
   onde importação de calendário estrangeiro costuma errar por uma hora inteira
   quando muda o horário de verão de lá.
3. **Placar junto.** A coluna ``Result`` vem preenchida no jogo já realizado, o
   que faz a reimportação apurar o que faltava sem passo extra.

Limites, ditos na cara
----------------------
- **Não tem escudo.** Os clubes entram com o escudo desenhado pela plataforma.
- **Não tem jogo adiado marcado como tal**: linha sem data é pulada, igual ao
  coletor do GE.
- **Só liga de pontos corridos.** Mata-mata (Libertadores, Copa do Brasil) não
  está lá — para esses, GE ou CSV manual.
"""

from __future__ import annotations

import csv
import io
import re
from collections.abc import Sequence
from datetime import UTC, datetime

import httpx
from slugify import slugify

from app.core.logging import get_logger
from app.data.ligas import LIGAS, LIGAS_POR_SLUG, TEMPORADA
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

BASE_URL = "https://fixturedownload.com/download"

#: Colunas do arquivo. Se alguma sumir, a coleta para com nome e tudo em vez de
#: importar meia tabela.
COLUNAS = ("Round Number", "Date", "Home Team", "Away Team")

#: ``2 - 1``, ou vazio quando o jogo ainda não aconteceu.
PLACAR_RE = re.compile(r"^\s*(\d+)\s*-\s*(\d+)\s*$")


class FixtureDownloadError(ProviderError):
    """O formato do CSV mudou, ou o arquivo saiu do ar."""


def url_da_temporada(liga_slug: str, year: int) -> str:
    return f"{BASE_URL}/{liga_slug}-{year}-UTC.csv"


class FixtureDownloadProvider(FootballDataProvider):
    slug = "fixturedownload"
    name = "fixturedownload.com (CSV)"

    #: Preenchido pela última coleta, para o endpoint reportar o que foi pulado.
    ultimas_sem_data: list[str]

    def __init__(self, *, base_url: str = BASE_URL, client: httpx.AsyncClient | None = None):
        self._base_url = base_url.rstrip("/")
        self.ultimas_sem_data = []
        self._client = client or httpx.AsyncClient(
            timeout=httpx.Timeout(30.0),
            follow_redirects=True,
            headers={
                # Identifica quem está chamando em vez de fingir ser navegador.
                "User-Agent": "bolao-platform/0.1 (uso pessoal, rede local)",
                # `*/*` de propósito: o servidor devolve 406 para
                # `Accept: text/csv`, mesmo servindo CSV.
                "Accept": "*/*",
            },
        )

    # -- infraestrutura ----------------------------------------------------

    async def _baixar(self, liga_slug: str, year: int) -> list[dict[str, str]]:
        url = f"{self._base_url}/{liga_slug}-{year}-UTC.csv"
        try:
            resposta = await self._client.get(url)
        except httpx.HTTPError as exc:
            raise FixtureDownloadError(
                f"não consegui baixar {url}: {exc}. Verifique a conexão da máquina "
                "que roda a plataforma."
            ) from exc

        if resposta.status_code == 404:
            raise FixtureDownloadError(
                f"{liga_slug} não tem temporada {year} publicada. Ligas de virada "
                "são identificadas pelo ano em que começam: 2026 é a 2026-27."
            )
        if resposta.status_code >= 400:
            raise FixtureDownloadError(f"{url} devolveu HTTP {resposta.status_code}")

        leitor = csv.DictReader(io.StringIO(resposta.text.strip()))
        if leitor.fieldnames is None:
            raise FixtureDownloadError(f"{url} veio vazio")

        faltando = [coluna for coluna in COLUNAS if coluna not in leitor.fieldnames]
        if faltando:
            raise FixtureDownloadError(
                f"faltam colunas em {url}: {', '.join(faltando)}. O formato mudou "
                "e este coletor precisa ser atualizado."
            )
        return list(leitor)

    async def aclose(self) -> None:
        await self._client.aclose()

    # -- tradução ----------------------------------------------------------

    @staticmethod
    def _kickoff(bruto: str, linha: int) -> datetime:
        """``21/08/2026 19:00`` — dia/mês/ano, já em UTC pelo nome do arquivo."""
        texto = bruto.strip()
        if not texto:
            raise ValueError("sem data")
        try:
            ingenuo = datetime.strptime(texto, "%d/%m/%Y %H:%M")  # noqa: DTZ007
        except ValueError as exc:
            raise FixtureDownloadError(
                f"data '{bruto}' na linha {linha} não está em dd/mm/aaaa hh:mm. "
                "O formato do arquivo mudou."
            ) from exc
        return ingenuo.replace(tzinfo=UTC)

    @staticmethod
    def _placar(bruto: str | None) -> tuple[int | None, int | None]:
        if not bruto:
            return None, None
        achado = PLACAR_RE.match(bruto)
        if achado is None:
            # Pênalti, W.O. e afins aparecem aqui. Registrar e seguir é melhor
            # do que derrubar a importação inteira por causa de um jogo.
            log.warning("fixturedownload.placar_ilegivel", valor=bruto)
            return None, None
        return int(achado.group(1)), int(achado.group(2))

    #: Como o fixturedownload nomeia as fases eliminatórias, e o que elas são
    #: aqui. O número continua a contagem da fase de liga, para a ordenação da
    #: tela sair certa sem ninguém precisar entender o torneio.
    #:
    #: Sem este mapa, `int("R16 Game 1")` estourava e a rodada virava None — e
    #: jogo sem rodada entra no banco com `round_id` nulo, isto é, não aparece
    #: em rodada nenhuma para montar bolão. Numa Champions são 45 dos 189 jogos:
    #: todo o mata-mata, justamente a parte que as pessoas mais querem palpitar.
    FASES_ELIMINATORIAS: dict[str, tuple[str, int]] = {
        "play-off game 1": ("Play-off (ida)", 9),
        "play-off game 2": ("Play-off (volta)", 10),
        "r16 game 1": ("Oitavas (ida)", 11),
        "r16 game 2": ("Oitavas (volta)", 12),
        "qf game 1": ("Quartas (ida)", 13),
        "qf game 2": ("Quartas (volta)", 14),
        "sf game 1": ("Semifinal (ida)", 15),
        "sf game 2": ("Semifinal (volta)", 16),
        "final": ("Final", 17),
    }

    @classmethod
    def _rodada(cls, bruto: str | None) -> ProviderRound | None:
        texto = str(bruto or "").strip()
        if not texto:
            return None

        try:
            numero = int(texto)
        except ValueError:
            pass
        else:
            return ProviderRound(name=f"Rodada {numero}", number=numero)

        eliminatoria = cls.FASES_ELIMINATORIAS.get(texto.casefold())
        if eliminatoria is not None:
            nome, ordem = eliminatoria
            return ProviderRound(name=nome, number=ordem, is_knockout=True)

        # Rótulo que ainda não conhecemos. Vira rodada com o nome cru em vez de
        # sumir: melhor um nome esquisito na tela do que um jogo invisível.
        log.warning("fixturedownload.rodada_desconhecida", valor=texto)
        return ProviderRound(name=texto, is_knockout=True)

    def _traduzir(
        self, linhas: list[dict[str, str]], liga_slug: str, year: int, agora: datetime
    ) -> tuple[dict[str, ProviderTeam], list[ProviderFixture]]:
        times: dict[str, ProviderTeam] = {}
        jogos: list[ProviderFixture] = []
        sem_data: list[str] = []

        liga = LIGAS_POR_SLUG.get(liga_slug)
        pais = liga.country if liga else None

        for numero, linha in enumerate(linhas, start=2):
            casa = (linha.get("Home Team") or "").strip()
            fora = (linha.get("Away Team") or "").strip()
            if not casa or not fora:
                continue
            if casa == fora:
                raise FixtureDownloadError(f"linha {numero}: {casa} jogando contra si mesmo")

            for nome in (casa, fora):
                chave = slugify(nome)
                times.setdefault(
                    chave,
                    ProviderTeam(external_id=chave, name=nome, slug=chave, country=pais),
                )

            try:
                kickoff = self._kickoff(linha.get("Date") or "", numero)
            except ValueError:
                # Sem horário não dá para travar o palpite na hora certa. Pular é
                # melhor do que inventar data — o upsert traz o jogo depois.
                sem_data.append(f"{casa} x {fora}")
                continue

            rodada = self._rodada(linha.get("Round Number"))
            gols_casa, gols_fora = self._placar(linha.get("Result"))
            jogado = gols_casa is not None and gols_fora is not None

            jogos.append(
                ProviderFixture(
                    external_id=build_external_id(
                        liga_slug, str(year), slugify(casa), slugify(fora)
                    ),
                    home_team_external_id=slugify(casa),
                    away_team_external_id=slugify(fora),
                    kickoff_at=kickoff,
                    status=self._status(jogado, kickoff, agora),
                    round=rodada,
                    venue=(linha.get("Location") or "").strip() or None,
                    home_ft=gols_casa,
                    away_ft=gols_fora,
                )
            )

        self.ultimas_sem_data = sem_data
        return times, jogos

    @staticmethod
    def _status(jogado: bool, kickoff: datetime, agora: datetime) -> FixtureStatus:
        """Placar manda; na ausência dele, o relógio.

        Um jogo com placar e ``kickoff`` no futuro seria data errada na fonte —
        e travaria o palpite antes da hora. Por isso ``FINISHED`` só sai quando
        o jogo já começou de verdade.
        """
        if jogado and agora >= kickoff:
            return FixtureStatus.FINISHED
        return FixtureStatus.SCHEDULED

    # -- contrato ----------------------------------------------------------

    async def list_competitions(self) -> Sequence[ProviderCompetition]:
        return tuple(
            ProviderCompetition(
                external_id=liga.slug,
                slug=slugify(liga.name),
                name=liga.name,
                country=liga.country,
                type=CompetitionType.LEAGUE,
            )
            for liga in LIGAS
        )

    async def list_seasons(self, competition_external_id: str) -> Sequence[ProviderSeason]:
        liga = LIGAS_POR_SLUG.get(competition_external_id)
        ano = liga.season_year(TEMPORADA) if liga else TEMPORADA
        return (
            ProviderSeason(
                external_id=str(TEMPORADA),
                competition_external_id=competition_external_id,
                year=ano,
                is_current=True,
            ),
        )

    async def list_teams(self, competition_external_id: str, year: int) -> Sequence[ProviderTeam]:
        linhas = await self._baixar(competition_external_id, year)
        times, _ = self._traduzir(linhas, competition_external_id, year, datetime.now(UTC))
        return tuple(times.values())

    async def list_fixtures(
        self, competition_external_id: str, year: int
    ) -> Sequence[ProviderFixture]:
        linhas = await self._baixar(competition_external_id, year)
        _, jogos = self._traduzir(linhas, competition_external_id, year, datetime.now(UTC))
        return tuple(jogos)

    async def get_fixture(self, external_id: str) -> ProviderFixture | None:
        # O CSV é por temporada; buscar um jogo isolado significaria baixar a
        # tabela inteira. A apuração usa o import completo, que é idempotente.
        return None

    async def import_season(self, competition_external_id: str, year: int) -> ProviderSnapshot:
        """Uma requisição só: o CSV já traz times, jogos e placares."""
        liga = LIGAS_POR_SLUG.get(competition_external_id)
        linhas = await self._baixar(competition_external_id, year)
        times, jogos = self._traduzir(linhas, competition_external_id, year, datetime.now(UTC))

        nome = liga.name if liga else competition_external_id
        return ProviderSnapshot(
            competition=ProviderCompetition(
                external_id=competition_external_id,
                slug=slugify(nome),
                name=nome,
                country=liga.country if liga else None,
                type=CompetitionType.LEAGUE,
                config={"league": competition_external_id},
            ),
            season=ProviderSeason(
                external_id=str(year),
                competition_external_id=competition_external_id,
                # O arquivo é nomeado pelo ano em que a temporada começa; o
                # banco guarda o ano em que ela termina.
                year=liga.season_year(year) if liga else year,
                is_current=True,
            ),
            teams=tuple(times.values()),
            fixtures=tuple(jogos),
        )
