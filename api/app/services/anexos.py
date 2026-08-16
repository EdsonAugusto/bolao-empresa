"""Guarda de anexo em disco.

Upload é a superfície mais perigosa de uma aplicação web, e quase todo furo
conhecido cabe em três frases:

1. **Confiar no nome do arquivo.** ``../../etc/passwd`` ou ``..\\..\\`` escapam
   do diretório. Aqui o nome no disco é gerado por nós — o nome original só
   existe para mostrar na tela, e nunca entra num caminho.
2. **Confiar no tipo declarado.** O navegador diz ``image/png`` e manda um
   HTML; servido de volta com aquele tipo, vira XSS armazenado na sessão de
   quem abrir. Aqui o tipo sai da **assinatura do arquivo**, não do cabeçalho.
3. **Servir com o tipo errado.** SVG é imagem e executa script. Não entra.

O que é aceito está numa lista curta e fechada, com a assinatura de cada
formato. O que não bater é recusado com o motivo — nada de "erro ao enviar".
"""

from __future__ import annotations

import uuid
from dataclasses import dataclass
from pathlib import Path
from urllib.parse import quote

from app.core.config import settings
from app.core.logging import get_logger

log = get_logger(__name__)

#: Teto por arquivo. O nginx corta em 8 MB antes de chegar aqui; este é o
#: limite da aplicação, para o caso de alguém falar direto com a API.
MAX_BYTES = 8 * 1024 * 1024

#: Teto por relato. Segura o disco de encher com um relato só.
MAX_POR_RELATO = 5


@dataclass(frozen=True, slots=True)
class Formato:
    content_type: str
    extensao: str
    kind: str
    """``imagem`` ou ``audio``, para a tela saber como exibir."""


class AnexoInvalido(ValueError):
    """Arquivo recusado. A mensagem vai direto para quem enviou."""


def _assinatura_webm_ou_mkv(dados: bytes) -> bool:
    return dados[:4] == b"\x1a\x45\xdf\xa3"


def _assinatura_mp4(dados: bytes) -> bool:
    return len(dados) >= 12 and dados[4:8] == b"ftyp"


def _assinatura_ogg(dados: bytes) -> bool:
    return dados[:4] == b"OggS"


def _assinatura_wav(dados: bytes) -> bool:
    return len(dados) >= 12 and dados[:4] == b"RIFF" and dados[8:12] == b"WAVE"


def _assinatura_mp3(dados: bytes) -> bool:
    # ID3 ou frame MPEG sincronizado.
    return dados[:3] == b"ID3" or dados[:2] in (b"\xff\xfb", b"\xff\xf3", b"\xff\xf2")


#: Formatos aceitos, com o teste de assinatura de cada um. A ordem importa: o
#: primeiro que reconhecer vence.
#:
#: **SVG não está aqui e não vai estar.** É XML que executa script; servi-lo de
#: volta para quem abre o relato seria XSS na própria sessão de quem administra.
FORMATOS: tuple[tuple[Formato, object], ...] = (
    (Formato("image/png", "png", "imagem"), lambda d: d[:8] == b"\x89PNG\r\n\x1a\n"),
    (Formato("image/jpeg", "jpg", "imagem"), lambda d: d[:3] == b"\xff\xd8\xff"),
    (Formato("image/gif", "gif", "imagem"), lambda d: d[:6] in (b"GIF87a", b"GIF89a")),
    (
        Formato("image/webp", "webp", "imagem"),
        lambda d: len(d) >= 12 and d[:4] == b"RIFF" and d[8:12] == b"WEBP",
    ),
    (Formato("audio/webm", "webm", "audio"), _assinatura_webm_ou_mkv),
    (Formato("audio/ogg", "ogg", "audio"), _assinatura_ogg),
    (Formato("audio/mp4", "m4a", "audio"), _assinatura_mp4),
    (Formato("audio/wav", "wav", "audio"), _assinatura_wav),
    (Formato("audio/mpeg", "mp3", "audio"), _assinatura_mp3),
)


def detectar(dados: bytes) -> Formato:
    """Descobre o formato pela assinatura. Recusa o que não reconhecer.

    O ``Content-Type`` que o navegador manda é ignorado de propósito: é entrada
    do cliente como qualquer outra, e é justamente por confiar nele que upload
    vira XSS armazenado.
    """
    if not dados:
        raise AnexoInvalido("arquivo vazio")

    for formato, reconhece in FORMATOS:
        if reconhece(dados):  # type: ignore[operator]
            return formato

    aceitos = ", ".join(sorted({item.extensao for item, _ in FORMATOS}))
    raise AnexoInvalido(
        f"formato não reconhecido. Aceito: {aceitos}. "
        "SVG não é aceito por ser um formato que executa script."
    )


def raiz() -> Path:
    """Pasta dos anexos, criada na primeira vez que for usada."""
    caminho = Path(settings.uploads_dir)
    caminho.mkdir(parents=True, exist_ok=True)
    return caminho


def caminho_de(storage_name: str) -> Path:
    """Caminho no disco de um anexo já gravado.

    Recusa qualquer nome que não seja exatamente o que geramos. Mesmo vindo do
    banco: se um dia alguém conseguir escrever nessa coluna, a leitura não pode
    virar travessia de diretório.
    """
    if not storage_name or "/" in storage_name or "\\" in storage_name or ".." in storage_name:
        raise AnexoInvalido("nome de armazenamento inválido")

    # `is_relative_to` compara componente a componente. Prefixo de texto não
    # serve: `/data/uploads-outro` começa com `/data/uploads` e passaria.
    base = raiz().resolve()
    destino = (base / storage_name).resolve()
    if destino.parent != base:
        raise AnexoInvalido("nome de armazenamento inválido")
    return destino


def gravar(dados: bytes) -> tuple[str, Formato]:
    """Grava e devolve ``(nome no disco, formato detectado)``.

    O nome é um UUID mais a extensão que **nós** decidimos a partir da
    assinatura. Nada do que o cliente mandou entra nele.
    """
    if len(dados) > MAX_BYTES:
        raise AnexoInvalido(
            f"arquivo de {len(dados) // 1024} KB passa do limite de {MAX_BYTES // 1024 // 1024} MB"
        )

    formato = detectar(dados)
    nome = f"{uuid.uuid4().hex}.{formato.extensao}"
    destino = caminho_de(nome)
    destino.write_bytes(dados)

    log.info("anexo.gravado", nome=nome, tipo=formato.content_type, bytes=len(dados))
    return nome, formato


def apagar(storage_name: str) -> None:
    """Some com o arquivo. Silencioso se ele já não existe."""
    try:
        caminho_de(storage_name).unlink(missing_ok=True)
    except AnexoInvalido:
        log.warning("anexo.nome_invalido_ao_apagar", nome=storage_name)


def nome_seguro_para_download(original: str, formato_extensao: str) -> str:
    """Nome de exibição saneado, para o cabeçalho de download.

    Aspas e quebras de linha aqui permitiriam injetar cabeçalho HTTP; caminho
    permitiria sugerir onde salvar. Sobra letra, número, ponto, hífen e espaço —
    **e só em ASCII**.

    O corte em ASCII não é frescura: ``str.isalnum()`` diz sim para ``Скриншот``
    e ``日本語``, mas cabeçalho HTTP é latin-1. Deixar passar transforma um nome
    de arquivo em cirílico num erro 500 na hora de baixar. O nome de verdade
    volta inteiro em ``filename*``, que é justamente para isso.
    """
    base = (original or "").rsplit(".", 1)[0]
    limpo = "".join(
        letra for letra in base if letra.isascii() and (letra.isalnum() or letra in " ._-")
    ).strip(" .")[:60]

    # A extensão vem sempre da assinatura que detectamos, nunca do que veio
    # junto: é ela que descreve o que o arquivo realmente é.
    return f"{limpo or 'anexo'}.{formato_extensao}"


def cabecalho_de_download(original: str, formato_extensao: str) -> str:
    """Valor do ``Content-Disposition``, nos dois formatos da RFC 6266.

    ``filename`` é o ASCII que todo navegador entende; ``filename*`` carrega o
    nome original em UTF-8 percent-encoded, e é o que os navegadores atuais
    preferem. Quem mandou ``Captura ação 日本.png`` recebe o nome de volta como
    mandou, sem que um caractere fora do latin-1 derrube a resposta.
    """
    ascii_puro = nome_seguro_para_download(original, formato_extensao)
    cabecalho = f'inline; filename="{ascii_puro}"'

    # `filename*` só entra quando há o que recuperar: nome que já é ASCII puro
    # não ganha nada com ele, e nome que virou lixo ao sanear não tem o que
    # oferecer de volta.
    completo = (original or "").strip()[:120]
    if completo and not completo.isascii():
        # `quote` sem caracteres seguros: aspas, ponto-e-vírgula e quebra de
        # linha saem percent-encoded, então não há o que injetar aqui.
        cabecalho += f"; filename*=UTF-8''{quote(completo, safe='')}"
    return cabecalho
