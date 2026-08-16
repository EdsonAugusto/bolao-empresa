"""Relatos, anexos e permissão.

Upload é a superfície mais perigosa da aplicação, e a maior parte destes testes
verifica **recusa**: formato que executa script, nome que escapa do diretório,
arquivo grande demais, e gente lendo relato que não é dela.

O caso de vazamento é o mais fácil de subestimar. Captura de tela de bug carrega
palpite alheio, nome de participante e às vezes a tela inteira de outra pessoa —
um anexo servido sem permissão fura a blindagem por um caminho que nenhum teste
de palpite cobriria.
"""

from __future__ import annotations

from urllib.parse import quote

import pytest
from httpx import AsyncClient
from sqlalchemy.ext.asyncio import AsyncSession

from app.core.security import create_access_token
from app.models import ReportKind, ReportSeverity, ReportStatus
from app.services import anexos as anexo_service
from app.services import reports as report_service
from tests.factories import make_user

PNG = b"\x89PNG\r\n\x1a\n" + b"\x00" * 40
JPG = b"\xff\xd8\xff\xe0" + b"\x00" * 40
WEBM = b"\x1a\x45\xdf\xa3" + b"\x00" * 40
SVG = b'<svg xmlns="http://www.w3.org/2000/svg"><script>alert(1)</script></svg>'
HTML = b"<!doctype html><script>alert(document.cookie)</script>"


async def _admin(session: AsyncSession, marca: int):
    user = await make_user(session, f"admin{marca}@casa.local", "Admin")
    user.is_superuser = True
    await session.flush()
    return user


# ---------------------------------------------------------------------------
# Detecção de formato — o tipo declarado é ignorado
# ---------------------------------------------------------------------------


def test_formatos_legitimos_sao_reconhecidos() -> None:
    assert anexo_service.detectar(PNG).content_type == "image/png"
    assert anexo_service.detectar(JPG).content_type == "image/jpeg"
    assert anexo_service.detectar(WEBM).kind == "audio"


def test_svg_e_recusado_com_o_motivo() -> None:
    """SVG é XML que executa script. Servi-lo de volta seria XSS armazenado."""
    with pytest.raises(anexo_service.AnexoInvalido, match="SVG"):
        anexo_service.detectar(SVG)


def test_html_disfarcado_de_imagem_e_recusado() -> None:
    """O navegador diz `image/png` e manda HTML. A assinatura desmente."""
    with pytest.raises(anexo_service.AnexoInvalido):
        anexo_service.detectar(HTML)


def test_arquivo_vazio_e_recusado() -> None:
    with pytest.raises(anexo_service.AnexoInvalido, match="vazio"):
        anexo_service.detectar(b"")


def test_arquivo_grande_demais_e_recusado() -> None:
    with pytest.raises(anexo_service.AnexoInvalido, match="limite"):
        anexo_service.gravar(PNG + b"\x00" * anexo_service.MAX_BYTES)


# ---------------------------------------------------------------------------
# Caminho no disco — travessia de diretório
# ---------------------------------------------------------------------------


@pytest.mark.parametrize(
    "nome",
    [
        "../../etc/passwd",
        "..\\..\\windows\\system32\\config\\sam",
        "sub/dir/arquivo.png",
        "..",
        "",
    ],
)
def test_nome_que_escapa_do_diretorio_e_recusado(nome: str) -> None:
    with pytest.raises(anexo_service.AnexoInvalido):
        anexo_service.caminho_de(nome)


def test_nome_gerado_nao_carrega_nada_do_cliente() -> None:
    """O nome no disco é nosso; a extensão sai da assinatura, não do upload."""
    nome, formato = anexo_service.gravar(PNG)
    try:
        assert nome.endswith(".png")
        assert len(nome) == len("0" * 32) + len(".png")
        assert formato.content_type == "image/png"
    finally:
        anexo_service.apagar(nome)


def test_nome_de_download_e_saneado() -> None:
    """Aspas e quebra de linha aqui permitiriam injetar cabeçalho HTTP."""
    sujo = 'foto";\r\nSet-Cookie: a=b\r\n\r\n<script>.png'
    limpo = anexo_service.nome_seguro_para_download(sujo, "png")

    assert '"' not in limpo
    assert "\r" not in limpo and "\n" not in limpo
    assert "<" not in limpo


def test_nome_vazio_ganha_um_padrao() -> None:
    assert anexo_service.nome_seguro_para_download("", "webm") == "anexo.webm"


# ---------------------------------------------------------------------------
# Criação
# ---------------------------------------------------------------------------


async def test_relato_nasce_aberto_com_codigo(db_session: AsyncSession) -> None:
    autor = await make_user(db_session, "gente1@casa.local", "Gente")

    relato = await report_service.criar(
        db_session, autor=autor, kind=ReportKind.BUG, title="O placar não atualizou"
    )

    assert relato.status is ReportStatus.ABERTO
    assert relato.code.startswith("R-")
    assert relato.reporter_name == "Gente"


async def test_titulo_curto_demais_e_recusado(db_session: AsyncSession) -> None:
    autor = await make_user(db_session, "gente2@casa.local", "Gente")

    with pytest.raises(report_service.ReportError, match="poucas palavras"):
        await report_service.criar(db_session, autor=autor, kind=ReportKind.BUG, title="ue")


async def test_gravidade_so_vale_para_bug(db_session: AsyncSession) -> None:
    """Em retorno e ideia ela viraria ruído na triagem."""
    autor = await make_user(db_session, "gente3@casa.local", "Gente")

    relato = await report_service.criar(
        db_session,
        autor=autor,
        kind=ReportKind.FEEDBACK,
        title="Gostei muito da tela nova",
        severity=ReportSeverity.CRITICA,
    )

    assert relato.severity is ReportSeverity.BAIXA


async def test_nome_de_quem_relatou_sobrevive_a_conta_apagada(
    db_session: AsyncSession,
) -> None:
    """A correção continua necessária mesmo se a pessoa sair."""
    autor = await make_user(db_session, "gente4@casa.local", "Some Depois")

    relato = await report_service.criar(
        db_session, autor=autor, kind=ReportKind.BUG, title="Um problema qualquer"
    )
    await db_session.delete(autor)
    await db_session.flush()
    await db_session.refresh(relato)

    assert relato.reporter_id is None
    assert relato.reporter_name == "Some Depois"


# ---------------------------------------------------------------------------
# Permissão
# ---------------------------------------------------------------------------


async def test_cada_um_ve_o_proprio_relato(db_session: AsyncSession) -> None:
    dono = await make_user(db_session, "dono5@casa.local", "Dono")
    outro = await make_user(db_session, "outro5@casa.local", "Outro")

    relato = await report_service.criar(
        db_session, autor=dono, kind=ReportKind.BUG, title="Só meu problema"
    )

    assert report_service.pode_ver(relato, dono) is True
    assert report_service.pode_ver(relato, outro) is False


async def test_quem_administra_ve_tudo(db_session: AsyncSession) -> None:
    dono = await make_user(db_session, "dono6@casa.local", "Dono")
    admin = await _admin(db_session, 6)

    relato = await report_service.criar(
        db_session, autor=dono, kind=ReportKind.BUG, title="Um problema qualquer"
    )

    assert report_service.pode_ver(relato, admin) is True


async def test_relato_de_outro_responde_como_inexistente(db_session: AsyncSession) -> None:
    """Mesma resposta para "não existe" e "não é seu".

    Diferenciar as duas transformaria o código em um oráculo de quais relatos
    existem na plataforma.
    """
    dono = await make_user(db_session, "dono7@casa.local", "Dono")
    outro = await make_user(db_session, "outro7@casa.local", "Outro")
    relato = await report_service.criar(
        db_session, autor=dono, kind=ReportKind.BUG, title="Um problema qualquer"
    )
    await db_session.flush()

    with pytest.raises(report_service.NaoAutorizado, match="não encontrado"):
        await report_service.obter(db_session, code=relato.code, user=outro)

    with pytest.raises(report_service.NaoAutorizado, match="não encontrado"):
        await report_service.obter(db_session, code="R-XXXX", user=outro)


async def test_so_quem_administra_tria(db_session: AsyncSession) -> None:
    dono = await make_user(db_session, "dono8@casa.local", "Dono")
    relato = await report_service.criar(
        db_session, autor=dono, kind=ReportKind.BUG, title="Um problema qualquer"
    )

    with pytest.raises(report_service.NaoAutorizado):
        await report_service.triar(
            db_session, report=relato, user=dono, status=ReportStatus.RESOLVIDO
        )


async def test_so_quem_administra_apaga(db_session: AsyncSession) -> None:
    dono = await make_user(db_session, "dono9@casa.local", "Dono")
    relato = await report_service.criar(
        db_session, autor=dono, kind=ReportKind.BUG, title="Um problema qualquer"
    )

    with pytest.raises(report_service.NaoAutorizado):
        await report_service.apagar(db_session, report=relato, user=dono)


async def test_quem_relatou_pode_comentar(db_session: AsyncSession) -> None:
    """É o que fecha o ciclo: relato sem retorno ninguém manda de novo."""
    dono = await make_user(db_session, "dono10@casa.local", "Dono")
    relato = await report_service.criar(
        db_session, autor=dono, kind=ReportKind.BUG, title="Um problema qualquer"
    )

    comentario = await report_service.comentar(
        db_session, report=relato, autor=dono, body="Voltou a acontecer hoje"
    )

    assert comentario.author_name == "Dono"


async def test_estranho_nao_comenta(db_session: AsyncSession) -> None:
    dono = await make_user(db_session, "dono11@casa.local", "Dono")
    outro = await make_user(db_session, "outro11@casa.local", "Outro")
    relato = await report_service.criar(
        db_session, autor=dono, kind=ReportKind.BUG, title="Um problema qualquer"
    )

    with pytest.raises(report_service.NaoAutorizado):
        await report_service.comentar(db_session, report=relato, autor=outro, body="oi")


# ---------------------------------------------------------------------------
# Triagem
# ---------------------------------------------------------------------------


async def test_resolver_carimba_a_data(db_session: AsyncSession) -> None:
    dono = await make_user(db_session, "dono12@casa.local", "Dono")
    admin = await _admin(db_session, 12)
    relato = await report_service.criar(
        db_session, autor=dono, kind=ReportKind.BUG, title="Um problema qualquer"
    )

    await report_service.triar(db_session, report=relato, user=admin, status=ReportStatus.RESOLVIDO)

    assert relato.resolved_at is not None


async def test_reabrir_limpa_a_data(db_session: AsyncSession) -> None:
    """Sem isso a lista mente sobre o que realmente fechou."""
    dono = await make_user(db_session, "dono13@casa.local", "Dono")
    admin = await _admin(db_session, 13)
    relato = await report_service.criar(
        db_session, autor=dono, kind=ReportKind.BUG, title="Um problema qualquer"
    )

    await report_service.triar(db_session, report=relato, user=admin, status=ReportStatus.RESOLVIDO)
    await report_service.triar(db_session, report=relato, user=admin, status=ReportStatus.FAZENDO)

    assert relato.resolved_at is None


# ---------------------------------------------------------------------------
# Anexos
# ---------------------------------------------------------------------------


async def test_anexo_guarda_o_tipo_detectado(db_session: AsyncSession) -> None:
    dono = await make_user(db_session, "dono14@casa.local", "Dono")
    relato = await report_service.criar(
        db_session, autor=dono, kind=ReportKind.BUG, title="Um problema qualquer"
    )

    anexo = await report_service.anexar(
        db_session, report=relato, autor=dono, dados=PNG, original_name="print.jpg"
    )
    try:
        # O nome dizia jpg; a assinatura diz png. Vale a assinatura.
        assert anexo.content_type == "image/png"
        assert anexo.original_name == "print.jpg"
        assert anexo.storage_name.endswith(".png")
    finally:
        anexo_service.apagar(anexo.storage_name)


async def test_audio_guarda_a_duracao(db_session: AsyncSession) -> None:
    dono = await make_user(db_session, "dono15@casa.local", "Dono")
    relato = await report_service.criar(
        db_session, autor=dono, kind=ReportKind.BUG, title="Um problema qualquer"
    )

    anexo = await report_service.anexar(
        db_session, report=relato, autor=dono, dados=WEBM, duration_ms=12_000
    )
    try:
        assert anexo.content_type == "audio/webm"
        assert anexo.duration_ms == 12_000
    finally:
        anexo_service.apagar(anexo.storage_name)


async def test_duracao_e_ignorada_em_imagem(db_session: AsyncSession) -> None:
    dono = await make_user(db_session, "dono16@casa.local", "Dono")
    relato = await report_service.criar(
        db_session, autor=dono, kind=ReportKind.BUG, title="Um problema qualquer"
    )

    anexo = await report_service.anexar(
        db_session, report=relato, autor=dono, dados=PNG, duration_ms=999
    )
    try:
        assert anexo.duration_ms is None
    finally:
        anexo_service.apagar(anexo.storage_name)


async def test_estranho_nao_anexa(db_session: AsyncSession) -> None:
    dono = await make_user(db_session, "dono17@casa.local", "Dono")
    outro = await make_user(db_session, "outro17@casa.local", "Outro")
    relato = await report_service.criar(
        db_session, autor=dono, kind=ReportKind.BUG, title="Um problema qualquer"
    )

    with pytest.raises(report_service.NaoAutorizado):
        await report_service.anexar(db_session, report=relato, autor=outro, dados=PNG)


async def test_limite_de_anexos_por_relato(db_session: AsyncSession) -> None:
    dono = await make_user(db_session, "dono18@casa.local", "Dono")
    relato = await report_service.criar(
        db_session, autor=dono, kind=ReportKind.BUG, title="Um problema qualquer"
    )

    gravados = []
    try:
        for _ in range(anexo_service.MAX_POR_RELATO):
            gravados.append(
                await report_service.anexar(db_session, report=relato, autor=dono, dados=PNG)
            )

        with pytest.raises(report_service.ReportError, match="até"):
            await report_service.anexar(db_session, report=relato, autor=dono, dados=PNG)
    finally:
        for anexo in gravados:
            anexo_service.apagar(anexo.storage_name)


async def test_apagar_relato_leva_o_arquivo_do_disco(db_session: AsyncSession) -> None:
    """Deixar o arquivo seria guardar captura de tela de quem pediu para sumir."""
    dono = await make_user(db_session, "dono19@casa.local", "Dono")
    admin = await _admin(db_session, 19)
    relato = await report_service.criar(
        db_session, autor=dono, kind=ReportKind.BUG, title="Um problema qualquer"
    )
    anexo = await report_service.anexar(db_session, report=relato, autor=dono, dados=PNG)
    caminho = anexo_service.caminho_de(anexo.storage_name)
    assert caminho.exists()

    await report_service.apagar(db_session, report=relato, user=admin)

    assert not caminho.exists()


# ---------------------------------------------------------------- cabeçalhos


@pytest.mark.parametrize(
    "original",
    [
        "Скриншот da tela.png",
        "日本語.png",
        "captura ação.png",
        "relatório — bug.png",
        "🐞.png",
        "",
    ],
)
def test_nome_de_download_sempre_cabe_num_cabecalho_http(original: str) -> None:
    """Cabeçalho HTTP é latin-1. Nome de arquivo é o que a pessoa quiser.

    ``isalnum()`` diz sim para cirílico e CJK, então sanear só por ele deixava
    passar caractere que estoura ao serializar a resposta — erro 500 na hora de
    baixar, e não recusa na hora de enviar.
    """
    anexo_service.cabecalho_de_download(original, "png").encode("latin-1")


def test_nome_de_download_preserva_o_original_em_filename_estrela() -> None:
    cabecalho = anexo_service.cabecalho_de_download("Скриншот.png", "png")
    assert 'filename="anexo.png"' in cabecalho
    assert "filename*=UTF-8''" in cabecalho
    assert quote("Скриншот.png", safe="") in cabecalho


def test_nome_de_download_nao_deixa_injetar_cabecalho() -> None:
    sujo = 'a";' + chr(13) + chr(10) + "X-Vaza: 1;.png"
    cabecalho = anexo_service.cabecalho_de_download(sujo, "png")
    assert chr(13) not in cabecalho
    assert chr(10) not in cabecalho
    assert "X-Vaza: 1" not in cabecalho
    cabecalho.encode("latin-1")


def test_nome_de_download_sem_original_usa_a_extensao_detectada() -> None:
    assert anexo_service.cabecalho_de_download("", "webm") == 'inline; filename="anexo.webm"'
    assert anexo_service.cabecalho_de_download("...", "png") == 'inline; filename="anexo.png"'


def test_caminho_recusa_irmao_com_prefixo_igual() -> None:
    """``/data/uploads-outro`` começa com ``/data/uploads``: comparar texto passava."""
    with pytest.raises(anexo_service.AnexoInvalido):
        anexo_service.caminho_de("../uploads-outro/x.png")


@pytest.mark.asyncio
async def test_baixar_anexo_com_nome_cirilico_nao_derruba_a_resposta(
    client: AsyncClient, db_session: AsyncSession
) -> None:
    """Upload a download, com nome de arquivo fora do latin-1."""
    admin = await _admin(db_session, 900)
    relato = await report_service.criar(
        db_session, autor=admin, kind=ReportKind.BUG, title="Nome em cirilico"
    )
    await db_session.commit()

    cabecalho = {"Authorization": f"Bearer {create_access_token(admin.id)}"}
    resposta = await client.post(
        f"/api/v1/reports/{relato.code}/attachments",
        headers=cabecalho,
        files={"file": ("Скриншот.png", PNG, "image/png")},
    )
    assert resposta.status_code == 200
    anexo_id = resposta.json()["attachments"][-1]["id"]

    baixada = await client.get(f"/api/v1/reports/attachments/{anexo_id}", headers=cabecalho)
    assert baixada.status_code == 200
    assert baixada.headers["content-type"] == "image/png"
    assert "filename*=UTF-8''" in baixada.headers["content-disposition"]
