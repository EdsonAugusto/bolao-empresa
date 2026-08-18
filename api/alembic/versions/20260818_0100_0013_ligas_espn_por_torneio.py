"""Mais de uma liga da ESPN por competição, para o placar da Champions.

Um torneio pode estar partido na ESPN. A Champions tem a qualificação em
``uefa.champions_qual`` e o resto em ``uefa.champions`` — competições diferentes
para a fonte, uma só para quem palpita.

Guardar apenas a principal fazia o placar dos jogos de agosto nunca ser
encontrado: eles são todos de qualificação, a consulta ia para a liga certa do
torneio e errada do jogo, e voltava vazia. O jogo ficava 0 a 0 em campo para
sempre, sem erro nenhum em log nenhum — não achar jogo não é falha.

``espn_league`` no singular continua gravado, apontando para a principal, para
quem ainda ler a chave antiga.

Revision ID: 0013
Revises: 0012
"""

import json
from collections.abc import Sequence

import sqlalchemy as sa

from alembic import op

revision: str = "0013"
down_revision: str | None = "0012"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None

#: Conferidas em 17/08/2026 contra a API: a qualificação tinha 90 jogos e a
#: principal, zero — o sorteio da fase de liga ainda não havia acontecido.
POR_SLUG: dict[str, list[str]] = {
    "champions-league": ["uefa.champions_qual", "uefa.champions"],
    "copa-do-brasil": ["bra.copa_do_brazil"],
}


def upgrade() -> None:
    conexao = op.get_bind()
    for slug, ligas in POR_SLUG.items():
        # `||` mescla, não substitui: outras chaves do provider_config ficam
        # onde estão. Trocar o dicionário inteiro já apagou o `espn_league` do
        # Brasileirão uma vez, e o placar ao vivo simplesmente parou.
        conexao.execute(
            sa.text(
                """
                UPDATE competitions
                   SET provider_config = provider_config || CAST(:extra AS jsonb)
                 WHERE slug = :slug
                """
            ),
            {
                "extra": json.dumps({"espn_leagues": ligas, "espn_league": ligas[-1]}),
                "slug": slug,
            },
        )


def downgrade() -> None:
    op.get_bind().execute(
        sa.text("UPDATE competitions SET provider_config = provider_config - 'espn_leagues'")
    )
