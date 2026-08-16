"""API-Football (api-sports.io) — opcional, para quem tiver chave.

Não é o padrão da plataforma porque exige cadastro e o tier gratuito é de 100
requisições por dia, o que não sustenta placar ao vivo. Está aqui porque é o
único provedor acessível com estaduais e Copinha, e porque depender de um único
provedor é risco de continuidade, não preciosismo.
"""

from __future__ import annotations

import asyncio
import json
from collections.abc import Sequence
from datetime import UTC, date, datetime
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
    ProviderQuotaExceeded,
    ProviderRound,
    ProviderSeason,
    ProviderTeam,
)

log = get_logger(__name__)

BASE_URL = "https://v3.football.api-sports.io"

STATUS_MAP: dict[str, FixtureStatus] = {
    "TBD": FixtureStatus.SCHEDULED,
    "NS": FixtureStatus.SCHEDULED,
    "1H": FixtureStatus.LIVE,
    "HT": FixtureStatus.HT,
    "2H": FixtureStatus.LIVE,
    "ET": FixtureStatus.LIVE,
    "BT": FixtureStatus.LIVE,
    "P": FixtureStatus.LIVE,
    "SUSP": FixtureStatus.SUSPENDED,
    "INT": FixtureStatus.SUSPENDED,
    "FT": FixtureStatus.FINISHED,
    "AET": FixtureStatus.FINISHED,
    "PEN": FixtureStatus.FINISHED,
    "PST": FixtureStatus.POSTPONED,
    "CANC": FixtureStatus.CANCELLED,
    "ABD": FixtureStatus.ABANDONED,
    "AWD": FixtureStatus.FINISHED,
    "WO": FixtureStatus.FINISHED,
}

TYPE_MAP = {"League": CompetitionType.LEAGUE, "Cup": CompetitionType.CUP}


class ApiFootballProvider(FootballDataProvider):
    slug = "api_football"
    name = "API-Football"

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
            raise ProviderError("API_FOOTBALL_KEY não configurada")
        self._base_url = base_url.rstrip("/")
        self._cache = cache
        self._min_interval = 60.0 / max(requests_per_minute, 1)
        self._last_request_at = 0.0
        self._lock = asyncio.Lock()
        self._client = client or httpx.AsyncClient(
            timeout=httpx.Timeout(20.0),
            headers={"x-apisports-key": api_key},
        )

    async def _throttle(self) -> None:
        async with self._lock:
            loop = asyncio.get_running_loop()
            elapsed = loop.time() - self._last_request_at
            if elapsed < self._min_interval:
                await asyncio.sleep(self._min_interval - elapsed)
            self._last_request_at = loop.time()

    async def _get(self, path: str, params: dict[str, Any], *, cache_ttl: int = 3600) -> Any:
        cache_key = f"apifootball:{path}:{sorted(params.items())}"
        if self._cache is not None:
            cached = await self._cache.get(cache_key)
            if cached is not None:
                return json.loads(cached)

        await self._throttle()

        for attempt in range(4):
            try:
                response = await self._client.get(f"{self._base_url}{path}", params=params)
            except httpx.HTTPError as exc:
                if attempt == 3:
                    raise ProviderError(f"falha de rede em {path}: {exc}") from exc
                await asyncio.sleep(2**attempt)
                continue

            if response.status_code == 429:
                raise ProviderQuotaExceeded("cota diária do API-Football estourada")
            if response.status_code >= 500:
                if attempt == 3:
                    raise ProviderError(f"{path} devolveu {response.status_code}")
                await asyncio.sleep(2**attempt)
                continue
            if response.status_code >= 400:
                raise ProviderError(f"{path} devolveu {response.status_code}")

            payload = response.json()
            # A API devolve 200 com o erro no corpo — o status HTTP não basta.
            errors = payload.get("errors")
            if errors and not isinstance(errors, list):
                raise ProviderError(f"API-Football recusou {path}: {errors}")

            items = payload.get("response", [])
            if self._cache is not None:
                await self._cache.set(cache_key, json.dumps(items), ex=cache_ttl)
            return items

        raise ProviderError(f"{path} falhou após 4 tentativas")

    async def aclose(self) -> None:
        await self._client.aclose()

    async def list_competitions(self) -> Sequence[ProviderCompetition]:
        items = await self._get("/leagues", {"country": "Brazil"}, cache_ttl=86_400)
        result = []
        for item in items:
            league = item.get("league") or {}
            country = item.get("country") or {}
            result.append(
                ProviderCompetition(
                    external_id=str(league.get("id")),
                    slug=slugify(str(league.get("name"))),
                    name=str(league.get("name")),
                    country=country.get("name"),
                    type=TYPE_MAP.get(str(league.get("type")), CompetitionType.LEAGUE),
                    logo_url=league.get("logo"),
                )
            )
        return tuple(result)

    async def list_seasons(self, competition_external_id: str) -> Sequence[ProviderSeason]:
        items = await self._get("/leagues", {"id": competition_external_id}, cache_ttl=86_400)
        if not items:
            return ()
        seasons = items[0].get("seasons", [])
        return tuple(
            ProviderSeason(
                external_id=str(season.get("year")),
                competition_external_id=competition_external_id,
                year=int(season["year"]),
                start_date=_parse_date(season.get("start")),
                end_date=_parse_date(season.get("end")),
                is_current=bool(season.get("current")),
            )
            for season in seasons
        )

    async def list_teams(self, competition_external_id: str, year: int) -> Sequence[ProviderTeam]:
        items = await self._get(
            "/teams", {"league": competition_external_id, "season": year}, cache_ttl=86_400
        )
        return tuple(
            ProviderTeam(
                external_id=str((item.get("team") or {}).get("id")),
                name=str((item.get("team") or {}).get("name")),
                slug=slugify(str((item.get("team") or {}).get("name"))),
                short_name=(item.get("team") or {}).get("code"),
                crest_url=(item.get("team") or {}).get("logo"),
                country=(item.get("team") or {}).get("country"),
            )
            for item in items
        )

    async def list_fixtures(
        self, competition_external_id: str, year: int
    ) -> Sequence[ProviderFixture]:
        items = await self._get(
            "/fixtures", {"league": competition_external_id, "season": year}, cache_ttl=900
        )
        return tuple(self._to_fixture(item) for item in items)

    async def get_fixture(self, external_id: str) -> ProviderFixture | None:
        items = await self._get("/fixtures", {"id": external_id}, cache_ttl=60)
        return self._to_fixture(items[0]) if items else None

    def _to_fixture(self, item: dict[str, Any]) -> ProviderFixture:
        fixture = item.get("fixture") or {}
        teams = item.get("teams") or {}
        goals = item.get("goals") or {}
        score = item.get("score") or {}
        league = item.get("league") or {}

        raw_status = str((fixture.get("status") or {}).get("short", ""))
        status = STATUS_MAP.get(raw_status)
        if status is None:
            log.warning("provider.status_desconhecido", provider=self.slug, status=raw_status)
            status = FixtureStatus.SCHEDULED

        extra = score.get("extratime") or {}
        penalty = score.get("penalty") or {}
        full = score.get("fulltime") or {}

        round_name = str(league.get("round") or "Fase única")
        number = _round_number(round_name)

        return ProviderFixture(
            external_id=str(fixture.get("id")),
            home_team_external_id=str((teams.get("home") or {}).get("id")),
            away_team_external_id=str((teams.get("away") or {}).get("id")),
            kickoff_at=_parse_datetime(str(fixture.get("date"))),
            status=status,
            round=ProviderRound(
                name=round_name,
                number=number,
                stage_name=None if number else round_name,
                is_knockout=number is None,
            ),
            minute=(fixture.get("status") or {}).get("elapsed"),
            venue=(fixture.get("venue") or {}).get("name"),
            home_ft=full.get("home") if full.get("home") is not None else goals.get("home"),
            away_ft=full.get("away") if full.get("away") is not None else goals.get("away"),
            home_et=extra.get("home"),
            away_et=extra.get("away"),
            home_pen=penalty.get("home"),
            away_pen=penalty.get("away"),
        )


def _round_number(round_name: str) -> int | None:
    """ "Regular Season - 12" → 12. Mata-mata não tem número."""
    tail = round_name.rsplit("-", 1)[-1].strip()
    return int(tail) if tail.isdigit() else None


def _parse_datetime(value: str) -> datetime:
    parsed = datetime.fromisoformat(value.replace("Z", "+00:00"))
    return parsed if parsed.tzinfo else parsed.replace(tzinfo=UTC)


def _parse_date(value: Any) -> date | None:
    return None if not value else date.fromisoformat(str(value)[:10])
