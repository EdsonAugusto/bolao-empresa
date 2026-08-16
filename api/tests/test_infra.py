"""Fundação: migrations aplicadas, extensões instaladas, seed idempotente."""

from __future__ import annotations

import pytest
from sqlalchemy import text
from sqlalchemy.ext.asyncio import AsyncSession

pytestmark = pytest.mark.integration


async def test_migrations_chegaram_ate_head(db_session: AsyncSession) -> None:
    """O banco de teste está na última revisão.

    Comparamos com o head que o Alembic conhece, não com um número fixo: uma
    migration nova não deve exigir editar este teste.
    """
    from pathlib import Path

    from alembic.config import Config
    from alembic.script import ScriptDirectory

    api_root = Path(__file__).resolve().parents[1]
    config = Config(str(api_root / "alembic.ini"))
    config.set_main_option("script_location", str(api_root / "alembic"))
    expected = ScriptDirectory.from_config(config).get_current_head()

    revision = (
        await db_session.execute(text("SELECT version_num FROM alembic_version"))
    ).scalar_one()

    assert revision == expected


@pytest.mark.parametrize("extension", ["pgcrypto", "citext", "pg_trgm", "unaccent"])
async def test_extensao_instalada(db_session: AsyncSession, extension: str) -> None:
    found = (
        await db_session.execute(
            text("SELECT 1 FROM pg_extension WHERE extname = :name"), {"name": extension}
        )
    ).scalar_one_or_none()

    assert found == 1, f"extensão {extension} ausente — rode as migrations"


async def test_banco_de_teste_esta_em_utc(db_session: AsyncSession) -> None:
    """Se o servidor não estiver em UTC, todo cálculo de lock de palpite mente."""
    timezone = (await db_session.execute(text("SHOW timezone"))).scalar_one()

    assert timezone in {"UTC", "Etc/UTC"}


async def test_seed_e_idempotente() -> None:
    from app.seed import run_seeds

    primeira = await run_seeds()
    segunda = await run_seeds()

    assert primeira == segunda
