"""Schemas de autenticação e perfil."""

from __future__ import annotations

import re
from datetime import datetime
from typing import Annotated

from pydantic import AfterValidator, BaseModel, Field, field_validator

from app.schemas.common import ORMModel

MIN_PASSWORD_LENGTH = 8

# Deliberadamente permissivo. A plataforma **nunca envia e-mail** — ele é só o
# identificador de login. Validação estrita (`EmailStr`) recusaria endereços de
# rede local como `pai@casa.local`, que é exatamente o caso de uso aqui.
_EMAIL_SHAPE = re.compile(r"^[^@\s]+@[^@\s]+\.[^@\s]+$")


def _validate_login_email(value: str) -> str:
    normalized = value.strip().lower()
    if not _EMAIL_SHAPE.match(normalized):
        raise ValueError("informe um e-mail no formato nome@dominio")
    if len(normalized) > 320:
        raise ValueError("e-mail longo demais")
    return normalized


LoginEmail = Annotated[str, AfterValidator(_validate_login_email)]


class RegisterRequest(BaseModel):
    email: LoginEmail
    password: str = Field(min_length=MIN_PASSWORD_LENGTH, max_length=128)
    display_name: str = Field(min_length=2, max_length=80)
    timezone: str = "America/Sao_Paulo"
    accepted_terms: bool = False
    #: Código do bolão que convidou. Só é exigido quando a instalação está em
    #: modo `convite` — em rede local o campo nem aparece na tela.
    invite_code: str = Field(default="", max_length=16)

    @field_validator("password")
    @classmethod
    def _not_trivial(cls, value: str) -> str:
        # Sem política de complexidade: comprimento é o que importa, e regra
        # de "um símbolo e um número" só produz senha pior e anotada no papel.
        if value.strip() != value:
            raise ValueError("a senha não pode começar ou terminar com espaço")
        return value


class LoginRequest(BaseModel):
    email: LoginEmail
    password: str


class RefreshRequest(BaseModel):
    refresh_token: str


class TokenPair(BaseModel):
    access_token: str
    refresh_token: str
    token_type: str = "bearer"
    expires_in: int


class ChangePasswordRequest(BaseModel):
    current_password: str
    new_password: str = Field(min_length=MIN_PASSWORD_LENGTH, max_length=128)


class TimeDoCoracao(BaseModel):
    """O time, resolvido, para a tela não precisar de uma segunda requisição."""

    id: int
    name: str
    short_name: str | None = None
    crest_url: str | None = None


class UserOut(ORMModel):
    id: int
    email: str
    display_name: str
    avatar_url: str | None = None
    timezone: str

    favorite_team_id: int | None = None
    favorite_team: TimeDoCoracao | None = None
    """Preenchido pela API, não vem do ORM — o modelo não tem a relação."""

    titulos: int = 0
    must_change_password: bool = False
    is_superuser: bool
    notify_in_app: bool
    notify_telegram: bool
    telegram_chat_id: str | None = None
    quiet_hours_start: int
    quiet_hours_end: int
    created_at: datetime


class UserUpdate(BaseModel):
    display_name: str | None = Field(default=None, min_length=2, max_length=80)
    avatar_url: str | None = None
    timezone: str | None = None
    favorite_team_id: int | None = None

    @field_validator("avatar_url")
    @classmethod
    def _avatar_conhecido(cls, valor: str | None) -> str | None:
        """Só avatar do catálogo ou foto que nós gravamos.

        Este campo chega do cliente e vai para um `<img src>` na tela de todo
        mundo. Sem esta trava dava para apontar para um endereço de fora — um
        pixel que registra quem abriu a lista de pessoas — ou para uma URL
        `javascript:`.
        """
        from app.services.avatares import AvatarInvalido, validar

        try:
            return validar(valor)
        except AvatarInvalido as exc:
            raise ValueError(str(exc)) from exc

    notify_in_app: bool | None = None
    notify_telegram: bool | None = None
    telegram_chat_id: str | None = Field(default=None, max_length=64)
    quiet_hours_start: int | None = Field(default=None, ge=0, le=23)
    quiet_hours_end: int | None = Field(default=None, ge=0, le=23)
