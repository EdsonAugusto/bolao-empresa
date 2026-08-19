"""Rotação de refresh e o que encerra uma sessão.

O aplicativo instalado no celular não é aberto: é retomado. Ele passa a noite
congelado, volta com o token vencido e a rede oscilando — e cada uma dessas
coisas já derrubou a sessão de alguém sem que nada estivesse errado.

A defesa contra token roubado continua sendo a derrubada da família inteira ao
primeiro reuso. O que estes testes fixam é a fronteira: reapresentar o token
segundos depois é retentativa de rede, reapresentá-lo horas depois é reuso.
"""

from __future__ import annotations

from datetime import UTC, datetime, timedelta

import pytest
from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from app.core.security import TokenError
from app.models import RefreshSession
from app.services import auth as auth_service
from tests.factories import make_user


async def _sessao_nova(session: AsyncSession, marca: str) -> tuple[object, str]:
    user = await make_user(session, f"{marca}@teste.local", "Pessoa")
    _access, refresh = await auth_service.issue_tokens(session, user)
    await session.flush()
    return user, refresh


async def test_rotacao_normal_troca_o_par(db_session: AsyncSession) -> None:
    _user, refresh = await _sessao_nova(db_session, "rot1")

    _u, _access, novo = await auth_service.rotate_refresh_token(db_session, refresh)

    assert novo != refresh


async def test_reapresentar_logo_depois_e_retentativa_de_rede(db_session: AsyncSession) -> None:
    """A rotação acontece no servidor ANTES de a resposta chegar.

    Se ela se perde no caminho — o normal num celular trocando de rede — o app
    continua com o token velho sem saber que ele já virou. Tratar isso como
    roubo deslogava a pessoa de todos os aparelhos por causa de um pacote.
    """
    _user, refresh = await _sessao_nova(db_session, "rot2")

    _u, _a, primeiro = await auth_service.rotate_refresh_token(db_session, refresh)
    await db_session.flush()

    # O app não recebeu `primeiro` e tenta de novo com o token velho.
    _u2, _a2, segundo = await auth_service.rotate_refresh_token(db_session, refresh)

    assert segundo not in (refresh, primeiro)

    # E o mais importante: a família continua de pé.
    familias = (
        await db_session.scalars(
            select(RefreshSession).where(RefreshSession.token_hash.is_not(None))
        )
    ).all()
    vivas = [s for s in familias if s.revoked_at is None]
    assert vivas, "a família inteira foi derrubada por uma retentativa de rede"


async def test_reapresentar_muito_depois_continua_sendo_roubo(db_session: AsyncSession) -> None:
    """Fora da carência o comportamento é o de sempre: derruba tudo.

    É a única defesa prática contra um refresh copiado, já que ele vale meses.
    """
    _user, refresh = await _sessao_nova(db_session, "rot3")

    _u, _a, _novo = await auth_service.rotate_refresh_token(db_session, refresh)
    await db_session.flush()

    # Envelhece a revogação para além da janela.
    gasto = await db_session.scalar(
        select(RefreshSession).where(
            RefreshSession.token_hash == auth_service.hash_refresh_token(refresh)
        )
    )
    assert gasto is not None
    gasto.revoked_at = datetime.now(UTC) - auth_service.GRACA_DE_REUSO - timedelta(minutes=5)
    await db_session.flush()

    with pytest.raises(TokenError, match="reutilizado"):
        await auth_service.rotate_refresh_token(db_session, refresh)


async def test_a_carencia_e_curta_o_bastante_para_nao_virar_janela(
    db_session: AsyncSession,
) -> None:
    """Um minuto já seria tempo demais para quem replica um token roubado.

    Trinta segundos cobrem a reconexão de um celular e nada além disso.
    """
    assert timedelta(seconds=60) >= auth_service.GRACA_DE_REUSO


async def test_refresh_vale_uma_temporada(db_session: AsyncSession) -> None:
    """Cada renovação emite prazo cheio, então isto é "quanto tempo o app pode
    ficar fechado". Trinta dias deslogava quem sumia numa pausa do campeonato."""
    from app.core.config import settings

    assert settings.refresh_token_ttl_days >= 90
