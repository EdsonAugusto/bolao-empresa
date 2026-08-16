"""Escudos desenhados pela plataforma.

Por que não usar os oficiais: são marca registrada, e apontar para a imagem
hospedada por terceiro quebraria o uso offline — que é a razão de o provedor
manual ser o padrão. Um escudo com as cores do clube e a sigla identifica tão
bem quanto numa lista de jogos, e é original.

São SVG gerados na hora: nada gravado em disco, nada para servir de arquivo
estático, e escala bem em qualquer tela.
"""

from __future__ import annotations

from app.data.brasileirao import CLUBS_BY_SLUG, Club

FALLBACK = Club(
    slug="desconhecido",
    name="Time",
    code="???",
    primary="#5B6472",
    secondary="#FFFFFF",
    state="",
)


def crest_svg(slug: str) -> str:
    """Escudo do clube em SVG.

    Formato de brasão: base retangular com a ponta inferior arredondada, faixa
    diagonal na cor secundária e a sigla centralizada.
    """
    club = CLUBS_BY_SLUG.get(slug, FALLBACK)
    texto = _contrast_color(club.primary)

    return f"""<svg xmlns="http://www.w3.org/2000/svg" viewBox="0 0 64 64" \
role="img" aria-label="{_escape(club.name)}">
  <title>{_escape(club.name)}</title>
  <defs>
    <clipPath id="escudo-{club.slug}">
      <path d="M6 5h52v33c0 12-11 19-26 24C17 57 6 50 6 38Z"/>
    </clipPath>
  </defs>
  <g clip-path="url(#escudo-{club.slug})">
    <rect width="64" height="64" fill="{club.primary}"/>
    <path d="M-10 46 L74 4 L74 20 L-10 62 Z" fill="{club.secondary}" opacity="0.9"/>
  </g>
  <path d="M6 5h52v33c0 12-11 19-26 24C17 57 6 50 6 38Z"
        fill="none" stroke="{texto}" stroke-width="2.5" opacity="0.65"/>
  <text x="32" y="30" text-anchor="middle" dominant-baseline="middle"
        font-family="system-ui, -apple-system, 'Segoe UI', Roboto, sans-serif"
        font-size="19" font-weight="800" fill="{texto}"
        stroke="{club.primary}" stroke-width="0.6" paint-order="stroke">{club.code}</text>
</svg>"""


def crest_path(slug: str) -> str:
    """URL relativa do escudo, do jeito que vai para ``teams.crest_url``."""
    return f"/api/v1/catalog/teams/{slug}/crest.svg"


def _contrast_color(hex_color: str) -> str:
    """Preto ou branco, o que ler melhor sobre a cor do clube.

    Luminância relativa (fórmula do WCAG). Sem isso, a sigla de um time de
    camisa amarela some no fundo claro.
    """
    valor = hex_color.lstrip("#")
    if len(valor) != 6:
        return "#FFFFFF"

    canais = [int(valor[i : i + 2], 16) / 255 for i in (0, 2, 4)]
    linear = [c / 12.92 if c <= 0.04045 else ((c + 0.055) / 1.055) ** 2.4 for c in canais]
    luminancia = 0.2126 * linear[0] + 0.7152 * linear[1] + 0.0722 * linear[2]
    return "#111111" if luminancia > 0.45 else "#FFFFFF"


def _escape(texto: str) -> str:
    return (
        texto.replace("&", "&amp;").replace("<", "&lt;").replace(">", "&gt;").replace('"', "&quot;")
    )
