"""seleção de jogos por bolão

Revision ID: 0006
Revises: 0005
Create Date: 2026-07-31

Até aqui a inclusão era só por rodada: ou entrava a rodada inteira, ou nenhum
jogo dela. Num Brasileirão de 380 jogos isso é grosso demais — o organizador
quer poder tirar o jogo de quinta-feira, ou montar um bolão só com os clássicos.

``pool_fixtures`` é uma tabela de **exceção**, não a lista completa:

- sem linha para o jogo → vale o que a rodada disser;
- com linha → a linha manda.

Assim um bolão de 380 jogos não precisa de 380 linhas para se comportar do
jeito padrão, e tirar um jogo custa uma linha só.
"""

from collections.abc import Sequence

import sqlalchemy as sa
from alembic import op

revision: str = "0006"
down_revision: str | None = "0005"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None


def upgrade() -> None:
    op.create_table(
        "pool_fixtures",
        sa.Column("id", sa.BigInteger(), autoincrement=True, nullable=False),
        sa.Column("pool_id", sa.BigInteger(), nullable=False),
        sa.Column("fixture_id", sa.BigInteger(), nullable=False),
        sa.Column("included", sa.Boolean(), nullable=False),
        sa.ForeignKeyConstraint(
            ["fixture_id"],
            ["fixtures.id"],
            name=op.f("fk_pool_fixtures_fixture_id_fixtures"),
            ondelete="CASCADE",
        ),
        sa.ForeignKeyConstraint(
            ["pool_id"],
            ["pools.id"],
            name=op.f("fk_pool_fixtures_pool_id_pools"),
            ondelete="CASCADE",
        ),
        sa.PrimaryKeyConstraint("id", name=op.f("pk_pool_fixtures")),
        sa.UniqueConstraint("pool_id", "fixture_id", name="uq_pool_fixtures_pool_fixture"),
    )
    op.create_index(
        op.f("ix_pool_fixtures_pool_id"), "pool_fixtures", ["pool_id"], unique=False
    )
    op.create_index(
        op.f("ix_pool_fixtures_fixture_id"), "pool_fixtures", ["fixture_id"], unique=False
    )


def downgrade() -> None:
    op.drop_index(op.f("ix_pool_fixtures_fixture_id"), table_name="pool_fixtures")
    op.drop_index(op.f("ix_pool_fixtures_pool_id"), table_name="pool_fixtures")
    op.drop_table("pool_fixtures")
