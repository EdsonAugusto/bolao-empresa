"""Gera os ícones PNG da aplicação a partir de uma descrição geométrica.

    python infra/icones/gerar.py

Por que gerar em vez de commitar um PNG pronto: o ícone precisa existir em
vários tamanhos e em duas variantes (normal e *maskable*, que o Android recorta
em círculo), e mexer numa cor significaria reexportar tudo à mão. Aqui a fonte
da verdade é este arquivo, e os PNGs são saída.

Sem dependência externa: o PNG é escrito na mão com ``zlib`` e ``struct``. Uma
biblioteca de imagem para desenhar cinco círculos e um pentágono seria um
requisito a mais na máquina de quem for mexer nisto.

A suavização de borda vem de supersampling 4×4 — desenha grande, tira a média.
É o suficiente para formas geométricas e evita implementar rasterização com
anti-aliasing de verdade.
"""

from __future__ import annotations

import math
import struct
import zlib
from pathlib import Path

SAIDA = Path(__file__).resolve().parents[2] / "web" / "public"

# Verde do sistema de design (--verde / --verde-escuro em web/assets/css/main.css).
VERDE_CLARO = (24, 145, 92)
VERDE_ESCURO = (9, 90, 57)
BRANCO = (255, 255, 255)
COSTURA = (12, 58, 40)

AMOSTRAS = 4
"""Lado do bloco de supersampling. 4 dá 16 amostras por pixel."""


def _mistura(a: tuple[int, int, int], b: tuple[int, int, int], t: float) -> tuple[int, int, int]:
    return (
        round(a[0] + (b[0] - a[0]) * t),
        round(a[1] + (b[1] - a[1]) * t),
        round(a[2] + (b[2] - a[2]) * t),
    )


def _pentagono(cx: float, cy: float, raio: float, giro: float) -> list[tuple[float, float]]:
    return [
        (
            cx + raio * math.cos(giro + i * 2 * math.pi / 5),
            cy + raio * math.sin(giro + i * 2 * math.pi / 5),
        )
        for i in range(5)
    ]


def _dentro_do_poligono(x: float, y: float, vertices: list[tuple[float, float]]) -> bool:
    """Regra do cruzamento de raio. Suficiente para polígono convexo pequeno."""
    dentro = False
    j = len(vertices) - 1
    for i, (xi, yi) in enumerate(vertices):
        xj, yj = vertices[j]
        if (yi > y) != (yj > y) and x < (xj - xi) * (y - yi) / (yj - yi) + xi:
            dentro = not dentro
        j = i
    return dentro


def _perto_do_segmento(
    x: float, y: float, ax: float, ay: float, bx: float, by: float, espessura: float
) -> bool:
    dx, dy = bx - ax, by - ay
    comprimento = dx * dx + dy * dy
    if comprimento == 0:
        return math.hypot(x - ax, y - ay) <= espessura
    t = max(0.0, min(1.0, ((x - ax) * dx + (y - ay) * dy) / comprimento))
    return math.hypot(x - (ax + t * dx), y - (ay + t * dy)) <= espessura


def _cor_do_ponto(x: float, y: float, lado: float, maskable: bool) -> tuple[int, int, int, int]:
    """Cor de uma amostra em coordenadas de pixel."""
    u, v = x / lado, y / lado

    # --- fundo -------------------------------------------------------------
    # Maskable é recortado em círculo pelo sistema: precisa sangrar até a borda
    # e manter o desenho dentro da zona segura (80% central).
    if maskable:
        dentro_do_fundo = True
    else:
        raio_canto = 0.22 * lado
        px, py = x, y
        cx = min(max(px, raio_canto), lado - raio_canto)
        cy = min(max(py, raio_canto), lado - raio_canto)
        dentro_do_fundo = math.hypot(px - cx, py - cy) <= raio_canto

    if not dentro_do_fundo:
        return (0, 0, 0, 0)

    fundo = _mistura(VERDE_CLARO, VERDE_ESCURO, (u + v) / 2)

    # Faixas de gramado, discretas: dão textura sem virar ruído em 48px.
    if int(u * 7) % 2:
        fundo = _mistura(fundo, VERDE_ESCURO, 0.1)

    # --- bola --------------------------------------------------------------
    escala = 0.62 if maskable else 0.74
    centro = lado / 2
    raio_bola = lado * escala / 2
    distancia = math.hypot(x - centro, y - centro)

    if distancia > raio_bola:
        return (*fundo, 255)

    # Sombra interna crescendo para a borda: separa a bola do fundo sem
    # precisar de contorno preto, que engorda demais em tamanho pequeno.
    borda = (distancia / raio_bola) ** 3
    bola = _mistura(BRANCO, (214, 226, 219), borda * 0.55)

    # Pentágono central mais cinco ao redor, girados meia volta em relação a
    # ele. É a projeção achatada da bola clássica, e é o que faz a forma ser
    # lida como "bola de futebol" mesmo com 32px — costura fina some, mancha
    # escura não.
    giro_central = -math.pi / 2
    raio_pentagono = raio_bola * 0.27

    if _dentro_do_poligono(x, y, _pentagono(centro, centro, raio_pentagono, giro_central)):
        return (*COSTURA, 255)

    for i in range(5):
        angulo = giro_central + math.pi / 5 + i * 2 * math.pi / 5
        px = centro + math.cos(angulo) * raio_bola * 0.72
        py = centro + math.sin(angulo) * raio_bola * 0.72
        vizinho = _pentagono(px, py, raio_pentagono * 1.05, angulo + math.pi / 2)
        if _dentro_do_poligono(x, y, vizinho):
            return (*COSTURA, 255)

    return (*bola, 255)


def desenhar(lado: int, *, maskable: bool = False) -> bytes:
    """Devolve os bytes de um PNG RGBA de ``lado``×``lado``."""
    linhas = bytearray()
    passo = 1.0 / AMOSTRAS
    peso = AMOSTRAS * AMOSTRAS

    for py in range(lado):
        linhas.append(0)  # filtro "None" para esta linha
        for px in range(lado):
            r = g = b = a = 0
            for sy in range(AMOSTRAS):
                for sx in range(AMOSTRAS):
                    cor = _cor_do_ponto(
                        px + (sx + 0.5) * passo,
                        py + (sy + 0.5) * passo,
                        lado,
                        maskable,
                    )
                    r += cor[0] * cor[3]
                    g += cor[1] * cor[3]
                    b += cor[2] * cor[3]
                    a += cor[3]
            if a:
                # Média ponderada pelo alfa: sem isto a borda transparente
                # puxaria a cor para preto e o ícone ganharia halo escuro.
                linhas.extend((r // a, g // a, b // a, a // peso))
            else:
                linhas.extend((0, 0, 0, 0))

    def bloco(tipo: bytes, dados: bytes) -> bytes:
        return (
            struct.pack(">I", len(dados))
            + tipo
            + dados
            + struct.pack(">I", zlib.crc32(tipo + dados) & 0xFFFFFFFF)
        )

    cabecalho = struct.pack(">IIBBBBB", lado, lado, 8, 6, 0, 0, 0)
    return (
        b"\x89PNG\r\n\x1a\n"
        + bloco(b"IHDR", cabecalho)
        + bloco(b"IDAT", zlib.compress(bytes(linhas), 9))
        + bloco(b"IEND", b"")
    )


def svg() -> str:
    """A mesma bola em vetor, para onde o tamanho é livre.

    Gerada daqui e não escrita à mão de propósito: duas descrições da mesma
    forma divergem no primeiro ajuste de geometria.
    """
    lado = 512.0
    centro = lado / 2
    raio_bola = lado * 0.74 / 2
    raio_pentagono = raio_bola * 0.27
    giro = -math.pi / 2

    def caminho(vertices: list[tuple[float, float]]) -> str:
        pontos = " ".join(f"{x:.1f},{y:.1f}" for x, y in vertices)
        return f'<polygon points="{pontos}" fill="#0c3a28"/>'

    formas = [caminho(_pentagono(centro, centro, raio_pentagono, giro))]
    for i in range(5):
        angulo = giro + math.pi / 5 + i * 2 * math.pi / 5
        px = centro + math.cos(angulo) * raio_bola * 0.72
        py = centro + math.sin(angulo) * raio_bola * 0.72
        formas.append(caminho(_pentagono(px, py, raio_pentagono * 1.05, angulo + math.pi / 2)))

    return f"""<svg xmlns="http://www.w3.org/2000/svg" viewBox="0 0 512 512" role="img" aria-label="Bolão">
  <defs>
    <linearGradient id="gramado" x1="0" y1="0" x2="1" y2="1">
      <stop offset="0" stop-color="#18915c"/>
      <stop offset="1" stop-color="#095a39"/>
    </linearGradient>
    <clipPath id="bola">
      <circle cx="{centro:.0f}" cy="{centro:.0f}" r="{raio_bola:.0f}"/>
    </clipPath>
  </defs>
  <rect width="512" height="512" rx="112" fill="url(#gramado)"/>
  <circle cx="{centro:.0f}" cy="{centro:.0f}" r="{raio_bola:.0f}" fill="#f4f7f5"/>
  <g clip-path="url(#bola)">
    {"\n    ".join(formas)}
  </g>
</svg>
"""


ARQUIVOS: tuple[tuple[str, int, bool], ...] = (
    ("icone-192.png", 192, False),
    ("icone-512.png", 512, False),
    ("icone-mascara-192.png", 192, True),
    ("icone-mascara-512.png", 512, True),
    # O Safari não lê o manifest para o ícone da tela de início: ele quer um
    # apple-touch-icon de 180px, e não aplica cantos arredondados por conta
    # própria em versões recentes.
    ("apple-touch-icon.png", 180, False),
    ("favicon-32.png", 32, False),
    ("favicon-48.png", 48, False),
)


def main() -> None:
    SAIDA.mkdir(parents=True, exist_ok=True)
    for nome, lado, maskable in ARQUIVOS:
        destino = SAIDA / nome
        destino.write_bytes(desenhar(lado, maskable=maskable))
        print(f"  {nome:<26} {lado}x{lado}  {destino.stat().st_size / 1024:.1f} KB")

    vetor = SAIDA / "icone.svg"
    vetor.write_text(svg(), encoding="utf-8")
    print(f"  {'icone.svg':<26} vetor    {vetor.stat().st_size / 1024:.1f} KB")


if __name__ == "__main__":
    main()
