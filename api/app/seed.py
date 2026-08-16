"""Seed de dados base.

Contrato: **idempotente**. Rodar `seed` dez vezes deixa o banco no mesmo
estado que rodar uma vez. Cada fase registra seus seeders em ``SEEDERS``.
"""

from __future__ import annotations

from collections.abc import Awaitable, Callable

from sqlalchemy.ext.asyncio import AsyncSession

from app.core.db import SessionLocal
from app.core.logging import get_logger

log = get_logger(__name__)

Seeder = Callable[[AsyncSession], Awaitable[int]]

# (nome, função). A função devolve quantos registros criou/atualizou.
# Fase 2 acrescenta seed_providers; Fase 3, os presets de pontuação.
SEEDERS: list[tuple[str, Seeder]] = []


async def run_seeds() -> dict[str, int]:
    results: dict[str, int] = {}
    async with SessionLocal() as session:
        for name, seeder in SEEDERS:
            count = await seeder(session)
            await session.commit()
            results[name] = count
            log.info("seed.done", seeder=name, records=count)
    if not SEEDERS:
        log.info("seed.empty", detail="nenhum seeder registrado nesta fase")
    return results
