"""Relatos: bug, feedback e ideia, com anexo de imagem e áudio.

Três tabelas. O arquivo do anexo **não** fica no banco — fica em disco, num
volume separado do código, e o que se guarda aqui é o suficiente para servi-lo
com segurança: o tipo que nós detectamos e o nome que nós geramos.

Revision ID: 0011
Revises: 0010
"""

from collections.abc import Sequence

import sqlalchemy as sa

from alembic import op

revision: str = "0011"
down_revision: str | None = "0010"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None

KIND = sa.Enum("BUG", "FEEDBACK", "IDEIA", name="report_kind", native_enum=False)
SEVERITY = sa.Enum("BAIXA", "MEDIA", "ALTA", "CRITICA", name="report_severity", native_enum=False)
STATUS = sa.Enum(
    "ABERTO",
    "TRIADO",
    "FAZENDO",
    "RESOLVIDO",
    "DESCARTADO",
    name="report_status",
    native_enum=False,
)


def upgrade() -> None:
    op.create_table(
        "reports",
        sa.Column("id", sa.BigInteger(), autoincrement=True, nullable=False),
        sa.Column("code", sa.String(length=12), nullable=False),
        sa.Column("kind", KIND, nullable=False),
        sa.Column("severity", SEVERITY, nullable=False),
        sa.Column("status", STATUS, nullable=False),
        sa.Column("title", sa.String(length=160), nullable=False),
        sa.Column("body", sa.Text(), nullable=False, server_default=""),
        sa.Column("reporter_id", sa.BigInteger(), nullable=True),
        sa.Column("reporter_name", sa.String(length=80), nullable=False, server_default=""),
        sa.Column("pool_id", sa.BigInteger(), nullable=True),
        sa.Column("page_url", sa.String(length=500), nullable=False, server_default=""),
        sa.Column("user_agent", sa.String(length=300), nullable=False, server_default=""),
        sa.Column("viewport", sa.String(length=24), nullable=False, server_default=""),
        sa.Column("app_version", sa.String(length=40), nullable=False, server_default=""),
        sa.Column("resolution", sa.Text(), nullable=False, server_default=""),
        sa.Column("resolved_at", sa.DateTime(timezone=True), nullable=True),
        sa.Column(
            "created_at",
            sa.DateTime(timezone=True),
            nullable=False,
            server_default=sa.text("now()"),
        ),
        sa.Column(
            "updated_at",
            sa.DateTime(timezone=True),
            nullable=False,
            server_default=sa.text("now()"),
        ),
        sa.PrimaryKeyConstraint("id", name=op.f("pk_reports")),
        sa.UniqueConstraint("code", name=op.f("uq_reports_code")),
        # Quem relatou pode apagar a conta; o relato fica, porque a correção
        # ainda precisa ser feita.
        sa.ForeignKeyConstraint(
            ["reporter_id"],
            ["users.id"],
            name=op.f("fk_reports_reporter_id_users"),
            ondelete="SET NULL",
        ),
        sa.ForeignKeyConstraint(
            ["pool_id"], ["pools.id"], name=op.f("fk_reports_pool_id_pools"), ondelete="SET NULL"
        ),
    )
    op.create_index("ix_reports_status_created", "reports", ["status", "created_at"])
    op.create_index("ix_reports_reporter", "reports", ["reporter_id"])
    op.create_index(op.f("ix_reports_pool_id"), "reports", ["pool_id"])

    op.create_table(
        "report_attachments",
        sa.Column("id", sa.BigInteger(), autoincrement=True, nullable=False),
        sa.Column("report_id", sa.BigInteger(), nullable=False),
        sa.Column("storage_name", sa.String(length=80), nullable=False),
        sa.Column("original_name", sa.String(length=160), nullable=False, server_default=""),
        sa.Column("content_type", sa.String(length=80), nullable=False),
        sa.Column("size_bytes", sa.BigInteger(), nullable=False),
        sa.Column("duration_ms", sa.Integer(), nullable=True),
        sa.Column(
            "created_at",
            sa.DateTime(timezone=True),
            nullable=False,
            server_default=sa.text("now()"),
        ),
        sa.PrimaryKeyConstraint("id", name=op.f("pk_report_attachments")),
        sa.UniqueConstraint("storage_name", name=op.f("uq_report_attachments_storage_name")),
        sa.CheckConstraint("size_bytes > 0", name="anexo_nao_vazio"),
        sa.ForeignKeyConstraint(
            ["report_id"],
            ["reports.id"],
            name=op.f("fk_report_attachments_report_id_reports"),
            ondelete="CASCADE",
        ),
    )
    op.create_index(op.f("ix_report_attachments_report_id"), "report_attachments", ["report_id"])

    op.create_table(
        "report_comments",
        sa.Column("id", sa.BigInteger(), autoincrement=True, nullable=False),
        sa.Column("report_id", sa.BigInteger(), nullable=False),
        sa.Column("author_id", sa.BigInteger(), nullable=True),
        sa.Column("author_name", sa.String(length=80), nullable=False, server_default=""),
        sa.Column("body", sa.Text(), nullable=False),
        sa.Column(
            "created_at",
            sa.DateTime(timezone=True),
            nullable=False,
            server_default=sa.text("now()"),
        ),
        sa.PrimaryKeyConstraint("id", name=op.f("pk_report_comments")),
        sa.ForeignKeyConstraint(
            ["report_id"],
            ["reports.id"],
            name=op.f("fk_report_comments_report_id_reports"),
            ondelete="CASCADE",
        ),
        sa.ForeignKeyConstraint(
            ["author_id"],
            ["users.id"],
            name=op.f("fk_report_comments_author_id_users"),
            ondelete="SET NULL",
        ),
    )
    op.create_index(op.f("ix_report_comments_report_id"), "report_comments", ["report_id"])


def downgrade() -> None:
    op.drop_table("report_comments")
    op.drop_table("report_attachments")
    op.drop_index("ix_reports_reporter", table_name="reports")
    op.drop_index("ix_reports_status_created", table_name="reports")
    op.drop_table("reports")
