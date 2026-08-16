"""Campeonatos que alimentam a rodada de um bolão montado à mão.

Filtro de garimpo, não regra: encurta a agenda de onde o organizador escolhe os
jogos da semana. Sem nenhuma linha, vale tudo que estiver importado.

Escrita à mão de propósito. O autogenerate desta base também propõe derrubar os
``server_default`` de ``pools.kind``, ``matchdays.multiplier`` e
``seasons.outcome`` — diferença de representação entre o modelo e o banco, não
mudança real. Aplicar aquilo tiraria default de coluna NOT NULL.

Revision ID: 0008
Revises: 0007
"""

from collections.abc import Sequence

import sqlalchemy as sa
from alembic import op

revision: str = "0008"
down_revision: str | None = "0007"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None


def upgrade() -> None:
    op.create_table(
        "pool_competitions",
        sa.Column("id", sa.BigInteger(), autoincrement=True, nullable=False),
        sa.Column("pool_id", sa.BigInteger(), nullable=False),
        sa.Column("competition_id", sa.BigInteger(), nullable=False),
        sa.PrimaryKeyConstraint("id", name=op.f("pk_pool_competitions")),
        sa.ForeignKeyConstraint(
            ["pool_id"],
            ["pools.id"],
            name=op.f("fk_pool_competitions_pool_id_pools"),
            ondelete="CASCADE",
        ),
        sa.ForeignKeyConstraint(
            ["competition_id"],
            ["competitions.id"],
            name=op.f("fk_pool_competitions_competition_id_competitions"),
            ondelete="CASCADE",
        ),
        sa.UniqueConstraint(
            "pool_id", "competition_id", name="uq_pool_competitions_pool_competition"
        ),
    )
    op.create_index(
        op.f("ix_pool_competitions_pool_id"), "pool_competitions", ["pool_id"], unique=False
    )
    op.create_index(
        op.f("ix_pool_competitions_competition_id"),
        "pool_competitions",
        ["competition_id"],
        unique=False,
    )


def downgrade() -> None:
    op.drop_index(op.f("ix_pool_competitions_competition_id"), table_name="pool_competitions")
    op.drop_index(op.f("ix_pool_competitions_pool_id"), table_name="pool_competitions")
    op.drop_table("pool_competitions")
