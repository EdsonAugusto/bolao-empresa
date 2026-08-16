"""Dependências compartilhadas dos endpoints."""

from __future__ import annotations

from typing import Annotated

from fastapi import Depends, Header, HTTPException, Path, status
from sqlalchemy.ext.asyncio import AsyncSession

from app.core.db import get_session
from app.core.permissoes import DESCRICOES, Permissao
from app.core.security import TokenError, user_id_from_token
from app.models import Membership, Pool, User
from app.services import permissoes as permissao_service
from app.services import pools as pool_service

SessionDep = Annotated[AsyncSession, Depends(get_session)]


async def get_current_user(
    session: SessionDep,
    authorization: Annotated[str | None, Header()] = None,
) -> User:
    if not authorization or not authorization.lower().startswith("bearer "):
        raise HTTPException(
            status_code=status.HTTP_401_UNAUTHORIZED,
            detail="autenticação necessária",
            headers={"WWW-Authenticate": "Bearer"},
        )

    token = authorization.split(" ", 1)[1].strip()
    try:
        user_id = user_id_from_token(token)
    except TokenError as exc:
        raise HTTPException(
            status_code=status.HTTP_401_UNAUTHORIZED,
            detail=str(exc),
            headers={"WWW-Authenticate": "Bearer"},
        ) from exc

    user = await session.get(User, user_id)
    if user is None or not user.is_active:
        raise HTTPException(status_code=status.HTTP_401_UNAUTHORIZED, detail="conta indisponível")
    return user


CurrentUser = Annotated[User, Depends(get_current_user)]


async def get_optional_user(
    session: SessionDep,
    authorization: Annotated[str | None, Header()] = None,
) -> User | None:
    if not authorization:
        return None
    try:
        return await get_current_user(session, authorization)
    except HTTPException:
        return None


OptionalUser = Annotated[User | None, Depends(get_optional_user)]


async def require_superuser(user: CurrentUser) -> User:
    if not user.is_superuser:
        raise HTTPException(
            status_code=status.HTTP_403_FORBIDDEN,
            detail="só o administrador da instalação pode fazer isso",
        )
    return user


SuperUser = Annotated[User, Depends(require_superuser)]


def Requer(permissao: Permissao):  # noqa: N802
    """Dependência que exige uma permissão da conta.

    Substitui ``SuperUser`` onde a pergunta certa não é "administra a
    plataforma?" mas "pode fazer *isto*?". A diferença aparece no organizador
    que precisa importar campeonato sem poder mexer nas contas dos outros — o
    caso mais comum num bolão entre amigos.

    A mensagem de recusa nomeia a capacidade que falta, não o cargo: quem leva
    o 403 precisa saber o que pedir a quem administra.
    """

    async def guarda(session: SessionDep, user: CurrentUser) -> User:
        if not await permissao_service.pode(session, user, permissao):
            descricao = DESCRICOES[permissao]
            raise HTTPException(
                status_code=status.HTTP_403_FORBIDDEN,
                detail=(
                    f'você não tem a permissão "{descricao.rotulo}". '
                    f"{descricao.ajuda} Peça a quem administra a plataforma."
                ),
            )
        return user

    return Annotated[User, Depends(guarda)]


async def get_pool(session: SessionDep, slug: Annotated[str, Path()]) -> Pool:
    pool = await pool_service.get_pool_by_slug(session, slug)
    if pool is None:
        raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail="bolão não encontrado")
    return pool


PoolDep = Annotated[Pool, Depends(get_pool)]


async def get_membership(session: SessionDep, pool: PoolDep, user: CurrentUser) -> Membership:
    try:
        return await pool_service.require_membership(session, pool.id, user.id)
    except pool_service.NotAMember as exc:
        raise HTTPException(status_code=status.HTTP_403_FORBIDDEN, detail=str(exc)) from exc


MembershipDep = Annotated[Membership, Depends(get_membership)]


async def get_manager_membership(
    session: SessionDep, pool: PoolDep, user: CurrentUser
) -> Membership:
    try:
        return await pool_service.require_manager(session, pool.id, user.id)
    except pool_service.NotAMember as exc:
        raise HTTPException(status_code=status.HTTP_403_FORBIDDEN, detail=str(exc)) from exc
    except pool_service.NotAuthorized as exc:
        raise HTTPException(status_code=status.HTTP_403_FORBIDDEN, detail=str(exc)) from exc


ManagerDep = Annotated[Membership, Depends(get_manager_membership)]
