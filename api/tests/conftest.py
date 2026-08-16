"""Fixtures de teste.

O banco de teste é **separado** do de desenvolvimento (sufixo ``_test``) e é
criado e migrado uma vez por sessão. Cada teste roda dentro de uma transação
que sofre rollback no final — testes não enxergam a sujeira uns dos outros.

As migrations rodam de verdade (``alembic upgrade head``), não
``metadata.create_all``: assim um erro de migration quebra o teste, que é
exatamente o que queremos.
"""

from __future__ import annotations

import os

# --- ANTES de qualquer import de app: aponta a configuração para o banco de
# --- teste. app.core.config lê o ambiente na importação e cacheia.
# Atribuição, não setdefault: o container já exporta ENVIRONMENT=development
# pelo env_file, e teste tem que rodar em ambiente de teste.
os.environ["ENVIRONMENT"] = "test"
os.environ.setdefault("LOG_LEVEL", "WARNING")
_base_db = os.environ.get("POSTGRES_DB", "bolao")
if not _base_db.endswith("_test"):
    os.environ["POSTGRES_DB"] = f"{_base_db}_test"

import asyncio  # noqa: E402
from collections.abc import AsyncIterator, Iterator  # noqa: E402
from pathlib import Path  # noqa: E402

import asyncpg  # noqa: E402
import pytest  # noqa: E402
from alembic.config import Config  # noqa: E402
from httpx import ASGITransport, AsyncClient  # noqa: E402
from sqlalchemy.ext.asyncio import AsyncSession  # noqa: E402

from alembic import command  # noqa: E402
from app.core.config import settings  # noqa: E402
from app.core.db import engine, get_session  # noqa: E402
from app.main import app  # noqa: E402

API_ROOT = Path(__file__).resolve().parents[1]


async def _ensure_test_database() -> None:
    """Cria o banco de teste se ele ainda não existir."""
    conn = await asyncpg.connect(
        user=settings.postgres_user,
        password=settings.postgres_password,
        host=settings.postgres_host,
        port=settings.postgres_port,
        database="postgres",
    )
    try:
        exists = await conn.fetchval(
            "SELECT 1 FROM pg_database WHERE datname = $1", settings.postgres_db
        )
        if not exists:
            # Identificador não pode ser parametrizado; vem da configuração,
            # não de entrada de usuário, e é sanitizado abaixo.
            safe = settings.postgres_db.replace('"', "")
            await conn.execute(f'CREATE DATABASE "{safe}"')
    finally:
        await conn.close()


def _run_migrations() -> None:
    config = Config(str(API_ROOT / "alembic.ini"))
    config.set_main_option("script_location", str(API_ROOT / "alembic"))
    command.upgrade(config, "head")


@pytest.fixture(scope="session", autouse=True)
def prepared_database() -> Iterator[None]:
    """Banco de teste criado e migrado antes do primeiro teste."""
    asyncio.run(_ensure_test_database())
    _run_migrations()
    yield


@pytest.fixture(autouse=True)
async def _limites_zerados() -> AsyncIterator[None]:
    """Cada teste começa com o limitador de tentativas zerado.

    Sem isto a suíte inteira conta como um IP só: passados trinta logins, os
    testes seguintes recebem 429 e falham por um motivo que não tem nada a ver
    com o que eles verificam. O limitador continua ativo — quem o testa de
    propósito está em ``test_limites.py``.
    """
    from app.core.redis import get_redis

    async def limpar() -> None:
        try:
            redis = get_redis()
            chaves = [chave async for chave in redis.scan_iter("limite:*")]
            if chaves:
                await redis.delete(*chaves)
        except Exception:  # noqa: BLE001
            pass  # Redis fora do ar: o limitador já falha aberto.

    await limpar()
    yield
    await limpar()


@pytest.fixture
async def db_session() -> AsyncIterator[AsyncSession]:
    """Sessão isolada: tudo que o teste gravar é descartado no final."""
    async with engine.connect() as connection:
        transaction = await connection.begin()
        session = AsyncSession(
            bind=connection,
            expire_on_commit=False,
            join_transaction_mode="create_savepoint",
        )
        try:
            yield session
        finally:
            await session.close()
            await transaction.rollback()


@pytest.fixture
async def client(db_session: AsyncSession) -> AsyncIterator[AsyncClient]:
    """Cliente HTTP ligado à aplicação, compartilhando a sessão do teste."""

    async def _override() -> AsyncIterator[AsyncSession]:
        yield db_session

    app.dependency_overrides[get_session] = _override
    transport = ASGITransport(app=app)
    async with AsyncClient(transport=transport, base_url="http://test") as async_client:
        yield async_client
    app.dependency_overrides.clear()
