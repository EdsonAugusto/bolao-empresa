"""Notificação do navegador, a que chega com o app fechado.

O aviso dentro da plataforma só é visto por quem abre a plataforma — e quem
esqueceu de palpitar é justamente quem não abriu. Web Push resolve isso sem
contratar nada: é padrão do navegador, a chave VAPID é gerada na instalação, e
o servidor fala direto com o serviço de push do fabricante.

Uma pessoa tem VÁRIOS aparelhos inscritos: o celular, o computador do trabalho.
Por isso é tabela e não coluna. O ``endpoint`` é a URL que o navegador devolve
ao assinar e serve de identidade — reinscrever o mesmo aparelho devolve a mesma
URL, então ela é única e a reinscrição vira atualização em vez de duplicata.

``notify_push`` nasce ``true`` porque ligar a preferência não envia nada: só
recebe quem inscreveu um aparelho, e inscrever exige a pessoa autorizar no
próprio celular. O interruptor existe para quem quiser desligar depois.

Revision ID: 0014
Revises: 0013
"""

from collections.abc import Sequence

import sqlalchemy as sa

from alembic import op

revision: str = "0014"
down_revision: str | None = "0013"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None


def upgrade() -> None:
    op.add_column(
        "users",
        sa.Column("notify_push", sa.Boolean(), nullable=False, server_default=sa.true()),
    )

    op.create_table(
        "push_subscriptions",
        sa.Column("id", sa.BigInteger(), autoincrement=True, nullable=False),
        sa.Column("user_id", sa.BigInteger(), nullable=False),
        # `Text` e não `String(n)`: o endpoint é uma URL do serviço de push do
        # fabricante e não tem tamanho contratado. Cortar em 255 quebraria a
        # inscrição de alguns aparelhos só às vezes, que é o pior modo de
        # quebrar.
        sa.Column("endpoint", sa.Text(), nullable=False),
        sa.Column("p256dh", sa.String(length=255), nullable=False),
        sa.Column("auth", sa.String(length=255), nullable=False),
        sa.Column("user_agent", sa.String(length=255), nullable=True),
        sa.Column("last_sent_at", sa.DateTime(timezone=True), nullable=True),
        sa.Column(
            "created_at", sa.DateTime(timezone=True), server_default=sa.func.now(), nullable=False
        ),
        sa.Column(
            "updated_at", sa.DateTime(timezone=True), server_default=sa.func.now(), nullable=False
        ),
        sa.ForeignKeyConstraint(
            ["user_id"],
            ["users.id"],
            name=op.f("fk_push_subscriptions_user_id_users"),
            ondelete="CASCADE",
        ),
        sa.PrimaryKeyConstraint("id", name=op.f("pk_push_subscriptions")),
        sa.UniqueConstraint("endpoint", name="uq_push_subscriptions_endpoint"),
    )
    op.create_index(op.f("ix_push_subscriptions_user_id"), "push_subscriptions", ["user_id"])


def downgrade() -> None:
    op.drop_table("push_subscriptions")
    op.drop_column("users", "notify_push")
