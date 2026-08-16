"""Catálogo de campeonatos coletáveis do GE.

Cada entrada é o par ``(tabela, fase)`` que o endpoint interno do GE exige. Não
existe API de descoberta: o identificador da tabela é um UUID que só aparece
embutido na página do campeonato, e nem todas as páginas o expõem.

Por isso duas coisas convivem aqui:

- **Presets verificados**: cada um destes foi testado contra a API e devolveu
  jogos de verdade. A data da verificação está no campo ``verificado_em``.
- **Cadastro pelo administrador**: qualquer outro campeonato pode ser
  acrescentado pela tela, colando tabela e fase. É o que evita que a lista
  envelheça esperando alguém mexer no código.

A fase muda de ano: ligas europeias usam ``-2025-2026``, as de ano civil usam
``-2026``. Por isso ela faz parte da entrada, não é derivada.
"""

from __future__ import annotations

from dataclasses import dataclass


@dataclass(frozen=True, slots=True)
class GeCompetition:
    slug: str
    name: str
    country: str
    table_id: str
    phase: str
    rounds: int
    verificado_em: str
    """Quando o par (tabela, fase) foi confirmado contra a API."""

    @property
    def season_year(self) -> int:
        """Ano da temporada, extraído da fase.

        ``fase-unica-campeonato-ingles-2025-2026`` → 2026 (o ano em que acaba,
        que é como as ligas de virada são referidas no Brasil).
        """
        partes = self.phase.rsplit("-", 2)
        for candidato in reversed(partes):
            if candidato.isdigit() and len(candidato) == 4:
                return int(candidato)
        raise ValueError(f"não consegui extrair o ano de {self.phase}")


#: Verificados em 31/07/2026 contra a API do GE.
GE_COMPETITIONS: tuple[GeCompetition, ...] = (
    GeCompetition(
        slug="brasileirao-serie-a",
        name="Brasileirão Série A",
        country="Brasil",
        table_id="d1a37fa4-e948-43a6-ba53-ab24ab3a45b1",
        phase="fase-unica-campeonato-brasileiro-2026",
        rounds=38,
        verificado_em="2026-07-31",
    ),
    GeCompetition(
        slug="premier-league",
        name="Premier League",
        country="Inglaterra",
        table_id="c33769be-62ec-43c6-a633-8c826e14a696",
        phase="fase-unica-campeonato-ingles-2025-2026",
        rounds=38,
        verificado_em="2026-07-31",
    ),
    GeCompetition(
        slug="ligue-1",
        name="Ligue 1",
        country="França",
        table_id="ef801316-e69d-47c3-916c-f45a0b3ca702",
        phase="fase-unica-campeonato-frances-2025-2026",
        rounds=34,
        verificado_em="2026-07-31",
    ),
    GeCompetition(
        slug="libertadores",
        name="Libertadores (fase de grupos)",
        country="América do Sul",
        table_id="83ad0ca5-f84e-4906-9242-a40d6585ebca",
        phase="fase-de-grupos-libertadores-2026",
        rounds=6,
        verificado_em="2026-07-31",
    ),
)

BY_SLUG: dict[str, GeCompetition] = {item.slug: item for item in GE_COMPETITIONS}


#: Instruções para quem for cadastrar um campeonato novo pela tela.
COMO_DESCOBRIR = [
    "Abra a página do campeonato no ge.globo.com e veja o código-fonte.",
    "Procure por 'fase-' seguido do nome e do ano — esse é o campo Fase "
    "(ex.: fase-unica-campeonato-espanhol-2025-2026).",
    "Procure um UUID (8-4-4-4-12 caracteres) perto dela — esse é o campo Tabela.",
    "Salve e clique em testar: a plataforma confere se o par devolve jogos "
    "antes de importar qualquer coisa.",
]
