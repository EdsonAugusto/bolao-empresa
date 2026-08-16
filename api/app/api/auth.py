"""Endpoints de autenticação e perfil."""

from __future__ import annotations

from typing import Annotated

from fastapi import APIRouter, Header, HTTPException, Request, status

from app.api.deps import CurrentUser, SessionDep
from app.core import limites
from app.core.config import settings
from app.core.security import TokenError
from app.schemas.auth import (
    ChangePasswordRequest,
    LoginRequest,
    RefreshRequest,
    RegisterRequest,
    TokenPair,
    UserOut,
    UserUpdate,
)
from app.schemas.common import Message
from app.services import auth as auth_service

router = APIRouter(prefix="/auth", tags=["auth"])


def _tokens(access: str, refresh: str) -> TokenPair:
    return TokenPair(
        access_token=access,
        refresh_token=refresh,
        expires_in=settings.access_token_ttl_minutes * 60,
    )


@router.post("/register", response_model=TokenPair, status_code=status.HTTP_201_CREATED)
async def register(
    payload: RegisterRequest,
    session: SessionDep,
    request: Request,
    user_agent: Annotated[str | None, Header()] = None,
) -> TokenPair:
    # Cadastro é caro (um Argon2 por chamada) e cria linha no banco. Vinte por
    # IP a cada quarto de hora cobre a família inteira chegando junto e ainda
    # assim fecha a porta para um laço.
    await limites.barrar_por_ip(request, "cadastro", teto=20, janela=900)

    try:
        await auth_service.cadastro_permitido(session, convite=payload.invite_code)
        user = await auth_service.register_user(
            session,
            email=payload.email,
            password=payload.password,
            display_name=payload.display_name,
            timezone=payload.timezone,
            accepted_terms=payload.accepted_terms,
        )
    except auth_service.CadastroFechado as exc:
        raise HTTPException(status_code=status.HTTP_403_FORBIDDEN, detail=str(exc)) from exc
    except auth_service.EmailAlreadyUsed as exc:
        raise HTTPException(status_code=status.HTTP_409_CONFLICT, detail=str(exc)) from exc

    access, refresh = await auth_service.issue_tokens(
        session,
        user,
        user_agent=user_agent,
        ip_address=request.client.host if request.client else None,
    )
    await session.commit()
    return _tokens(access, refresh)


@router.post("/login", response_model=TokenPair)
async def login(
    payload: LoginRequest,
    session: SessionDep,
    request: Request,
    user_agent: Annotated[str | None, Header()] = None,
) -> TokenPair:
    # Antes de qualquer Argon2: a conta do limite é justamente para o hash não
    # chegar a rodar quando a tentativa é a milésima do mesmo IP.
    await limites.barrar_login(request, payload.email)

    try:
        user = await auth_service.authenticate(
            session, email=payload.email, password=payload.password
        )
    except auth_service.AuthError as exc:
        raise HTTPException(status_code=status.HTTP_401_UNAUTHORIZED, detail=str(exc)) from exc

    await limites.limpar_login(payload.email)

    access, refresh = await auth_service.issue_tokens(
        session,
        user,
        user_agent=user_agent,
        ip_address=request.client.host if request.client else None,
    )
    await session.commit()
    return _tokens(access, refresh)


@router.post("/refresh", response_model=TokenPair)
async def refresh_tokens(
    payload: RefreshRequest,
    session: SessionDep,
    request: Request,
    user_agent: Annotated[str | None, Header()] = None,
) -> TokenPair:
    try:
        _, access, refresh = await auth_service.rotate_refresh_token(
            session,
            payload.refresh_token,
            user_agent=user_agent,
            ip_address=request.client.host if request.client else None,
        )
    except (TokenError, auth_service.AuthError) as exc:
        await session.commit()  # a revogação da família precisa persistir
        raise HTTPException(status_code=status.HTTP_401_UNAUTHORIZED, detail=str(exc)) from exc

    await session.commit()
    return _tokens(access, refresh)


@router.post("/logout", response_model=Message)
async def logout(payload: RefreshRequest, session: SessionDep) -> Message:
    await auth_service.revoke_refresh_token(session, payload.refresh_token)
    await session.commit()
    return Message(detail="sessão encerrada")


@router.get("/me", response_model=UserOut)
async def me(user: CurrentUser) -> UserOut:
    return UserOut.model_validate(user)


@router.patch("/me", response_model=UserOut)
async def update_me(payload: UserUpdate, user: CurrentUser, session: SessionDep) -> UserOut:
    for field, value in payload.model_dump(exclude_unset=True).items():
        setattr(user, field, value)
    await session.commit()
    await session.refresh(user)
    return UserOut.model_validate(user)


@router.post("/me/password", response_model=Message)
async def change_password(
    payload: ChangePasswordRequest, user: CurrentUser, session: SessionDep
) -> Message:
    try:
        await auth_service.change_password(
            session,
            user,
            current_password=payload.current_password,
            new_password=payload.new_password,
        )
    except auth_service.AuthError as exc:
        raise HTTPException(status_code=status.HTTP_400_BAD_REQUEST, detail=str(exc)) from exc
    await session.commit()
    return Message(detail="senha alterada; entre de novo nos outros aparelhos")


@router.delete("/me", response_model=Message)
async def delete_account(user: CurrentUser, session: SessionDep) -> Message:
    """Exclusão de conta (LGPD).

    Anonimiza em vez de apagar: remover a linha quebraria o histórico e o
    ranking de todos os bolões de que a pessoa participou.
    """
    await auth_service.anonymize_account(session, user)
    await session.commit()
    return Message(detail="conta removida; seus dados pessoais foram apagados")
