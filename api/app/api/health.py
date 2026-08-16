"""Health checks.

``live``  → o processo está de pé. Usado pelo orquestrador para reiniciar.
``ready`` → as dependências respondem. Usado pelo load balancer para rotear.

A distinção importa: um Postgres fora do ar não deve fazer o container ser
reiniciado em loop, só deve tirá-lo do balanceamento.
"""

from __future__ import annotations

from typing import Literal

from fastapi import APIRouter, Response, status
from pydantic import BaseModel
from sqlalchemy import text

from app.core.config import settings
from app.core.db import SessionLocal
from app.core.redis import get_redis

router = APIRouter(tags=["health"])


class LiveResponse(BaseModel):
    status: Literal["ok"]
    environment: str
    version: str


class ReadyResponse(BaseModel):
    status: Literal["ok", "degraded"]
    checks: dict[str, str]


@router.get("/health/live", response_model=LiveResponse)
async def live() -> LiveResponse:
    return LiveResponse(status="ok", environment=settings.environment, version="0.1.0")


@router.get("/health/ready", response_model=ReadyResponse)
async def ready(response: Response) -> ReadyResponse:
    checks: dict[str, str] = {}

    try:
        async with SessionLocal() as session:
            await session.execute(text("SELECT 1"))
        checks["postgres"] = "ok"
    except Exception as exc:  # noqa: BLE001 - queremos reportar, não engolir
        checks["postgres"] = f"error: {type(exc).__name__}"

    try:
        await get_redis().ping()
        checks["redis"] = "ok"
    except Exception as exc:  # noqa: BLE001
        checks["redis"] = f"error: {type(exc).__name__}"

    healthy = all(value == "ok" for value in checks.values())
    if not healthy:
        response.status_code = status.HTTP_503_SERVICE_UNAVAILABLE

    return ReadyResponse(status="ok" if healthy else "degraded", checks=checks)
