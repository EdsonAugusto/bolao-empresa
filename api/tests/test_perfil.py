"""Foto de perfil, time do coração, títulos e senha redefinida por terceiro.

O que estes testes protegem
---------------------------
``avatar_url`` chega do cliente e vai para um ``<img src>`` na tela de todo
mundo. Sem trava, dava para apontar para fora — um pixel que registra quem
abriu a lista de pessoas, ou uma URL ``javascript:``.

E a senha redefinida por quem administra é conhecida por um terceiro. Se ela
continuasse valendo, um socorro ("perdi minha senha") viraria conta
compartilhada sem ninguém perceber.
"""

from __future__ import annotations

from pathlib import Path

import pytest
from sqlalchemy.ext.asyncio import AsyncSession

from app.data.avatares import AVATARES, URLS, url_de
from app.services import auth as auth_service
from app.services import avatares as avatar_service
from tests.factories import make_user

# ---------------------------------------------------------------------------
# Catálogo
# ---------------------------------------------------------------------------


def test_catalogo_bate_com_os_arquivos_gerados() -> None:
    """A lista que a API valida e a arte que o frontend publica são dois
    arquivos diferentes, mantidos à mão em lugares diferentes.

    A lista vive em `app/data` porque a imagem Docker da API não leva a pasta
    `web/` — ler o índice de lá funcionaria em desenvolvimento e falharia em
    produção. O preço dessa separação é poderem divergir, e é isto que este
    teste cobra.
    """
    publicados = Path(__file__).resolve().parents[2] / "web" / "public" / "avatares"
    if not publicados.is_dir():
        pytest.skip("arte dos avatares não está neste checkout")

    no_disco = {arquivo.stem for arquivo in publicados.glob("*.svg")}
    assert no_disco == set(AVATARES), (
        "o catálogo de app/data/avatares.py e os arquivos de web/public/avatares/ "
        "divergiram; rode infra/marca/gerar_avatares.py"
    )


def test_toda_url_do_catalogo_e_reconhecida() -> None:
    for identificador in AVATARES:
        assert avatar_service.validar(url_de(identificador)) == url_de(identificador)


# ---------------------------------------------------------------------------
# Validação do avatar
# ---------------------------------------------------------------------------


@pytest.mark.parametrize(
    "endereco",
    [
        "https://exemplo.com/pixel.png",
        "//exemplo.com/pixel.png",
        "javascript:alert(1)",
        "/avatares/../../etc/senha",
        "/api/v1/usuarios/avatar/../../../etc/senha",
        "data:image/png;base64,AAAA",
    ],
)
def test_endereco_de_fora_e_recusado(endereco: str) -> None:
    """Um endereço externo aqui vira pixel de rastreio na tela dos outros."""
    with pytest.raises(avatar_service.AvatarInvalido):
        avatar_service.validar(endereco)


def test_avatar_vazio_significa_sem_foto() -> None:
    assert avatar_service.validar(None) is None
    assert avatar_service.validar("") is None


def test_foto_que_nao_existe_mais_e_recusada() -> None:
    """Referência a arquivo apagado não pode virar imagem quebrada em toda
    tela onde a pessoa aparece."""
    with pytest.raises(avatar_service.AvatarInvalido, match="não existe mais"):
        avatar_service.validar(f"{avatar_service.PREFIXO_ENVIADO}naoexiste.png")


# ---------------------------------------------------------------------------
# Upload
# ---------------------------------------------------------------------------

#: PNG de 1x1 de verdade, gerado e colado aqui. O detector só olha a
#: assinatura, mas um arquivo bem formado evita que alguém leia este teste
#: e conclua que a plataforma aceita lixo com cabeçalho certo.
PNG_MINIMO = bytes.fromhex(
    "89504e470d0a1a0a0000000d49484452000000010000000108060000001f"
    "15c4890000000d49444154789c6360a8f2fd0f00030c01c7c27d30100000"
    "000049454e44ae426082"
)


def test_upload_de_imagem_grava_e_valida() -> None:
    nome, formato = avatar_service.gravar(PNG_MINIMO)
    try:
        assert formato.kind == "imagem"
        assert avatar_service.caminho_de(nome).is_file()
        url = f"{avatar_service.PREFIXO_ENVIADO}{nome}"
        assert avatar_service.validar(url) == url
    finally:
        avatar_service.apagar(f"{avatar_service.PREFIXO_ENVIADO}{nome}")


def test_upload_recusa_o_que_nao_e_imagem() -> None:
    """O detector é compartilhado com o anexo de relato, que aceita áudio.
    Foto de perfil, não — e um `.exe` renomeado para `.png` também não passa,
    porque o formato sai da assinatura e não do nome."""
    with pytest.raises(avatar_service.AvatarInvalido):
        avatar_service.gravar(b"MZ\x90\x00isto e um executavel")


def test_upload_recusa_arquivo_grande() -> None:
    with pytest.raises(avatar_service.AvatarInvalido, match="passa do limite"):
        avatar_service.gravar(b"\x89PNG\r\n\x1a\n" + b"0" * (avatar_service.MAX_BYTES + 1))


def test_apagar_ignora_avatar_do_catalogo() -> None:
    """Trocar de avatar chama `apagar` no anterior. Se o anterior era um dos
    desenhados, apagá-lo tiraria a opção de todo mundo."""
    avatar_service.apagar(next(iter(URLS)))
    publicados = Path(__file__).resolve().parents[2] / "web" / "public" / "avatares"
    if publicados.is_dir():
        assert list(publicados.glob("*.svg")), "o gerador não foi apagado por engano"


# ---------------------------------------------------------------------------
# Senha redefinida por quem administra
# ---------------------------------------------------------------------------


async def test_trocar_a_propria_senha_desarma_a_obrigacao(db_session: AsyncSession) -> None:
    """A obrigação existe porque um TERCEIRO escolheu a senha. Quando a dona
    escolhe a dela, ela some — senão a pessoa ficaria presa no perfil para
    sempre."""
    user = await make_user(db_session, "troca@teste.local", "Pessoa", password="senha-antiga")
    user.must_change_password = True
    await db_session.flush()

    await auth_service.change_password(
        db_session, user, current_password="senha-antiga", new_password="senha-nova-longa"
    )

    assert user.must_change_password is False


async def test_senha_atual_errada_nao_troca_nem_desarma(db_session: AsyncSession) -> None:
    user = await make_user(db_session, "errada@teste.local", "Pessoa", password="senha-antiga")
    user.must_change_password = True
    hash_antes = user.password_hash
    await db_session.flush()

    with pytest.raises(auth_service.AuthError):
        await auth_service.change_password(
            db_session, user, current_password="chute", new_password="senha-nova-longa"
        )

    assert user.must_change_password is True
    assert user.password_hash == hash_antes
