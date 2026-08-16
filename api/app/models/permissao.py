"""Grupos de permissão e ajustes por pessoa.

O nível mora em ``users.nivel``; o que está aqui é o que ajusta o padrão dele.

Por que as permissões de um grupo ficam em JSONB e não numa tabela de ligação:
elas são lidas sempre juntas, nunca consultadas isoladamente ("quais grupos têm
`relatos.triar`?" não é uma pergunta que a tela faça), e mantê-las numa coluna
evita uma junção em todo carregamento de sessão. A validação do conteúdo é do
serviço, que só aceita chaves do catálogo.
"""

from __future__ import annotations

from sqlalchemy import (
    Boolean,
    CheckConstraint,
    ForeignKey,
    Index,
    String,
    Text,
    UniqueConstraint,
)
from sqlalchemy.dialects.postgresql import JSONB
from sqlalchemy.orm import Mapped, mapped_column

from app.models.base import Base, IdMixin, TimestampMixin


class PermissionGroup(IdMixin, TimestampMixin, Base):
    """Um conjunto nomeado de permissões, para não marcar caixa uma a uma."""

    __tablename__ = "permission_groups"

    slug: Mapped[str] = mapped_column(String(48), unique=True, nullable=False, index=True)
    name: Mapped[str] = mapped_column(String(80), nullable=False)
    description: Mapped[str] = mapped_column(Text, default="", nullable=False)

    #: Lista de chaves de ``core.permissoes.Permissao``.
    permissions: Mapped[list[str]] = mapped_column(JSONB, default=list, nullable=False)

    #: Grupo que a plataforma cria sozinha. Pode ter as permissões editadas,
    #: mas não pode ser apagado — apagar o grupo que dá acesso ao painel
    #: deixaria a instalação sem caminho de volta pela tela.
    is_system: Mapped[bool] = mapped_column(Boolean, default=False, nullable=False)


class UserGroup(IdMixin, TimestampMixin, Base):
    """Quem está em qual grupo."""

    __tablename__ = "user_groups"
    __table_args__ = (UniqueConstraint("user_id", "group_id", name="uq_user_groups"),)

    user_id: Mapped[int] = mapped_column(
        ForeignKey("users.id", ondelete="CASCADE"), nullable=False, index=True
    )
    group_id: Mapped[int] = mapped_column(
        ForeignKey("permission_groups.id", ondelete="CASCADE"), nullable=False, index=True
    )


class UserPermission(IdMixin, TimestampMixin, Base):
    """Ajuste de uma permissão para uma pessoa específica.

    ``granted=True`` concede algo que o nível não daria; ``granted=False``
    tira algo que ele daria. Guardar a revogação explicitamente — em vez de só
    listar o que a pessoa tem — é o que permite mudar o padrão de um nível sem
    ressuscitar acesso que alguém tirou de propósito.
    """

    __tablename__ = "user_permissions"
    __table_args__ = (
        UniqueConstraint("user_id", "permission", name="uq_user_permissions"),
        CheckConstraint("permission <> ''", name="ck_user_permissions_nao_vazia"),
        Index("ix_user_permissions_user", "user_id"),
    )

    user_id: Mapped[int] = mapped_column(ForeignKey("users.id", ondelete="CASCADE"), nullable=False)
    permission: Mapped[str] = mapped_column(String(64), nullable=False)
    granted: Mapped[bool] = mapped_column(Boolean, nullable=False)

    #: Quem fez o ajuste e por quê. Numa plataforma entre amigos isso não é
    #: auditoria formal — é lembrar daqui a seis meses por que fulano tem
    #: acesso a lançar placar.
    granted_by_id: Mapped[int | None] = mapped_column(
        ForeignKey("users.id", ondelete="SET NULL"), default=None
    )
    reason: Mapped[str] = mapped_column(Text, default="", nullable=False)
