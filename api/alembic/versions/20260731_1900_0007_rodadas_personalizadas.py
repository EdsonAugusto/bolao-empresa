"""rodadas montadas pelo organizador

Revision ID: 0007
Revises: 0006
Create Date: 2026-07-31

Até aqui um bolão estava preso a **uma** temporada: `pools.season_id` era
obrigatório, as rodadas vinham do campeonato e a apuração encontrava os bolões
de um jogo por `pool.season_id == fixture.season_id`.

Isso impede o caso que o organizador quer: montar a rodada da semana com o
clássico do Brasileirão, o jogo grande da Premier League e a decisão da
Libertadores.

O que muda:

- `pools.kind` distingue bolão **de campeonato** (comportamento atual) de bolão
  **de rodada personalizada**.
- `pools.season_id` vira opcional — um bolão personalizado não pertence a
  temporada nenhuma.
- `matchdays` são as rodadas do bolão personalizado, criadas pelo organizador.
- `matchday_fixtures` diz quais jogos entram em cada uma. Aqui é lista
  completa, não exceção: a rodada é exatamente o que foi escolhido.
- `prediction_scores.matchday_id` guarda a rodada do bolão, para o ranking por
  rodada funcionar igual nos dois tipos.
"""

from collections.abc import Sequence

import sqlalchemy as sa
from alembic import op

revision: str = "0007"
down_revision: str | None = "0006"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None


def upgrade() -> None:
    op.create_table(
        "matchdays",
        sa.Column("id", sa.BigInteger(), autoincrement=True, nullable=False),
        sa.Column("pool_id", sa.BigInteger(), nullable=False),
        sa.Column("number", sa.Integer(), nullable=False),
        sa.Column("name", sa.String(length=120), nullable=False),
        sa.Column("multiplier", sa.Integer(), server_default="1", nullable=False),
        sa.Column("created_at", sa.DateTime(timezone=True), server_default=sa.func.now(), nullable=False),
        sa.Column("updated_at", sa.DateTime(timezone=True), server_default=sa.func.now(), nullable=False),
        sa.CheckConstraint("multiplier >= 1", name=op.f("ck_matchdays_multiplier_positivo")),
        sa.ForeignKeyConstraint(
            ["pool_id"], ["pools.id"], name=op.f("fk_matchdays_pool_id_pools"), ondelete="CASCADE"
        ),
        sa.PrimaryKeyConstraint("id", name=op.f("pk_matchdays")),
        sa.UniqueConstraint("pool_id", "number", name="uq_matchdays_pool_number"),
    )
    op.create_index(op.f("ix_matchdays_pool_id"), "matchdays", ["pool_id"], unique=False)

    op.create_table(
        "matchday_fixtures",
        sa.Column("id", sa.BigInteger(), autoincrement=True, nullable=False),
        sa.Column("matchday_id", sa.BigInteger(), nullable=False),
        sa.Column("fixture_id", sa.BigInteger(), nullable=False),
        sa.ForeignKeyConstraint(
            ["fixture_id"],
            ["fixtures.id"],
            name=op.f("fk_matchday_fixtures_fixture_id_fixtures"),
            ondelete="CASCADE",
        ),
        sa.ForeignKeyConstraint(
            ["matchday_id"],
            ["matchdays.id"],
            name=op.f("fk_matchday_fixtures_matchday_id_matchdays"),
            ondelete="CASCADE",
        ),
        sa.PrimaryKeyConstraint("id", name=op.f("pk_matchday_fixtures")),
        sa.UniqueConstraint(
            "matchday_id", "fixture_id", name="uq_matchday_fixtures_matchday_fixture"
        ),
    )
    op.create_index(
        op.f("ix_matchday_fixtures_matchday_id"), "matchday_fixtures", ["matchday_id"]
    )
    op.create_index(
        op.f("ix_matchday_fixtures_fixture_id"), "matchday_fixtures", ["fixture_id"]
    )

    # Bolões existentes são todos de campeonato.
    op.add_column(
        "pools",
        sa.Column("kind", sa.String(length=16), server_default="season", nullable=False),
    )
    op.alter_column("pools", "season_id", existing_type=sa.BigInteger(), nullable=True)

    op.add_column("prediction_scores", sa.Column("matchday_id", sa.BigInteger(), nullable=True))
    op.create_foreign_key(
        op.f("fk_prediction_scores_matchday_id_matchdays"),
        "prediction_scores",
        "matchdays",
        ["matchday_id"],
        ["id"],
        ondelete="SET NULL",
    )
    op.create_index(
        op.f("ix_prediction_scores_matchday_id"), "prediction_scores", ["matchday_id"]
    )


def downgrade() -> None:
    op.drop_index(op.f("ix_prediction_scores_matchday_id"), table_name="prediction_scores")
    op.drop_constraint(
        op.f("fk_prediction_scores_matchday_id_matchdays"),
        "prediction_scores",
        type_="foreignkey",
    )
    op.drop_column("prediction_scores", "matchday_id")

    # Bolões personalizados não têm temporada; sem eles a coluna volta a ser
    # obrigatória.
    op.execute("DELETE FROM pools WHERE season_id IS NULL")
    op.alter_column("pools", "season_id", existing_type=sa.BigInteger(), nullable=False)
    op.drop_column("pools", "kind")

    op.drop_index(op.f("ix_matchday_fixtures_fixture_id"), table_name="matchday_fixtures")
    op.drop_index(op.f("ix_matchday_fixtures_matchday_id"), table_name="matchday_fixtures")
    op.drop_table("matchday_fixtures")
    op.drop_index(op.f("ix_matchdays_pool_id"), table_name="matchdays")
    op.drop_table("matchdays")
