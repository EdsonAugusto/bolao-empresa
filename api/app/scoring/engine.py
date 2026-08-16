"""Motor de pontuação.

Função pura: mesmas entradas, mesma saída, sempre. Sem banco, sem relógio, sem
rede. É o núcleo do produto e a única parte cujo erro é impossível de esconder
do usuário.

Modelo mental
-------------
Cada critério é um **predicado independente** sobre (palpite, resultado). Um
palpite pode satisfazer vários ao mesmo tempo — quem acerta o placar exato
também acertou o vencedor. A regra é: **o participante leva o critério
habilitado de maior pontuação que ele satisfaz.**

Isso é o que faz o modo personalizado funcionar sem surpresa. Se o organizador
põe ``winner_only=9`` e ``exact=5``, quem cravar o placar leva 9, porque também
acertou o vencedor e 9 é o melhor que ele qualifica. Uma ordem fixa de
avaliação daria 5 e o organizador teria que explicar isso a alguém.
"""

from __future__ import annotations

from collections.abc import Callable, Iterable, Sequence

from app.scoring.models import (
    Criterion,
    CriterionKey,
    RankedEntry,
    RankEntry,
    Score,
    ScoreResult,
    ScoringConfig,
)

# ---------------------------------------------------------------------------
# Predicados
# ---------------------------------------------------------------------------
#
# Convenções que valem para todos:
#
# - "vencedor" só existe em jogo decidido. Num empate real não há vencedor a
#   acertar nem a errar, então os critérios de vencedor não se aplicam — nem
#   para dar ponto, nem para tirar. É por isso que num 2x2 o palpite 2x1 não
#   ganha nada, mesmo tendo acertado o "2" do mandante: `one_score_only` exige
#   um vencedor que tenha sido errado.
# - Pênaltis nunca chegam aqui. O placar considerado é tempo normal +
#   prorrogação, resolvido antes de chamar o motor.


def _exact(prediction: Score, actual: Score) -> bool:
    return prediction == actual


def _winner_and_one_score(prediction: Score, actual: Score) -> bool:
    if actual.is_draw or prediction.outcome is not actual.outcome:
        return False
    return prediction.home == actual.home or prediction.away == actual.away


def _winner_and_goal_diff(prediction: Score, actual: Score) -> bool:
    if actual.is_draw or prediction.outcome is not actual.outcome:
        return False
    return prediction.goal_diff == actual.goal_diff


def _draw(prediction: Score, actual: Score) -> bool:
    # `exact` já cobre o empate cravado; aqui só entra o empate com placar
    # diferente, senão os dois critérios se sobreporiam e a configuração
    # personalizada ficaria ambígua.
    return actual.is_draw and prediction.is_draw and prediction != actual


def _winner_only(prediction: Score, actual: Score) -> bool:
    return not actual.is_draw and prediction.outcome is actual.outcome


def _one_score_only(prediction: Score, actual: Score) -> bool:
    if actual.is_draw or prediction.outcome is actual.outcome:
        return False
    return prediction.home == actual.home or prediction.away == actual.away


Predicate = Callable[[Score, Score], bool]

PREDICATES: dict[CriterionKey, Predicate] = {
    CriterionKey.EXACT: _exact,
    CriterionKey.WINNER_AND_ONE_SCORE: _winner_and_one_score,
    CriterionKey.WINNER_AND_GOAL_DIFF: _winner_and_goal_diff,
    CriterionKey.DRAW: _draw,
    CriterionKey.WINNER_ONLY: _winner_only,
    CriterionKey.ONE_SCORE_ONLY: _one_score_only,
}

REASONS: dict[CriterionKey, str] = {
    CriterionKey.EXACT: "placar exato",
    CriterionKey.WINNER_AND_ONE_SCORE: "vencedor e o placar de um dos times",
    CriterionKey.WINNER_AND_GOAL_DIFF: "vencedor e o saldo de gols",
    CriterionKey.DRAW: "empate, com placar diferente",
    CriterionKey.WINNER_ONLY: "vencedor",
    CriterionKey.ONE_SCORE_ONLY: "o placar de um time, mas errou o vencedor",
    CriterionKey.MISS: "nada",
}

MISS = ScoreResult(criterion_key=CriterionKey.MISS, base_points=0, matched_reasons=())


def matches(criterion_key: CriterionKey, prediction: Score, actual: Score) -> bool:
    """O palpite satisfaz este critério? Ignora se ele está habilitado."""
    predicate = PREDICATES.get(criterion_key)
    return False if predicate is None else predicate(prediction, actual)


def satisfied_criteria(prediction: Score, actual: Score) -> tuple[CriterionKey, ...]:
    """Todos os critérios que o palpite satisfaz, do mais específico ao menos.

    Serve para explicar a pontuação ao usuário: "você acertou o vencedor e o
    saldo, mas o bolão só pontua placar exato".
    """
    return tuple(key for key, predicate in PREDICATES.items() if predicate(prediction, actual))


def score(prediction: Score | None, actual: Score, config: ScoringConfig) -> ScoreResult:
    """Pontua um palpite. Função pura.

    ``prediction=None`` significa que a pessoa não palpitou antes do apito —
    zero, sem exceção.
    """
    if prediction is None:
        return MISS

    for criterion in config.evaluation_order:
        if matches(criterion.key, prediction, actual):
            return ScoreResult(
                criterion_key=criterion.key,
                base_points=criterion.points,
                matched_reasons=(REASONS[criterion.key],),
            )
    return MISS


def apply_multiplier(base_points: int, multiplier: int) -> int:
    """Aplica o multiplicador da rodada. Inteiro sempre, nunca float."""
    if multiplier < 1:
        raise ValueError(f"multiplicador precisa ser >= 1: {multiplier}")
    return base_points * multiplier


# ---------------------------------------------------------------------------
# Ranking e desempate
# ---------------------------------------------------------------------------


def tiebreak_order(config: ScoringConfig) -> tuple[CriterionKey, ...]:
    """Ordem dos critérios no desempate.

    Derivada da configuração, não fixa: quem tem mais acertos no critério que
    **este bolão** mais valoriza leva vantagem. Se o organizador decidiu que
    acertar o vencedor vale mais que cravar o placar, o desempate segue essa
    decisão.
    """
    return tuple(criterion.key for criterion in config.evaluation_order)


def _sort_key(entry: RankEntry, order: Sequence[CriterionKey]) -> tuple[int, ...]:
    """Chave de ordenação. Tudo negativo porque ordenamos crescente.

    Sequência de desempate:

    1. pontos totais
    2. acertos em cada critério, na ordem de valor do bolão
    3. quem entrou antes no bolão

    O último passo garante ordem total: nunca há duas linhas empatadas de
    verdade, e o campeão nunca depende da ordem em que o banco devolveu as
    linhas.
    """
    hits = tuple(-entry.criterion_hits.get(key, 0) for key in order)
    return (-entry.points, *hits, entry.joined_order, entry.membership_id)


def rank(
    entries: Iterable[RankEntry],
    config: ScoringConfig,
    previous_positions: dict[int, int] | None = None,
) -> tuple[RankedEntry, ...]:
    """Ordena o ranking e atribui posições.

    Participantes genuinamente empatados — mesma pontuação e mesmos acertos em
    todos os critérios — **dividem a posição**. O que os separa é só a ordem de
    exibição, resolvida pela data de entrada. Um pódio que inventa diferença
    onde não existe é pior do que um pódio com dois segundos lugares.
    """
    order = tiebreak_order(config)
    previous = previous_positions or {}

    ordered = sorted(entries, key=lambda entry: _sort_key(entry, order))

    ranked: list[RankedEntry] = []
    last_comparable: tuple[int, ...] | None = None
    last_position = 0

    for index, entry in enumerate(ordered, start=1):
        # Só pontos e acertos definem empate real; a data de entrada é
        # desempate de exibição e fica de fora da comparação.
        comparable = _sort_key(entry, order)[: 1 + len(order)]
        if comparable == last_comparable:
            position = last_position
        else:
            position = index
            last_comparable = comparable
            last_position = index

        ranked.append(
            RankedEntry(
                entry=entry,
                position=position,
                previous_position=previous.get(entry.membership_id),
            )
        )

    return tuple(ranked)


def summarize(results: Iterable[ScoreResult]) -> dict[CriterionKey, int]:
    """Conta acertos por critério — é o que alimenta o desempate."""
    hits: dict[CriterionKey, int] = {}
    for result in results:
        if result.is_hit:
            hits[result.criterion_key] = hits.get(result.criterion_key, 0) + 1
    return hits


def max_points_for(config: ScoringConfig, fixtures: int, multiplier: int = 1) -> int:
    """Teto de pontuação. Usado em barra de progresso e em teste de sanidade."""
    return config.max_points * fixtures * multiplier


def explain(prediction: Score | None, actual: Score, config: ScoringConfig) -> dict[str, object]:
    """Explicação legível da pontuação.

    Existe porque "por que eu levei 7 e ele 10" é o ticket de suporte número um
    desse tipo de produto, e a resposta tem que sair do mesmo código que
    pontuou — não de uma reimplementação na tela.
    """
    result = score(prediction, actual, config)
    satisfied = () if prediction is None else satisfied_criteria(prediction, actual)
    enabled_keys = {criterion.key for criterion in config.enabled}

    return {
        "prediction": None if prediction is None else str(prediction),
        "actual": str(actual),
        "criterion": str(result.criterion_key),
        "points": result.base_points,
        "reason": REASONS[result.criterion_key],
        "also_satisfied": [str(key) for key in satisfied if key is not result.criterion_key],
        "not_scored_because_disabled": [str(key) for key in satisfied if key not in enabled_keys],
    }


__all__ = [
    "Criterion",
    "apply_multiplier",
    "explain",
    "matches",
    "max_points_for",
    "rank",
    "satisfied_criteria",
    "score",
    "summarize",
    "tiebreak_order",
]
