"""Relatos: bug, feedback e ideia.

O endpoint de download é o que merece atenção. Anexo de relato é conteúdo que
veio de fora, e servi-lo de volta com o tipo errado transforma upload em XSS na
sessão de quem administra — que é justamente quem abre todos os relatos. Por
isso ele responde com o tipo que **nós** detectamos, com ``nosniff``, e com uma
política de conteúdo que proíbe execução de script naquela resposta.
"""

from __future__ import annotations

from datetime import datetime
from typing import Annotated

from fastapi import APIRouter, File, Form, HTTPException, Query, Response, UploadFile, status
from pydantic import BaseModel, Field
from sqlalchemy import select

from app.api.deps import CurrentUser, SessionDep
from app.core.logging import get_logger
from app.models import Report, ReportAttachment, ReportKind, ReportSeverity, ReportStatus
from app.schemas.common import Message
from app.services import anexos as anexo_service
from app.services import reports as report_service

log = get_logger(__name__)

router = APIRouter(prefix="/reports", tags=["relatos"])


class AnexoOut(BaseModel):
    id: int
    kind: str
    content_type: str
    original_name: str
    size_bytes: int
    duration_ms: int | None
    created_at: datetime


class ComentarioOut(BaseModel):
    id: int
    author_name: str
    body: str
    created_at: datetime


class RelatoOut(BaseModel):
    code: str
    kind: str
    severity: str
    status: str
    title: str
    body: str
    reporter_name: str
    page_url: str
    user_agent: str
    viewport: str
    app_version: str
    resolution: str
    created_at: datetime
    resolved_at: datetime | None
    attachments: list[AnexoOut]
    comments: list[ComentarioOut]
    can_triage: bool


class RelatoResumo(BaseModel):
    code: str
    kind: str
    severity: str
    status: str
    title: str
    reporter_name: str
    created_at: datetime
    attachments: int


class RelatoIn(BaseModel):
    kind: str = Field(default="bug")
    title: str = Field(min_length=4, max_length=160)
    body: str = Field(default="", max_length=8000)
    severity: str = Field(default="media")
    pool_id: int | None = None
    page_url: str = Field(default="", max_length=500)
    user_agent: str = Field(default="", max_length=300)
    viewport: str = Field(default="", max_length=24)
    app_version: str = Field(default="", max_length=40)


class TriagemIn(BaseModel):
    status: str | None = None
    severity: str | None = None
    resolution: str | None = Field(default=None, max_length=8000)


class ComentarioIn(BaseModel):
    body: str = Field(min_length=1, max_length=4000)


def _tipo_de(anexo: ReportAttachment) -> str:
    return "audio" if anexo.content_type.startswith("audio/") else "imagem"


def _saida(report: Report, *, pode_triar: bool) -> RelatoOut:
    return RelatoOut(
        code=report.code,
        kind=str(report.kind),
        severity=str(report.severity),
        status=str(report.status),
        title=report.title,
        body=report.body,
        reporter_name=report.reporter_name,
        page_url=report.page_url,
        user_agent=report.user_agent,
        viewport=report.viewport,
        app_version=report.app_version,
        resolution=report.resolution,
        created_at=report.created_at,
        resolved_at=report.resolved_at,
        attachments=[
            AnexoOut(
                id=anexo.id,
                kind=_tipo_de(anexo),
                content_type=anexo.content_type,
                original_name=anexo.original_name,
                size_bytes=anexo.size_bytes,
                duration_ms=anexo.duration_ms,
                created_at=anexo.created_at,
            )
            for anexo in sorted(report.attachments, key=lambda item: item.id)
        ],
        comments=[
            ComentarioOut(
                id=item.id,
                author_name=item.author_name,
                body=item.body,
                created_at=item.created_at,
            )
            for item in sorted(report.comments, key=lambda item: item.id)
        ],
        can_triage=pode_triar,
    )


def _enum(valor: str | None, tipo, campo: str):  # type: ignore[no-untyped-def]
    if valor is None:
        return None
    try:
        return tipo(valor)
    except ValueError as exc:
        aceitos = ", ".join(str(item) for item in tipo)
        raise HTTPException(
            status_code=status.HTTP_422_UNPROCESSABLE_ENTITY,
            detail=f"{campo} inválido. Aceito: {aceitos}",
        ) from exc


@router.get("", response_model=list[RelatoResumo])
async def listar(
    session: SessionDep,
    user: CurrentUser,
    estado: str | None = Query(default=None, description="aberto, triado, fazendo…"),
    tipo: str | None = Query(default=None, description="bug, feedback, ideia"),
) -> list[RelatoResumo]:
    """Relatos que você pode ver. Quem administra vê todos; os demais, os seus."""
    achados = await report_service.listar(
        session,
        user=user,
        status=_enum(estado, ReportStatus, "estado"),
        kind=_enum(tipo, ReportKind, "tipo"),
    )
    return [
        RelatoResumo(
            code=item.code,
            kind=str(item.kind),
            severity=str(item.severity),
            status=str(item.status),
            title=item.title,
            reporter_name=item.reporter_name,
            created_at=item.created_at,
            attachments=len(item.attachments),
        )
        for item in achados
    ]


@router.get("/resumo")
async def resumo(session: SessionDep, user: CurrentUser) -> dict:
    """Contagem por estado. Vazio para quem não administra."""
    if not report_service.pode_triar(user):
        return {}
    return await report_service.resumo(session)


@router.post("", response_model=RelatoOut, status_code=status.HTTP_201_CREATED)
async def criar(payload: RelatoIn, session: SessionDep, user: CurrentUser) -> RelatoOut:
    """Abre um relato. Os anexos vão depois, pelo código."""
    try:
        report = await report_service.criar(
            session,
            autor=user,
            kind=_enum(payload.kind, ReportKind, "tipo") or ReportKind.BUG,
            title=payload.title,
            body=payload.body,
            severity=_enum(payload.severity, ReportSeverity, "gravidade") or ReportSeverity.MEDIA,
            pool_id=payload.pool_id,
            page_url=payload.page_url,
            user_agent=payload.user_agent,
            viewport=payload.viewport,
            app_version=payload.app_version,
        )
    except report_service.ReportError as exc:
        raise HTTPException(status_code=status.HTTP_400_BAD_REQUEST, detail=str(exc)) from exc

    await session.commit()
    await session.refresh(report)
    return _saida(report, pode_triar=report_service.pode_triar(user))


@router.get("/{code}", response_model=RelatoOut)
async def detalhe(code: str, session: SessionDep, user: CurrentUser) -> RelatoOut:
    report = await _obter(session, code, user)
    return _saida(report, pode_triar=report_service.pode_triar(user))


@router.patch("/{code}", response_model=RelatoOut)
async def triar(code: str, payload: TriagemIn, session: SessionDep, user: CurrentUser) -> RelatoOut:
    """Muda estado, gravidade e o que foi feito. Só quem administra."""
    report = await _obter(session, code, user)
    try:
        await report_service.triar(
            session,
            report=report,
            user=user,
            status=_enum(payload.status, ReportStatus, "estado"),
            severity=_enum(payload.severity, ReportSeverity, "gravidade"),
            resolution=payload.resolution,
        )
    except report_service.NaoAutorizado as exc:
        raise HTTPException(status_code=status.HTTP_403_FORBIDDEN, detail=str(exc)) from exc

    await session.commit()
    await session.refresh(report)
    return _saida(report, pode_triar=True)


@router.delete("/{code}", response_model=Message)
async def apagar(code: str, session: SessionDep, user: CurrentUser) -> Message:
    report = await _obter(session, code, user)
    try:
        await report_service.apagar(session, report=report, user=user)
    except report_service.NaoAutorizado as exc:
        raise HTTPException(status_code=status.HTTP_403_FORBIDDEN, detail=str(exc)) from exc

    await session.commit()
    return Message(detail=f"Relato {code} apagado.")


@router.post("/{code}/comments", response_model=RelatoOut)
async def comentar(
    code: str, payload: ComentarioIn, session: SessionDep, user: CurrentUser
) -> RelatoOut:
    report = await _obter(session, code, user)
    try:
        await report_service.comentar(session, report=report, autor=user, body=payload.body)
    except report_service.ReportError as exc:
        raise HTTPException(status_code=status.HTTP_400_BAD_REQUEST, detail=str(exc)) from exc

    await session.commit()
    await session.refresh(report)
    return _saida(report, pode_triar=report_service.pode_triar(user))


@router.post("/{code}/attachments", response_model=RelatoOut)
async def anexar(
    code: str,
    session: SessionDep,
    user: CurrentUser,
    file: Annotated[UploadFile, File(description="Imagem ou áudio")],
    duration_ms: Annotated[int | None, Form()] = None,
) -> RelatoOut:
    """Anexa imagem ou áudio.

    O tipo é detectado pela assinatura do arquivo; o ``Content-Type`` que o
    navegador declara é ignorado. SVG é recusado.
    """
    report = await _obter(session, code, user)

    # Lê com teto: sem isso, um cliente mal-intencionado ocupa memória do
    # processo antes de qualquer validação acontecer.
    dados = await file.read(anexo_service.MAX_BYTES + 1)
    if len(dados) > anexo_service.MAX_BYTES:
        raise HTTPException(
            status_code=status.HTTP_413_REQUEST_ENTITY_TOO_LARGE,
            detail=f"limite de {anexo_service.MAX_BYTES // 1024 // 1024} MB por arquivo",
        )

    try:
        await report_service.anexar(
            session,
            report=report,
            autor=user,
            dados=dados,
            original_name=file.filename or "",
            duration_ms=duration_ms,
        )
    except anexo_service.AnexoInvalido as exc:
        raise HTTPException(
            status_code=status.HTTP_422_UNPROCESSABLE_ENTITY, detail=str(exc)
        ) from exc
    except report_service.ReportError as exc:
        raise HTTPException(status_code=status.HTTP_400_BAD_REQUEST, detail=str(exc)) from exc

    await session.commit()
    await session.refresh(report)
    return _saida(report, pode_triar=report_service.pode_triar(user))


@router.get(
    "/attachments/{anexo_id}",
    response_class=Response,
    responses={200: {"content": {"application/octet-stream": {}}}},
)
async def baixar(anexo_id: int, session: SessionDep, user: CurrentUser) -> Response:
    """Serve o anexo, com autenticação e sem deixar o navegador executar nada.

    Captura de tela de bug carrega palpite alheio, nome de participante e às
    vezes a tela inteira de outra pessoa — por isso a permissão é a mesma do
    relato, e não existe caminho público.
    """
    anexo = await session.get(ReportAttachment, anexo_id)
    if anexo is None:
        raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail="anexo não encontrado")

    report = await session.get(Report, anexo.report_id)
    if report is None or not report_service.pode_ver(report, user):
        raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail="anexo não encontrado")

    try:
        caminho = anexo_service.caminho_de(anexo.storage_name)
        conteudo = caminho.read_bytes()
    except (anexo_service.AnexoInvalido, OSError) as exc:
        log.warning("anexo.ilegivel", anexo=anexo_id, erro=str(exc))
        raise HTTPException(
            status_code=status.HTTP_404_NOT_FOUND, detail="arquivo do anexo sumiu do disco"
        ) from exc

    extensao = anexo.content_type.rsplit("/", 1)[-1]
    disposicao = anexo_service.cabecalho_de_download(anexo.original_name, extensao)

    return Response(
        content=conteudo,
        # O tipo é o que **nós** detectamos na gravação, nunca o declarado.
        media_type=anexo.content_type,
        headers={
            # `nosniff` impede o navegador de reinterpretar o conteúdo como
            # HTML; a política de conteúdo garante que, mesmo se ele o fizer,
            # nada executa. As duas juntas, porque cada uma cobre um furo.
            "X-Content-Type-Options": "nosniff",
            "Content-Security-Policy": "default-src 'none'; sandbox",
            "Content-Disposition": disposicao,
            "Cache-Control": "private, max-age=3600",
        },
    )


@router.get("/export/markdown", response_class=Response)
async def exportar(session: SessionDep, user: CurrentUser) -> Response:
    """Todos os relatos em Markdown, para levar direto para o trabalho.

    Existe para fechar o ciclo que o produto se propõe: os relatos saem daqui
    num formato que se cola numa conversa ou num editor sem reformatar nada.
    """
    if not report_service.pode_triar(user):
        raise HTTPException(
            status_code=status.HTTP_403_FORBIDDEN,
            detail="só quem administra a plataforma exporta relatos",
        )

    relatos = (await session.scalars(select(Report).order_by(Report.created_at.desc()))).all()

    linhas = [f"# Relatos da plataforma ({len(relatos)})", ""]
    for item in relatos:
        linhas += [
            f"## {item.code} — {item.title}",
            "",
            f"- **Tipo**: {item.kind} · **Gravidade**: {item.severity} · **Estado**: {item.status}",
            f"- **Quem**: {item.reporter_name or '—'} em {item.created_at:%d/%m/%Y %H:%M} UTC",
            f"- **Tela**: {item.page_url or '—'}",
            f"- **Aparelho**: {item.viewport or '—'} · {item.user_agent or '—'}",
            "",
        ]
        if item.body:
            linhas += [item.body, ""]
        if item.attachments:
            linhas.append(
                "Anexos: "
                + ", ".join(
                    f"{_tipo_de(anexo)} ({anexo.size_bytes // 1024} KB)"
                    for anexo in item.attachments
                )
            )
            linhas.append("")
        for comentario in sorted(item.comments, key=lambda x: x.id):
            linhas.append(f"> **{comentario.author_name}**: {comentario.body}")
        if item.resolution:
            linhas += ["", f"**Resolução**: {item.resolution}"]
        linhas += ["", "---", ""]

    return Response(
        content="\n".join(linhas),
        media_type="text/markdown; charset=utf-8",
        headers={"Content-Disposition": 'attachment; filename="relatos.md"'},
    )


async def _obter(session: SessionDep, code: str, user: CurrentUser) -> Report:
    try:
        return await report_service.obter(session, code=code, user=user)
    except report_service.NaoAutorizado as exc:
        raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail=str(exc)) from exc
