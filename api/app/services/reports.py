"""Relatos: criar, triar e conversar.

Quem pode o quê
---------------
- **Criar**: qualquer pessoa com conta. Feedback com atrito não chega, e um
  bolão entre amigos não tem por que dificultar isso.
- **Ver tudo e triar**: só quem administra a plataforma (``is_superuser``).
- **Ver o próprio relato**: quem o criou, sempre — inclusive as respostas, que
  é o que fecha o ciclo para quem se deu ao trabalho de relatar.

Anexo herda a permissão do relato: quem não pode ver o relato não baixa a
imagem dele. Isso importa mais do que parece — captura de tela de bug carrega
palpite alheio, nome de participante e às vezes a tela inteira de outra pessoa.
"""

from __future__ import annotations

import secrets
from collections.abc import Sequence
from datetime import UTC, datetime

from sqlalchemy import func, select
from sqlalchemy.ext.asyncio import AsyncSession

from app.core.logging import get_logger
from app.models import (
    Report,
    ReportAttachment,
    ReportComment,
    ReportKind,
    ReportSeverity,
    ReportStatus,
    User,
)
from app.services import anexos as anexo_service

log = get_logger(__name__)

#: Sem vogais nem caracteres que se confundem ao ditar (0/O, 1/I/L).
ALFABETO = "23456789ABCDEFGHJKMNPQRSTVWXYZ"


class ReportError(Exception):
    """Regra de relato violada. A mensagem vai para a tela."""


class NaoAutorizado(ReportError):
    pass


async def _codigo_livre(session: AsyncSession) -> str:
    """``R-7K3M``: curto o bastante para ditar por telefone."""
    for _ in range(20):
        codigo = "R-" + "".join(secrets.choice(ALFABETO) for _ in range(4))
        if not await session.scalar(select(Report.id).where(Report.code == codigo)):
            return codigo
    # 30^4 combinações; chegar aqui significa muitos relatos ou muito azar.
    return "R-" + secrets.token_hex(4).upper()


def pode_ver(report: Report, user: User) -> bool:
    return user.is_superuser or report.reporter_id == user.id


async def pode_triar_async(session: AsyncSession, user: User) -> bool:
    """Quem tria relatos.

    Passou a ser a permissão ``relatos.triar``, e não mais "administra a
    plataforma": responder bug é trabalho que se delega — e delegar isso não
    deveria implicar em poder mexer nas contas de todo mundo.
    """
    from app.core.permissoes import Permissao
    from app.services import permissoes as permissao_service

    return await permissao_service.pode(session, user, Permissao.RELATOS_TRIAR)


def pode_triar(user: User) -> bool:
    """Versão síncrona, para onde não há sessão à mão.

    Mantida porque `is_superuser` continua sendo verdade para quem administra;
    quem tem só a permissão delegada passa pela versão assíncrona.
    """
    return bool(user.is_superuser)


async def criar(
    session: AsyncSession,
    *,
    autor: User,
    kind: ReportKind,
    title: str,
    body: str = "",
    severity: ReportSeverity = ReportSeverity.MEDIA,
    pool_id: int | None = None,
    page_url: str = "",
    user_agent: str = "",
    viewport: str = "",
    app_version: str = "",
) -> Report:
    titulo = title.strip()
    if len(titulo) < 4:
        raise ReportError("descreva em poucas palavras o que aconteceu")

    report = Report(
        code=await _codigo_livre(session),
        kind=kind,
        # Severidade só quer dizer alguma coisa em bug. Em feedback e ideia ela
        # vira ruído na triagem, então nasce baixa.
        severity=severity if kind is ReportKind.BUG else ReportSeverity.BAIXA,
        status=ReportStatus.ABERTO,
        title=titulo[:160],
        body=(body or "").strip(),
        reporter_id=autor.id,
        reporter_name=autor.display_name,
        pool_id=pool_id,
        page_url=(page_url or "")[:500],
        user_agent=(user_agent or "")[:300],
        viewport=(viewport or "")[:24],
        app_version=(app_version or "")[:40],
    )
    session.add(report)
    await session.flush()

    log.info("relato.criado", codigo=report.code, tipo=str(kind), autor=autor.id)
    return report


async def anexar(
    session: AsyncSession,
    *,
    report: Report,
    autor: User,
    dados: bytes,
    original_name: str = "",
    duration_ms: int | None = None,
) -> ReportAttachment:
    """Grava um anexo. O formato é detectado, não declarado."""
    if not pode_ver(report, autor):
        raise NaoAutorizado("este relato não é seu")

    quantos = await session.scalar(
        select(func.count())
        .select_from(ReportAttachment)
        .where(ReportAttachment.report_id == report.id)
    )
    if (quantos or 0) >= anexo_service.MAX_POR_RELATO:
        raise ReportError(
            f"um relato aceita até {anexo_service.MAX_POR_RELATO} anexos. "
            "Abra outro relato para o resto."
        )

    nome, formato = anexo_service.gravar(dados)
    anexo = ReportAttachment(
        report_id=report.id,
        storage_name=nome,
        original_name=(original_name or "")[:160],
        content_type=formato.content_type,
        size_bytes=len(dados),
        duration_ms=duration_ms if formato.kind == "audio" else None,
    )
    session.add(anexo)
    await session.flush()
    return anexo


async def listar(
    session: AsyncSession,
    *,
    user: User,
    status: ReportStatus | None = None,
    kind: ReportKind | None = None,
    limite: int = 100,
) -> Sequence[Report]:
    """Relatos que esta pessoa pode ver, do mais novo para o mais velho."""
    consulta = select(Report).order_by(Report.created_at.desc()).limit(limite)

    if not pode_triar(user):
        consulta = consulta.where(Report.reporter_id == user.id)
    if status is not None:
        consulta = consulta.where(Report.status == status)
    if kind is not None:
        consulta = consulta.where(Report.kind == kind)

    return (await session.scalars(consulta)).all()


async def obter(session: AsyncSession, *, code: str, user: User) -> Report:
    report = await session.scalar(select(Report).where(Report.code == code.strip().upper()))
    if report is None or not pode_ver(report, user):
        # Mesma resposta para "não existe" e "não é seu": senão o código vira
        # um oráculo de quais relatos existem.
        raise NaoAutorizado("relato não encontrado")
    return report


async def triar(
    session: AsyncSession,
    *,
    report: Report,
    user: User,
    status: ReportStatus | None = None,
    severity: ReportSeverity | None = None,
    resolution: str | None = None,
) -> Report:
    if not pode_triar(user):
        raise NaoAutorizado("só quem administra a plataforma pode triar relatos")

    if severity is not None:
        report.severity = severity
    if resolution is not None:
        report.resolution = resolution.strip()

    if status is not None and status is not report.status:
        report.status = status
        # A data de resolução acompanha o estado nos dois sentidos: reabrir um
        # relato tem que limpar a data, senão a lista mente sobre o que fechou.
        report.resolved_at = datetime.now(UTC) if status.is_final else None
        log.info("relato.status", codigo=report.code, para=str(status), por=user.id)

    await session.flush()
    return report


async def comentar(
    session: AsyncSession, *, report: Report, autor: User, body: str
) -> ReportComment:
    if not pode_ver(report, autor):
        raise NaoAutorizado("este relato não é seu")

    texto = (body or "").strip()
    if not texto:
        raise ReportError("comentário vazio")

    comentario = ReportComment(
        report_id=report.id,
        author_id=autor.id,
        author_name=autor.display_name,
        body=texto,
    )
    session.add(comentario)
    await session.flush()
    return comentario


async def apagar(session: AsyncSession, *, report: Report, user: User) -> None:
    """Apaga o relato e os arquivos dele.

    Só quem administra. Os anexos saem do disco junto — deixá-los ali seria
    guardar captura de tela de gente que pediu para o relato sumir.
    """
    if not pode_triar(user):
        raise NaoAutorizado("só quem administra a plataforma pode apagar relatos")

    # Consulta explícita em vez de `report.attachments`: a relação pode não
    # estar carregada nesta sessão, e um carregamento tardio aqui explodiria
    # justo no caminho que precisa limpar disco antes de apagar a linha.
    guardados = (
        await session.scalars(
            select(ReportAttachment.storage_name).where(ReportAttachment.report_id == report.id)
        )
    ).all()
    for nome in guardados:
        anexo_service.apagar(nome)

    await session.delete(report)
    await session.flush()
    log.info("relato.apagado", codigo=report.code, por=user.id)


async def resumo(session: AsyncSession) -> dict[str, int]:
    """Contagem por estado, para o painel e para o aviso no menu."""
    linhas = (
        await session.execute(select(Report.status, func.count()).group_by(Report.status))
    ).all()
    contagem = {str(status): total for status, total in linhas}
    contagem["total"] = sum(contagem.values())
    contagem["pendentes"] = sum(
        total
        for status, total in contagem.items()
        if status in {str(ReportStatus.ABERTO), str(ReportStatus.TRIADO), str(ReportStatus.FAZENDO)}
    )
    return contagem
