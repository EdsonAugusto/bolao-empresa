"""baseline: extensões do Postgres

Revision ID: 0001
Revises:
Create Date: 2026-07-31

Migration de fundação. Não cria tabela — instala as extensões de que as fases
seguintes dependem, para que nenhuma migration de modelo precise fazer isso e
falhar por falta de privilégio no meio do caminho.

- pgcrypto  → gen_random_uuid() e gen_random_bytes() para códigos de convite
- citext    → e-mail case-insensitive sem índice funcional espalhado
- pg_trgm   → busca por nome de bolão/time
- unaccent  → slug e busca sem acento ("sao-paulo" acha "São Paulo")
"""

from collections.abc import Sequence

from alembic import op

revision: str = "0001"
down_revision: str | None = None
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None

EXTENSIONS = ("pgcrypto", "citext", "pg_trgm", "unaccent")


def upgrade() -> None:
    for extension in EXTENSIONS:
        op.execute(f'CREATE EXTENSION IF NOT EXISTS "{extension}"')


def downgrade() -> None:
    # Não removemos extensões: outra coisa no banco pode depender delas e
    # DROP EXTENSION em cascata é irreversível.
    pass
