"""Apuração, ranking e recomputação.

O teste principal é o de ponta a ponta: 10 participantes, 5 jogos, e uma tabela
de resultados esperados escrita à mão e conferida critério a critério. Se o
motor, o multiplicador ou o desempate mudarem sem querer, esse teste cai.
"""

from __future__ import annotations

from datetime import UTC, datetime, timedelta

import pytest
from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from app.models import (
    Fixture,
    FixtureStatus,
    Membership,
    PredictionScore,
    Standing,
    StandingsSnapshot,
)
from app.services import pools as pool_service
from app.services import predictions as prediction_service
from app.services import settlement as settlement_service
from tests.factories import (
    add_member,
    make_fixture,
    make_pool,
    make_round,
    make_season,
    make_team,
    make_user,
)

pytestmark = pytest.mark.integration


# Resultados reais da rodada.
RESULTADOS = {
    "f1": (3, 1),
    "f2": (2, 2),
    "f3": (0, 1),
    "f4": (1, 0),
    "f5": (4, 2),
}

# Palpite de cada participante, jogo a jogo.
PALPITES: dict[str, dict[str, tuple[int, int]]] = {
    "Ana": {"f1": (3, 1), "f2": (2, 2), "f3": (0, 1), "f4": (1, 0), "f5": (4, 2)},
    "Bruno": {"f1": (2, 1), "f2": (1, 1), "f3": (0, 2), "f4": (2, 0), "f5": (4, 1)},
    "Carla": {"f1": (1, 0), "f2": (0, 0), "f3": (1, 2), "f4": (3, 1), "f5": (3, 1)},
    "Diego": {"f1": (0, 1), "f2": (2, 1), "f3": (1, 0), "f4": (0, 1), "f5": (2, 4)},
    "Elena": {},
    "Felipe": {"f1": (3, 1)},
    "Gabi": {"f1": (3, 2), "f2": (3, 3), "f3": (0, 1), "f4": (1, 0), "f5": (0, 0)},
    "Hugo": {"f1": (5, 1), "f2": (2, 2), "f3": (0, 3), "f4": (1, 2), "f5": (4, 2)},
    "Ines": {"f1": (1, 1), "f2": (1, 2), "f3": (0, 1), "f4": (0, 0), "f5": (4, 2)},
    "Joao": {"f1": (3, 0), "f2": (2, 2), "f3": (0, 1), "f4": (2, 1), "f5": (4, 2)},
}

# Tabela conferida à mão com o preset CLÁSSICO
# (exato 10 · vencedor+1 placar 7 · vencedor 5 · empate 5 · 1 placar 2).
#
# Ana    50 = 10+10+10+10+10   cravou os cinco
# Joao   42 = 7+10+10+5+10
# Hugo   36 = 7+10+7+2+10
# Bruno  33 = 7+5+7+7+7
# Gabi   32 = 7+5+10+10+0
# Carla  25 = 5+5+5+5+5        acertou só o vencedor em tudo
# Ines   24 = 2+0+10+2+10
# Felipe 10 = 10               palpitou num jogo só
# Diego   2 = 2+0+0+0+0
# Elena   0 = não palpitou
ESPERADO = {
    "Ana": 50,
    "Joao": 42,
    "Hugo": 36,
    "Bruno": 33,
    "Gabi": 32,
    "Carla": 25,
    "Ines": 24,
    "Felipe": 10,
    "Diego": 2,
    "Elena": 0,
}

POSICOES = {
    "Ana": 1,
    "Joao": 2,
    "Hugo": 3,
    "Bruno": 4,
    "Gabi": 5,
    "Carla": 6,
    "Ines": 7,
    "Felipe": 8,
    "Diego": 9,
    "Elena": 10,
}


async def _montar_rodada(session: AsyncSession, *, year: int = 2030, multiplier: int = 1):
    """Bolão com 10 participantes, 5 jogos e todos os palpites gravados."""
    dono = await make_user(session, f"ana{year}@teste.local", "Ana")
    season = await make_season(session, year)
    rodada = await make_round(session, season)

    times = {}
    for nome in ["Alfa", "Beta", "Gama", "Delta", "Epsilon", "Zeta", "Eta", "Teta", "Iota", "Kapa"]:
        times[nome] = await make_team(session, season, f"{nome} {year}")

    pares = [
        ("f1", "Alfa", "Beta"),
        ("f2", "Gama", "Delta"),
        ("f3", "Epsilon", "Zeta"),
        ("f4", "Eta", "Teta"),
        ("f5", "Iota", "Kapa"),
    ]
    fixtures = {
        chave: await make_fixture(
            session, season, rodada, times[casa], times[fora], kickoff_in=timedelta(hours=2)
        )
        for chave, casa, fora in pares
    }

    pool = await make_pool(
        session, dono, season, rounds=[rodada], name=f"Bolão {year}", multiplier=multiplier
    )

    membros: dict[str, Membership] = {}
    membro_dono = await pool_service.get_membership(session, pool.id, dono.id)
    assert membro_dono is not None
    membros["Ana"] = membro_dono

    for nome in PALPITES:
        if nome == "Ana":
            continue
        usuario = await make_user(session, f"{nome.lower()}{year}@teste.local", nome)
        membros[nome] = await add_member(session, pool, usuario, nome)

    for nome, palpites in PALPITES.items():
        for chave, (casa, fora) in palpites.items():
            await prediction_service.upsert_prediction(
                session,
                membership=membros[nome],
                fixture=fixtures[chave],
                home_goals=casa,
                away_goals=fora,
            )

    return pool, fixtures, membros, rodada


async def _encerrar(session: AsyncSession, fixtures: dict[str, Fixture]) -> None:
    """Marca todos os jogos como terminados com o placar real."""
    for chave, fixture in fixtures.items():
        casa, fora = RESULTADOS[chave]
        fixture.home_ft = casa
        fixture.away_ft = fora
        fixture.status = FixtureStatus.FINISHED
        fixture.kickoff_at = datetime.now(UTC) - timedelta(hours=3)
    await session.flush()


async def _pontos_por_nome(session: AsyncSession, pool_id: int) -> dict[str, int]:
    rows = (
        await session.execute(
            select(Membership.display_name, Standing.points)
            .join(Standing, Standing.membership_id == Membership.id)
            .where(Standing.pool_id == pool_id)
        )
    ).all()
    return dict(rows)


async def _posicoes_por_nome(session: AsyncSession, pool_id: int) -> dict[str, int]:
    rows = (
        await session.execute(
            select(Membership.display_name, Standing.position)
            .join(Standing, Standing.membership_id == Membership.id)
            .where(Standing.pool_id == pool_id)
        )
    ).all()
    return dict(rows)


# ---------------------------------------------------------------------------
# Ponta a ponta
# ---------------------------------------------------------------------------


async def test_rodada_completa_bate_com_a_tabela_esperada(db_session: AsyncSession) -> None:
    pool, fixtures, _, _ = await _montar_rodada(db_session)
    await _encerrar(db_session, fixtures)

    for fixture in fixtures.values():
        await settlement_service.settle_fixture(db_session, fixture.id)

    assert await _pontos_por_nome(db_session, pool.id) == ESPERADO


async def test_posicoes_do_ranking(db_session: AsyncSession) -> None:
    pool, fixtures, _, _ = await _montar_rodada(db_session, year=2031)
    await _encerrar(db_session, fixtures)
    for fixture in fixtures.values():
        await settlement_service.settle_fixture(db_session, fixture.id)

    assert await _posicoes_por_nome(db_session, pool.id) == POSICOES


async def test_apuracao_e_idempotente(db_session: AsyncSession) -> None:
    """Rodar de novo não duplica ponto nem muda o resultado."""
    pool, fixtures, _, _ = await _montar_rodada(db_session, year=2032)
    await _encerrar(db_session, fixtures)

    for fixture in fixtures.values():
        await settlement_service.settle_fixture(db_session, fixture.id)
    primeira = await _pontos_por_nome(db_session, pool.id)

    for _ in range(3):
        for fixture in fixtures.values():
            await settlement_service.settle_fixture(db_session, fixture.id)
    terceira = await _pontos_por_nome(db_session, pool.id)

    assert primeira == terceira == ESPERADO

    total_scores = len(
        (
            await db_session.scalars(
                select(PredictionScore.id).where(PredictionScore.pool_id == pool.id)
            )
        ).all()
    )
    palpites = sum(len(itens) for itens in PALPITES.values())
    assert total_scores == palpites


async def test_multiplicador_de_rodada_dobra_a_pontuacao(db_session: AsyncSession) -> None:
    pool, fixtures, _, _ = await _montar_rodada(db_session, year=2033, multiplier=2)
    await _encerrar(db_session, fixtures)
    for fixture in fixtures.values():
        await settlement_service.settle_fixture(db_session, fixture.id)

    pontos = await _pontos_por_nome(db_session, pool.id)

    assert pontos == {nome: valor * 2 for nome, valor in ESPERADO.items()}


# ---------------------------------------------------------------------------
# Correção de placar
# ---------------------------------------------------------------------------


async def test_correcao_de_placar_recalcula_e_muda_o_ranking(
    db_session: AsyncSession,
) -> None:
    """VAR, W.O., tribunal. Acontece — e não pode exigir mexer no banco à mão."""
    pool, fixtures, _, _ = await _montar_rodada(db_session, year=2034)
    await _encerrar(db_session, fixtures)
    for fixture in fixtures.values():
        await settlement_service.settle_fixture(db_session, fixture.id)

    assert (await _pontos_por_nome(db_session, pool.id))["Ana"] == 50

    # O f1 vira 2x1 no tribunal. Ana tinha cravado 3x1 e perde os 10.
    f1 = fixtures["f1"]
    f1.home_ft = 2
    f1.away_ft = 1
    await db_session.flush()

    resultado = await settlement_service.settle_fixture(db_session, f1.id)
    pontos = await _pontos_por_nome(db_session, pool.id)

    assert resultado.settled
    # Ana: perde o exato (10) e passa a levar vencedor + 1 placar (7).
    assert pontos["Ana"] == 47
    # Bruno tinha cravado 2x1 sem saber: agora leva 10 em vez de 7.
    assert pontos["Bruno"] == 36
    # Quem não palpitou no f1 não muda.
    assert pontos["Elena"] == 0
    assert pontos["Felipe"] == 7  # tinha 10 pelo exato, agora vencedor + 1 placar


async def test_jogo_cancelado_nao_pontua_ninguem(db_session: AsyncSession) -> None:
    pool, fixtures, _, _ = await _montar_rodada(db_session, year=2035)
    await _encerrar(db_session, fixtures)
    for fixture in fixtures.values():
        await settlement_service.settle_fixture(db_session, fixture.id)

    f5 = fixtures["f5"]
    f5.status = FixtureStatus.CANCELLED
    await db_session.flush()
    await settlement_service.settle_fixture(db_session, f5.id)

    pontos = await _pontos_por_nome(db_session, pool.id)

    # Ana tinha 10 no f5; sem o jogo, fica com 40.
    assert pontos["Ana"] == 40
    restantes = len(
        (
            await db_session.scalars(
                select(PredictionScore.id).where(PredictionScore.fixture_id == f5.id)
            )
        ).all()
    )
    assert restantes == 0


async def test_jogo_sem_placar_nao_e_apurado(db_session: AsyncSession) -> None:
    _pool, fixtures, _membros, _rodada = await _montar_rodada(db_session, year=2036)
    f1 = fixtures["f1"]
    f1.status = FixtureStatus.FINISHED
    await db_session.flush()

    resultado = await settlement_service.settle_fixture(db_session, f1.id)

    assert not resultado.settled
    assert "sem placar" in (resultado.skipped_reason or "")


async def test_prorrogacao_conta_e_penaltis_nao(db_session: AsyncSession) -> None:
    """Regra do produto: tempo normal + prorrogação. Pênaltis decidem quem
    avança, não o resultado do jogo."""
    _pool, fixtures, membros, _rodada = await _montar_rodada(db_session, year=2037)
    await _encerrar(db_session, fixtures)

    f1 = fixtures["f1"]
    # Terminou 3x1 no normal, 4x1 na prorrogação, e 5x4 nos pênaltis.
    f1.home_et = 4
    f1.away_et = 1
    f1.home_pen = 5
    f1.away_pen = 4
    await db_session.flush()
    await settlement_service.settle_fixture(db_session, f1.id)

    score = await db_session.scalar(
        select(PredictionScore).where(
            PredictionScore.fixture_id == f1.id,
            PredictionScore.membership_id == membros["Ana"].id,
        )
    )

    # Ana palpitou 3x1: com a prorrogação o placar válido é 4x1, então ela
    # levou vencedor + 1 placar (o "1" do visitante), não o exato.
    assert score is not None
    assert score.base_points == 7


# ---------------------------------------------------------------------------
# Histórico e recomputação
# ---------------------------------------------------------------------------


async def test_snapshot_da_rodada_e_gravado_quando_ela_termina(
    db_session: AsyncSession,
) -> None:
    pool, fixtures, _, rodada = await _montar_rodada(db_session, year=2038)
    await _encerrar(db_session, fixtures)
    for fixture in fixtures.values():
        await settlement_service.settle_fixture(db_session, fixture.id)

    snapshot = await db_session.scalar(
        select(StandingsSnapshot).where(
            StandingsSnapshot.pool_id == pool.id, StandingsSnapshot.round_id == rodada.id
        )
    )

    assert snapshot is not None
    assert len(snapshot.payload) == 10
    lider = min(snapshot.payload, key=lambda item: item["position"])
    assert lider["points"] == 50
    assert lider["round_points"] == 50


async def test_ranking_da_rodada(db_session: AsyncSession) -> None:
    pool, fixtures, _, rodada = await _montar_rodada(db_session, year=2039)
    await _encerrar(db_session, fixtures)
    for fixture in fixtures.values():
        await settlement_service.settle_fixture(db_session, fixture.id)

    ranking = await settlement_service.round_standings(db_session, pool.id, rodada.id)

    assert ranking[0]["display_name"] == "Ana"
    assert ranking[0]["points"] == 50
    assert ranking[0]["position"] == 1


async def test_recompute_pool_reconstroi_tudo(db_session: AsyncSession) -> None:
    """Botão de emergência: apaga e refaz, sem resíduo."""
    pool, fixtures, _, _ = await _montar_rodada(db_session, year=2040)
    await _encerrar(db_session, fixtures)
    for fixture in fixtures.values():
        await settlement_service.settle_fixture(db_session, fixture.id)

    # Sujeira: um standing com pontuação inventada.
    standing = await db_session.scalar(select(Standing).where(Standing.pool_id == pool.id))
    assert standing is not None
    standing.points = 9999
    await db_session.flush()

    resultado = await settlement_service.recompute_pool(db_session, pool.id)

    assert resultado["fixtures"] == 5
    assert await _pontos_por_nome(db_session, pool.id) == ESPERADO


async def test_movimentacao_de_posicao_e_registrada(db_session: AsyncSession) -> None:
    pool, fixtures, _, _ = await _montar_rodada(db_session, year=2041)
    await _encerrar(db_session, fixtures)

    # Apura só o f3, em que Ines cravou e Ana também.
    await settlement_service.settle_fixture(db_session, fixtures["f3"].id)
    # Agora o resto.
    for chave, fixture in fixtures.items():
        if chave != "f3":
            await settlement_service.settle_fixture(db_session, fixture.id)

    standings = (
        await db_session.scalars(select(Standing).where(Standing.pool_id == pool.id))
    ).all()

    assert any(item.previous_position is not None for item in standings)
    assert all(item.computed_at is not None for item in standings)
