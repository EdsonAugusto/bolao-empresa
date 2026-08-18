"""Tipos compartilhados entre schemas."""

from __future__ import annotations

from datetime import datetime

from pydantic import BaseModel, ConfigDict, Field


class ORMModel(BaseModel):
    model_config = ConfigDict(from_attributes=True)


class Page[T](BaseModel):
    items: list[T]
    total: int
    limit: int
    offset: int


class Message(BaseModel):
    detail: str


class TeamBrief(ORMModel):
    id: int
    name: str
    short_name: str | None = None
    slug: str
    crest_url: str | None = None


class RoundBrief(ORMModel):
    id: int
    number: int | None = None
    name: str
    starts_at: datetime | None = None
    ends_at: datetime | None = None


class FixtureOut(ORMModel):
    """Jogo.

    ``kickoff_at`` sai sempre em UTC. A conversão para o fuso de quem lê é
    responsabilidade do frontend — o servidor não adivinha fuso de cliente.
    """

    id: int
    kickoff_at: datetime
    status: str
    minute: int | None = None
    venue: str | None = None

    competition: str | None = Field(
        default=None,
        description="Nome do campeonato. Preenchido pela API, não vem do ORM.",
    )

    home_team: TeamBrief
    away_team: TeamBrief
    round: RoundBrief | None = None

    home_ft: int | None = None
    away_ft: int | None = None
    home_et: int | None = None
    away_et: int | None = None
    home_pen: int | None = None
    away_pen: int | None = None

    is_locked: bool = Field(default=False, description="Palpites fechados para este jogo.")
    is_included: bool = Field(default=True, description="Este jogo vale pontos no bolão.")
    settled_at: datetime | None = None

    home_form: str = Field(
        default="",
        description="Últimos jogos do mandante, do mais recente ao mais antigo (V/E/D).",
    )
    away_form: str = Field(default="", description="Idem, do visitante.")
    synced_at: datetime | None = Field(
        default=None,
        description="Quando o placar foi conferido na fonte pela última vez.",
    )
