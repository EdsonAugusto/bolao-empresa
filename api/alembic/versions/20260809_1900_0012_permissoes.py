"""Hierarquia e permissões: nível por conta, grupos e ajustes individuais.

Revision ID: 0012
Revises: 0011
"""

from __future__ import annotations

import json

import sqlalchemy as sa
from sqlalchemy.dialects import postgresql

from alembic import op

revision = "0012"
down_revision = "0011"
branch_labels = None
depends_on = None


#: Grupos que a instalação já nasce com. São atalhos para o que as pessoas
#: realmente pedem — "deixa o fulano importar campeonato" — sem obrigar ninguém
#: a marcar permissão por permissão na primeira semana.
GRUPOS_INICIAIS = [
    (
        "curadoria",
        "Curadoria de campeonatos",
        "Importa tabela e escudo, e corrige placar. Não mexe em conta de ninguém.",
        ["campeonatos.importar", "campeonatos.placar"],
    ),
    (
        "suporte",
        "Suporte",
        "Lê e responde os relatos de bug e retorno.",
        ["relatos.triar", "usuarios.ver"],
    ),
    (
        "organizacao",
        "Organização de bolões",
        "Cria bolão e monta rodada personalizada.",
        ["boloes.criar", "rodadas.montar"],
    ),
]


def upgrade() -> None:
    op.add_column(
        "users",
        sa.Column("nivel", sa.String(16), nullable=False, server_default="jogador"),
    )
    op.create_index("ix_users_nivel", "users", ["nivel"])

    # Quem já era superusuário vira administrador; a conta mais antiga com essa
    # marca vira dona. Sem isso a instalação existente acordaria sem ninguém no
    # topo da escada, e o painel ficaria inalcançável pela própria tela.
    op.execute("UPDATE users SET nivel = 'admin' WHERE is_superuser = true")
    op.execute(
        """
        UPDATE users SET nivel = 'dono'
        WHERE id = (SELECT id FROM users WHERE is_superuser = true ORDER BY id LIMIT 1)
        """
    )

    op.create_table(
        "permission_groups",
        sa.Column("id", sa.BigInteger, sa.Identity(always=True), primary_key=True),
        sa.Column("slug", sa.String(48), nullable=False),
        sa.Column("name", sa.String(80), nullable=False),
        sa.Column("description", sa.Text, nullable=False, server_default=""),
        sa.Column(
            "permissions",
            postgresql.JSONB(astext_type=sa.Text()),
            nullable=False,
            server_default="[]",
        ),
        sa.Column("is_system", sa.Boolean, nullable=False, server_default=sa.false()),
        sa.Column(
            "created_at", sa.DateTime(timezone=True), nullable=False, server_default=sa.func.now()
        ),
        sa.Column(
            "updated_at", sa.DateTime(timezone=True), nullable=False, server_default=sa.func.now()
        ),
        sa.UniqueConstraint("slug", name="uq_permission_groups_slug"),
    )
    op.create_index("ix_permission_groups_slug", "permission_groups", ["slug"])

    op.create_table(
        "user_groups",
        sa.Column("id", sa.BigInteger, sa.Identity(always=True), primary_key=True),
        sa.Column(
            "user_id",
            sa.BigInteger,
            sa.ForeignKey("users.id", ondelete="CASCADE"),
            nullable=False,
        ),
        sa.Column(
            "group_id",
            sa.BigInteger,
            sa.ForeignKey("permission_groups.id", ondelete="CASCADE"),
            nullable=False,
        ),
        sa.Column(
            "created_at", sa.DateTime(timezone=True), nullable=False, server_default=sa.func.now()
        ),
        sa.Column(
            "updated_at", sa.DateTime(timezone=True), nullable=False, server_default=sa.func.now()
        ),
        sa.UniqueConstraint("user_id", "group_id", name="uq_user_groups"),
    )
    op.create_index("ix_user_groups_user_id", "user_groups", ["user_id"])
    op.create_index("ix_user_groups_group_id", "user_groups", ["group_id"])

    op.create_table(
        "user_permissions",
        sa.Column("id", sa.BigInteger, sa.Identity(always=True), primary_key=True),
        sa.Column(
            "user_id",
            sa.BigInteger,
            sa.ForeignKey("users.id", ondelete="CASCADE"),
            nullable=False,
        ),
        sa.Column("permission", sa.String(64), nullable=False),
        sa.Column("granted", sa.Boolean, nullable=False),
        sa.Column(
            "granted_by_id",
            sa.BigInteger,
            sa.ForeignKey("users.id", ondelete="SET NULL"),
            nullable=True,
        ),
        sa.Column("reason", sa.Text, nullable=False, server_default=""),
        sa.Column(
            "created_at", sa.DateTime(timezone=True), nullable=False, server_default=sa.func.now()
        ),
        sa.Column(
            "updated_at", sa.DateTime(timezone=True), nullable=False, server_default=sa.func.now()
        ),
        sa.UniqueConstraint("user_id", "permission", name="uq_user_permissions"),
        sa.CheckConstraint("permission <> ''", name="ck_user_permissions_nao_vazia"),
    )
    op.create_index("ix_user_permissions_user", "user_permissions", ["user_id"])

    # O asyncpg não infere o tipo de um parâmetro dentro de função JSON; por
    # isso o JSON é montado em Python e entra como texto com CAST explícito.
    # (É a armadilha 24 do CLAUDE.md, e já custou uma migration.)
    for slug, nome, descricao, permissoes in GRUPOS_INICIAIS:
        op.execute(
            sa.text(
                """
                INSERT INTO permission_groups (slug, name, description, permissions, is_system)
                VALUES (:slug, :nome, :descricao, CAST(:permissoes AS jsonb), true)
                ON CONFLICT (slug) DO NOTHING
                """
            ).bindparams(
                slug=slug, nome=nome, descricao=descricao, permissoes=json.dumps(permissoes)
            )
        )


def downgrade() -> None:
    op.drop_table("user_permissions")
    op.drop_table("user_groups")
    op.drop_table("permission_groups")
    op.drop_index("ix_users_nivel", table_name="users")
    op.drop_column("users", "nivel")
