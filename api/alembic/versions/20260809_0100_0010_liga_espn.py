"""Liga da ESPN por competição, para haver placar ao vivo.

O CSV das ligas europeias publica o resultado horas depois do apito, e a
Wikipédia depende de alguém editar o artigo. A ESPN publica durante o jogo, por
liga e por dia — mas só serve como **sobreposição de placar**: o calendário
continua sendo de quem o importou, e o casamento é feito por confronto.

Guardar o mapeamento aqui, e não numa tabela de código, é o que permite ao
organizador ligar placar ao vivo num campeonato que ele mesmo cadastrar.

Revision ID: 0010
Revises: 0009
"""

import json
from collections.abc import Sequence

import sqlalchemy as sa
from alembic import op

revision: str = "0010"
down_revision: str | None = "0009"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None

#: Conferidas em 09/08/2026: as dez responderam com nome e jogos do dia.
LIGAS = {
    "brasileirao-serie-a": "bra.1",
    "eredivisie": "ned.1",
    "primeira-liga": "por.1",
    "premier-league": "eng.1",
    "premier-league-2": "eng.1",
    "laliga": "esp.1",
    "serie-a": "ita.1",
    "bundesliga": "ger.1",
    "ligue-1": "fra.1",
    "ligue-1-2": "fra.1",
    "efl-league-one": "eng.3",
    "libertadores-mata-mata": "conmebol.libertadores",
    "libertadores-fase-de-grupos": "conmebol.libertadores",
}


def upgrade() -> None:
    conexao = op.get_bind()
    for slug, liga in LIGAS.items():
        conexao.execute(
            sa.text(
                """
                UPDATE competitions
                   SET provider_config = provider_config || CAST(:extra AS jsonb)
                 WHERE slug = :slug
                """
            ),
            {"extra": json.dumps({"espn_league": liga}), "slug": slug},
        )


def downgrade() -> None:
    op.get_bind().execute(
        sa.text("UPDATE competitions SET provider_config = provider_config - 'espn_league'")
    )
