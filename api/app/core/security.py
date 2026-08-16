"""Senhas, tokens e códigos.

Decisões:

- **Argon2id** para senha. É o que a OWASP recomenda hoje; bcrypt tem teto de
  72 bytes e não resiste a GPU como o Argon2.
- **JWT curto + refresh rotativo**. O access token vive 30 minutos e não é
  revogável; quem revoga é o refresh, que é opaco, guardado como hash e trocado
  a cada uso. Reuso de refresh já rotacionado derruba a família inteira — é a
  assinatura de token roubado.
- **Códigos de convite sem ambiguidade visual**: sem 0/O e 1/I/L, porque
  alguém vai ditar o código no grupo.
"""

from __future__ import annotations

import hashlib
import hmac
import secrets
from datetime import UTC, datetime, timedelta
from typing import Any, Literal

import jwt
from argon2 import PasswordHasher
from argon2.exceptions import InvalidHashError, VerificationError, VerifyMismatchError

from app.core.config import settings

ALGORITHM = "HS256"
TokenType = Literal["access", "refresh"]

# Parâmetros conservadores para hardware modesto — a plataforma roda num PC
# de casa, não num servidor dedicado. Ainda assim bem acima do mínimo da OWASP.
_hasher = PasswordHasher(time_cost=2, memory_cost=64 * 1024, parallelism=2)

# Alfabeto sem caracteres que se confundem quando alguém dita o código.
INVITE_ALPHABET = "ABCDEFGHJKMNPQRSTUVWXYZ23456789"
INVITE_LENGTH = 8


class TokenError(Exception):
    """Token ausente, expirado, adulterado ou do tipo errado."""


# ---------------------------------------------------------------------------
# Senhas
# ---------------------------------------------------------------------------


def hash_password(password: str) -> str:
    return _hasher.hash(password)


def verify_password(password: str, password_hash: str) -> bool:
    try:
        return _hasher.verify(password_hash, password)
    except (VerifyMismatchError, VerificationError, InvalidHashError):
        return False


def needs_rehash(password_hash: str) -> bool:
    """Os parâmetros do Argon2 mudaram desde que esta senha foi gravada?"""
    try:
        return _hasher.check_needs_rehash(password_hash)
    except InvalidHashError:
        return True


# ---------------------------------------------------------------------------
# JWT
# ---------------------------------------------------------------------------


def _now() -> datetime:
    return datetime.now(UTC)


def create_access_token(user_id: int, *, extra: dict[str, Any] | None = None) -> str:
    issued_at = _now()
    payload: dict[str, Any] = {
        "sub": str(user_id),
        "type": "access",
        "iat": issued_at,
        "exp": issued_at + timedelta(minutes=settings.access_token_ttl_minutes),
        "jti": secrets.token_urlsafe(12),
    }
    if extra:
        payload.update(extra)
    return jwt.encode(payload, settings.secret_key, algorithm=ALGORITHM)


def decode_token(token: str, *, expected_type: TokenType = "access") -> dict[str, Any]:
    try:
        payload: dict[str, Any] = jwt.decode(token, settings.secret_key, algorithms=[ALGORITHM])
    except jwt.ExpiredSignatureError as exc:
        raise TokenError("token expirado") from exc
    except jwt.InvalidTokenError as exc:
        raise TokenError("token inválido") from exc

    if payload.get("type") != expected_type:
        raise TokenError(f"esperava token do tipo {expected_type}")
    if "sub" not in payload:
        raise TokenError("token sem sujeito")
    return payload


def user_id_from_token(token: str) -> int:
    payload = decode_token(token, expected_type="access")
    try:
        return int(payload["sub"])
    except (TypeError, ValueError) as exc:
        raise TokenError("sujeito do token não é um id") from exc


# ---------------------------------------------------------------------------
# Refresh tokens
# ---------------------------------------------------------------------------


def generate_refresh_token() -> str:
    """Token opaco. Não é JWT: não precisa carregar informação nenhuma."""
    return secrets.token_urlsafe(48)


def hash_refresh_token(token: str) -> str:
    """SHA-256 com chave.

    Argon2 aqui seria desperdício: o token tem 384 bits de entropia, não é
    adivinhável por força bruta como uma senha humana. O que se quer é que um
    dump do banco não entregue tokens utilizáveis.
    """
    return hmac.new(
        settings.secret_key.encode("utf-8"), token.encode("utf-8"), hashlib.sha256
    ).hexdigest()


def refresh_expiry() -> datetime:
    return _now() + timedelta(days=settings.refresh_token_ttl_days)


def new_family_id() -> str:
    return secrets.token_urlsafe(16)


# ---------------------------------------------------------------------------
# Códigos e slugs
# ---------------------------------------------------------------------------


def generate_invite_code(length: int = INVITE_LENGTH) -> str:
    return "".join(secrets.choice(INVITE_ALPHABET) for _ in range(length))


def constant_time_equals(left: str, right: str) -> bool:
    return hmac.compare_digest(left, right)
