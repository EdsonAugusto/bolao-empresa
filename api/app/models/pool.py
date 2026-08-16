"""Bolões, configuração de pontuação e participação."""

from __future__ import annotations

from datetime import datetime
from typing import TYPE_CHECKING

from sqlalchemy import (
    Boolean,
    CheckConstraint,
    DateTime,
    Enum,
    ForeignKey,
    Index,
    Integer,
    String,
    Text,
    UniqueConstraint,
    func,
)
from sqlalchemy.dialects.postgresql import JSONB
from sqlalchemy.orm import Mapped, mapped_column, relationship

from app.models.base import Base, IdMixin, TimestampMixin
from app.models.enums import (
    MembershipRole,
    MembershipStatus,
    PoolKind,
    PoolStatus,
    PoolVisibility,
    ScoringMode,
)

if TYPE_CHECKING:
    # Só para o type checker: em runtime o SQLAlchemy resolve "User"/"Round"/
    # "Season" pelo registro de mapeamentos, e importar de verdade aqui criaria
    # ciclo entre os módulos de modelo.
    from app.models.catalog import Round, Season
    from app.models.user import User


class Pool(IdMixin, TimestampMixin, Base):
    __tablename__ = "pools"

    owner_id: Mapped[int] = mapped_column(
        ForeignKey("users.id", ondelete="RESTRICT"), nullable=False, index=True
    )

    kind: Mapped[PoolKind] = mapped_column(
        Enum(PoolKind, native_enum=False, name="pool_kind"),
        default=PoolKind.SEASON,
        nullable=False,
    )

    # Nulo em bolão de rodada personalizada: ele não pertence a temporada
    # nenhuma, e é justamente isso que permite misturar campeonatos.
    season_id: Mapped[int | None] = mapped_column(
        ForeignKey("seasons.id", ondelete="RESTRICT"), default=None, index=True
    )

    name: Mapped[str] = mapped_column(String(120), nullable=False)
    description: Mapped[str | None] = mapped_column(Text, default=None)

    # Identificadores públicos. As rotas usam estes, nunca o bigint.
    slug: Mapped[str] = mapped_column(String(140), unique=True, nullable=False, index=True)
    invite_code: Mapped[str] = mapped_column(String(12), unique=True, nullable=False, index=True)

    status: Mapped[PoolStatus] = mapped_column(
        Enum(PoolStatus, native_enum=False, name="pool_status"),
        default=PoolStatus.OPEN,
        nullable=False,
    )
    visibility: Mapped[PoolVisibility] = mapped_column(
        Enum(PoolVisibility, native_enum=False, name="pool_visibility"),
        default=PoolVisibility.PRIVATE,
        nullable=False,
    )

    # Sem plano e sem cobrança: o limite existe só para o organizador conter o
    # tamanho do grupo se quiser. NULL = sem limite.
    max_participants: Mapped[int | None] = mapped_column(Integer, default=None)

    # Aparência do bolão (cor, emblema). Nada de whitelabel comercial.
    branding: Mapped[dict] = mapped_column(JSONB, default=dict, nullable=False)

    # Palpites de temporada (campeão, G-4, rebaixados) travam neste instante.
    season_bonus_locks_at: Mapped[datetime | None] = mapped_column(
        DateTime(timezone=True), default=None
    )

    owner: Mapped[User] = relationship()
    season: Mapped[Season | None] = relationship()
    matchdays: Mapped[list[Matchday]] = relationship(
        back_populates="pool", cascade="all, delete-orphan", order_by="Matchday.number"
    )
    scoring_configs: Mapped[list[ScoringConfig]] = relationship(
        back_populates="pool", cascade="all, delete-orphan", order_by="ScoringConfig.version"
    )
    memberships: Mapped[list[Membership]] = relationship(
        back_populates="pool", cascade="all, delete-orphan"
    )
    pool_rounds: Mapped[list[PoolRound]] = relationship(
        back_populates="pool", cascade="all, delete-orphan"
    )

    @property
    def active_scoring_config(self) -> ScoringConfig:
        """A configuração vigente é sempre a de maior versão."""
        return max(self.scoring_configs, key=lambda config: config.version)


class ScoringConfig(IdMixin, TimestampMixin, Base):
    """Configuração de pontuação, **versionada e imutável**.

    Se o organizador mudar a regra no meio do campeonato, nasce uma versão
    nova; a anterior não é reescrita, e os ``prediction_scores`` já gravados
    continuam apontando para a versão que os produziu. Sem isso, mudar a regra
    reescreveria o passado e ninguém consegue explicar o ranking.
    """

    __tablename__ = "scoring_configs"
    __table_args__ = (
        UniqueConstraint("pool_id", "version", name="uq_scoring_configs_pool_version"),
    )

    pool_id: Mapped[int] = mapped_column(
        ForeignKey("pools.id", ondelete="CASCADE"), nullable=False, index=True
    )
    version: Mapped[int] = mapped_column(Integer, default=1, nullable=False)
    mode: Mapped[ScoringMode] = mapped_column(
        Enum(ScoringMode, native_enum=False, name="scoring_mode"),
        default=ScoringMode.CLASSIC,
        nullable=False,
    )

    # [{"key": "exact", "enabled": true, "points": 10}, ...]
    # A ordem de avaliação NÃO vem daqui: é derivada dos pontos, decrescente.
    criteria: Mapped[list[dict]] = mapped_column(JSONB, nullable=False)

    # Bônus de mata-mata: acertar quem avança, independente do placar.
    knockout_advance_points: Mapped[int] = mapped_column(Integer, default=0, nullable=False)
    # Palpites de temporada.
    champion_points: Mapped[int] = mapped_column(Integer, default=0, nullable=False)
    top4_points: Mapped[int] = mapped_column(Integer, default=0, nullable=False)
    relegated_points: Mapped[int] = mapped_column(Integer, default=0, nullable=False)

    # Congela quando o primeiro palpite é pontuado com esta versão.
    frozen_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True), default=None)

    pool: Mapped[Pool] = relationship(back_populates="scoring_configs")

    @property
    def is_frozen(self) -> bool:
        return self.frozen_at is not None


class PoolRound(IdMixin, Base):
    """Quais rodadas entram no bolão e quanto cada uma vale."""

    __tablename__ = "pool_rounds"
    __table_args__ = (
        UniqueConstraint("pool_id", "round_id", name="uq_pool_rounds_pool_round"),
        CheckConstraint("multiplier >= 1", name="multiplier_positivo"),
    )

    pool_id: Mapped[int] = mapped_column(
        ForeignKey("pools.id", ondelete="CASCADE"), nullable=False, index=True
    )
    round_id: Mapped[int] = mapped_column(
        ForeignKey("rounds.id", ondelete="CASCADE"), nullable=False, index=True
    )
    multiplier: Mapped[int] = mapped_column(Integer, default=1, nullable=False)
    included: Mapped[bool] = mapped_column(Boolean, default=True, nullable=False)

    pool: Mapped[Pool] = relationship(back_populates="pool_rounds")
    round: Mapped[Round] = relationship()


class Matchday(IdMixin, TimestampMixin, Base):
    """Rodada de um bolão personalizado.

    Diferente de ``rounds``, que pertence a um campeonato: esta é do bolão, e
    seus jogos podem vir de competições diferentes. É o que permite juntar o
    clássico do Brasileirão com a decisão da Libertadores na mesma rodada.
    """

    __tablename__ = "matchdays"
    __table_args__ = (
        UniqueConstraint("pool_id", "number", name="uq_matchdays_pool_number"),
        CheckConstraint("multiplier >= 1", name="multiplier_positivo"),
    )

    pool_id: Mapped[int] = mapped_column(
        ForeignKey("pools.id", ondelete="CASCADE"), nullable=False, index=True
    )
    number: Mapped[int] = mapped_column(Integer, nullable=False)
    name: Mapped[str] = mapped_column(String(120), nullable=False)
    multiplier: Mapped[int] = mapped_column(Integer, default=1, nullable=False)

    pool: Mapped[Pool] = relationship(back_populates="matchdays")
    entries: Mapped[list[MatchdayFixture]] = relationship(
        back_populates="matchday", cascade="all, delete-orphan"
    )


class MatchdayFixture(IdMixin, Base):
    """Jogo escolhido para uma rodada do bolão.

    Aqui é lista completa, não exceção: a rodada é exatamente o que o
    organizador marcou. É o oposto de ``pool_fixtures``, que só guarda desvios
    do que a temporada já define.
    """

    __tablename__ = "matchday_fixtures"
    __table_args__ = (
        UniqueConstraint("matchday_id", "fixture_id", name="uq_matchday_fixtures_matchday_fixture"),
    )

    matchday_id: Mapped[int] = mapped_column(
        ForeignKey("matchdays.id", ondelete="CASCADE"), nullable=False, index=True
    )
    fixture_id: Mapped[int] = mapped_column(
        ForeignKey("fixtures.id", ondelete="CASCADE"), nullable=False, index=True
    )

    matchday: Mapped[Matchday] = relationship(back_populates="entries")


class PoolCompetition(IdMixin, Base):
    """Campeonato que alimenta as rodadas de um bolão montado à mão.

    É **filtro de garimpo**, não regra de pontuação: reduz a agenda de onde o
    organizador escolhe os jogos da semana. Sem nenhuma linha, vale tudo que
    estiver importado — que é o padrão e o certo para quem quer misturar.

    Escolher aqui não trava nada: o organizador continua podendo puxar um jogo
    de fora, e um campeonato que sai da lista não apaga jogo já escolhido. A
    rodada montada é a verdade; isto só encurta a lista para achá-la.
    """

    __tablename__ = "pool_competitions"
    __table_args__ = (
        UniqueConstraint("pool_id", "competition_id", name="uq_pool_competitions_pool_competition"),
    )

    pool_id: Mapped[int] = mapped_column(
        ForeignKey("pools.id", ondelete="CASCADE"), nullable=False, index=True
    )
    competition_id: Mapped[int] = mapped_column(
        ForeignKey("competitions.id", ondelete="CASCADE"), nullable=False, index=True
    )


class PoolFixture(IdMixin, Base):
    """Exceção de inclusão para um jogo específico.

    Tabela de exceção, não a lista completa: sem linha, vale o que a rodada
    disser; com linha, a linha manda. Um bolão de 380 jogos rodando no padrão
    não gera nenhuma linha aqui, e tirar um jogo custa uma linha só.
    """

    __tablename__ = "pool_fixtures"
    __table_args__ = (
        UniqueConstraint("pool_id", "fixture_id", name="uq_pool_fixtures_pool_fixture"),
    )

    pool_id: Mapped[int] = mapped_column(
        ForeignKey("pools.id", ondelete="CASCADE"), nullable=False, index=True
    )
    fixture_id: Mapped[int] = mapped_column(
        ForeignKey("fixtures.id", ondelete="CASCADE"), nullable=False, index=True
    )
    included: Mapped[bool] = mapped_column(Boolean, nullable=False)


class Membership(IdMixin, TimestampMixin, Base):
    """Participação de um usuário num bolão.

    Todo palpite pendura em ``membership``, não em ``user``: o mesmo usuário
    pode estar em vários bolões, e sair de um não afeta os outros.
    """

    __tablename__ = "memberships"
    __table_args__ = (
        UniqueConstraint("pool_id", "user_id", name="uq_memberships_pool_user"),
        Index("ix_memberships_pool_status", "pool_id", "status"),
    )

    pool_id: Mapped[int] = mapped_column(
        ForeignKey("pools.id", ondelete="CASCADE"), nullable=False, index=True
    )
    user_id: Mapped[int] = mapped_column(
        ForeignKey("users.id", ondelete="CASCADE"), nullable=False, index=True
    )

    display_name: Mapped[str] = mapped_column(String(80), nullable=False)
    role: Mapped[MembershipRole] = mapped_column(
        Enum(MembershipRole, native_enum=False, name="membership_role"),
        default=MembershipRole.PLAYER,
        nullable=False,
    )
    status: Mapped[MembershipStatus] = mapped_column(
        Enum(MembershipStatus, native_enum=False, name="membership_status"),
        default=MembershipStatus.ACTIVE,
        nullable=False,
    )
    joined_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), nullable=False, server_default=func.now()
    )

    pool: Mapped[Pool] = relationship(back_populates="memberships")
    user: Mapped[User] = relationship()

    @property
    def is_active(self) -> bool:
        return self.status is MembershipStatus.ACTIVE
