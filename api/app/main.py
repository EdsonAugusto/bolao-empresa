"""Ponto de entrada da API."""

from __future__ import annotations

from collections.abc import AsyncIterator
from contextlib import asynccontextmanager

from fastapi import FastAPI
from fastapi.middleware.cors import CORSMiddleware

from app.api.router import api_router
from app.core.config import settings
from app.core.db import dispose_engine
from app.core.logging import configure_logging, get_logger

log = get_logger(__name__)


@asynccontextmanager
async def lifespan(_: FastAPI) -> AsyncIterator[None]:
    configure_logging()
    log.info("api.startup", environment=settings.environment)
    yield
    await dispose_engine()
    log.info("api.shutdown")


app = FastAPI(
    title=settings.project_name,
    version="0.1.0",
    lifespan=lifespan,
    # O nginx expõe a API sob /api/ e remove o prefixo antes do proxy_pass;
    # root_path faz o Swagger montar as URLs corretas do lado de fora.
    root_path="/api",
    # A documentação interativa fica aberta em rede local — é onde ela ajuda —
    # e fechada num servidor público, onde é um índice pronto de tudo que a
    # API aceita. Quem administra e precisa dela abre um túnel SSH.
    docs_url=None if settings.is_production else "/docs",
    redoc_url=None,
    openapi_url=None if settings.is_production else "/openapi.json",
)

# Em rede local o endereço muda conforme o roteador (192.168.0.x, 10.0.0.x...),
# e ninguém vai reconfigurar CORS toda vez que trocar de Wi-Fi. Liberamos
# qualquer origem de rede privada — e só de rede privada.
PRIVATE_ORIGIN_PATTERN = (
    r"^https?://("
    r"localhost|127\.0\.0\.1|\[::1\]|"
    r"10\.\d{1,3}\.\d{1,3}\.\d{1,3}|"
    r"192\.168\.\d{1,3}\.\d{1,3}|"
    r"172\.(1[6-9]|2\d|3[01])\.\d{1,3}\.\d{1,3}"
    r")(:\d+)?$"
)

app.add_middleware(
    CORSMiddleware,
    allow_origins=settings.cors_origins,
    allow_origin_regex=(PRIVATE_ORIGIN_PATTERN if settings.allow_private_network_origins else None),
    allow_credentials=True,
    allow_methods=["*"],
    allow_headers=["*"],
)

app.include_router(api_router)
