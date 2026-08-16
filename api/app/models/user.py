"""Usuários e sessões.

LGPD desde o schema, não depois: consentimento com data, exclusão de conta que
anonimiza em vez de quebrar histórico, e nenhum dado pessoal obrigatório além
do necessário para entrar.
"""

from __future__ import annotations

from datetime import datetime

from sqlalchemy import Boolean, DateTime, ForeignKey, String, Text, UniqueConstraint, func
from sqlalchemy.dialects.postgresql import JSONB
from sqlalchemy.orm import Mapped, mapped_column, relationship

from app.models.base import Base, IdMixin, TimestampMixin


class User(IdMixin, TimestampMixin, Base):
    __tablename__ = "users"

    # CITEXT daria comparação case-insensitive nativa, mas exige a extensão em
    # todo ambiente. Normalizar para minúsculas na borda é mais portátil e
    # deixa o índice único simples.
    email: Mapped[str] = mapped_column(String(320), unique=True, nullable=False, index=True)
    password_hash: Mapped[str] = mapped_column(Text, nullable=False)

    display_name: Mapped[str] = mapped_column(String(80), nullable=False)
    avatar_url: Mapped[str | None] = mapped_column(Text, default=None)
    timezone: Mapped[str] = mapped_column(String(64), default="America/Sao_Paulo", nullable=False)

    is_active: Mapped[bool] = mapped_column(Boolean, default=True, nullable=False)

    #: Posição na hierarquia da plataforma. Ver ``app.core.permissoes``.
    nivel: Mapped[str] = mapped_column(String(16), default="jogador", nullable=False, index=True)

    #: Derivada de `nivel`, e mantida por `services.permissoes.aplicar_nivel`.
    #:
    #: Existe como coluna, e não como propriedade, porque é usada em consulta
    #: (`WHERE is_superuser`) e por código anterior à hierarquia. Duas fontes de
    #: verdade divergem no primeiro descuido — por isso ninguém escreve nesta
    #: coluna diretamente, só o serviço que muda o nível.
    is_superuser: Mapped[bool] = mapped_column(Boolean, default=False, nullable=False)

    # --- Notificação ------------------------------------------------------
    notify_in_app: Mapped[bool] = mapped_column(Boolean, default=True, nullable=False)
    notify_telegram: Mapped[bool] = mapped_column(Boolean, default=False, nullable=False)
    telegram_chat_id: Mapped[str | None] = mapped_column(String(64), default=None)
    # Fora desta janela (hora local do usuário) nada é enviado.
    quiet_hours_start: Mapped[int] = mapped_column(default=23, nullable=False)
    quiet_hours_end: Mapped[int] = mapped_column(default=8, nullable=False)

    # --- LGPD -------------------------------------------------------------
    consented_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True), default=None)
    anonymized_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True), default=None)
    last_login_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True), default=None)

    sessions: Mapped[list[RefreshSession]] = relationship(
        back_populates="user", cascade="all, delete-orphan"
    )

    @property
    def is_anonymized(self) -> bool:
        return self.anonymized_at is not None


class RefreshSession(IdMixin, TimestampMixin, Base):
    """Sessão de refresh token, rotativa.

    Guardamos o *hash* do token, nunca o token. Cada refresh rotaciona: o
    antigo é revogado e aponta para o sucessor. Se um token já revogado for
    apresentado, toda a cadeia daquela família é derrubada — é a assinatura de
    um token roubado sendo reusado.
    """

    __tablename__ = "refresh_sessions"
    __table_args__ = (UniqueConstraint("token_hash", name="uq_refresh_sessions_token_hash"),)

    user_id: Mapped[int] = mapped_column(
        ForeignKey("users.id", ondelete="CASCADE"), nullable=False, index=True
    )
    # Identifica a linhagem de rotação: sobrevive a cada troca de token.
    family_id: Mapped[str] = mapped_column(String(64), nullable=False, index=True)
    token_hash: Mapped[str] = mapped_column(String(128), nullable=False)

    expires_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), nullable=False)
    revoked_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True), default=None)
    replaced_by_id: Mapped[int | None] = mapped_column(
        ForeignKey("refresh_sessions.id", ondelete="SET NULL"), default=None
    )

    user_agent: Mapped[str | None] = mapped_column(String(255), default=None)
    ip_address: Mapped[str | None] = mapped_column(String(64), default=None)

    user: Mapped[User] = relationship(back_populates="sessions")


class AuditLog(IdMixin, Base):
    """Trilha de auditoria.

    Existe principalmente por causa de correção de placar: quando um resultado
    muda depois de apurado, é preciso conseguir explicar a alguém por que o
    ranking mudou.
    """

    __tablename__ = "audit_log"

    action: Mapped[str] = mapped_column(String(64), nullable=False, index=True)
    actor_user_id: Mapped[int | None] = mapped_column(
        ForeignKey("users.id", ondelete="SET NULL"), default=None
    )
    pool_id: Mapped[int | None] = mapped_column(
        ForeignKey("pools.id", ondelete="CASCADE"), default=None, index=True
    )
    entity: Mapped[str | None] = mapped_column(String(64), default=None)
    entity_id: Mapped[int | None] = mapped_column(default=None)
    payload: Mapped[dict] = mapped_column(JSONB, default=dict, nullable=False)

    created_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), server_default=func.now(), nullable=False
    )
