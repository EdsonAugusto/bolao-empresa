"""Schemas de bolão, pontuação, palpite e ranking."""

from __future__ import annotations

from datetime import datetime

from pydantic import BaseModel, Field, field_validator

from app.scoring import CONFIGURABLE_KEYS, MAX_POINTS, CriterionKey, ScoringMode


# --- Pontuação -------------------------------------------------------------
class CriterionIn(BaseModel):
    key: CriterionKey
    enabled: bool = True
    points: int = Field(ge=0, le=MAX_POINTS)


class ScoringConfigIn(BaseModel):
    mode: ScoringMode = ScoringMode.CLASSIC
    criteria: list[CriterionIn] | None = None
    knockout_advance_points: int = Field(default=0, ge=0, le=MAX_POINTS)
    champion_points: int = Field(default=0, ge=0, le=MAX_POINTS)
    top4_points: int = Field(default=0, ge=0, le=MAX_POINTS)
    relegated_points: int = Field(default=0, ge=0, le=MAX_POINTS)

    @field_validator("criteria")
    @classmethod
    def _no_duplicates(cls, value: list[CriterionIn] | None) -> list[CriterionIn] | None:
        if value is None:
            return value
        keys = [item.key for item in value]
        if len(keys) != len(set(keys)):
            raise ValueError("critério repetido")
        unknown = set(keys) - set(CONFIGURABLE_KEYS)
        if unknown:
            raise ValueError(f"critério inválido: {unknown}")
        return value


class CriterionOut(BaseModel):
    key: str
    enabled: bool
    points: int
    label: str
    description: str


class ScoringConfigOut(BaseModel):
    version: int
    mode: str
    criteria: list[CriterionOut]
    evaluation_order: list[str]
    tiebreak_order: list[str]
    max_points: int
    is_frozen: bool
    knockout_advance_points: int
    champion_points: int
    top4_points: int
    relegated_points: int


# --- Bolão -----------------------------------------------------------------
class PoolCreate(BaseModel):
    name: str = Field(min_length=3, max_length=120)
    kind: str = Field(
        default="season",
        description="'season' segue as rodadas do campeonato; 'custom' é montado por você.",
    )
    season_id: int | None = None
    description: str | None = Field(default=None, max_length=2000)
    visibility: str = "private"
    max_participants: int | None = Field(default=None, ge=2, le=1000)
    scoring: ScoringConfigIn = Field(default_factory=ScoringConfigIn)
    round_ids: list[int] | None = None
    competition_ids: list[int] | None = Field(
        default=None,
        description=(
            "Só para bolão montado: de quais campeonatos garimpar os jogos da "
            "rodada. Vazio ou ausente = todos os importados."
        ),
    )
    multipliers: dict[int, int] | None = None


class PoolUpdate(BaseModel):
    name: str | None = Field(default=None, min_length=3, max_length=120)
    description: str | None = Field(default=None, max_length=2000)
    visibility: str | None = None
    max_participants: int | None = Field(default=None, ge=2, le=1000)
    status: str | None = None
    branding: dict | None = None


class MemberOut(BaseModel):
    membership_id: int
    display_name: str
    role: str
    status: str
    joined_at: datetime
    is_me: bool = False


class PoolSummary(BaseModel):
    id: int
    slug: str
    name: str
    description: str | None
    status: str
    visibility: str
    kind: str
    season: str
    competition: str
    members: int
    max_participants: int | None
    my_role: str | None = None
    my_position: int | None = None
    my_points: int | None = None
    created_at: datetime


class PoolDetail(PoolSummary):
    invite_code: str | None = Field(default=None, description="Só para quem organiza o bolão.")
    scoring: ScoringConfigOut
    branding: dict = Field(default_factory=dict)
    rounds_included: int
    owner_name: str
    competition_ids: list[int] = Field(
        default_factory=list,
        description="Campeonatos que alimentam a rodada. Vazio = todos.",
    )
    is_owner: bool = False
    """Só o dono pode excluir o bolão."""


class JoinRequest(BaseModel):
    invite_code: str = Field(min_length=4, max_length=12)
    display_name: str | None = Field(default=None, min_length=2, max_length=80)


class MultiplierIn(BaseModel):
    round_id: int
    multiplier: int = Field(ge=1, le=10)


class FixtureSelectionIn(BaseModel):
    """Liga/desliga jogos. Informe ``round_id`` OU ``fixture_ids``."""

    included: bool
    round_id: int | None = None
    fixture_ids: list[int] = Field(default_factory=list, max_length=500)

    @field_validator("fixture_ids")
    @classmethod
    def _um_ou_outro(cls, value: list[int], info) -> list[int]:  # type: ignore[no-untyped-def]
        if not value and info.data.get("round_id") is None:
            raise ValueError("informe round_id ou fixture_ids")
        return value


class RulesOut(BaseModel):
    """Regulamento gerado a partir da configuração real do bolão."""

    pool_name: str
    scoring_version: int
    max_points_per_fixture: int
    fixtures_included: int
    max_points_total: int
    criteria: list[dict]
    tiebreak: list[str]
    bonus: dict[str, int]
    notes: list[str]


# --- Palpites --------------------------------------------------------------
class PredictionIn(BaseModel):
    fixture_id: int
    home_goals: int = Field(ge=0, le=99)
    away_goals: int = Field(ge=0, le=99)


class PredictionBatch(BaseModel):
    predictions: list[PredictionIn] = Field(min_length=1, max_length=100)


class MyPrediction(BaseModel):
    fixture_id: int
    home_goals: int
    away_goals: int
    updated_at: datetime


class OtherPrediction(BaseModel):
    """Palpite de terceiro.

    Antes do apito, ``home_goals``/``away_goals`` vêm ``null`` e ``is_hidden``
    vem ``true``. O servidor nunca envia o número e depois pede para a tela
    escondê-lo — o dado simplesmente não sai daqui.
    """

    membership_id: int
    display_name: str
    fixture_id: int
    home_goals: int | None
    away_goals: int | None
    is_hidden: bool


class PredictionResult(BaseModel):
    saved: list[MyPrediction]
    rejected: list[dict]


class ScoreBreakdown(BaseModel):
    fixture_id: int

    membership_id: int
    display_name: str
    """Quem palpitou.

    Sem o nome a tabela é uma lista de placares soltos: dá para ver que alguém
    levou 7 e alguém levou 5, e não dá para saber quem — que é exatamente a
    pergunta que a tela existe para responder.
    """

    is_me: bool
    """Para a tela destacar a própria linha sem comparar id no template."""

    prediction: str | None
    actual: str | None
    criterion: str
    reason: str
    base_points: int
    multiplier: int
    final_points: int


# --- Ranking ---------------------------------------------------------------
class StandingOut(BaseModel):
    position: int
    previous_position: int | None
    movement: int
    membership_id: int
    display_name: str
    points: int
    predictions_made: int
    fixtures_settled: int
    criterion_hits: dict[str, int]
    is_me: bool = False


class RoundStandingOut(BaseModel):
    position: int
    membership_id: int
    display_name: str
    points: int
    fixtures_scored: int


class SnapshotOut(BaseModel):
    round_id: int
    round_name: str
    computed_at: datetime
    payload: list[dict]


class EngagementReport(BaseModel):
    """Relatório do organizador: quem está jogando de verdade."""

    members: int
    rounds_played: int
    overall_fill_rate: int
    by_round: list[dict]
    by_member: list[dict]
