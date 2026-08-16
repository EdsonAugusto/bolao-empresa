"""Motor de pontuação.

PURO: sem I/O, sem banco, sem ``datetime.now()``. Recebe dataclasses e devolve
dataclasses. Cobertura mínima de 95%.

Uso típico::

    from app.scoring import Score, preset_for, ScoringMode, score

    resultado = score(Score(2, 1), Score(3, 1), preset_for(ScoringMode.CLASSIC))
    resultado.criterion_key   # CriterionKey.WINNER_AND_ONE_SCORE
    resultado.base_points     # 7
"""

from app.scoring.engine import (
    apply_multiplier,
    explain,
    matches,
    max_points_for,
    rank,
    satisfied_criteria,
    score,
    summarize,
    tiebreak_order,
)
from app.scoring.models import (
    CONFIGURABLE_KEYS,
    MAX_POINTS,
    MIN_POINTS,
    Criterion,
    CriterionKey,
    Outcome,
    RankedEntry,
    RankEntry,
    Score,
    ScoreResult,
    ScoringConfig,
    ScoringMode,
)
from app.scoring.presets import CLASSIC, CUSTOM_TEMPLATE, PRESETS, SIMPLE, preset_for

__all__ = [
    "CLASSIC",
    "CONFIGURABLE_KEYS",
    "CUSTOM_TEMPLATE",
    "MAX_POINTS",
    "MIN_POINTS",
    "PRESETS",
    "SIMPLE",
    "Criterion",
    "CriterionKey",
    "Outcome",
    "RankEntry",
    "RankedEntry",
    "Score",
    "ScoreResult",
    "ScoringConfig",
    "ScoringMode",
    "apply_multiplier",
    "explain",
    "matches",
    "max_points_for",
    "preset_for",
    "rank",
    "satisfied_criteria",
    "score",
    "summarize",
    "tiebreak_order",
]
