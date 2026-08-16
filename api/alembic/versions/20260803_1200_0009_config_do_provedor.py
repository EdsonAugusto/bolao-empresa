"""Configuração do provedor por competição, para haver recoleta.

Sem isto não existe atualização automática de resultado: o ``external_id``
sozinho não reconstrói o coletor do GE, que precisa também da **fase**
(``fase-unica-campeonato-brasileiro-2026``) e de quantas rodadas percorrer.
Esses dados só existiam no instante da importação e se perdiam depois.

O backfill preenche as quatro competições do GE que já estão no banco a partir
dos presets verificados. Competição de outro provedor não precisa de nada além
do que já tem, e fica com ``{}``.

Escrita à mão: o autogenerate desta base propõe derrubar ``server_default`` de
colunas NOT NULL que não têm nada a ver com isto.

Revision ID: 0009
Revises: 0008
"""

import json
from collections.abc import Sequence

import sqlalchemy as sa
from sqlalchemy.dialects import postgresql

from alembic import op

revision: str = "0009"
down_revision: str | None = "0008"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None

#: (external_id no banco, table_id, fase, rodadas). Conferidos em 31/07/2026.
GE_CONHECIDOS = (
    (
        "brasileirao-serie-a",
        "d1a37fa4-e948-43a6-ba53-ab24ab3a45b1",
        "fase-unica-campeonato-brasileiro-2026",
        38,
    ),
    (
        "c33769be-62ec-43c6-a633-8c826e14a696",
        "c33769be-62ec-43c6-a633-8c826e14a696",
        "fase-unica-campeonato-ingles-2025-2026",
        38,
    ),
    (
        "ef801316-e69d-47c3-916c-f45a0b3ca702",
        "ef801316-e69d-47c3-916c-f45a0b3ca702",
        "fase-unica-campeonato-frances-2025-2026",
        34,
    ),
    (
        "83ad0ca5-f84e-4906-9242-a40d6585ebca",
        "83ad0ca5-f84e-4906-9242-a40d6585ebca",
        "fase-de-grupos-libertadores-2026",
        6,
    ),
)


def upgrade() -> None:
    op.add_column(
        "competitions",
        sa.Column(
            "provider_config",
            postgresql.JSONB(astext_type=sa.Text()),
            server_default=sa.text("'{}'::jsonb"),
            nullable=False,
        ),
    )

    conexao = op.get_bind()

    # O JSON é montado aqui e entra como texto com cast explícito: o asyncpg
    # não consegue inferir o tipo de parâmetro dentro de `jsonb_build_object`
    # e recusa a instrução com "could not determine data type of parameter".
    for external_id, table_id, fase, rodadas in GE_CONHECIDOS:
        conexao.execute(
            sa.text(
                """
                UPDATE competitions c
                   SET provider_config = CAST(:config AS jsonb)
                  FROM providers p
                 WHERE p.id = c.provider_id
                   AND p.slug = 'globo'
                   AND c.external_id = :external_id
                """
            ),
            {
                "config": json.dumps({"table_id": table_id, "phase": fase, "rounds": rodadas}),
                "external_id": external_id,
            },
        )

    # fixturedownload e wikipedia se reconstroem só com o external_id, mas
    # gravar explícito evita depender do formato dele mais tarde.
    for slug_provedor, chave in (("fixturedownload", "league"), ("wikipedia", "article")):
        conexao.execute(
            sa.text(
                f"""
                UPDATE competitions c
                   SET provider_config = jsonb_build_object('{chave}', c.external_id)
                  FROM providers p
                 WHERE p.id = c.provider_id AND p.slug = :provedor
                """
            ),
            {"provedor": slug_provedor},
        )


def downgrade() -> None:
    op.drop_column("competitions", "provider_config")
