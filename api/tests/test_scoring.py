"""Motor de pontuação — tabela-verdade e propriedades.

Nada de banco e nada de FastAPI neste arquivo. Se um teste daqui precisar de
fixture assíncrona, o motor deixou de ser puro e isso é o bug.
"""

from __future__ import annotations

import pytest
from hypothesis import given
from hypothesis import strategies as st

from app.scoring import (
    CLASSIC,
    CUSTOM_TEMPLATE,
    SIMPLE,
    Criterion,
    CriterionKey,
    Outcome,
    RankEntry,
    Score,
    ScoringConfig,
    ScoringMode,
    apply_multiplier,
    explain,
    matches,
    max_points_for,
    preset_for,
    rank,
    satisfied_criteria,
    score,
    summarize,
    tiebreak_order,
)

# ---------------------------------------------------------------------------
# Score
# ---------------------------------------------------------------------------


@pytest.mark.parametrize(
    ("home", "away", "expected"),
    [
        (3, 1, Outcome.HOME),
        (1, 3, Outcome.AWAY),
        (2, 2, Outcome.DRAW),
        (0, 0, Outcome.DRAW),
    ],
)
def test_outcome(home: int, away: int, expected: Outcome) -> None:
    assert Score(home, away).outcome is expected


def test_placar_negativo_e_rejeitado() -> None:
    with pytest.raises(ValueError, match="negativo"):
        Score(-1, 0)


def test_score_e_imutavel_e_comparavel() -> None:
    assert Score(2, 1) == Score(2, 1)
    assert Score(2, 1) != Score(1, 2)
    assert str(Score(3, 0)) == "3x0"
    with pytest.raises(AttributeError):
        Score(1, 1).home = 2  # type: ignore[misc]


# ---------------------------------------------------------------------------
# Tabela-verdade do preset CLÁSSICO
# ---------------------------------------------------------------------------

# (resultado, palpite, pontos, critério)
CLASSIC_CASES = [
    # Jogo 3x1 — o caso canônico da especificação
    ((3, 1), (3, 1), 10, CriterionKey.EXACT),
    ((3, 1), (2, 1), 7, CriterionKey.WINNER_AND_ONE_SCORE),
    ((3, 1), (3, 2), 7, CriterionKey.WINNER_AND_ONE_SCORE),
    ((3, 1), (1, 0), 5, CriterionKey.WINNER_ONLY),
    ((3, 1), (4, 2), 5, CriterionKey.WINNER_ONLY),
    ((3, 1), (0, 1), 2, CriterionKey.ONE_SCORE_ONLY),
    ((3, 1), (3, 4), 2, CriterionKey.ONE_SCORE_ONLY),
    ((3, 1), (1, 3), 0, CriterionKey.MISS),
    ((3, 1), (0, 2), 0, CriterionKey.MISS),
    ((3, 1), (2, 2), 0, CriterionKey.MISS),
    # Jogo 2x2 — empate real
    ((2, 2), (2, 2), 10, CriterionKey.EXACT),
    ((2, 2), (1, 1), 5, CriterionKey.DRAW),
    ((2, 2), (0, 0), 5, CriterionKey.DRAW),
    ((2, 2), (2, 1), 0, CriterionKey.MISS),
    ((2, 2), (1, 2), 0, CriterionKey.MISS),
    ((2, 2), (3, 0), 0, CriterionKey.MISS),
    # Jogo 0x0
    ((0, 0), (0, 0), 10, CriterionKey.EXACT),
    ((0, 0), (1, 1), 5, CriterionKey.DRAW),
    ((0, 0), (1, 0), 0, CriterionKey.MISS),
    # Vitória do visitante
    ((0, 2), (0, 2), 10, CriterionKey.EXACT),
    ((0, 2), (1, 2), 7, CriterionKey.WINNER_AND_ONE_SCORE),
    ((0, 2), (0, 3), 7, CriterionKey.WINNER_AND_ONE_SCORE),
    ((0, 2), (1, 3), 5, CriterionKey.WINNER_ONLY),
    ((0, 2), (2, 0), 0, CriterionKey.MISS),
    ((0, 2), (0, 1), 7, CriterionKey.WINNER_AND_ONE_SCORE),
    ((0, 2), (2, 2), 2, CriterionKey.ONE_SCORE_ONLY),
    # Goleada
    ((5, 0), (5, 0), 10, CriterionKey.EXACT),
    ((5, 0), (5, 1), 7, CriterionKey.WINNER_AND_ONE_SCORE),
    ((5, 0), (1, 0), 7, CriterionKey.WINNER_AND_ONE_SCORE),
    ((5, 0), (2, 1), 5, CriterionKey.WINNER_ONLY),
    ((5, 0), (0, 5), 0, CriterionKey.MISS),
    # Palpitou empate: errou o vencedor, mas acertou o 0 do visitante.
    ((5, 0), (0, 0), 2, CriterionKey.ONE_SCORE_ONLY),
    ((5, 0), (0, 1), 0, CriterionKey.MISS),
]


@pytest.mark.parametrize(("actual", "prediction", "points", "criterion"), CLASSIC_CASES)
def test_preset_classico(
    actual: tuple[int, int],
    prediction: tuple[int, int],
    points: int,
    criterion: CriterionKey,
) -> None:
    result = score(Score(*prediction), Score(*actual), CLASSIC)

    assert result.base_points == points
    assert result.criterion_key is criterion


# ---------------------------------------------------------------------------
# Tabela-verdade do preset SIMPLES
# ---------------------------------------------------------------------------

SIMPLE_CASES = [
    ((3, 1), (3, 1), 10, CriterionKey.EXACT),
    ((3, 1), (2, 1), 7, CriterionKey.WINNER_ONLY),
    ((3, 1), (1, 0), 7, CriterionKey.WINNER_ONLY),
    # Sem one_score_only habilitado, acertar só um placar não vale nada.
    ((3, 1), (0, 1), 0, CriterionKey.MISS),
    ((3, 1), (1, 3), 0, CriterionKey.MISS),
    ((2, 2), (2, 2), 10, CriterionKey.EXACT),
    ((2, 2), (1, 1), 5, CriterionKey.DRAW),
    ((2, 2), (2, 1), 0, CriterionKey.MISS),
    ((0, 2), (1, 3), 7, CriterionKey.WINNER_ONLY),
]


@pytest.mark.parametrize(("actual", "prediction", "points", "criterion"), SIMPLE_CASES)
def test_preset_simples(
    actual: tuple[int, int],
    prediction: tuple[int, int],
    points: int,
    criterion: CriterionKey,
) -> None:
    result = score(Score(*prediction), Score(*actual), SIMPLE)

    assert result.base_points == points
    assert result.criterion_key is criterion


# ---------------------------------------------------------------------------
# Modo personalizado — onde a ordem de avaliação importa
# ---------------------------------------------------------------------------


def test_custom_com_vencedor_valendo_mais_que_placar_exato() -> None:
    """O caso que quebra qualquer implementação com ordem fixa.

    Organizador põe `winner_only=9` acima de `exact=5`. Quem acertou só o
    vencedor tem que levar 9 — não 5, e não zero.
    """
    config = CUSTOM_TEMPLATE.with_criterion(
        CriterionKey.WINNER_ONLY, enabled=True, points=9
    ).with_criterion(CriterionKey.EXACT, enabled=True, points=5)

    result = score(Score(1, 0), Score(3, 1), config)

    assert result.base_points == 9
    assert result.criterion_key is CriterionKey.WINNER_ONLY


def test_custom_placar_exato_leva_o_criterio_mais_valioso_que_satisfaz() -> None:
    """Quem crava o placar também acertou o vencedor.

    Com `winner_only` valendo mais que `exact`, cravar o placar não pode
    pagar menos do que chutar o vencedor — seria punir a previsão melhor.
    """
    config = CUSTOM_TEMPLATE.with_criterion(
        CriterionKey.WINNER_ONLY, enabled=True, points=9
    ).with_criterion(CriterionKey.EXACT, enabled=True, points=5)

    result = score(Score(3, 1), Score(3, 1), config)

    assert result.base_points == 9


def test_custom_com_saldo_de_gols_habilitado() -> None:
    config = CUSTOM_TEMPLATE.with_criterion(
        CriterionKey.WINNER_AND_GOAL_DIFF, enabled=True, points=8
    )

    # 3x1 e 4x2: mesmo vencedor, mesmo saldo, nenhum placar igual.
    result = score(Score(4, 2), Score(3, 1), config)

    assert result.criterion_key is CriterionKey.WINNER_AND_GOAL_DIFF
    assert result.base_points == 8


def test_custom_pode_desligar_tudo_e_ninguem_pontua() -> None:
    config = ScoringConfig(
        mode=ScoringMode.CUSTOM,
        criteria=tuple(
            Criterion(key=criterion.key, enabled=False, points=0) for criterion in CLASSIC.criteria
        ),
    )

    assert score(Score(3, 1), Score(3, 1), config).base_points == 0
    assert config.max_points == 0


def test_empate_de_pontos_resolve_pela_especificidade() -> None:
    """Dois critérios valendo o mesmo: vence o mais específico.

    Sem isso, a pontuação de um mesmo palpite poderia variar conforme a ordem
    em que a configuração foi carregada.
    """
    config = CUSTOM_TEMPLATE.with_criterion(
        CriterionKey.WINNER_ONLY, enabled=True, points=7
    ).with_criterion(CriterionKey.WINNER_AND_ONE_SCORE, enabled=True, points=7)

    result = score(Score(2, 1), Score(3, 1), config)

    assert result.criterion_key is CriterionKey.WINNER_AND_ONE_SCORE


# ---------------------------------------------------------------------------
# Regras transversais
# ---------------------------------------------------------------------------


def test_sem_palpite_e_zero() -> None:
    result = score(None, Score(3, 1), CLASSIC)

    assert result.base_points == 0
    assert result.criterion_key is CriterionKey.MISS
    assert result.is_hit is False


def test_draw_nao_se_sobrepoe_a_exact() -> None:
    """`draw` só entra quando o placar difere, senão os dois se sobrepõem."""
    assert matches(CriterionKey.DRAW, Score(2, 2), Score(2, 2)) is False
    assert matches(CriterionKey.DRAW, Score(1, 1), Score(2, 2)) is True


def test_criterios_de_vencedor_nao_se_aplicam_a_empate_real() -> None:
    """Num empate não há vencedor a acertar nem a errar.

    É por isso que 2x1 num jogo que terminou 2x2 não ganha nada, mesmo tendo
    acertado o "2" do mandante.
    """
    empate = Score(2, 2)

    assert matches(CriterionKey.WINNER_ONLY, Score(2, 1), empate) is False
    assert matches(CriterionKey.WINNER_AND_ONE_SCORE, Score(2, 1), empate) is False
    assert matches(CriterionKey.WINNER_AND_GOAL_DIFF, Score(2, 1), empate) is False
    assert matches(CriterionKey.ONE_SCORE_ONLY, Score(2, 1), empate) is False


def test_satisfied_criteria_lista_tudo_que_bateu() -> None:
    satisfeitos = satisfied_criteria(Score(3, 1), Score(3, 1))

    assert CriterionKey.EXACT in satisfeitos
    assert CriterionKey.WINNER_ONLY in satisfeitos
    assert CriterionKey.WINNER_AND_ONE_SCORE in satisfeitos
    assert CriterionKey.WINNER_AND_GOAL_DIFF in satisfeitos
    assert CriterionKey.DRAW not in satisfeitos


@pytest.mark.parametrize(("base", "multiplier", "expected"), [(10, 1, 10), (7, 2, 14), (5, 3, 15)])
def test_multiplicador_de_rodada(base: int, multiplier: int, expected: int) -> None:
    assert apply_multiplier(base, multiplier) == expected


def test_multiplicador_invalido() -> None:
    with pytest.raises(ValueError, match=">= 1"):
        apply_multiplier(10, 0)


# ---------------------------------------------------------------------------
# Configuração
# ---------------------------------------------------------------------------


def test_pontos_fora_da_faixa_sao_rejeitados() -> None:
    with pytest.raises(ValueError, match="fora da faixa"):
        Criterion(key=CriterionKey.EXACT, enabled=True, points=101)
    with pytest.raises(ValueError, match="fora da faixa"):
        Criterion(key=CriterionKey.EXACT, enabled=True, points=-1)


def test_miss_nao_pode_valer_pontos() -> None:
    with pytest.raises(ValueError, match="sempre 0"):
        Criterion(key=CriterionKey.MISS, enabled=True, points=3)


def test_criterio_repetido_e_rejeitado() -> None:
    with pytest.raises(ValueError, match="repetido"):
        ScoringConfig(
            mode=ScoringMode.CUSTOM,
            criteria=(
                Criterion(key=CriterionKey.EXACT, enabled=True, points=10),
                Criterion(key=CriterionKey.EXACT, enabled=True, points=5),
            ),
        )


def test_roundtrip_de_serializacao() -> None:
    payload = CLASSIC.to_payload()
    recovered = ScoringConfig.from_payload(payload, mode=ScoringMode.CLASSIC)

    assert recovered == CLASSIC
    assert recovered.evaluation_order == CLASSIC.evaluation_order


def test_with_criterion_nao_muta_o_original() -> None:
    alterado = CLASSIC.with_criterion(CriterionKey.EXACT, enabled=True, points=20)

    assert alterado.points_for(CriterionKey.EXACT) == 20
    assert CLASSIC.points_for(CriterionKey.EXACT) == 10


def test_points_for_ignora_criterio_desabilitado() -> None:
    assert CLASSIC.points_for(CriterionKey.WINNER_AND_GOAL_DIFF) == 0
    assert SIMPLE.points_for(CriterionKey.ONE_SCORE_ONLY) == 0


def test_preset_for_devolve_os_tres_modos() -> None:
    assert preset_for(ScoringMode.CLASSIC) is CLASSIC
    assert preset_for(ScoringMode.SIMPLE) is SIMPLE
    assert preset_for(ScoringMode.CUSTOM).mode is ScoringMode.CUSTOM


def test_max_points_for() -> None:
    assert max_points_for(CLASSIC, fixtures=10) == 100
    assert max_points_for(CLASSIC, fixtures=10, multiplier=2) == 200


# ---------------------------------------------------------------------------
# Ranking e desempate
# ---------------------------------------------------------------------------


def _entry(
    membership_id: int,
    points: int,
    hits: dict[CriterionKey, int] | None = None,
    joined_order: int = 0,
) -> RankEntry:
    return RankEntry(
        membership_id=membership_id,
        display_name=f"jogador {membership_id}",
        points=points,
        criterion_hits=hits or {},
        joined_order=joined_order,
    )


def test_ranking_ordena_por_pontos() -> None:
    ranked = rank([_entry(1, 50), _entry(2, 87), _entry(3, 12)], CLASSIC)

    assert [item.entry.membership_id for item in ranked] == [2, 1, 3]
    assert [item.position for item in ranked] == [1, 2, 3]


def test_desempate_usa_o_criterio_de_maior_valor_do_bolao() -> None:
    """Mesmos 87 pontos: quem tem mais placares exatos é o campeão."""
    ranked = rank(
        [
            _entry(1, 87, {CriterionKey.EXACT: 3, CriterionKey.WINNER_ONLY: 12}),
            _entry(2, 87, {CriterionKey.EXACT: 5, CriterionKey.WINNER_ONLY: 8}),
        ],
        CLASSIC,
    )

    assert ranked[0].entry.membership_id == 2
    assert ranked[0].position == 1
    assert ranked[1].position == 2


def test_desempate_segue_a_configuracao_e_nao_uma_lista_fixa() -> None:
    """Se o bolão valoriza mais o vencedor, o desempate valoriza também."""
    config = CUSTOM_TEMPLATE.with_criterion(
        CriterionKey.WINNER_ONLY, enabled=True, points=9
    ).with_criterion(CriterionKey.EXACT, enabled=True, points=5)

    ranked = rank(
        [
            _entry(1, 87, {CriterionKey.EXACT: 9, CriterionKey.WINNER_ONLY: 2}),
            _entry(2, 87, {CriterionKey.EXACT: 1, CriterionKey.WINNER_ONLY: 6}),
        ],
        config,
    )

    assert ranked[0].entry.membership_id == 2


def test_desempate_final_e_a_ordem_de_entrada_no_bolao() -> None:
    ranked = rank(
        [
            _entry(7, 40, {CriterionKey.EXACT: 2}, joined_order=9),
            _entry(3, 40, {CriterionKey.EXACT: 2}, joined_order=1),
        ],
        CLASSIC,
    )

    assert [item.entry.membership_id for item in ranked] == [3, 7]


def test_empate_genuino_divide_a_posicao() -> None:
    """Mesma pontuação e mesmos acertos: os dois são segundo lugar.

    O terceiro colocado real fica em quarto, como em qualquer classificação
    esportiva.
    """
    ranked = rank(
        [
            _entry(1, 90, {CriterionKey.EXACT: 5}),
            _entry(2, 80, {CriterionKey.EXACT: 3}, joined_order=1),
            _entry(3, 80, {CriterionKey.EXACT: 3}, joined_order=2),
            _entry(4, 70, {CriterionKey.EXACT: 1}),
        ],
        CLASSIC,
    )

    posicoes = {item.entry.membership_id: item.position for item in ranked}
    assert posicoes == {1: 1, 2: 2, 3: 2, 4: 4}


def test_movimentacao_de_posicao() -> None:
    ranked = rank(
        [_entry(1, 50), _entry(2, 80)],
        CLASSIC,
        previous_positions={1: 1, 2: 5},
    )

    por_id = {item.entry.membership_id: item for item in ranked}
    assert por_id[2].movement == 4  # subiu 4
    assert por_id[1].movement == -1  # caiu 1


def test_movimentacao_e_zero_na_primeira_apuracao() -> None:
    ranked = rank([_entry(1, 50)], CLASSIC)

    assert ranked[0].movement == 0
    assert ranked[0].previous_position is None


def test_ranking_vazio() -> None:
    assert rank([], CLASSIC) == ()


def test_tiebreak_order_segue_os_pontos() -> None:
    ordem = tiebreak_order(CLASSIC)

    assert ordem[0] is CriterionKey.EXACT
    assert ordem[1] is CriterionKey.WINNER_AND_ONE_SCORE
    assert CriterionKey.WINNER_AND_GOAL_DIFF not in ordem  # desabilitado no clássico


def test_summarize_conta_acertos_e_ignora_miss() -> None:
    resultados = [
        score(Score(3, 1), Score(3, 1), CLASSIC),
        score(Score(2, 1), Score(3, 1), CLASSIC),
        score(Score(1, 3), Score(3, 1), CLASSIC),
    ]

    assert summarize(resultados) == {
        CriterionKey.EXACT: 1,
        CriterionKey.WINNER_AND_ONE_SCORE: 1,
    }


# ---------------------------------------------------------------------------
# Explicação ao usuário
# ---------------------------------------------------------------------------


def test_explain_diz_o_que_bateu_e_o_que_nao_pontuou() -> None:
    explicacao = explain(Score(4, 2), Score(3, 1), CLASSIC)

    assert explicacao["points"] == 5
    assert explicacao["criterion"] == "winner_only"
    # Acertou o saldo também, mas o clássico não pontua saldo.
    assert "winner_and_goal_diff" in explicacao["not_scored_because_disabled"]  # type: ignore[operator]


def test_explain_sem_palpite() -> None:
    explicacao = explain(None, Score(3, 1), CLASSIC)

    assert explicacao["prediction"] is None
    assert explicacao["points"] == 0
    assert explicacao["also_satisfied"] == []


# ---------------------------------------------------------------------------
# Propriedades (hypothesis)
# ---------------------------------------------------------------------------

goals = st.integers(min_value=0, max_value=20)
scores = st.builds(Score, goals, goals)


@given(prediction=scores, actual=scores)
def test_pontuacao_nunca_negativa_nem_acima_do_teto(prediction: Score, actual: Score) -> None:
    result = score(prediction, actual, CLASSIC)

    assert 0 <= result.base_points <= CLASSIC.max_points


@given(prediction=scores, actual=scores)
def test_pontuacao_e_deterministica(prediction: Score, actual: Score) -> None:
    assert score(prediction, actual, CLASSIC) == score(prediction, actual, CLASSIC)


@given(prediction=scores, actual=scores)
def test_placar_exato_sempre_paga_o_maximo_no_classico(prediction: Score, actual: Score) -> None:
    """No clássico, nenhum palpite errado pode pagar mais que o exato."""
    pontos = score(prediction, actual, CLASSIC).base_points
    if prediction != actual:
        assert pontos < CLASSIC.points_for(CriterionKey.EXACT)


@given(actual=scores)
def test_cravar_o_placar_sempre_pontua_o_maximo(actual: Score) -> None:
    result = score(actual, actual, CLASSIC)

    assert result.base_points == CLASSIC.max_points
    assert result.criterion_key is CriterionKey.EXACT


@given(prediction=scores, actual=scores)
def test_o_criterio_devolvido_realmente_casa(prediction: Score, actual: Score) -> None:
    """Coerência: se o motor disse que foi X, o predicado de X tem que bater."""
    result = score(prediction, actual, CLASSIC)

    if result.is_hit:
        assert matches(result.criterion_key, prediction, actual)


@given(prediction=scores, actual=scores)
def test_nunca_paga_menos_que_um_criterio_habilitado_que_o_palpite_satisfaz(
    prediction: Score, actual: Score
) -> None:
    """A promessa central: você leva o melhor critério que qualifica."""
    result = score(prediction, actual, CLASSIC)
    satisfeitos = satisfied_criteria(prediction, actual)
    melhor = max(
        (CLASSIC.points_for(key) for key in satisfeitos),
        default=0,
    )

    assert result.base_points == melhor


@given(
    entries=st.lists(
        st.tuples(st.integers(1, 50), st.integers(0, 500)),
        min_size=1,
        max_size=20,
        unique_by=lambda item: item[0],
    )
)
def test_ranking_e_ordem_total_e_monotonica(entries: list[tuple[int, int]]) -> None:
    ranked = rank([_entry(mid, pts) for mid, pts in entries], CLASSIC)

    assert len(ranked) == len(entries)
    pontos = [item.entry.points for item in ranked]
    assert pontos == sorted(pontos, reverse=True)
    posicoes = [item.position for item in ranked]
    assert posicoes == sorted(posicoes)
    assert posicoes[0] == 1
