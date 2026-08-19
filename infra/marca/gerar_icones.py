"""Gera os ícones do app a partir da arte da marca.

    python infra/marca/gerar_icones.py

A arte de origem é `bolao_sem_fundo.png`, um brasão circular com fundo
transparente e margem sobrando nas laterais. Daqui saem sete arquivos em
`web/public/`, e cada um existe por um motivo diferente:

- **`icone-192` / `icone-512`** — o ícone comum do manifesto. Fundo
  transparente: quem desenha a moldura é o sistema.
- **`icone-mascara-192` / `icone-mascara-512`** — o ícone `maskable`. O
  Android recorta esses num formato que ele escolhe (círculo, quadrado com
  cantos, gota), e o recorte pode comer até 20% de cada borda. Por isso a arte
  entra reduzida a 78% no meio de um fundo CHEIO: sem o fundo, o recorte
  deixaria cantos transparentes; sem a redução, ele cortaria o anel do brasão.
- **`apple-touch-icon`** — 180×180 e **opaco**. O iOS ignora transparência e
  pinta de preto o que estiver vazio, então o fundo é aplicado aqui também.
- **`favicon-32` / `favicon-48`** — a aba do navegador.

A cor de fundo sai do próprio anel do brasão, e não de um valor escolhido à
mão: assim a moldura do ícone mascarado não descola da arte se a arte mudar.
"""

from __future__ import annotations

import math
from collections import Counter
from pathlib import Path

from PIL import Image

RAIZ = Path(__file__).resolve().parents[2]
ORIGEM = Path(__file__).resolve().parent / "bolao_sem_fundo.png"
DESTINO = RAIZ / "web" / "public"

#: Quanto da largura o desenho ocupa dentro do ícone mascarado. O Android
#: garante apenas os 80% centrais; 78% deixa uma folga para o anel não encostar
#: na borda do recorte.
FRACAO_SEGURA = 0.78


def recortar_quadrado(imagem: Image.Image) -> Image.Image:
    """Tira a margem transparente e devolve um quadrado com o brasão centrado."""
    caixa = imagem.getbbox()
    if caixa is None:
        raise SystemExit("a arte de origem está inteiramente transparente")

    conteudo = imagem.crop(caixa)
    lado = max(conteudo.size)
    quadrado = Image.new("RGBA", (lado, lado), (0, 0, 0, 0))
    quadrado.paste(
        conteudo,
        ((lado - conteudo.width) // 2, (lado - conteudo.height) // 2),
        conteudo,
    )
    return quadrado


def cor_do_anel(quadrado: Image.Image) -> tuple[int, int, int, int]:
    """O anel externo do brasão, achado varrendo de fora para dentro.

    Amostrar a alguns por cento da borda não serve: o brasão tem um aro claro
    por fora do anel azul, e uma amostra a 4% caía nele — a moldura do ícone
    mascarado saía creme, descolada do desenho. Caminhando de fora para dentro
    em muitos ângulos, o PRIMEIRO pixel opaco é, por definição, a borda da
    arte; a cor mais frequente entre eles é o anel.

    Sair da própria arte, em vez de fixar um hexadecimal, mantém a moldura
    combinando com o desenho mesmo se a arte for trocada.
    """
    centro = quadrado.width / 2
    raio = centro
    encontradas: Counter[tuple[int, int, int]] = Counter()

    for grau in range(0, 360, 2):
        radianos = math.radians(grau)
        passo = raio
        while passo > raio * 0.80:
            x = int(centro + math.cos(radianos) * passo)
            y = int(centro + math.sin(radianos) * passo)
            if 0 <= x < quadrado.width and 0 <= y < quadrado.height:
                pixel = quadrado.getpixel((x, y))
                if pixel[3] > 200:
                    encontradas[pixel[:3]] += 1
                    break
            passo -= 1

    if not encontradas:
        return (17, 54, 90, 255)
    cor = encontradas.most_common(1)[0][0]
    return (cor[0], cor[1], cor[2], 255)


def sobre_fundo(
    quadrado: Image.Image, lado: int, fundo: tuple[int, int, int, int], fracao: float = 1.0
) -> Image.Image:
    """Desenha a arte, reduzida por ``fracao``, centrada sobre um fundo cheio."""
    tela = Image.new("RGBA", (lado, lado), fundo)
    alvo = max(1, int(lado * fracao))
    arte = quadrado.resize((alvo, alvo), Image.LANCZOS)
    desloca = (lado - alvo) // 2
    tela.paste(arte, (desloca, desloca), arte)
    return tela


def transparente(quadrado: Image.Image, lado: int) -> Image.Image:
    return quadrado.resize((lado, lado), Image.LANCZOS)


def main() -> None:
    if not ORIGEM.exists():
        raise SystemExit(f"não achei a arte em {ORIGEM}")

    quadrado = recortar_quadrado(Image.open(ORIGEM).convert("RGBA"))
    fundo = cor_do_anel(quadrado)
    print(f"  arte {quadrado.size[0]}px · fundo #{fundo[0]:02x}{fundo[1]:02x}{fundo[2]:02x}")

    saidas = {
        "icone-512.png": transparente(quadrado, 512),
        "icone-192.png": transparente(quadrado, 192),
        # Usado DENTRO do app — cabeçalho e tela de entrada. Existe separado
        # dos ícones do sistema porque ali ele aparece a 26px e a 52px: mandar
        # o de 192 seria baixar oito vezes mais bytes para desenhar o mesmo.
        "marca-96.png": transparente(quadrado, 96),
        "favicon-48.png": transparente(quadrado, 48),
        "favicon-32.png": transparente(quadrado, 32),
        "icone-mascara-512.png": sobre_fundo(quadrado, 512, fundo, FRACAO_SEGURA),
        "icone-mascara-192.png": sobre_fundo(quadrado, 192, fundo, FRACAO_SEGURA),
        # iOS pinta de preto o que for transparente: este vai opaco e cheio.
        "apple-touch-icon.png": sobre_fundo(quadrado, 180, fundo),
    }

    for nome, imagem in saidas.items():
        caminho = DESTINO / nome
        # Paleta de 256 cores.
        #
        # O ícone é baixado e guardado por todo mundo que instala o app, e em
        # RGBA cheio o de 512 passava de meio megabyte — mais que a página
        # inteira. Numa ilustração de traço a diferença não se vê no tamanho em
        # que o ícone aparece, e o arquivo cai para um quinto. `method=2`
        # preserva a transparência, que `ADAPTIVE` sozinho não faz.
        reduzida = imagem.quantize(colors=256, method=Image.Quantize.FASTOCTREE)
        reduzida.save(caminho, "PNG", optimize=True)
        print(f"  {nome:26} {imagem.size[0]}x{imagem.size[1]}  {caminho.stat().st_size // 1024} KB")


if __name__ == "__main__":
    main()
