"""Palpites e apuração.

Duas regras que o banco garante sozinho, sem depender da aplicação:

1. Não existe palpite gravado ou alterado depois do apito (trigger).
2. Um palpite por participante por jogo (índice único).

A blindagem — ninguém vê palpite alheio antes do início — não é aplicada aqui;
ela vive no serializer, porque depende do relógio no momento da leitura.
"""

from __future__ import annotations

from datetime import datetime
from typing import TYPE_CHECKING

from sqlalchemy import (
    CheckConstraint,
    DateTime,
    Enum,
    ForeignKey,
    Index,
    Integer,
    String,
    UniqueConstraint,
    func,
)
from sqlalchemy.dialects.postgresql import JSONB
from sqlalchemy.orm import Mapped, mapped_column, relationship

from app.models.base import Base, IdMixin, TimestampMixin
from app.models.enums import BonusKind, CriterionKey

if TYPE_CHECKING:
    # Resolvidos pelo registro do SQLAlchemy em runtime; importar de verdade
    # criaria ciclo entre os módulos de modelo.
    from app.models.catalog import Fixture
    from app.models.pool import Membership


class Prediction(IdMixin, TimestampMixin, Base):
    __tablename__ = "predictions"
    __table_args__ = (
        UniqueConstraint("membership_id", "fixture_id", name="uq_predictions_membership_fixture"),
        CheckConstraint("home_goals >= 0 AND away_goals >= 0", name="gols_nao_negativos"),
        CheckConstraint("home_goals <= 99 AND away_goals <= 99", name="gols_plausiveis"),
        Index("ix_predictions_fixture", "fixture_id"),
    )

    membership_id: Mapped[int] = mapped_column(
        ForeignKey("memberships.id", ondelete="CASCADE"), nullable=False, index=True
    )
    fixture_id: Mapped[int] = mapped_column(
        ForeignKey("fixtures.id", ondelete="CASCADE"), nullable=False
    )

    home_goals: Mapped[int] = mapped_column(Integer, nullable=False)
    away_goals: Mapped[int] = mapped_column(Integer, nullable=False)

    membership: Mapped[Membership] = relationship()
    fixture: Mapped[Fixture] = relationship()
    score: Mapped[PredictionScore | None] = relationship(
        back_populates="prediction", cascade="all, delete-orphan", uselist=False
    )


class BonusPrediction(IdMixin, TimestampMixin, Base):
    """Palpites que não são de placar.

    - ``champion`` / ``top4`` / ``relegated``: travados no início da temporada,
      apurados no fim.
    - ``knockout_advance``: quem passa de fase, independente do placar.
    """

    __tablename__ = "bonus_predictions"
    __table_args__ = (
        UniqueConstraint(
            "membership_id", "kind", "reference_id", name="uq_bonus_membership_kind_reference"
        ),
    )

    membership_id: Mapped[int] = mapped_column(
        ForeignKey("memberships.id", ondelete="CASCADE"), nullable=False, index=True
    )
    kind: Mapped[BonusKind] = mapped_column(
        Enum(BonusKind, native_enum=False, name="bonus_kind"), nullable=False
    )
    # Para knockout_advance é o fixture; para os de temporada, 0.
    reference_id: Mapped[int] = mapped_column(Integer, default=0, nullable=False)

    # {"team_ids": [12]} ou {"team_ids": [3, 7, 11, 19]}
    payload: Mapped[dict] = mapped_column(JSONB, nullable=False)

    locks_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), nullable=False)
    points_awarded: Mapped[int | None] = mapped_column(Integer, default=None)
    settled_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True), default=None)

    membership: Mapped[Membership] = relationship()


class PredictionScore(IdMixin, Base):
    """Resultado da apuração de um palpite.

    Guarda **qual critério bateu**, não só o total. Sem isso não dá para
    desempatar nem para responder "por que eu levei 7 e ele 10" — que é o
    ticket de suporte número um desse tipo de produto.
    """

    __tablename__ = "prediction_scores"
    __table_args__ = (
        UniqueConstraint("prediction_id", name="uq_prediction_scores_prediction"),
        Index("ix_prediction_scores_membership", "membership_id"),
    )

    prediction_id: Mapped[int] = mapped_column(
        ForeignKey("predictions.id", ondelete="CASCADE"), nullable=False
    )
    # Desnormalizado de propósito: o ranking agrega por participante e por
    # rodada o tempo todo, e o join extra em predictions custa caro.
    membership_id: Mapped[int] = mapped_column(
        ForeignKey("memberships.id", ondelete="CASCADE"), nullable=False
    )
    pool_id: Mapped[int] = mapped_column(
        ForeignKey("pools.id", ondelete="CASCADE"), nullable=False, index=True
    )
    fixture_id: Mapped[int] = mapped_column(
        ForeignKey("fixtures.id", ondelete="CASCADE"), nullable=False, index=True
    )
    round_id: Mapped[int | None] = mapped_column(
        ForeignKey("rounds.id", ondelete="SET NULL"), default=None, index=True
    )
    # Rodada do bolão personalizado. Só um dos dois é preenchido: bolão de
    # campeonato usa `round_id`, personalizado usa este.
    matchday_id: Mapped[int | None] = mapped_column(
        ForeignKey("matchdays.id", ondelete="SET NULL"), default=None, index=True
    )

    criterion_key: Mapped[CriterionKey] = mapped_column(
        Enum(CriterionKey, native_enum=False, name="criterion_key"), nullable=False
    )
    base_points: Mapped[int] = mapped_column(Integer, nullable=False)
    multiplier: Mapped[int] = mapped_column(Integer, default=1, nullable=False)
    final_points: Mapped[int] = mapped_column(Integer, nullable=False)

    scoring_version: Mapped[int] = mapped_column(Integer, nullable=False)
    computed_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), nullable=False)

    prediction: Mapped[Prediction] = relationship(back_populates="score")


class Standing(IdMixin, Base):
    """Posição atual de um participante no bolão.

    Tabela derivada: pode ser apagada e recomputada a qualquer momento a partir
    de ``prediction_scores``. Existe por desempenho de leitura, não como fonte
    de verdade.
    """

    __tablename__ = "standings"
    __table_args__ = (
        UniqueConstraint("pool_id", "membership_id", name="uq_standings_pool_membership"),
        Index("ix_standings_pool_position", "pool_id", "position"),
    )

    pool_id: Mapped[int] = mapped_column(
        ForeignKey("pools.id", ondelete="CASCADE"), nullable=False, index=True
    )
    membership_id: Mapped[int] = mapped_column(
        ForeignKey("memberships.id", ondelete="CASCADE"), nullable=False
    )

    points: Mapped[int] = mapped_column(Integer, default=0, nullable=False)
    predictions_made: Mapped[int] = mapped_column(Integer, default=0, nullable=False)
    fixtures_settled: Mapped[int] = mapped_column(Integer, default=0, nullable=False)

    # Contagem por critério, usada no desempate. {"exact": 4, "winner_only": 9}
    criterion_hits: Mapped[dict] = mapped_column(JSONB, default=dict, nullable=False)

    position: Mapped[int] = mapped_column(Integer, default=0, nullable=False)
    previous_position: Mapped[int | None] = mapped_column(Integer, default=None)
    computed_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), nullable=False)

    membership: Mapped[Membership] = relationship()

    @property
    def movement(self) -> int:
        """Positivo = subiu. Zero = ficou ou é a primeira apuração."""
        if self.previous_position is None:
            return 0
        return self.previous_position - self.position


class StandingsSnapshot(IdMixin, Base):
    """Foto do ranking ao fim de uma rodada, para o histórico de evolução."""

    __tablename__ = "standings_snapshots"
    __table_args__ = (UniqueConstraint("pool_id", "round_id", name="uq_snapshots_pool_round"),)

    pool_id: Mapped[int] = mapped_column(
        ForeignKey("pools.id", ondelete="CASCADE"), nullable=False, index=True
    )
    round_id: Mapped[int] = mapped_column(
        ForeignKey("rounds.id", ondelete="CASCADE"), nullable=False
    )
    # [{"membership_id": 3, "position": 1, "points": 87, "round_points": 22}, ...]
    payload: Mapped[list[dict]] = mapped_column(JSONB, nullable=False)
    computed_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), nullable=False)


class SyncRun(IdMixin, Base):
    """Registro de cada execução de job de ingestão."""

    __tablename__ = "sync_runs"

    provider_id: Mapped[int | None] = mapped_column(
        ForeignKey("providers.id", ondelete="SET NULL"), default=None, index=True
    )
    kind: Mapped[str] = mapped_column(String(32), nullable=False, index=True)
    status: Mapped[str] = mapped_column(String(16), nullable=False)

    started_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), nullable=False)
    finished_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True), default=None)

    stats: Mapped[dict] = mapped_column(JSONB, default=dict, nullable=False)
    error: Mapped[str | None] = mapped_column(String(1000), default=None)


class Notification(IdMixin, Base):
    """Notificação enfileirada ou entregue.

    A deduplicação por ``(membership_id, template, reference_id)`` é o que
    impede o participante de receber cinco vezes o mesmo "sua rodada fechou"
    quando o job roda de novo.
    """

    __tablename__ = "notifications"
    __table_args__ = (
        UniqueConstraint(
            "membership_id", "template", "reference_id", name="uq_notifications_dedup"
        ),
        Index("ix_notifications_status_created", "status", "created_at"),
    )

    membership_id: Mapped[int] = mapped_column(
        ForeignKey("memberships.id", ondelete="CASCADE"), nullable=False, index=True
    )
    template: Mapped[str] = mapped_column(String(48), nullable=False)
    reference_id: Mapped[int] = mapped_column(Integer, default=0, nullable=False)
    channel: Mapped[str] = mapped_column(String(24), nullable=False)
    status: Mapped[str] = mapped_column(String(16), nullable=False, default="pending")

    payload: Mapped[dict] = mapped_column(JSONB, default=dict, nullable=False)
    attempts: Mapped[int] = mapped_column(Integer, default=0, nullable=False)
    error: Mapped[str | None] = mapped_column(String(500), default=None)

    read_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True), default=None)
    sent_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True), default=None)
    created_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), nullable=False, server_default=func.now()
    )
