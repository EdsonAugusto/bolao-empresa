"""Catálogo dos avatares prontos.

Por que a lista mora aqui, e não no arquivo que o gerador escreve
-----------------------------------------------------------------
A arte é gerada por `infra/marca/gerar_avatares.py` e publicada em
`web/public/avatares/`. A API precisa da lista para **validar**: `avatar_url`
chega do cliente e vai para um `<img src>` na tela de todo mundo, então só pode
valer o que a plataforma reconhece.

Só que a imagem Docker da API não leva a pasta `web/` — ela empacota apenas
`api/`. Ler o índice de lá funcionaria em desenvolvimento e falharia em
produção, que é o pior lugar para descobrir. A lista vive aqui, dentro do que a
API empacota, e um teste garante que ela e os arquivos gerados não divergem.
"""

from __future__ import annotations

#: Identificadores dos avatares desenhados. A ordem é a que aparece na tela.
AVATARES: tuple[str, ...] = (
    "bola",
    "escudo",
    "estrela",
    "bandeira",
    "camisa",
    "trofeu",
    "coroa",
    "raio",
    "gol",
    "cronometro",
    "apito",
    "coracao",
)

#: Onde o frontend publica cada um.
PREFIXO = "/avatares/"


def url_de(identificador: str) -> str:
    return f"{PREFIXO}{identificador}.svg"


#: As URLs válidas, para a validação não montar texto a cada chamada.
URLS: frozenset[str] = frozenset(url_de(item) for item in AVATARES)


def catalogo() -> list[dict[str, str]]:
    """Lista pronta para a tela desenhar o seletor."""
    return [{"id": item, "url": url_de(item)} for item in AVATARES]
