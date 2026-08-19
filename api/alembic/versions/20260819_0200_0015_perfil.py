"""Time do coração, títulos e troca de senha obrigatória.

Três campos que o perfil passa a ter, e um deles muda o fluxo de entrada.

``favorite_team_id`` é enfeite por desenho: aparece no perfil e em lugar nenhum
mais. Se um dia entrasse em pontuação ou desempate, torcer pelo time certo
viraria vantagem — e o bolão deixaria de ser sobre acertar placar.

``titulos`` é informado, não apurado. O bolão do grupo existe há anos e as
edições antigas não estão em banco nenhum; quem administra registra o número
para a conquista aparecer no perfil de quem já ganhou.

``must_change_password`` é o que fecha o buraco de quem administra redefinir a
senha de outra pessoa: a senha passa a ser conhecida por um terceiro, e não
pode continuar valendo depois que a dona entrar. Ligado na redefinição,
desligado quando ela escolhe a própria.

Revision ID: 0015
Revises: 0014
"""

from collections.abc import Sequence

import sqlalchemy as sa

from alembic import op

revision: str = "0015"
down_revision: str | None = "0014"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None


def upgrade() -> None:
    op.add_column("users", sa.Column("favorite_team_id", sa.BigInteger(), nullable=True))
    op.create_foreign_key(
        op.f("fk_users_favorite_team_id_teams"),
        "users",
        "teams",
        ["favorite_team_id"],
        ["id"],
        # SET NULL e não CASCADE: apagar um time do catálogo não pode apagar a
        # conta de quem torce para ele.
        ondelete="SET NULL",
    )

    op.add_column("users", sa.Column("titulos", sa.Integer(), nullable=False, server_default="0"))
    op.add_column(
        "users",
        sa.Column("must_change_password", sa.Boolean(), nullable=False, server_default=sa.false()),
    )


def downgrade() -> None:
    op.drop_column("users", "must_change_password")
    op.drop_column("users", "titulos")
    op.drop_constraint(op.f("fk_users_favorite_team_id_teams"), "users", type_="foreignkey")
    op.drop_column("users", "favorite_team_id")
