"""Estruturas do motor de pontuação.

Tudo aqui é dataclass imutável. Nada de SQLAlchemy, nada de Pydantic, nada de
I/O — este módulo tem que poder ser exercitado sem banco, sem rede e sem
relógio.
"""

from __future__ import annotations

import enum
from collections.abc import Mapping, Sequence
from dataclasses import dataclass, field, replace
from typing import Any

MIN_POINTS = 0
MAX_POINTS = 100


class Outcome(enum.StrEnum):
    HOME = "home"
    AWAY = "away"
    DRAW = "draw"


class CriterionKey(enum.StrEnum):
    """Critérios de pontuação.

    A ordem desta declaração é a **especificidade**, do mais específico ao
    menos. Ela não decide quanto vale nem em que ordem se avalia — só serve de
    desempate estável quando dois critérios habilitados valem o mesmo.
    """

    EXACT = "exact"
    WINNER_AND_ONE_SCORE = "winner_and_one_score"
    WINNER_AND_GOAL_DIFF = "winner_and_goal_diff"
    DRAW = "draw"
    WINNER_ONLY = "winner_only"
    ONE_SCORE_ONLY = "one_score_only"
    MISS = "miss"


SPECIFICITY: dict[CriterionKey, int] = {key: index for index, key in enumerate(CriterionKey)}

#: Critérios que o organizador pode ligar, desligar e precificar.
CONFIGURABLE_KEYS: tuple[CriterionKey, ...] = tuple(
    key for key in CriterionKey if key is not CriterionKey.MISS
)


class ScoringMode(enum.StrEnum):
    CLASSIC = "classic"
    SIMPLE = "simple"
    CUSTOM = "custom"


@dataclass(frozen=True, slots=True)
class Score:
    """Placar. Sempre gols do mandante e do visitante, nessa ordem."""

    home: int
    away: int

    def __post_init__(self) -> None:
        if self.home < 0 or self.away < 0:
            raise ValueError(f"placar não pode ser negativo: {self.home}x{self.away}")

    @property
    def outcome(self) -> Outcome:
        if self.home > self.away:
            return Outcome.HOME
        if self.home < self.away:
            return Outcome.AWAY
        return Outcome.DRAW

    @property
    def goal_diff(self) -> int:
        return self.home - self.away

    @property
    def is_draw(self) -> bool:
        return self.outcome is Outcome.DRAW

    def __str__(self) -> str:
        return f"{self.home}x{self.away}"


@dataclass(frozen=True, slots=True)
class Criterion:
    key: CriterionKey
    enabled: bool
    points: int

    def __post_init__(self) -> None:
        if not MIN_POINTS <= self.points <= MAX_POINTS:
            raise ValueError(
                f"pontos de {self.key} fora da faixa {MIN_POINTS}..{MAX_POINTS}: {self.points}"
            )
        if self.key is CriterionKey.MISS and self.points != 0:
            raise ValueError("o critério 'miss' vale sempre 0")


@dataclass(frozen=True, slots=True)
class ScoringConfig:
    """Configuração de pontuação de um bolão.

    ``criteria`` guarda todos os critérios configuráveis; os desabilitados
    permanecem na lista para que a configuração continue legível e para que
    reativar um critério não exija adivinhar o valor anterior.
    """

    mode: ScoringMode
    criteria: tuple[Criterion, ...]
    version: int = 1

    def __post_init__(self) -> None:
        seen = [criterion.key for criterion in self.criteria]
        if len(seen) != len(set(seen)):
            raise ValueError(f"critério repetido na configuração: {seen}")

    @property
    def enabled(self) -> tuple[Criterion, ...]:
        return tuple(criterion for criterion in self.criteria if criterion.enabled)

    @property
    def evaluation_order(self) -> tuple[Criterion, ...]:
        """Ordem de avaliação: **pontos decrescentes**.

        Nunca a ordem de declaração. No modo personalizado o organizador pode
        colocar ``winner_only=9`` acima de ``exact=5``, e nesse caso quem
        acertar só o vencedor tem que levar 9.

        Empate de pontos cai na especificidade (o critério mais específico
        primeiro), o que torna o resultado determinístico.
        """
        return tuple(
            sorted(
                self.enabled,
                key=lambda criterion: (-criterion.points, SPECIFICITY[criterion.key]),
            )
        )

    @property
    def max_points(self) -> int:
        """Maior pontuação-base possível para um único jogo."""
        return max((criterion.points for criterion in self.enabled), default=0)

    def points_for(self, key: CriterionKey) -> int:
        for criterion in self.criteria:
            if criterion.key is key:
                return criterion.points if criterion.enabled else 0
        return 0

    def with_criterion(self, key: CriterionKey, *, enabled: bool, points: int) -> ScoringConfig:
        """Devolve uma cópia com um critério alterado. Não muta o original."""
        updated = tuple(
            replace(criterion, enabled=enabled, points=points)
            if criterion.key is key
            else criterion
            for criterion in self.criteria
        )
        return replace(self, criteria=updated)

    def to_payload(self) -> list[dict[str, Any]]:
        """Serialização para o JSONB de ``scoring_configs.criteria``."""
        return [
            {"key": str(criterion.key), "enabled": criterion.enabled, "points": criterion.points}
            for criterion in self.criteria
        ]

    @classmethod
    def from_payload(
        cls, payload: Sequence[Mapping[str, Any]], *, mode: ScoringMode, version: int = 1
    ) -> ScoringConfig:
        """Reconstrói a configuração a partir do JSONB.

        Tolera ausência de ``enabled``/``points`` porque o JSON vem do banco e
        pode ter sido gravado por uma versão anterior do schema.
        """
        criteria = tuple(
            Criterion(
                key=CriterionKey(str(item["key"])),
                enabled=bool(item.get("enabled", True)),
                points=int(item.get("points", 0)),
            )
            for item in payload
        )
        return cls(mode=mode, criteria=criteria, version=version)


@dataclass(frozen=True, slots=True)
class ScoreResult:
    """Resultado da avaliação de um palpite contra um placar."""

    criterion_key: CriterionKey
    base_points: int
    matched_reasons: tuple[str, ...] = ()

    @property
    def is_hit(self) -> bool:
        return self.criterion_key is not CriterionKey.MISS


@dataclass(frozen=True, slots=True)
class RankEntry:
    """Uma linha do ranking, pronta para ser ordenada.

    ``criterion_hits`` conta quantas vezes cada critério bateu — é o que
    decide o desempate.
    """

    membership_id: int
    display_name: str
    points: int
    criterion_hits: dict[CriterionKey, int] = field(default_factory=dict)
    joined_order: int = 0
    """Posição de chegada no bolão. Menor = entrou antes. Desempate final."""

    predictions_made: int = 0
    fixtures_settled: int = 0


@dataclass(frozen=True, slots=True)
class RankedEntry:
    """``RankEntry`` já posicionada."""

    entry: RankEntry
    position: int
    previous_position: int | None = None

    @property
    def movement(self) -> int:
        """Positivo = subiu."""
        if self.previous_position is None:
            return 0
        return self.previous_position - self.position
