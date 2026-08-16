"""Health checks — o critério de pronto da Fase 0."""

from __future__ import annotations

import pytest
from httpx import AsyncClient


async def test_live_nao_depende_de_infraestrutura(client: AsyncClient) -> None:
    response = await client.get("/health/live")

    assert response.status_code == 200
    body = response.json()
    assert body["status"] == "ok"
    assert body["environment"] == "test"
    assert body["version"]


@pytest.mark.integration
async def test_ready_reporta_postgres_e_redis(client: AsyncClient) -> None:
    response = await client.get("/health/ready")

    assert response.status_code == 200, response.text
    body = response.json()
    assert body["status"] == "ok"
    assert body["checks"] == {"postgres": "ok", "redis": "ok"}


async def test_openapi_disponivel(client: AsyncClient) -> None:
    response = await client.get("/openapi.json")

    assert response.status_code == 200
    assert "/health/live" in response.json()["paths"]
