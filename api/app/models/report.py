"""Relatos: bugs, feedback e ideias vindos de quem usa.

O que faz um relato virar correção não é o texto — é o **contexto**. Metade dos
bugs de interface morrem em "não consegui reproduzir" porque ninguém anotou em
que tela estava, em que aparelho, e o que exatamente apareceu. Por isso o modelo
guarda página, navegador e tamanho de tela junto com o que a pessoa escreveu, e
aceita anexo de imagem e de áudio.

Anexo não fica no banco. Fica em disco, com nome gerado por nós — nunca o nome
que veio do navegador.
"""

from __future__ import annotations

from datetime import datetime

from sqlalchemy import (
    BigInteger,
    CheckConstraint,
    DateTime,
    Enum,
    ForeignKey,
    Index,
    Integer,
    String,
    Text,
)
from sqlalchemy.orm import Mapped, mapped_column, relationship

from app.models.base import Base, IdMixin, TimestampMixin
from app.models.enums import ReportKind, ReportSeverity, ReportStatus


class Report(IdMixin, TimestampMixin, Base):
    __tablename__ = "reports"
    __table_args__ = (
        Index("ix_reports_status_created", "status", "created_at"),
        Index("ix_reports_reporter", "reporter_id"),
    )

    code: Mapped[str] = mapped_column(String(12), unique=True, nullable=False)
    """Código curto para falar sobre o relato em voz alta: ``R-7K3M``.

    O id sequencial não serve para isso — vaza volume e é chato de ditar.
    """

    kind: Mapped[ReportKind] = mapped_column(
        Enum(ReportKind, native_enum=False, name="report_kind"), nullable=False
    )
    severity: Mapped[ReportSeverity] = mapped_column(
        Enum(ReportSeverity, native_enum=False, name="report_severity"),
        default=ReportSeverity.MEDIA,
        nullable=False,
    )
    status: Mapped[ReportStatus] = mapped_column(
        Enum(ReportStatus, native_enum=False, name="report_status"),
        default=ReportStatus.ABERTO,
        nullable=False,
    )

    title: Mapped[str] = mapped_column(String(160), nullable=False)
    body: Mapped[str] = mapped_column(Text, default="", nullable=False)

    # Quem relatou some (conta apagada) e o relato fica: a correção já foi
    # feita ou ainda precisa ser, e isso não depende de a pessoa existir.
    reporter_id: Mapped[int | None] = mapped_column(
        ForeignKey("users.id", ondelete="SET NULL"), default=None, index=True
    )
    reporter_name: Mapped[str] = mapped_column(String(80), default="", nullable=False)
    """Cópia do nome no momento do relato. Sobrevive à conta apagada."""

    pool_id: Mapped[int | None] = mapped_column(
        ForeignKey("pools.id", ondelete="SET NULL"), default=None, index=True
    )

    # --- Contexto capturado pela tela, não digitado -------------------------
    page_url: Mapped[str] = mapped_column(String(500), default="", nullable=False)
    user_agent: Mapped[str] = mapped_column(String(300), default="", nullable=False)
    viewport: Mapped[str] = mapped_column(String(24), default="", nullable=False)
    app_version: Mapped[str] = mapped_column(String(40), default="", nullable=False)

    resolution: Mapped[str] = mapped_column(Text, default="", nullable=False)
    """O que foi feito. Fica no relato para a próxima pessoa que ler."""

    resolved_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True), default=None)

    attachments: Mapped[list[ReportAttachment]] = relationship(
        back_populates="report", cascade="all, delete-orphan", lazy="selectin"
    )
    comments: Mapped[list[ReportComment]] = relationship(
        back_populates="report", cascade="all, delete-orphan", lazy="selectin"
    )


class ReportAttachment(IdMixin, Base):
    """Imagem ou áudio de um relato.

    O arquivo mora em disco. O que fica no banco é o suficiente para servi-lo
    com segurança: o tipo que **nós** detectamos (não o que o navegador
    declarou) e um nome de armazenamento que nós geramos.
    """

    __tablename__ = "report_attachments"
    __table_args__ = (CheckConstraint("size_bytes > 0", name="anexo_nao_vazio"),)

    report_id: Mapped[int] = mapped_column(
        ForeignKey("reports.id", ondelete="CASCADE"), nullable=False, index=True
    )

    storage_name: Mapped[str] = mapped_column(String(80), unique=True, nullable=False)
    """Nome no disco, gerado por nós. Nunca contém nada que veio do cliente."""

    original_name: Mapped[str] = mapped_column(String(160), default="", nullable=False)
    """Só para exibir. Nunca usado para montar caminho."""

    content_type: Mapped[str] = mapped_column(String(80), nullable=False)
    size_bytes: Mapped[int] = mapped_column(BigInteger, nullable=False)
    duration_ms: Mapped[int | None] = mapped_column(Integer, default=None)
    """Duração do áudio, informada pela tela. Só para mostrar na lista."""

    created_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), nullable=False, server_default="now()"
    )

    report: Mapped[Report] = relationship(back_populates="attachments")


class ReportComment(IdMixin, Base):
    """Conversa sobre o relato — é o que torna isto trabalho em conjunto."""

    __tablename__ = "report_comments"

    report_id: Mapped[int] = mapped_column(
        ForeignKey("reports.id", ondelete="CASCADE"), nullable=False, index=True
    )
    author_id: Mapped[int | None] = mapped_column(
        ForeignKey("users.id", ondelete="SET NULL"), default=None
    )
    author_name: Mapped[str] = mapped_column(String(80), default="", nullable=False)
    body: Mapped[str] = mapped_column(Text, nullable=False)

    created_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), nullable=False, server_default="now()"
    )

    report: Mapped[Report] = relationship(back_populates="comments")
