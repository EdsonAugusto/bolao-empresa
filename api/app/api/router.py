"""Composição das rotas da API."""

from __future__ import annotations

from fastapi import APIRouter

from app.api.auth import router as auth_router
from app.api.bonuses import router as bonuses_router
from app.api.catalog import router as catalog_router
from app.api.health import router as health_router
from app.api.matchdays import router as matchdays_router
from app.api.notifications import router as notifications_router
from app.api.pools import router as pools_router
from app.api.reports import router as reports_router
from app.api.system import router as system_router
from app.api.usuarios import router as usuarios_router

api_router = APIRouter()
api_router.include_router(health_router)

v1 = APIRouter(prefix="/v1")
v1.include_router(auth_router)
v1.include_router(catalog_router)
v1.include_router(pools_router)
v1.include_router(bonuses_router)
v1.include_router(matchdays_router)
v1.include_router(notifications_router)
v1.include_router(reports_router)
v1.include_router(system_router)
v1.include_router(usuarios_router)

api_router.include_router(v1)
