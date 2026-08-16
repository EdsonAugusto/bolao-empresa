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

from dataclasses import dataclass
from datetime import UTC, date, datetime, timedelta

import httpx

from app.core.logging import get_logger
from app.models.enums import FixtureStatus
from app.providers.base import ProviderError

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
