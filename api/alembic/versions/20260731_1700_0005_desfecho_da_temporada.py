"""desfecho da temporada

Revision ID: 0005
Revises: 0004
Create Date: 2026-07-31

Guarda quem foi campeão, quem ficou no G-4 e quem caiu — é o que apura os
palpites de temporada.

Por que declarado e não calculado: montar a tabela do campeonato exige as
regras de classificação de cada competição (pontos, saldo, confronto direto,
cartões) e elas mudam por torneio e por ano. Numa plataforma em que o
organizador já lança os placares à mão, pedir três campos no fim do campeonato
é honesto; reimplementar o regulamento da CBF não é.
"""

from collections.abc import Sequence

import sqlalchemy as sa
from alembic import op
from sqlalchemy.dialects import postgresql

revision: str = "0005"
down_revision: str | None = "0004"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None


def upgrade() -> None:
    op.add_column(
        "seasons",
        sa.Column(
            "outcome",
            postgresql.JSONB(astext_type=sa.Text()),
            server_default=sa.text("'{}'::jsonb"),
            nullable=False,
        ),
    )


def downgrade() -> None:
    op.drop_column("seasons", "outcome")
