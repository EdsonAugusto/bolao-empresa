"""Catálogo de competições.

Tudo aqui pode vir de um provedor externo ou ser cadastrado à mão. A regra que
vale nos dois casos: a identidade externa é ``(provider_id, external_id)``, com
índice único, e toda escrita é upsert por essa chave. Rodar o sync duas vezes
não duplica nada.
"""

from __future__ import annotations

from datetime import date, datetime

from sqlalchemy import (
    Boolean,
    Date,
    DateTime,
    Enum,
    ForeignKey,
    Index,
    Integer,
    String,
    Text,
    UniqueConstraint,
    text,
)
from sqlalchemy.dialects.postgresql import JSONB
from sqlalchemy.orm import Mapped, mapped_column, relationship

from app.models.base import Base, IdMixin, TimestampMixin
from app.models.enums import CompetitionType, FixtureStatus


class Provider(IdMixin, TimestampMixin, Base):
    """Origem dos dados. ``manual`` é sempre um provedor válido."""

    __tablename__ = "providers"

    slug: Mapped[str] = mapped_column(String(40), unique=True, nullable=False)
    name: Mapped[str] = mapped_column(String(120), nullable=False)
    base_url: Mapped[str | None] = mapped_column(Text, default=None)
    config: Mapped[dict] = mapped_column(JSONB, default=dict, nullable=False)
    is_active: Mapped[bool] = mapped_column(Boolean, default=True, nullable=False)


class Competition(IdMixin, TimestampMixin, Base):
    __tablename__ = "competitions"
    __table_args__ = (
        UniqueConstraint("provider_id", "external_id", name="uq_competitions_provider_external"),
    )

    provider_id: Mapped[int] = mapped_column(
        ForeignKey("providers.id", ondelete="RESTRICT"), nullable=False, index=True
    )
    external_id: Mapped[str] = mapped_column(String(64), nullable=False)

    slug: Mapped[str] = mapped_column(String(120), unique=True, nullable=False)
    name: Mapped[str] = mapped_column(String(160), nullable=False)
    country: Mapped[str | None] = mapped_column(String(80), default=None)
    type: Mapped[CompetitionType] = mapped_column(
        Enum(CompetitionType, native_enum=False, name="competition_type"),
        default=CompetitionType.LEAGUE,
        nullable=False,
    )
    logo_url: Mapped[str | None] = mapped_column(Text, default=None)
    is_active: Mapped[bool] = mapped_column(Boolean, default=True, nullable=False)

    provider_config: Mapped[dict] = mapped_column(
        JSONB, default=dict, server_default=text("'{}'::jsonb"), nullable=False
    )
    """O que o provedor precisa para coletar esta competição de novo.

    Sem isto não há recoleta automática: o ``external_id`` sozinho não basta
    para o GE, que exige também a **fase** (``fase-unica-campeonato-brasileiro-2026``)
    e quantas rodadas percorrer — dados que só existiam no momento da
    importação e se perdiam depois.

    Formato por provedor:

    - ``globo``: ``{"table_id": uuid, "phase": slug, "rounds": 38}``
    - ``fixturedownload``: ``{"league": "epl", "file_year": 2026}``
    - ``wikipedia``: ``{"article": "Fase final da Copa ..."}``

    Fica solto em JSONB de propósito: cada provedor tem necessidades
    diferentes, e uma coluna por chave viraria uma tabela cheia de nulos.
    """

    seasons: Mapped[list[Season]] = relationship(
        back_populates="competition", cascade="all, delete-orphan"
    )


class Season(IdMixin, TimestampMixin, Base):
    __tablename__ = "seasons"
    __table_args__ = (
        UniqueConstraint("competition_id", "year", name="uq_seasons_competition_year"),
    )

    competition_id: Mapped[int] = mapped_column(
        ForeignKey("competitions.id", ondelete="CASCADE"), nullable=False, index=True
    )
    external_id: Mapped[str | None] = mapped_column(String(64), default=None)

    year: Mapped[int] = mapped_column(Integer, nullable=False)
    start_date: Mapped[date | None] = mapped_column(Date, default=None)
    end_date: Mapped[date | None] = mapped_column(Date, default=None)
    is_current: Mapped[bool] = mapped_column(Boolean, default=False, nullable=False)

    # Desfecho declarado pelo organizador no fim do campeonato. É o que apura
    # os palpites de temporada:
    #   {"champion_team_id": 3, "top4_team_ids": [...], "relegated_team_ids": [...]}
    # Declarado e não calculado de propósito — ver a migration 0005.
    outcome: Mapped[dict] = mapped_column(JSONB, default=dict, nullable=False)

    competition: Mapped[Competition] = relationship(back_populates="seasons")
    stages: Mapped[list[Stage]] = relationship(
        back_populates="season", cascade="all, delete-orphan"
    )
    rounds: Mapped[list[Round]] = relationship(
        back_populates="season", cascade="all, delete-orphan"
    )

    @property
    def label(self) -> str:
        return f"{self.competition.name} {self.year}"


class Stage(IdMixin, TimestampMixin, Base):
    """Fase: Fase de Grupos, Oitavas, Quartas...

    Existe porque rodada não é linear. Copa do Brasil e Libertadores têm ida e
    volta, chaves e mata-mata; modelar tudo como "rodada 1..38" quebra na
    primeira copa.
    """

    __tablename__ = "stages"
    __table_args__ = (UniqueConstraint("season_id", "name", name="uq_stages_season_name"),)

    season_id: Mapped[int] = mapped_column(
        ForeignKey("seasons.id", ondelete="CASCADE"), nullable=False, index=True
    )
    name: Mapped[str] = mapped_column(String(120), nullable=False)
    order_index: Mapped[int] = mapped_column(Integer, default=0, nullable=False)
    is_knockout: Mapped[bool] = mapped_column(Boolean, default=False, nullable=False)

    season: Mapped[Season] = relationship(back_populates="stages")
    rounds: Mapped[list[Round]] = relationship(back_populates="stage")


class Round(IdMixin, TimestampMixin, Base):
    """Rodada. Em mata-mata, cada confronto (ida/volta) é uma rodada."""

    __tablename__ = "rounds"
    __table_args__ = (
        UniqueConstraint("season_id", "number", "name", name="uq_rounds_season_number_name"),
        Index("ix_rounds_season_starts", "season_id", "starts_at"),
    )

    season_id: Mapped[int] = mapped_column(
        ForeignKey("seasons.id", ondelete="CASCADE"), nullable=False, index=True
    )
    stage_id: Mapped[int | None] = mapped_column(
        ForeignKey("stages.id", ondelete="SET NULL"), default=None, index=True
    )

    number: Mapped[int | None] = mapped_column(Integer, default=None)
    name: Mapped[str] = mapped_column(String(120), nullable=False)
    starts_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True), default=None)
    ends_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True), default=None)

    season: Mapped[Season] = relationship(back_populates="rounds")
    stage: Mapped[Stage | None] = relationship(back_populates="rounds")
    fixtures: Mapped[list[Fixture]] = relationship(back_populates="round")


class Team(IdMixin, TimestampMixin, Base):
    __tablename__ = "teams"
    __table_args__ = (
        UniqueConstraint("provider_id", "external_id", name="uq_teams_provider_external"),
    )

    provider_id: Mapped[int] = mapped_column(
        ForeignKey("providers.id", ondelete="RESTRICT"), nullable=False, index=True
    )
    external_id: Mapped[str] = mapped_column(String(64), nullable=False)

    slug: Mapped[str] = mapped_column(String(120), nullable=False, index=True)
    name: Mapped[str] = mapped_column(String(120), nullable=False)
    short_name: Mapped[str | None] = mapped_column(String(40), default=None)
    crest_url: Mapped[str | None] = mapped_column(Text, default=None)
    country: Mapped[str | None] = mapped_column(String(80), default=None)


class Fixture(IdMixin, TimestampMixin, Base):
    """Jogo.

    Placares em três camadas, porque a regra de pontuação depende disso:

    - ``home_ft``/``away_ft``  — fim do tempo normal
    - ``home_et``/``away_et``  — fim da prorrogação (acumulado, inclui o normal)
    - ``home_pen``/``away_pen`` — pênaltis, que **não** contam para pontuação

    O placar considerado é o de ``effective_score``: tempo normal + prorrogação.
    """

    __tablename__ = "fixtures"
    __table_args__ = (
        UniqueConstraint("provider_id", "external_id", name="uq_fixtures_provider_external"),
        Index("ix_fixtures_season_kickoff", "season_id", "kickoff_at"),
        Index("ix_fixtures_status_kickoff", "status", "kickoff_at"),
    )

    provider_id: Mapped[int] = mapped_column(
        ForeignKey("providers.id", ondelete="RESTRICT"), nullable=False, index=True
    )
    external_id: Mapped[str] = mapped_column(String(64), nullable=False)

    season_id: Mapped[int] = mapped_column(
        ForeignKey("seasons.id", ondelete="CASCADE"), nullable=False, index=True
    )
    round_id: Mapped[int | None] = mapped_column(
        ForeignKey("rounds.id", ondelete="SET NULL"), default=None, index=True
    )

    home_team_id: Mapped[int] = mapped_column(
        ForeignKey("teams.id", ondelete="RESTRICT"), nullable=False
    )
    away_team_id: Mapped[int] = mapped_column(
        ForeignKey("teams.id", ondelete="RESTRICT"), nullable=False
    )

    kickoff_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), nullable=False, index=True
    )
    status: Mapped[FixtureStatus] = mapped_column(
        Enum(FixtureStatus, native_enum=False, name="fixture_status"),
        default=FixtureStatus.SCHEDULED,
        nullable=False,
    )
    minute: Mapped[int | None] = mapped_column(Integer, default=None)
    venue: Mapped[str | None] = mapped_column(String(160), default=None)

    home_ft: Mapped[int | None] = mapped_column(Integer, default=None)
    away_ft: Mapped[int | None] = mapped_column(Integer, default=None)
    home_et: Mapped[int | None] = mapped_column(Integer, default=None)
    away_et: Mapped[int | None] = mapped_column(Integer, default=None)
    home_pen: Mapped[int | None] = mapped_column(Integer, default=None)
    away_pen: Mapped[int | None] = mapped_column(Integer, default=None)

    winner_team_id: Mapped[int | None] = mapped_column(
        ForeignKey("teams.id", ondelete="SET NULL"), default=None
    )
    advancing_team_id: Mapped[int | None] = mapped_column(
        ForeignKey("teams.id", ondelete="SET NULL"), default=None
    )

    provider_updated_at: Mapped[datetime | None] = mapped_column(
        DateTime(timezone=True), default=None
    )
    synced_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True), default=None)

    # Marca que a apuração precisa rodar de novo — placar corrigido por VAR,
    # W.O. ou decisão de tribunal. O job de settle consome isto.
    settlement_dirty: Mapped[bool] = mapped_column(Boolean, default=False, nullable=False)
    settled_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True), default=None)

    round: Mapped[Round | None] = relationship(back_populates="fixtures")
    home_team: Mapped[Team] = relationship(foreign_keys=[home_team_id])
    away_team: Mapped[Team] = relationship(foreign_keys=[away_team_id])

    @property
    def effective_score(self) -> tuple[int, int] | None:
        """Placar que vale para pontuação: tempo normal + prorrogação.

        Pênaltis não contam — decidem quem avança, não o resultado do jogo.
        Devolve ``None`` enquanto não houver placar utilizável.
        """
        if self.home_et is not None and self.away_et is not None:
            return self.home_et, self.away_et
        if self.home_ft is not None and self.away_ft is not None:
            return self.home_ft, self.away_ft
        return None

    @property
    def is_scoreable(self) -> bool:
        """Pode ser apurado agora?"""
        return self.status.is_final and self.effective_score is not None
