"""Presets de pontuação.

São pontos de partida, não jaulas: qualquer preset pode virar personalizado
mexendo em um critério. O modo personalizado é um superconjunto dos outros
dois — não há regra que o clássico consiga expressar e o personalizado não.
"""

from __future__ import annotations

from app.scoring.models import (
    CONFIGURABLE_KEYS,
    Criterion,
    CriterionKey,
    ScoringConfig,
    ScoringMode,
)


def _build(mode: ScoringMode, points: dict[CriterionKey, int]) -> ScoringConfig:
    """Monta a configuração mantendo os critérios desligados na lista.

    Guardar o critério desabilitado (em vez de omitir) faz a tela de
    configuração conseguir mostrar todas as opções sem inventar valores.
    """
    criteria = tuple(
        Criterion(key=key, enabled=key in points, points=points.get(key, 0))
        for key in CONFIGURABLE_KEYS
    )
    return ScoringConfig(mode=mode, criteria=criteria)


CLASSIC = _build(
    ScoringMode.CLASSIC,
    {
        CriterionKey.EXACT: 10,
        CriterionKey.WINNER_AND_ONE_SCORE: 7,
        CriterionKey.WINNER_ONLY: 5,
        CriterionKey.DRAW: 5,
        CriterionKey.ONE_SCORE_ONLY: 2,
        # winner_and_goal_diff fica de fora: no clássico ele se sobreporia ao
        # winner_and_one_score sem acrescentar nuance.
    },
)

SIMPLE = _build(
    ScoringMode.SIMPLE,
    {
        CriterionKey.EXACT: 10,
        CriterionKey.WINNER_ONLY: 7,
        CriterionKey.DRAW: 5,
    },
)

#: Base para quem vai personalizar: parte do clássico e mexe no que quiser.
CUSTOM_TEMPLATE = ScoringConfig(mode=ScoringMode.CUSTOM, criteria=CLASSIC.criteria)

PRESETS: dict[ScoringMode, ScoringConfig] = {
    ScoringMode.CLASSIC: CLASSIC,
    ScoringMode.SIMPLE: SIMPLE,
    ScoringMode.CUSTOM: CUSTOM_TEMPLATE,
}


def preset_for(mode: ScoringMode) -> ScoringConfig:
    return PRESETS[mode]


__all__ = ["CLASSIC", "CUSTOM_TEMPLATE", "PRESETS", "SIMPLE", "preset_for"]
