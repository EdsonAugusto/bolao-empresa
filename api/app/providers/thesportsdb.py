"""Busca de escudo no TheSportsDB.

É a fonte de escudo que **não exige cadastro**: a chave de teste ``3`` é
pública e documentada por eles. Em troca, ela é limitada com rigor — meia
dúzia de buscas seguidas já devolve HTTP 429 por um bom tempo.

Por isso este módulo:

1. **Espaça as requisições** por padrão, em vez de correr e ser bloqueado.
2. **Recua no 429** com espera crescente, e desiste declarando o motivo — não
   fica insistindo contra um bloqueio.
3. **Só aceita resultado da liga esperada.** Buscar "Inter" devolve um clube da
   quarta divisão espanhola antes da Internazionale; buscar "Man Utd" devolve um
   time da copa da Finlândia. Escudo trocado é pior do que escudo ausente.

Com uma chave de football-data.org (gratuita, mas exige cadastro) dá para pegar
a liga inteira em uma requisição. Este caminho existe para quem não quer se
cadastrar em lugar nenhum.
"""

from __future__ import annotations

import asyncio

import httpx

from app.core.logging import get_logger
from app.core.names import chave
from app.providers.base import ProviderError

log = get_logger(__name__)

BASE_URL = "https://www.thesportsdb.com/api/v1/json"

#: Chave pública de teste. Não é segredo e não identifica ninguém.
CHAVE_PUBLICA = "3"

#: Nome da liga no TheSportsDB, por competição nossa. Sem isso não dá para
#: descartar o homônimo, e é o descarte que impede escudo trocado.
#:
#: As chaves passam por :func:`chave` na montagem, então podem ser escritas do
#: jeito natural aqui — sem isso, entrada como "Sporting CP" viraria chave
#: "sporting" e nunca casaria com o que está escrito.
_LIGAS: dict[str, str] = {
    "premier league": "English Premier League",
    "laliga": "Spanish La Liga",
    "la liga": "Spanish La Liga",
    "serie a": "Italian Serie A",
    "bundesliga": "German Bundesliga",
    "ligue 1": "French Ligue 1",
    "ligue 2": "French Ligue 2",
    "eredivisie": "Dutch Eredivisie",
    "primeira liga": "Portuguese Primeira Liga",
    "championship": "English League Championship",
    "efl league one": "English League 1",
    "super lig": "Turkish Super Lig",
    "scottish premiership": "Scottish Premier League",
    "mls": "American Major League Soccer",
    "brasileirao serie a": "Brazilian Serie A",
    "libertadores mata mata": "Conmebol Libertadores",
    "libertadores fase de grupos": "Conmebol Libertadores",
}

#: Abreviações que a fonte de calendário usa e o TheSportsDB não conhece.
_APELIDOS: dict[str, str] = {
    "man utd": "Manchester United",
    "man city": "Manchester City",
    "nott m forest": "Nottingham Forest",
    "spurs": "Tottenham Hotspur",
    "wolves": "Wolverhampton Wanderers",
    "sheffield weds": "Sheffield Wednesday",
    "west brom": "West Bromwich Albion",
    "qpr": "Queens Park Rangers",
    "brighton": "Brighton and Hove Albion",
    "newcastle": "Newcastle United",
    "leeds": "Leeds United",
    "hull": "Hull City",
    "ipswich": "Ipswich Town",
    "coventry": "Coventry City",
    "leicester": "Leicester City",
    "norwich": "Norwich City",
    "stoke": "Stoke City",
    "cardiff": "Cardiff City",
    "swansea": "Swansea City",
    "birmingham": "Birmingham City",
    "blackburn": "Blackburn Rovers",
    "preston": "Preston North End",
    "bristol city": "Bristol City",
    "inter": "Internazionale",
    "milan": "AC Milan",
    "roma": "AS Roma",
    "lazio": "SS Lazio",
    "napoli": "SSC Napoli",
    "celta": "Celta Vigo",
    "athletic club": "Athletic Bilbao",
    "atletico madrid": "Atletico Madrid",
    "r racing club": "Racing Santander",
    "psv": "PSV Eindhoven",
    "az": "AZ Alkmaar",
    "academico": "Academico de Viseu",
    "maritimo m": "Maritimo",
    "sporting cp": "Sporting Lisbon",
    "estac troyes": "Troyes",
    "havre athletic club": "Le Havre",
    "le mans": "Le Mans FC",
    "ldu": "LDU Quito",
    # Estes doze sobraram na primeira passada real: o nome do calendário não
    # bate com o do catálogo de escudos, e sem o apelido a busca não acha nada.
    "FC Schalke 04": "Schalke 04",
    "Hamburger SV": "Hamburg",
    "Sport-Club Freiburg": "Freiburg",
    "Excelsior Rotterdam": "Excelsior",
    "FC Groningen": "Groningen",
    "FC Barcelona": "Barcelona",
    "RCD Espanyol de Barcelona": "Espanyol",
    "Olympique de Marseille": "Marseille",
    "CD Nacional": "Nacional Madeira",
    "FC Porto": "Porto",
}

#: Clubes que o TheSportsDB simplesmente não indexa de forma buscável. Ficam
#: registrados para ninguém perder tempo procurando de novo — eles usam o
#: escudo desenhado pela plataforma.
SEM_FONTE = frozenset({chave("Nottingham Forest"), chave("RC Deportivo")})

LIGAS: dict[str, str] = {chave(nome): valor for nome, valor in _LIGAS.items()}
APELIDOS: dict[str, str] = {chave(nome): valor for nome, valor in _APELIDOS.items()}


class TheSportsDbError(ProviderError):
    """Bloqueio ou mudança de formato na fonte de escudos."""


class TheSportsDbCrests:
    """Resolve nome de clube em URL de escudo."""

    def __init__(
        self,
        *,
        api_key: str = CHAVE_PUBLICA,
        base_url: str = BASE_URL,
        delay_seconds: float = 6.0,
        max_tentativas: int = 3,
        client: httpx.AsyncClient | None = None,
    ) -> None:
        self._url = f"{base_url.rstrip('/')}/{api_key}/searchteams.php"
        # Seis segundos entre buscas. É lento de propósito: a chave pública é
        # limitada e correr faz o bloqueio durar mais do que a coleta inteira.
        self._delay = delay_seconds
        self._max_tentativas = max_tentativas
        self._ultimo = 0.0
        self._bloqueado = False
        self._client = client or httpx.AsyncClient(
            timeout=httpx.Timeout(30.0),
            follow_redirects=True,
            headers={"User-Agent": "bolao-platform/0.1 (uso pessoal, rede local)"},
        )

    async def aclose(self) -> None:
        await self._client.aclose()

    async def _esperar(self) -> None:
        agora = asyncio.get_running_loop().time()
        restante = self._delay - (agora - self._ultimo)
        if restante > 0:
            await asyncio.sleep(restante)
        self._ultimo = asyncio.get_running_loop().time()

    async def _buscar(self, termo: str) -> list[dict]:
        espera = self._delay
        for tentativa in range(1, self._max_tentativas + 1):
            await self._esperar()
            try:
                resposta = await self._client.get(self._url, params={"t": termo})
            except httpx.HTTPError as exc:
                raise TheSportsDbError(f"não consegui alcançar o TheSportsDB: {exc}") from exc

            if resposta.status_code == 429:
                if tentativa == self._max_tentativas:
                    self._bloqueado = True
                    raise TheSportsDbError(
                        "o TheSportsDB bloqueou a busca (HTTP 429). A chave pública "
                        "é bem limitada — tente de novo daqui a alguns minutos, ou "
                        "configure FOOTBALL_DATA_ORG_KEY para pegar a liga inteira "
                        "de uma vez."
                    )
                espera *= 2
                log.warning("thesportsdb.429", termo=termo, espera=espera)
                await asyncio.sleep(espera)
                continue

            if resposta.status_code != 200:
                return []

            try:
                dados = resposta.json()
            except ValueError:
                return []
            times = dados.get("teams") if isinstance(dados, dict) else None
            return list(times) if isinstance(times, list) else []

        return []

    @staticmethod
    def _escolher(times: list[dict], liga: str | None, nome: str) -> str | None:
        """Escolhe o clube certo, ou nenhum.

        Sem liga conhecida, só aceita nome idêntico depois de normalizado. Com
        liga, aceita nome idêntico ou nome alternativo dentro dela — nunca "o
        primeiro da lista", que é como se coloca o escudo de um time da quarta
        divisão espanhola no lugar da Internazionale.
        """
        candidatos = [
            item for item in times if item.get("strSport") == "Soccer" and item.get("strBadge")
        ]
        alvo = chave(nome)

        na_liga = (
            [item for item in candidatos if (item.get("strLeague") or "") == liga] if liga else []
        )

        for grupo in (na_liga, candidatos):
            for item in grupo:
                if chave(str(item.get("strTeam") or "")) == alvo:
                    return str(item["strBadge"])
            for item in grupo:
                alternativos = str(item.get("strAlternate") or "").split(",")
                if any(chave(alt) == alvo for alt in alternativos if alt.strip()):
                    return str(item["strBadge"])

        # Um único clube da liga esperada é resposta boa: o filtro por liga já
        # descartou os homônimos.
        if len(na_liga) == 1:
            return str(na_liga[0]["strBadge"])
        return None

    async def escudo(self, nome: str, competicao: str) -> str | None:
        if self._bloqueado or chave(nome) in SEM_FONTE:
            return None

        liga = LIGAS.get(chave(competicao))
        termos = [nome]
        apelido = APELIDOS.get(chave(nome))
        if apelido:
            termos.insert(0, apelido)

        for termo in termos:
            achado = self._escolher(await self._buscar(termo), liga, nome)
            if achado:
                return achado
        return None
