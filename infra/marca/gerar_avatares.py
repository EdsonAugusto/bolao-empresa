"""Gera os avatares padrão, em SVG.

    python infra/marca/gerar_avatares.py

Por que gerados, e não baixados
-------------------------------
Avatar de banco de imagens traz licença junto, e licença de imagem é o tipo de
detalhe que ninguém confere até virar problema. Estes são desenhados aqui com
formas geométricas simples: são nossos, não têm procedência a rastrear, e
mudar a paleta é trocar uma linha.

Por que SVG
-----------
Um avatar aparece a 32px na lista e a 96px no perfil. Em SVG é o mesmo arquivo,
sempre nítido, e cada um pesa menos de um kilobyte — dez vezes menos que o PNG
equivalente, num recurso que a tela de pessoas carrega dezenas de vezes.

SVG aqui não contradiz a recusa de SVG em upload de anexo. A diferença é a
procedência: estes são estáticos, versionados e escritos por nós. O que não
entra é SVG **enviado por alguém**, porque é XML que executa script.
"""

from __future__ import annotations

from pathlib import Path

RAIZ = Path(__file__).resolve().parents[2]
DESTINO = RAIZ / "web" / "public" / "avatares"

#: Fundos. Escolhidos escuros o bastante para o desenho claro por cima manter
#: contraste, e distintos entre si para o avatar servir de identificação rápida
#: numa lista — que é a função dele.
FUNDOS = [
    "#0f7a4d",  # verde do app
    "#0b4f6c",  # azul profundo
    "#7a3e0f",  # terra
    "#5b2a70",  # roxo
    "#0e5c52",  # verde-água escuro
    "#8a2f3b",  # vinho
    "#2c3e6b",  # azul-marinho
    "#6b5a11",  # mostarda escura
    "#134e2a",  # verde mata
    "#7a2f5e",  # magenta escuro
    "#1d4f7a",  # azul aço
    "#5c4632",  # café
]

TINTA = "#f5f7f8"

#: Cada símbolo é um desenho fechado dentro de uma caixa de 100x100, para o
#: gerador só precisar centrá-lo. Formas simples de propósito: a 32px nada com
#: detalhe fino sobrevive.
SIMBOLOS: dict[str, str] = {
    "bola": (
        '<circle cx="50" cy="50" r="30" fill="none" stroke="{tinta}" stroke-width="6"/>'
        '<polygon points="50,34 62,43 57,57 43,57 38,43" fill="{tinta}"/>'
        '<path d="M50 20 L50 34 M74 40 L62 43 M68 72 L57 57 M32 72 L43 57 M26 40 L38 43" '
        'stroke="{tinta}" stroke-width="5" stroke-linecap="round"/>'
    ),
    "escudo": (
        '<path d="M50 20 L76 30 V52 C76 68 64 78 50 82 C36 78 24 68 24 52 V30 Z" '
        'fill="none" stroke="{tinta}" stroke-width="6" stroke-linejoin="round"/>'
        '<path d="M50 34 V68 M34 44 H66" stroke="{tinta}" stroke-width="5" '
        'stroke-linecap="round"/>'
    ),
    "estrela": (
        '<path d="M50 20 L59 41 L82 43 L64 58 L70 80 L50 68 L30 80 L36 58 '
        'L18 43 L41 41 Z" fill="{tinta}"/>'
    ),
    "bandeira": (
        '<path d="M32 22 V80" stroke="{tinta}" stroke-width="6" stroke-linecap="round"/>'
        '<path d="M32 26 L74 38 L32 52 Z" fill="{tinta}"/>'
    ),
    "camisa": (
        '<path d="M36 26 L28 34 L34 44 L38 40 V78 H62 V40 L66 44 L72 34 L64 26 '
        'L57 30 H43 Z" fill="none" stroke="{tinta}" stroke-width="6" '
        'stroke-linejoin="round"/>'
        '<path d="M43 30 A8 8 0 0 0 57 30" fill="none" stroke="{tinta}" stroke-width="5"/>'
    ),
    "trofeu": (
        '<path d="M36 24 H64 V44 A14 14 0 0 1 36 44 Z" fill="none" stroke="{tinta}" '
        'stroke-width="6" stroke-linejoin="round"/>'
        '<path d="M36 30 H26 A10 10 0 0 0 36 42 M64 30 H74 A10 10 0 0 1 64 42" '
        'fill="none" stroke="{tinta}" stroke-width="5"/>'
        '<path d="M50 58 V70 M38 78 H62" stroke="{tinta}" stroke-width="6" '
        'stroke-linecap="round"/>'
    ),
    "coroa": (
        '<path d="M26 68 L22 34 L38 46 L50 26 L62 46 L78 34 L74 68 Z" fill="none" '
        'stroke="{tinta}" stroke-width="6" stroke-linejoin="round"/>'
        '<path d="M28 76 H72" stroke="{tinta}" stroke-width="6" stroke-linecap="round"/>'
    ),
    "raio": (
        '<path d="M56 18 L32 54 H48 L44 82 L70 44 H53 Z" fill="{tinta}"/>'
    ),
    "gol": (
        '<path d="M22 72 V34 H78 V72" fill="none" stroke="{tinta}" stroke-width="6" '
        'stroke-linejoin="round"/>'
        '<path d="M36 34 V72 M50 34 V72 M64 34 V72 M22 48 H78 M22 60 H78" '
        'stroke="{tinta}" stroke-width="3"/>'
    ),
    "cronometro": (
        '<circle cx="50" cy="56" r="26" fill="none" stroke="{tinta}" stroke-width="6"/>'
        '<path d="M50 40 V56 L61 63" stroke="{tinta}" stroke-width="5" '
        'stroke-linecap="round" fill="none"/>'
        '<path d="M40 22 H60 M50 22 V30" stroke="{tinta}" stroke-width="6" '
        'stroke-linecap="round"/>'
    ),
    "apito": (
        '<path d="M22 44 H52 L74 34 V66 L52 56 H22 Z" fill="none" stroke="{tinta}" '
        'stroke-width="6" stroke-linejoin="round"/>'
        '<circle cx="34" cy="50" r="5" fill="{tinta}"/>'
    ),
    "coracao": (
        '<path d="M50 78 C26 62 22 46 30 36 C38 26 50 32 50 42 C50 32 62 26 70 36 '
        'C78 46 74 62 50 78 Z" fill="{tinta}"/>'
    ),
}


def svg_de(nome: str, fundo: str) -> str:
    desenho = SIMBOLOS[nome].format(tinta=TINTA)
    # `viewBox` de 100x100 e nada de `width`/`height`: quem decide o tamanho é
    # o CSS de quem usa, e o mesmo arquivo serve a 32px e a 96px.
    return (
        '<svg xmlns="http://www.w3.org/2000/svg" viewBox="0 0 100 100" '
        f'role="img" aria-label="Avatar {nome}">'
        f'<circle cx="50" cy="50" r="50" fill="{fundo}"/>'
        f"{desenho}"
        "</svg>"
    )


def main() -> None:
    DESTINO.mkdir(parents=True, exist_ok=True)

    for indice, nome in enumerate(SIMBOLOS):
        fundo = FUNDOS[indice % len(FUNDOS)]
        arquivo = DESTINO / f"{nome}.svg"
        arquivo.write_text(svg_de(nome, fundo), encoding="utf-8")
        print(f"  {nome:12} {fundo}  {arquivo.stat().st_size} bytes")

    # Nenhum índice em JSON aqui.
    #
    # A lista que a API usa para validar mora em `api/app/data/avatares.py`,
    # dentro do que a imagem Docker empacota — a pasta `web/` não vai para lá.
    # Escrever um índice também aqui criaria uma segunda lista, e duas listas
    # divergem no dia em que alguém acrescenta um desenho. O teste
    # `test_catalogo_bate_com_os_arquivos_gerados` cobra que os nomes batam.
    print(f"  {len(SIMBOLOS)} avatares — confira app/data/avatares.py se mudou a lista")


if __name__ == "__main__":
    main()
