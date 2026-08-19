"""Autenticação e conta.

Sem SMS e sem e-mail transacional: os dois custam dinheiro ou exigem um serviço
externo, e a plataforma roda na rede de casa. O cadastro é e-mail + senha, sem
verificação — quem entra é quem tem o link de convite do bolão.
"""

from __future__ import annotations

import secrets
from datetime import UTC, datetime, timedelta

from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession
from starlette.concurrency import run_in_threadpool

from app.core.config import settings
from app.core.logging import get_logger
from app.core.security import (
    TokenError,
    create_access_token,
    generate_refresh_token,
    hash_password,
    hash_refresh_token,
    needs_rehash,
    new_family_id,
    refresh_expiry,
    verify_password,
)
from app.models import RefreshSession, User

log = get_logger(__name__)

#: Por quanto tempo um refresh já rotacionado ainda é aceito como retentativa.
#:
#: Curta de propósito: é o tempo de uma requisição que não voltou, não o de uma
#: sessão. Trinta segundos cobrem folgado a reconexão de um celular trocando de
#: rede, e continuam estreitos demais para servir de janela a quem roubou o
#: token e voltar horas depois.
GRACA_DE_REUSO = timedelta(seconds=30)

#: Hash contra o qual senhas de e-mail inexistente são conferidas, para o tempo
#: de resposta não denunciar quais contas existem. Calculado uma vez, sobre uma
#: senha aleatória que ninguém digitou nem vai digitar.
_HASH_BONECO = hash_password(secrets.token_urlsafe(32))


class AuthError(Exception):
    """Credencial inválida, conta inativa ou token recusado."""


class CadastroFechado(AuthError):
    """O modo de cadastro da instalação recusou esta tentativa."""


class EmailAlreadyUsed(AuthError):
    pass


def normalize_email(email: str) -> str:
    return email.strip().lower()


async def cadastro_permitido(session: AsyncSession, *, convite: str = "") -> None:
    """Decide se esta tentativa de cadastro pode seguir.

    Em rede local o padrão é ``aberto``: estar na rede já é o convite, e pedir
    código a quem está no sofá da sala é atrito sem ganho. Num servidor com
    domínio público o formulário fica visível para a internet inteira, e aí o
    padrão passa a ser ``convite`` — o mesmo código de 8 letras que o
    organizador já manda no grupo.

    A primeira conta é exceção em qualquer modo: sem ela não existe nem quem
    crie o primeiro bolão para gerar convite.
    """
    modo = settings.registration_mode
    if modo == "aberto":
        return

    if (await session.scalar(select(User.id).limit(1))) is None:
        return

    if modo == "fechado":
        raise CadastroFechado(
            "esta instalação não aceita cadastro pelo site. "
            "Peça a quem administra para criar sua conta."
        )

    codigo = (convite or "").strip().upper()
    if not codigo:
        raise CadastroFechado(
            "para criar conta é preciso o código de convite de um bolão. Peça a quem te chamou."
        )

    from app.models import Pool

    existe = await session.scalar(select(Pool.id).where(Pool.invite_code == codigo))
    if existe is None:
        raise CadastroFechado("código de convite não encontrado")


async def register_user(
    session: AsyncSession,
    *,
    email: str,
    password: str,
    display_name: str,
    timezone: str = "America/Sao_Paulo",
    accepted_terms: bool = False,
) -> User:
    normalized = normalize_email(email)
    existing = await session.scalar(select(User).where(User.email == normalized))
    if existing is not None:
        raise EmailAlreadyUsed("esse e-mail já tem conta")

    # A primeira conta da instalação é a dona. Numa plataforma que roda na sua
    # própria rede, quem instalou é quem manda — não faz sentido exigir um passo
    # separado de promoção.
    #
    # `nivel` e `is_superuser` andam juntos, sempre: a coluna booleana é
    # derivada do nível e usada em consulta, e gravar só uma das duas deixa a
    # conta com poder que o painel não mostra (ou o contrário).
    from app.core.permissoes import NIVEIS_DE_ADMINISTRACAO, Nivel

    primeira = (await session.scalar(select(User.id).limit(1))) is None
    nivel = Nivel.DONO if primeira else Nivel.JOGADOR

    user = User(
        email=normalized,
        password_hash=await run_in_threadpool(hash_password, password),
        display_name=display_name.strip(),
        timezone=timezone,
        nivel=str(nivel),
        is_superuser=nivel in NIVEIS_DE_ADMINISTRACAO,
        consented_at=datetime.now(UTC) if accepted_terms else None,
    )
    session.add(user)
    await session.flush()
    return user


async def authenticate(session: AsyncSession, *, email: str, password: str) -> User:
    user = await session.scalar(select(User).where(User.email == normalize_email(email)))

    # Verificamos a senha mesmo com usuário inexistente, contra um hash
    # descartável, para que o tempo de resposta não revele quais e-mails
    # existem na instalação. O hash-boneco é calculado uma vez no import: gerá-lo
    # a cada tentativa custava um Argon2 inteiro (64 MiB) além do da verificação,
    # ou seja, o dobro de trabalho justamente no caminho do atacante.
    if user is None:
        await run_in_threadpool(verify_password, password, _HASH_BONECO)
        raise AuthError("e-mail ou senha incorretos")

    if not await run_in_threadpool(verify_password, password, user.password_hash):
        raise AuthError("e-mail ou senha incorretos")
    if not user.is_active:
        raise AuthError("conta desativada")

    if needs_rehash(user.password_hash):
        user.password_hash = await run_in_threadpool(hash_password, password)

    user.last_login_at = datetime.now(UTC)
    return user


async def issue_tokens(
    session: AsyncSession,
    user: User,
    *,
    family_id: str | None = None,
    user_agent: str | None = None,
    ip_address: str | None = None,
) -> tuple[str, str]:
    """Devolve (access_token, refresh_token). O refresh só existe aqui em claro."""
    refresh_token = generate_refresh_token()
    session.add(
        RefreshSession(
            user_id=user.id,
            family_id=family_id or new_family_id(),
            token_hash=hash_refresh_token(refresh_token),
            expires_at=refresh_expiry(),
            user_agent=(user_agent or "")[:255] or None,
            ip_address=(ip_address or "")[:64] or None,
        )
    )
    await session.flush()
    return create_access_token(user.id), refresh_token


async def rotate_refresh_token(
    session: AsyncSession,
    refresh_token: str,
    *,
    user_agent: str | None = None,
    ip_address: str | None = None,
) -> tuple[User, str, str]:
    """Troca um refresh por um par novo.

    Se o token apresentado já tinha sido rotacionado, alguém está usando uma
    cópia: derruba a família inteira e obriga a entrar de novo. É a única
    defesa prática contra refresh roubado, já que ele não expira rápido.
    """
    token_hash = hash_refresh_token(refresh_token)
    stored = await session.scalar(
        select(RefreshSession).where(RefreshSession.token_hash == token_hash)
    )
    if stored is None:
        raise TokenError("refresh token desconhecido")

    now = datetime.now(UTC)

    if stored.revoked_at is not None:
        # Resposta perdida não é roubo.
        #
        # A rotação acontece no servidor ANTES de a resposta chegar ao cliente.
        # Num celular — que é onde o app vive — a conexão cai no meio o tempo
        # todo: o servidor rotaciona, a resposta se perde, e o aplicativo
        # continua com o token velho sem saber que ele já virou. A próxima
        # tentativa era lida como reuso e derrubava a família inteira, ou seja,
        # deslogava a pessoa de todos os aparelhos por causa de um pacote
        # perdido.
        #
        # Dentro da janela de carência, o token revogado rotaciona de novo em
        # vez de derrubar tudo. Um ladrão que replique dentro desses segundos
        # não ganha nada que o dono já não tivesse; fora dela, o
        # comportamento é o de antes.
        recente = stored.revoked_at is not None and now - stored.revoked_at <= GRACA_DE_REUSO
        if not (recente and stored.replaced_by_id is not None):
            await _revoke_family(session, stored.family_id, reason=now)
            raise TokenError("refresh token reutilizado; todas as sessões foram encerradas")

        log.info(
            "auth.refresh_reapresentado",
            familia=stored.family_id,
            segundos=(now - stored.revoked_at).total_seconds(),
        )

    if stored.expires_at <= now:
        stored.revoked_at = now
        raise TokenError("refresh token expirado")

    user = await session.get(User, stored.user_id)
    if user is None or not user.is_active:
        raise AuthError("conta indisponível")

    new_token = generate_refresh_token()
    successor = RefreshSession(
        user_id=user.id,
        family_id=stored.family_id,
        token_hash=hash_refresh_token(new_token),
        expires_at=refresh_expiry(),
        user_agent=(user_agent or "")[:255] or None,
        ip_address=(ip_address or "")[:64] or None,
    )
    session.add(successor)
    await session.flush()

    stored.revoked_at = now
    stored.replaced_by_id = successor.id

    return user, create_access_token(user.id), new_token


async def revoke_refresh_token(session: AsyncSession, refresh_token: str) -> None:
    stored = await session.scalar(
        select(RefreshSession).where(RefreshSession.token_hash == hash_refresh_token(refresh_token))
    )
    if stored is not None and stored.revoked_at is None:
        stored.revoked_at = datetime.now(UTC)


async def revoke_all_sessions(session: AsyncSession, user_id: int) -> int:
    sessions = (
        await session.scalars(
            select(RefreshSession).where(
                RefreshSession.user_id == user_id, RefreshSession.revoked_at.is_(None)
            )
        )
    ).all()
    now = datetime.now(UTC)
    for item in sessions:
        item.revoked_at = now
    return len(sessions)


async def _revoke_family(session: AsyncSession, family_id: str, *, reason: datetime) -> None:
    sessions = (
        await session.scalars(
            select(RefreshSession).where(
                RefreshSession.family_id == family_id, RefreshSession.revoked_at.is_(None)
            )
        )
    ).all()
    for item in sessions:
        item.revoked_at = reason


async def change_password(
    session: AsyncSession, user: User, *, current_password: str, new_password: str
) -> None:
    if not await run_in_threadpool(verify_password, current_password, user.password_hash):
        raise AuthError("senha atual incorreta")
    user.password_hash = await run_in_threadpool(hash_password, new_password)
    await revoke_all_sessions(session, user.id)


async def anonymize_account(session: AsyncSession, user: User) -> None:
    """Exclusão de conta na prática (LGPD).

    Apagar a linha destruiria o histórico do bolão de todo mundo — os palpites
    e o ranking das rodadas passadas deixariam de fechar. Então anonimizamos:
    somem os dados pessoais, permanece o participante.
    """
    stamp = datetime.now(UTC)
    user.email = f"removido-{user.id}@invalido.local"
    user.display_name = "Participante removido"
    user.password_hash = hash_password(generate_refresh_token())
    user.avatar_url = None
    user.telegram_chat_id = None
    user.notify_telegram = False
    user.is_active = False
    user.anonymized_at = stamp
    await revoke_all_sessions(session, user.id)
