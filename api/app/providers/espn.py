"""Placar ao vivo pela API pública da ESPN.

Por que existe
--------------
O CSV das ligas europeias só publica o resultado horas depois do apito, e a
Wikipédia depende de alguém editar o artigo. Numa noite de rodada isso deixava
PSV contra Fortuna Sittard marcado como "em campo" muito depois de acabar, sem
placar e sem apuração.

A ESPN publica **durante** o jogo — placar, estado e minuto — por liga e por
dia. Uma requisição cobre todos os jogos de uma liga naquele dia.

O que esta fonte **não** faz
----------------------------
Ela não importa calendário. É uma **sobreposição de placar** sobre jogos que já
existem, e isso é deliberado: importar por aqui criaria uma segunda competição,
segundos times e segundos jogos ao lado dos que já estão no banco. O casamento é
feito por par de times dentro da liga e da janela de horário — o calendário
continua sendo de quem o importou.

Uma peculiaridade que custou meia hora
---------------------------------------
O endpoint **recusa User-Agent de navegador** com HTTP 403 (regra de bot do
Akamai) e responde 200 sem cabeçalho nenhum. Por isso este módulo não manda
``User-Agent`` — o contrário do que se faria por educação em qualquer outro
coletor.
"""

from __future__ import annotations

from collections.abc import Sequence
from dataclasses import dataclass
from datetime import UTC, date, datetime, timedelta

import httpx
from slugify import slugify

from app.core.logging import get_logger
from app.models.enums import FixtureStatus
from app.providers.base import (
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

BASE_URL = "https://site.api.espn.com/apis/site/v2/sports/soccer"

#: Liga da ESPN por competição nossa, para o backfill e para o padrão de quem
#: importar um campeonato conhecido. Conferidas em 09/08/2026: todas as dez
#: responderam com nome e jogos.
LIGAS_CONHECIDAS: dict[str, str] = {
    "brasileirao-serie-a": "bra.1",
    "eredivisie": "ned.1",
    "primeira-liga": "por.1",
    "premier-league": "eng.1",
    "laliga": "esp.1",
    "serie-a": "ita.1",
    "bundesliga": "ger.1",
    "ligue-1": "fra.1",
    "efl-league-one": "eng.3",
    "libertadores-mata-mata": "conmebol.libertadores",
    "libertadores-fase-de-grupos": "conmebol.libertadores",
}

#: ``state`` é o eixo estável da ESPN: "pre", "in", "post". O ``name`` detalha
#: (primeiro tempo, intervalo, prorrogação) e muda mais.
_DETALHE: dict[str, FixtureStatus] = {
    "STATUS_HALFTIME": FixtureStatus.HT,
    "STATUS_POSTPONED": FixtureStatus.POSTPONED,
    "STATUS_CANCELED": FixtureStatus.CANCELLED,
    "STATUS_CANCELLED": FixtureStatus.CANCELLED,
    "STATUS_ABANDONED": FixtureStatus.ABANDONED,
    "STATUS_SUSPENDED": FixtureStatus.SUSPENDED,
}


class EspnError(ProviderError):
    """A ESPN recusou a requisição ou mudou o formato."""


@dataclass(frozen=True, slots=True)
class PlacarAoVivo:
    """Um jogo como a ESPN o vê agora."""

    casa: str
    fora: str
    kickoff_at: datetime
    status: FixtureStatus
    home_ft: int | None
    away_ft: int | None
    minuto: int | None
    encerrado: bool


def _status_de(tipo: dict) -> FixtureStatus:
    """Traduz o estado da ESPN. ``state`` manda; ``name`` refina."""
    detalhado = _DETALHE.get(str(tipo.get("name") or "").upper())
    if detalhado is not None:
        return detalhado

    estado = str(tipo.get("state") or "").lower()
    if estado == "post":
        return FixtureStatus.FINISHED
    if estado == "in":
        return FixtureStatus.LIVE
    if estado == "pre":
        return FixtureStatus.SCHEDULED

    log.warning("espn.estado_desconhecido", estado=estado, nome=tipo.get("name"))
    return FixtureStatus.SCHEDULED


def _minuto(bruto: object) -> int | None:
    """``90'+4'`` → 90. Só o número inteiro interessa para a tela."""
    texto = str(bruto or "").strip()
    digitos = ""
    for caractere in texto:
        if caractere.isdigit():
            digitos += caractere
        elif digitos:
            break
    try:
        return int(digitos) if digitos else None
    except ValueError:
        return None


def _placar(bruto: object) -> int | None:
    try:
        return int(str(bruto))
    except (TypeError, ValueError):
        return None


class EspnScores:
    """Busca placar por liga e por dia. Não importa calendário."""

    slug = "espn"
    name = "ESPN (placar ao vivo)"

    def __init__(self, *, base_url: str = BASE_URL, client: httpx.AsyncClient | None = None):
        self._base_url = base_url.rstrip("/")
        self._client = client or httpx.AsyncClient(
            timeout=httpx.Timeout(20.0),
            follow_redirects=True,
            # Sem `User-Agent` de propósito: o endpoint devolve 403 para
            # cabeçalho de navegador e 200 sem cabeçalho nenhum.
            headers={"Accept": "application/json"},
        )

    async def aclose(self) -> None:
        await self._client.aclose()

    async def placares(self, liga: str, dia: date) -> list[PlacarAoVivo]:
        """Jogos daquela liga em torno daquele dia. Uma requisição.

        Pede a janela de três dias, não o dia exato, porque a ESPN agrupa pela
        **data local da liga** e o banco guarda UTC: Botafogo x Fluminense às
        00:00 UTC de 09/08 está na gaveta de 08/08, que é 21:00 em Brasília.
        Pedir o dia exato perdia esse jogo — e perderia todo jogo noturno da
        América do Sul.

        A janela custa a mesma requisição: o endpoint aceita intervalo. Quem
        separa os jogos é o casamento por confronto e horário, que não se
        confunde com um dia a mais de candidatos.
        """
        de = (dia - timedelta(days=1)).strftime("%Y%m%d")
        ate = (dia + timedelta(days=1)).strftime("%Y%m%d")

        url = f"{self._base_url}/{liga}/scoreboard"
        try:
            resposta = await self._client.get(url, params={"dates": f"{de}-{ate}"})
        except httpx.HTTPError as exc:
            raise EspnError(f"não consegui alcançar a ESPN em {liga}: {exc}") from exc

        if resposta.status_code == 404:
            raise EspnError(f"liga '{liga}' não existe na ESPN")
        if resposta.status_code == 403:
            raise EspnError(
                "a ESPN recusou a requisição (403). Ela bloqueia User-Agent de "
                "navegador — este coletor não manda nenhum de propósito."
            )
        if resposta.status_code >= 400:
            raise EspnError(f"{url} devolveu HTTP {resposta.status_code}")

        try:
            dados = resposta.json()
        except ValueError as exc:
            raise EspnError(f"{liga} não devolveu JSON. O formato da ESPN mudou.") from exc

        return [item for evento in dados.get("events") or [] if (item := self._traduzir(evento))]

    @staticmethod
    def _traduzir(evento: dict) -> PlacarAoVivo | None:
        competicoes = evento.get("competitions") or []
        if not competicoes:
            return None

        competicao = competicoes[0]
        lados = {item.get("homeAway"): item for item in competicao.get("competitors") or []}
        casa, fora = lados.get("home"), lados.get("away")
        if not casa or not fora:
            return None

        try:
            quando = datetime.fromisoformat(str(evento["date"]).replace("Z", "+00:00"))
        except (KeyError, ValueError):
            return None

        estado = (competicao.get("status") or {}).get("type") or {}
        return PlacarAoVivo(
            casa=str((casa.get("team") or {}).get("displayName") or ""),
            fora=str((fora.get("team") or {}).get("displayName") or ""),
            kickoff_at=quando.astimezone(UTC),
            status=_status_de(estado),
            home_ft=_placar(casa.get("score")),
            away_ft=_placar(fora.get("score")),
            minuto=_minuto((competicao.get("status") or {}).get("displayClock")),
            encerrado=bool(estado.get("completed")),
        )


# ---------------------------------------------------------------------------
# Calendário de copa
#
# A ressalva lá em cima — "esta fonte não importa calendário" — vale enquanto o
# campeonato já existe no banco por outra fonte: importar por aqui criaria uma
# competição paralela ao lado da que já está lá.
#
# A Copa do Brasil é o caso em que a ressalva não se aplica, porque não existe
# outra fonte. O fixturedownload não a publica. A Wikipédia monta o chaveamento
# com {{OneLegResult}} e {{TwoLegResult}}, que trazem o placar agregado e
# NENHUMA data — e sem horário não dá para travar palpite, então o coletor de
# mata-mata descarta o jogo. E o GE devolve lista vazia para fase eliminatória:
# as nove fases da edição existem como slug, mas nenhuma combinação de tabela e
# fase traz um jogo sequer.
#
# A ESPN traz data em UTC, mandante, placar, pênaltis e a fase — inclusive de
# jogo ainda não realizado, que é justamente o que um bolão precisa.


#: Fase da ESPN → como ela se chama aqui, e a ordem dentro da temporada.
#:
#: A ordem existe para a tela listar as rodadas sem precisar entender de
#: futebol. Os números têm folga entre si porque copa muda de formato: a Copa
#: do Brasil já teve mais e menos fases iniciais do que tem hoje.
FASES_DE_COPA: dict[str, tuple[str, int]] = {
    "first-round": ("Primeira fase", 10),
    "second-round": ("Segunda fase", 20),
    "third-round": ("Terceira fase", 30),
    "fourth-round": ("Quarta fase", 40),
    "fifth-round": ("Quinta fase", 50),
    "group-stage": ("Fase de grupos", 55),
    "league-phase": ("Fase de liga", 55),
    "round-of-32": ("Dezesseis avos", 60),
    "knockout-round-playoffs": ("Play-off", 65),
    "round-of-16": ("Oitavas de final", 70),
    "quarterfinals": ("Quartas de final", 80),
    "semifinals": ("Semifinal", 90),
    "final": ("Final", 100),
}


#: A mesma palavra quer dizer coisas diferentes em ligas diferentes.
#:
#: `first-round` na Copa do Brasil é a primeira fase do torneio; na
#: qualificação da Champions é a primeira ELIMINATÓRIA, que acontece antes de
#: tudo e não é o mesmo lugar na temporada. Sem esta separação, os 28 jogos de
#: julho entrariam misturados com a fase de liga de setembro.
FASES_POR_LIGA: dict[str, dict[str, tuple[str, int]]] = {
    "uefa.champions_qual": {
        "first-round": ("1ª eliminatória", 2),
        "second-round": ("2ª eliminatória", 4),
        "third-round": ("3ª eliminatória", 6),
        "playoff-round": ("Play-off de acesso", 8),
    },
    "uefa.europa_qual": {
        "first-round": ("1ª eliminatória", 2),
        "second-round": ("2ª eliminatória", 4),
        "third-round": ("3ª eliminatória", 6),
        "playoff-round": ("Play-off de acesso", 8),
    },
}


def _fase_de(slug: str | None, liga: str = "") -> ProviderRound | None:
    if not slug:
        return None

    especifica = FASES_POR_LIGA.get(liga, {})
    conhecida = especifica.get(slug.strip().lower()) or FASES_DE_COPA.get(slug.strip().lower())
    if conhecida is not None:
        nome, ordem = conhecida
        return ProviderRound(name=nome, number=ordem, is_knockout=ordem >= 60)

    # Formato novo não pode fazer jogo sumir. Jogo sem rodada entra no banco
    # com `round_id` nulo: existe, e não aparece em rodada nenhuma para montar
    # bolão. Nome cru na tela é feio e recuperável; invisível não é.
    log.warning("espn.fase_desconhecida", slug=slug)
    return ProviderRound(name=slug.replace("-", " ").capitalize(), is_knockout=True)


def _quando(bruto: object) -> datetime | None:
    """``2026-09-02T00:00Z`` — ISO, mas com o ``Z`` que o ``fromisoformat`` das
    versões mais antigas recusa. Normaliza e devolve sempre em UTC."""
    texto = str(bruto or "").strip()
    if not texto:
        return None
    try:
        momento = datetime.fromisoformat(texto.replace("Z", "+00:00"))
    except ValueError:
        return None
    return momento.astimezone(UTC) if momento.tzinfo else momento.replace(tzinfo=UTC)


class EspnCalendario:
    """Importa a temporada de uma copa a partir do placar público da ESPN.

    A API responde por janela de datas e **trunca janela longa**: pedir o ano
    inteiro de uma vez devolveu cem jogos que paravam em abril, enquanto as
    oitavas de agosto existiam e apareciam ao pedir só agosto. Por isso a coleta
    é mês a mês, juntando pelo id do evento — doze requisições, sem chave e sem
    cota.
    """

    slug = "espn"
    name = "ESPN"

    def __init__(self, *, base_url: str = BASE_URL, client: httpx.AsyncClient | None = None):
        self._base_url = base_url.rstrip("/")
        self._client = client or httpx.AsyncClient(
            timeout=httpx.Timeout(20.0),
            follow_redirects=True,
            # Sem `User-Agent` de propósito: o endpoint devolve 403 para
            # cabeçalho de navegador e 200 sem cabeçalho nenhum.
            headers={"Accept": "application/json"},
        )

    async def aclose(self) -> None:
        await self._client.aclose()

    async def _janela(self, liga: str, de: date, ate: date) -> list[dict]:
        url = f"{self._base_url}/{liga}/scoreboard"
        try:
            resposta = await self._client.get(
                url, params={"dates": f"{de:%Y%m%d}-{ate:%Y%m%d}", "limit": 1000}
            )
        except httpx.HTTPError as exc:
            raise EspnError(f"não consegui falar com a ESPN: {exc}") from exc

        if resposta.status_code == 400:
            raise EspnError(
                f"a ESPN não conhece a liga '{liga}'. Confira o código: o da "
                "Copa do Brasil é bra.copa_do_brazil, com z."
            )
        if resposta.status_code >= 400:
            raise EspnError(f"{url} devolveu HTTP {resposta.status_code}")

        try:
            dados = resposta.json()
        except ValueError as exc:
            raise EspnError(f"a ESPN devolveu algo que não é JSON: {exc}") from exc
        return list(dados.get("events") or [])

    @staticmethod
    def _meses(ano: int, virada: bool) -> list[tuple[date, date]]:
        """Os meses que a temporada cobre, um a um.

        Temporada de virada (Champions: agosto a maio) é referida pelo ano em
        que TERMINA — a mesma convenção do resto do projeto. A janela então vai
        de julho do ano anterior a junho deste, e são dezoito meses de folga
        para caber eliminatória em julho e final em maio.
        """
        if virada:
            primeiro = date(ano - 1, 7, 1)
            quantos = 12
        else:
            primeiro = date(ano, 1, 1)
            quantos = 12

        janelas: list[tuple[date, date]] = []
        for passo in range(quantos):
            mes = (primeiro.month - 1 + passo) % 12 + 1
            deste_ano = primeiro.year + (primeiro.month - 1 + passo) // 12
            inicio = date(deste_ano, mes, 1)
            fim = (
                date(deste_ano + 1, 1, 1) if mes == 12 else date(deste_ano, mes + 1, 1)
            ) - timedelta(days=1)
            # Bordas alargadas em três dias de cada lado.
            #
            # A ESPN agrupa por data LOCAL da liga, não UTC: um jogo à
            # meia-noite UTC do dia 1º está na gaveta do último dia do mês
            # anterior. Entre meses consecutivos isso não perderia nada, porque
            # as janelas se encostam — mas nas pontas perderia. A folga custa
            # zero: o `dict` por id descarta a repetição.
            janelas.append((inicio - timedelta(days=3), fim + timedelta(days=3)))
        return janelas

    async def import_season(
        self,
        ligas: str | Sequence[str],
        ano: int,
        *,
        virada: bool = False,
        identidade: str | None = None,
    ) -> ProviderSnapshot:
        """Junta uma temporada inteira, atravessando ligas e meses.

        ``ligas`` aceita mais de uma porque um torneio pode estar partido na
        ESPN: a Champions tem a qualificação em ``uefa.champions_qual`` e o
        resto em ``uefa.champions``. São a mesma competição para quem palpita,
        e separá-las criaria dois campeonatos com times diferentes na tela.

        ``identidade`` é o ``external_id`` da competição e dos jogos. Vem de
        fora de propósito: derivá-lo da primeira liga da lista faria reordenar
        a lista criar uma competição paralela no próximo import, com os mesmos
        jogos e nenhum aviso.
        """
        codigos = [ligas] if isinstance(ligas, str) else list(ligas)
        chave = identidade or codigos[0]
        eventos: dict[str, tuple[str, dict]] = {}

        for liga in codigos:
            for inicio, fim in self._meses(ano, virada):
                for evento in await self._janela(liga, inicio, fim):
                    identificador = str(evento.get("id") or "")
                    if identificador:
                        eventos[identificador] = (liga, evento)

        if not eventos:
            raise EspnError(
                f"a ESPN não tem nenhum jogo de {', '.join(codigos)} em {ano}. "
                "Se a temporada ainda não foi sorteada, não há o que importar."
            )

        times: dict[str, ProviderTeam] = {}
        jogos: list[ProviderFixture] = []

        for liga_do_evento, evento in eventos.values():
            competicao = (evento.get("competitions") or [{}])[0]
            competidores = competicao.get("competitors") or []
            casa = next((c for c in competidores if c.get("homeAway") == "home"), None)
            fora = next((c for c in competidores if c.get("homeAway") == "away"), None)
            if casa is None or fora is None:
                continue

            momento = _quando(evento.get("date"))
            if momento is None:
                # Sem horário não dá para travar o palpite na hora certa. Pular
                # é melhor do que inventar data: a próxima coleta traz o jogo.
                log.warning("espn.jogo_sem_data", evento=evento.get("id"))
                continue

            ids: dict[str, str] = {}
            for papel, lado in (("casa", casa), ("fora", fora)):
                time = lado.get("team") or {}
                nome = time.get("displayName") or ""
                identificador = str(time.get("id") or slugify(nome))
                if not identificador:
                    break
                ids[papel] = identificador
                times.setdefault(
                    identificador,
                    ProviderTeam(
                        external_id=identificador,
                        name=nome or identificador,
                        slug=slugify(nome or identificador),
                        short_name=time.get("shortDisplayName") or None,
                        crest_url=time.get("logo") or None,
                    ),
                )
            if len(ids) != 2:
                continue

            estado = (competicao.get("status") or {}).get("type") or {}
            jogos.append(
                ProviderFixture(
                    external_id=build_external_id(chave, str(ano), str(evento.get("id"))),
                    home_team_external_id=ids["casa"],
                    away_team_external_id=ids["fora"],
                    kickoff_at=momento,
                    status=_status_de(estado),
                    round=_fase_de((evento.get("season") or {}).get("slug"), liga_do_evento),
                    venue=(competicao.get("venue") or {}).get("fullName") or None,
                    home_ft=_placar(casa.get("score")),
                    away_ft=_placar(fora.get("score")),
                    # Pênalti de desempate NÃO conta para pontuação. Vai em
                    # campo próprio, nunca somado ao placar do tempo normal.
                    home_pen=_placar(casa.get("shootoutScore")),
                    away_pen=_placar(fora.get("shootoutScore")),
                )
            )

        jogos.sort(key=lambda jogo: jogo.kickoff_at)
        datas = [jogo.kickoff_at.date() for jogo in jogos]
        return ProviderSnapshot(
            competition=ProviderCompetition(external_id=chave, name=chave, slug=slugify(chave)),
            season=ProviderSeason(
                external_id=f"{chave}-{ano}",
                competition_external_id=chave,
                year=ano,
                start_date=min(datas) if datas else None,
                end_date=max(datas) if datas else None,
                is_current=True,
            ),
            teams=tuple(times.values()),
            fixtures=tuple(jogos),
        )
