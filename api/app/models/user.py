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

    # --- Perfil -----------------------------------------------------------
    #: Time do coração. Só enfeite de perfil: não entra em pontuação, em
    #: sorteio nem em nada que decida resultado — senão viraria vantagem.
    favorite_team_id: Mapped[int | None] = mapped_column(
        ForeignKey("teams.id", ondelete="SET NULL"), default=None
    )

    #: Quantas vezes a pessoa foi campeã, incluindo as edições anteriores à
    #: plataforma. É um número informado por quem administra, não apurado: o
    #: bolão existia antes daqui e esse histórico não está em lugar nenhum.
    titulos: Mapped[int] = mapped_column(default=0, nullable=False)

    #: Obriga a trocar a senha no próximo acesso.
    #:
    #: Ligado quando quem administra redefine a senha de outra pessoa: quem
    #: escolheu a senha foi um terceiro, e ela não pode continuar valendo
    #: depois que a dona entrar.
    must_change_password: Mapped[bool] = mapped_column(Boolean, default=False, nullable=False)

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
    #: Notificação do navegador, a que chega com o app fechado. Nasce ligada
    #: porque só sai para quem tiver inscrito um aparelho — e inscrever exige
    #: a pessoa autorizar no próprio celular. Sem inscrição, nada acontece.
    notify_push: Mapped[bool] = mapped_column(Boolean, default=True, nullable=False)
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


class PushSubscription(IdMixin, TimestampMixin, Base):
    """Um aparelho inscrito para receber notificação do navegador.

    Uma pessoa tem vários: o celular, o computador do trabalho, o tablet de
    casa. Cada um se inscreve sozinho e é revogado sozinho.

    O ``endpoint`` é uma URL do serviço de push do fabricante do navegador
    (Google, Mozilla, Apple) e funciona como identidade: reinscrever o mesmo
    aparelho devolve a mesma URL, então ela é a chave única. As duas chaves
    guardadas ao lado são o que cifra a mensagem ponta a ponta — o serviço de
    push encaminha sem conseguir ler.
    """

    __tablename__ = "push_subscriptions"
    __table_args__ = (UniqueConstraint("endpoint", name="uq_push_subscriptions_endpoint"),)

    user_id: Mapped[int] = mapped_column(
        ForeignKey("users.id", ondelete="CASCADE"), nullable=False, index=True
    )

    endpoint: Mapped[str] = mapped_column(Text, nullable=False)
    p256dh: Mapped[str] = mapped_column(String(255), nullable=False)
    auth: Mapped[str] = mapped_column(String(255), nullable=False)

    #: Só para a pessoa reconhecer o aparelho na lista e poder desligar o
    #: certo. Nunca usado para decidir nada.
    user_agent: Mapped[str | None] = mapped_column(String(255), default=None)
    last_sent_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True), default=None)
