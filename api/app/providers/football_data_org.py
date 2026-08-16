"""football-data.org — tier gratuito.

Por que este e não o API-Football como padrão de rede: o tier gratuito do
football-data.org cobre o Brasileirão Série A sem cartão de crédito, com 10
requisições por minuto. Não tem estaduais nem Copinha, mas para um bolão entre
amigos do Brasileirão resolve, e custa zero.

Limites respeitados aqui:

- 10 req/min: um semáforo com espaçamento fixo entre chamadas.
- Resposta cacheada no Redis. Calendário muda pouco; placar ao vivo tem TTL
  curto. Sem cache, um bolão com 12 jogos simultâneos estoura a cota numa
  rodada só.
"""

from __future__ import annotations

import asyncio
from collections.abc import Sequence
from datetime import UTC, date, datetime
from typing import Any

import httpx
from slugify import slugify

from app.core.logging import get_logger
from app.core.names import chave
from app.models.enums import CompetitionType, FixtureStatus
from app.providers.base import (
    FootballDataProvider,
    ProviderCompetition,
    ProviderError,
    ProviderFixture,
    ProviderQuotaExceeded,
    ProviderRound,
    ProviderSeason,
    ProviderTeam,
)

log = get_logger(__name__)

BASE_URL = "https://api.football-data.org/v4"

#: Tradução do status do provedor para o enum interno. O status cru nunca entra
#: no banco — se aparecer um valor novo, cai no fallback e fica visível no log.
STATUS_MAP: dict[str, FixtureStatus] = {
    "SCHEDULED": FixtureStatus.SCHEDULED,
    "TIMED": FixtureStatus.SCHEDULED,
    "IN_PLAY": FixtureStatus.LIVE,
    "PAUSED": FixtureStatus.HT,
    "FINISHED": FixtureStatus.FINISHED,
    "POSTPONED": FixtureStatus.POSTPONED,
    "SUSPENDED": FixtureStatus.SUSPENDED,
    "CANCELLED": FixtureStatus.CANCELLED,
    "CANCELED": FixtureStatus.CANCELLED,
    "AWARDED": FixtureStatus.FINISHED,
}

COMPETITION_TYPE_MAP: dict[str, CompetitionType] = {
    "LEAGUE": CompetitionType.LEAGUE,
    "CUP": CompetitionType.CUP,
    "PLAYOFFS": CompetitionType.GROUP_KNOCKOUT,
}

#: Código da competição no football-data.org, por competição nossa. Todas estas
#: estão no plano gratuito — o que **não** está é a Série A brasileira, que
#: continua vindo do GE.
#:
#: Serve para buscar escudo: uma requisição por liga devolve os 18 ou 20 clubes
#: com escudo, contra uma requisição por clube nas fontes sem cadastro.
_CODIGOS_GRATUITOS: dict[str, str] = {
    "Premier League": "PL",
    "LaLiga": "PD",
    "Serie A": "SA",
    "Bundesliga": "BL1",
    "Ligue 1": "FL1",
    "Eredivisie": "DED",
    "Primeira Liga": "PPL",
    "Championship": "ELC",
}

CODIGOS_GRATUITOS: dict[str, str] = {
    chave(nome): codigo for nome, codigo in _CODIGOS_GRATUITOS.items()
}


class FootballDataOrgProvider(FootballDataProvider):
    slug = "football_data_org"
    name = "football-data.org"

    def __init__(
        self,
        api_key: str,
        *,
        base_url: str = BASE_URL,
        requests_per_minute: int = 10,
        cache: Any | None = None,
        client: httpx.AsyncClient | None = None,
    ) -> None:
        if not api_key:
            raise ProviderError("football-data.org exige uma chave; cadastre-se de graça no site")
        self._api_key = api_key
        self._base_url = base_url.rstrip("/")
        self._cache = cache
        self._min_interval = 60.0 / max(requests_per_minute, 1)
        self._last_request_at = 0.0
        self._lock = asyncio.Lock()
        self._client = client or httpx.AsyncClient(
            timeout=httpx.Timeout(20.0),
            headers={"X-Auth-Token": api_key},
        )

    # -- infraestrutura ----------------------------------------------------

    async def _throttle(self) -> None:
        """Espaça as chamadas para caber na cota, sem estourar e sem 429."""
        async with self._lock:
            loop = asyncio.get_running_loop()
            elapsed = loop.time() - self._last_request_at
            if elapsed < self._min_interval:
                await asyncio.sleep(self._min_interval - elapsed)
            self._last_request_at = loop.time()

    async def _get(self, path: str, *, cache_ttl: int = 3600) -> dict[str, Any]:
        cache_key = f"fdorg:{path}"
        if self._cache is not None:
            cached = await self._cache.get(cache_key)
            if cached is not None:
                import json

                return dict(json.loads(cached))

        await self._throttle()

        for attempt in range(4):
            try:
                response = await self._client.get(f"{self._base_url}{path}")
            except httpx.HTTPError as exc:
                if attempt == 3:
                    raise ProviderError(f"falha de rede em {path}: {exc}") from exc
                await asyncio.sleep(2**attempt)
                continue

            if response.status_code == 429:
                raise ProviderQuotaExceeded(
                    "cota do football-data.org estourada; o job vai recuar e tentar depois"
                )
            if response.status_code == 403:
                raise ProviderError(
                    f"{path} não está incluído no plano gratuito do football-data.org"
                )
            if response.status_code >= 500:
                if attempt == 3:
                    raise ProviderError(f"{path} devolveu {response.status_code}")
                await asyncio.sleep(2**attempt)
                continue
            if response.status_code >= 400:
                raise ProviderError(
                    f"{path} devolveu {response.status_code}: {response.text[:200]}"
                )

            payload: dict[str, Any] = response.json()
            if self._cache is not None:
                import json

                await self._cache.set(cache_key, json.dumps(payload), ex=cache_ttl)
            return payload

        raise ProviderError(f"{path} falhou após 4 tentativas")

    async def aclose(self) -> None:
        await self._client.aclose()

    # -- contrato ----------------------------------------------------------

    async def list_competitions(self) -> Sequence[ProviderCompetition]:
        payload = await self._get("/competitions", cache_ttl=86_400)
        return tuple(
            ProviderCompetition(
                external_id=str(item["code"] or item["id"]),
                slug=slugify(str(item["name"])),
                name=str(item["name"]),
                country=(item.get("area") or {}).get("name"),
                type=COMPETITION_TYPE_MAP.get(str(item.get("type")), CompetitionType.LEAGUE),
                logo_url=item.get("emblem"),
            )
            for item in payload.get("competitions", [])
        )

    async def list_seasons(self, competition_external_id: str) -> Sequence[ProviderSeason]:
        payload = await self._get(f"/competitions/{competition_external_id}", cache_ttl=86_400)
        return tuple(
            ProviderSeason(
                external_id=str(item.get("id")),
                competition_external_id=competition_external_id,
                year=int(str(item["startDate"])[:4]),
                start_date=_parse_date(item.get("startDate")),
                end_date=_parse_date(item.get("endDate")),
                is_current=item.get("id") == (payload.get("currentSeason") or {}).get("id"),
            )
            for item in payload.get("seasons", [])
        )

    async def list_teams(self, competition_external_id: str, year: int) -> Sequence[ProviderTeam]:
        payload = await self._get(
            f"/competitions/{competition_external_id}/teams?season={year}", cache_ttl=86_400
        )
        return tuple(
            ProviderTeam(
                external_id=str(item["id"]),
                name=str(item["name"]),
                slug=slugify(str(item.get("shortName") or item["name"])),
                short_name=item.get("tla") or item.get("shortName"),
                crest_url=item.get("crest"),
                country=(item.get("area") or {}).get("name"),
            )
            for item in payload.get("teams", [])
        )

    async def list_fixtures(
        self, competition_external_id: str, year: int
    ) -> Sequence[ProviderFixture]:
        payload = await self._get(
            f"/competitions/{competition_external_id}/matches?season={year}", cache_ttl=900
        )
        return tuple(self._to_fixture(item) for item in payload.get("matches", []))

    async def escudos_da_competicao(self, codigo: str) -> dict[str, str]:
        """Escudo de cada clube da competição, indexado por nome comparável.

        Uma requisição devolve a liga inteira. É o que torna este caminho
        instantâneo comparado a buscar clube por clube — e por isso vale o
        cadastro gratuito que ele exige.
        """
        payload = await self._get(f"/competitions/{codigo}/teams", cache_ttl=86_400)
        indice: dict[str, str] = {}
        for item in payload.get("teams", []):
            escudo = item.get("crest")
            if not escudo:
                continue
            for nome in (item.get("name"), item.get("shortName"), item.get("tla")):
                if nome:
                    indice.setdefault(chave(str(nome)), str(escudo))
        return indice

    async def get_fixture(self, external_id: str) -> ProviderFixture | None:
        try:
            payload = await self._get(f"/matches/{external_id}", cache_ttl=60)
        except ProviderError:
            return None
        return self._to_fixture(payload.get("match", payload))

    # -- tradução ----------------------------------------------------------

    def _to_fixture(self, item: dict[str, Any]) -> ProviderFixture:
        raw_status = str(item.get("status", ""))
        status = STATUS_MAP.get(raw_status)
        if status is None:
            log.warning("provider.status_desconhecido", provider=self.slug, status=raw_status)
            status = FixtureStatus.SCHEDULED

        score = item.get("score") or {}
        full_time = score.get("fullTime") or {}
        extra_time = score.get("extraTime") or {}
        penalties = score.get("penalties") or {}

        matchday = item.get("matchday")
        stage = item.get("stage")
        is_knockout = bool(stage) and "GROUP" not in str(stage) and str(stage) != "REGULAR_SEASON"

        return ProviderFixture(
            external_id=str(item["id"]),
            home_team_external_id=str((item.get("homeTeam") or {}).get("id")),
            away_team_external_id=str((item.get("awayTeam") or {}).get("id")),
            kickoff_at=_parse_datetime(str(item["utcDate"])),
            status=status,
            round=ProviderRound(
                name=f"Rodada {matchday}" if matchday else str(stage or "Fase única"),
                number=int(matchday) if matchday else None,
                stage_name=_humanize_stage(stage),
                is_knockout=is_knockout,
            ),
            minute=item.get("minute"),
            venue=item.get("venue"),
            home_ft=full_time.get("home"),
            away_ft=full_time.get("away"),
            home_et=extra_time.get("home"),
            away_et=extra_time.get("away"),
            home_pen=penalties.get("home"),
            away_pen=penalties.get("away"),
            provider_updated_at=_parse_optional_datetime(item.get("lastUpdated")),
        )


STAGE_NAMES = {
    "REGULAR_SEASON": "Fase regular",
    "GROUP_STAGE": "Fase de grupos",
    "LAST_16": "Oitavas de final",
    "QUARTER_FINALS": "Quartas de final",
    "SEMI_FINALS": "Semifinal",
    "FINAL": "Final",
    "THIRD_PLACE": "Disputa de terceiro lugar",
}


def _humanize_stage(stage: Any) -> str | None:
    if not stage:
        return None
    return STAGE_NAMES.get(str(stage), str(stage).replace("_", " ").capitalize())


def _parse_datetime(value: str) -> datetime:
    parsed = datetime.fromisoformat(value.replace("Z", "+00:00"))
    return parsed if parsed.tzinfo else parsed.replace(tzinfo=UTC)


def _parse_optional_datetime(value: Any) -> datetime | None:
    return None if not value else _parse_datetime(str(value))


def _parse_date(value: Any) -> date | None:
    return None if not value else date.fromisoformat(str(value)[:10])
