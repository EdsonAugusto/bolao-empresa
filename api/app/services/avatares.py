"""Foto de perfil: os avatares prontos e a imagem que a pessoa envia.

Duas origens, uma coluna
------------------------
``users.avatar_url`` guarda as duas: um dos avatares desenhados por nós
(``/avatares/bola.svg``) ou uma imagem enviada
(``/api/v1/usuarios/avatar/<nome>.png``). Guardar num campo só evita o estado
impossível de ter os dois preenchidos e ninguém saber qual vale.

Por que a coluna é validada
---------------------------
``avatar_url`` chega do cliente num `PATCH` de perfil, e o valor vai direto
para um ``<img src>`` na tela de todo mundo. Sem validação, dava para apontar
para um endereço de fora — um pixel de rastreio que registra quem abriu a lista
de pessoas, ou uma URL ``javascript:``. Só passa o que esta camada reconhece:
um avatar do catálogo, ou um arquivo que nós mesmos gravamos.

O catálogo vem de `app.data.avatares`, dentro do que a imagem da API empacota.
Ler o índice que o gerador escreve em `web/public/` funcionaria em
desenvolvimento e falharia no container, que não leva essa pasta — e produção é
o pior lugar para descobrir isso. Um teste garante que a lista de lá e os
arquivos gerados aqui não divergem.
"""

from __future__ import annotations

import uuid
from pathlib import Path

from app.core.config import settings
from app.core.logging import get_logger
from app.data.avatares import URLS as URLS_DO_CATALOGO
from app.services.anexos import AnexoInvalido, Formato, detectar

log = get_logger(__name__)

#: Teto do arquivo enviado. Bem menor que o do anexo de relato: é uma foto de
#: rosto exibida a 96 pixels, e um retrato de celular moderno passa de 5 MB sem
#: acrescentar nada visível.
MAX_BYTES = 2 * 1024 * 1024

#: Prefixo das imagens enviadas, servidas pela própria API.
PREFIXO_ENVIADO = "/api/v1/usuarios/avatar/"


class AvatarInvalido(ValueError):
    """Avatar recusado. A mensagem vai direto para quem enviou."""


def raiz() -> Path:
    """Pasta das fotos enviadas, ao lado dos anexos e separada deles.

    Separada porque a permissão é outra: anexo de relato é privado e responde
    404 para quem não pode ver; foto de perfil é vista por todo mundo que
    divide um bolão. Misturar as duas na mesma pasta pediria que a regra de
    acesso morasse no nome do arquivo.
    """
    caminho = Path(settings.uploads_dir).parent / "avatares"
    caminho.mkdir(parents=True, exist_ok=True)
    return caminho


def caminho_de(nome: str) -> Path:
    """Caminho no disco de uma foto já gravada.

    Recusa qualquer nome que não seja exatamente o que geramos — mesmo vindo do
    banco. Se um dia alguém conseguir escrever naquela coluna, a leitura não
    pode virar travessia de diretório.
    """
    if not nome or "/" in nome or "\\" in nome or ".." in nome:
        raise AvatarInvalido("nome de arquivo inválido")

    base = raiz().resolve()
    destino = (base / nome).resolve()
    if destino.parent != base:
        raise AvatarInvalido("nome de arquivo inválido")
    return destino


def gravar(dados: bytes) -> tuple[str, Formato]:
    """Grava a foto enviada e devolve ``(nome no disco, formato)``.

    O formato sai da ASSINATURA do arquivo, não do ``Content-Type`` que o
    navegador mandou — que é entrada do cliente como qualquer outra. E áudio é
    recusado aqui: o detector de anexo reconhece som porque o relato aceita
    som; foto de perfil, não.
    """
    if not dados:
        raise AvatarInvalido("arquivo vazio")
    if len(dados) > MAX_BYTES:
        raise AvatarInvalido(
            f"imagem de {len(dados) // 1024} KB passa do limite de {MAX_BYTES // 1024 // 1024} MB"
        )

    try:
        formato = detectar(dados)
    except AnexoInvalido as exc:
        raise AvatarInvalido(str(exc)) from exc

    if formato.kind != "imagem":
        raise AvatarInvalido("o arquivo precisa ser uma imagem")

    nome = f"{uuid.uuid4().hex}.{formato.extensao}"
    caminho_de(nome).write_bytes(dados)
    log.info("avatar.gravado", nome=nome, tipo=formato.content_type, bytes=len(dados))
    return nome, formato


def apagar(url: str | None) -> None:
    """Some com a foto enviada que a URL aponta. Ignora avatar do catálogo.

    Chamado quando a pessoa troca de foto: sem isso, cada troca deixaria a
    anterior no disco para sempre.
    """
    if not url or not url.startswith(PREFIXO_ENVIADO):
        return
    try:
        caminho_de(url.removeprefix(PREFIXO_ENVIADO)).unlink(missing_ok=True)
    except AvatarInvalido:
        log.warning("avatar.nome_invalido_ao_apagar", url=url)


def validar(url: str | None) -> str | None:
    """Aceita ``None``, um avatar do catálogo, ou uma foto que nós gravamos.

    Qualquer outra coisa é recusada — inclusive endereço de fora, que viraria
    um pixel de rastreio na tela de quem abre a lista de pessoas.
    """
    if url is None or url == "":
        return None

    if url in URLS_DO_CATALOGO:
        return url

    if url.startswith(PREFIXO_ENVIADO):
        nome = url.removeprefix(PREFIXO_ENVIADO)
        try:
            if caminho_de(nome).exists():
                return url
        except AvatarInvalido:
            pass
        raise AvatarInvalido("esta foto de perfil não existe mais")

    raise AvatarInvalido("escolha um dos avatares ou envie uma imagem")
