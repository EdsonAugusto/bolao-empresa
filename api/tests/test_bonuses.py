"""Palpites de bônus: mata-mata e temporada."""

from __future__ import annotations

from datetime import UTC, datetime, timedelta

import pytest
from sqlalchemy import select
from sqlalchemy.exc import DBAPIError
from sqlalchemy.ext.asyncio import AsyncSession

from app.models import (
    BonusKind,
    BonusPrediction,
    FixtureStatus,
    ScoringConfig,
    Season,
    Stage,
    Standing,
)
from app.services import bonuses as bonus_service
from app.services import pools as pool_service
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


async def _cenario(session: AsyncSession, year: int):
    dono = await make_user(session, f"dono{year}@teste.local", "Dono")
    rival = await make_user(session, f"rival{year}@teste.local", "Rival")
    season = await make_season(session, year)
    rodada = await make_round(session, season)
    casa = await make_team(session, season, f"Casa {year}")
    fora = await make_team(session, season, f"Fora {year}")
    fixture = await make_fixture(session, season, rodada, casa, fora, kickoff_in=timedelta(hours=3))
    pool = await make_pool(session, dono, season, rounds=[rodada], name=f"Bolão {year}")

    config = await pool_service.active_config(session, pool.id)
    config.knockout_advance_points = 6
    config.champion_points = 20
    config.top4_points = 5
    config.relegated_points = 4
    await session.flush()

    m_dono = await pool_service.get_membership(session, pool.id, dono.id)
    assert m_dono is not None
    m_rival = await add_member(session, pool, rival, "Rival")
    return pool, season, fixture, casa, fora, m_dono, m_rival


# ---------------------------------------------------------------------------
# Mata-mata
# ---------------------------------------------------------------------------


async def test_acertar_quem_avanca_pontua(db_session: AsyncSession) -> None:
    pool, _season, fixture, casa, fora, m_dono, m_rival = await _cenario(db_session, 2060)

    await bonus_service.save_bonus(
        db_session,
        membership=m_dono,
        kind=BonusKind.KNOCKOUT_ADVANCE,
        team_ids=[casa.id],
        reference_id=fixture.id,
        locks_at=fixture.kickoff_at,
    )
    await bonus_service.save_bonus(
        db_session,
        membership=m_rival,
        kind=BonusKind.KNOCKOUT_ADVANCE,
        team_ids=[fora.id],
        reference_id=fixture.id,
        locks_at=fixture.kickoff_at,
    )

    fixture.status = FixtureStatus.FINISHED
    fixture.home_ft, fixture.away_ft = 2, 1
    fixture.advancing_team_id = casa.id
    await db_session.flush()

    resultado = await bonus_service.settle_knockout(db_session, fixture.id, pool.id)

    assert resultado.settled == 2
    assert resultado.points_awarded == 6

    pontos = {
        item.membership_id: item.points_awarded
        for item in (await db_session.scalars(select(BonusPrediction))).all()
    }
    assert pontos[m_dono.id] == 6
    assert pontos[m_rival.id] == 0


async def test_quem_avanca_pode_nao_ser_quem_venceu(db_session: AsyncSession) -> None:
    """Nos pênaltis o jogo empata, mas alguém classifica.

    É o único lugar do produto em que os pênaltis decidem alguma coisa — e o
    bônus tem que seguir o classificado, não o placar.
    """
    pool, _season, fixture, _casa, fora, _m_dono, m_rival = await _cenario(db_session, 2061)

    await bonus_service.save_bonus(
        db_session,
        membership=m_rival,
        kind=BonusKind.KNOCKOUT_ADVANCE,
        team_ids=[fora.id],
        reference_id=fixture.id,
        locks_at=fixture.kickoff_at,
    )

    fixture.status = FixtureStatus.FINISHED
    fixture.home_ft, fixture.away_ft = 1, 1
    fixture.home_pen, fixture.away_pen = 3, 5
    fixture.advancing_team_id = fora.id  # perdeu nos 90, passou nos pênaltis
    await db_session.flush()

    await bonus_service.settle_knockout(db_session, fixture.id, pool.id)

    palpite = await db_session.scalar(
        select(BonusPrediction).where(BonusPrediction.membership_id == m_rival.id)
    )
    assert palpite is not None
    assert palpite.points_awarded == 6


async def test_bolao_que_nao_pontua_mata_mata_ignora(db_session: AsyncSession) -> None:
    pool, _season, fixture, casa, _fora, m_dono, _m = await _cenario(db_session, 2062)
    config = await pool_service.active_config(db_session, pool.id)
    config.knockout_advance_points = 0
    await db_session.flush()

    await bonus_service.save_bonus(
        db_session,
        membership=m_dono,
        kind=BonusKind.KNOCKOUT_ADVANCE,
        team_ids=[casa.id],
        reference_id=fixture.id,
        locks_at=fixture.kickoff_at,
    )
    fixture.status = FixtureStatus.FINISHED
    fixture.advancing_team_id = casa.id
    await db_session.flush()

    resultado = await bonus_service.settle_knockout(db_session, fixture.id, pool.id)

    assert resultado.settled == 0
    assert "não pontua mata-mata" in (resultado.skipped_reason or "")


async def test_banco_recusa_bonus_depois_da_trava(db_session: AsyncSession) -> None:
    _pool, _season, fixture, casa, _fora, m_dono, _m = await _cenario(db_session, 2063)

    with pytest.raises((bonus_service.BonusLocked, DBAPIError)):
        await bonus_service.save_bonus(
            db_session,
            membership=m_dono,
            kind=BonusKind.KNOCKOUT_ADVANCE,
            team_ids=[casa.id],
            reference_id=fixture.id,
            locks_at=datetime.now(UTC) - timedelta(minutes=1),
        )


# ---------------------------------------------------------------------------
# Temporada
# ---------------------------------------------------------------------------


async def test_campeao_g4_e_rebaixados(db_session: AsyncSession) -> None:
    pool, season, _fixture, casa, fora, m_dono, m_rival = await _cenario(db_session, 2064)
    terceiro = await make_team(db_session, season, "Terceiro 2064")
    quarto = await make_team(db_session, season, "Quarto 2064")
    lanterna = await make_team(db_session, season, "Lanterna 2064")

    trava = datetime.now(UTC) + timedelta(days=1)

    # Dono crava o campeão e 3 dos 4 do G-4.
    await bonus_service.save_bonus(
        db_session,
        membership=m_dono,
        kind=BonusKind.CHAMPION,
        team_ids=[casa.id],
        locks_at=trava,
    )
    await bonus_service.save_bonus(
        db_session,
        membership=m_dono,
        kind=BonusKind.TOP4,
        team_ids=[casa.id, fora.id, terceiro.id, lanterna.id],
        locks_at=trava,
    )
    # Rival erra o campeão.
    await bonus_service.save_bonus(
        db_session,
        membership=m_rival,
        kind=BonusKind.CHAMPION,
        team_ids=[lanterna.id],
        locks_at=trava,
    )
    await bonus_service.save_bonus(
        db_session,
        membership=m_rival,
        kind=BonusKind.RELEGATED,
        team_ids=[lanterna.id],
        locks_at=trava,
    )

    season.outcome = {
        "champion_team_id": casa.id,
        "top4_team_ids": [casa.id, fora.id, terceiro.id, quarto.id],
        "relegated_team_ids": [lanterna.id],
    }
    await db_session.flush()

    resultado = await bonus_service.settle_season_bonuses(db_session, pool.id)

    assert resultado.settled == 4
    pontos = await bonus_service.bonus_points_by_membership(db_session, pool.id)
    # Dono: campeão 20 + 3 acertos no G-4 a 5 pontos cada = 35
    assert pontos[m_dono.id] == 35
    # Rival: campeão errado 0 + 1 rebaixado certo a 4 pontos = 4
    assert pontos[m_rival.id] == 4


async def test_sem_desfecho_declarado_nao_apura(db_session: AsyncSession) -> None:
    pool, _season, _fixture, casa, _fora, m_dono, _m = await _cenario(db_session, 2065)
    await bonus_service.save_bonus(
        db_session,
        membership=m_dono,
        kind=BonusKind.CHAMPION,
        team_ids=[casa.id],
        locks_at=datetime.now(UTC) + timedelta(days=1),
    )

    resultado = await bonus_service.settle_season_bonuses(db_session, pool.id)

    assert resultado.settled == 0
    assert "desfecho" in (resultado.skipped_reason or "")


async def test_g4_exige_exatamente_quatro_times(db_session: AsyncSession) -> None:
    _pool, _season, _fixture, casa, fora, m_dono, _m = await _cenario(db_session, 2066)

    with pytest.raises(bonus_service.BonusError, match="espera 4"):
        await bonus_service.save_bonus(
            db_session,
            membership=m_dono,
            kind=BonusKind.TOP4,
            # Só dois times: o G-4 exige exatamente quatro.
            team_ids=[casa.id, fora.id],
            locks_at=datetime.now(UTC) + timedelta(days=1),
        )


async def test_time_repetido_e_recusado(db_session: AsyncSession) -> None:
    _pool, _season, _fixture, casa, _fora, m_dono, _m = await _cenario(db_session, 2067)

    with pytest.raises(bonus_service.BonusError, match="repetido"):
        await bonus_service.save_bonus(
            db_session,
            membership=m_dono,
            kind=BonusKind.TOP4,
            team_ids=[casa.id, casa.id, casa.id, casa.id],
            locks_at=datetime.now(UTC) + timedelta(days=1),
        )


async def test_apuracao_de_bonus_e_idempotente(db_session: AsyncSession) -> None:
    pool, season, _fixture, casa, _fora, m_dono, _m = await _cenario(db_session, 2068)
    trava = datetime.now(UTC) + timedelta(days=1)
    await bonus_service.save_bonus(
        db_session,
        membership=m_dono,
        kind=BonusKind.CHAMPION,
        team_ids=[casa.id],
        locks_at=trava,
    )
    season.outcome = {"champion_team_id": casa.id, "top4_team_ids": [], "relegated_team_ids": []}
    await db_session.flush()

    primeira = await bonus_service.settle_season_bonuses(db_session, pool.id)
    for _ in range(3):
        await bonus_service.settle_season_bonuses(db_session, pool.id)
    pontos = await bonus_service.bonus_points_by_membership(db_session, pool.id)

    assert primeira.points_awarded == 20
    assert pontos[m_dono.id] == 20


async def test_bonus_entra_no_ranking(db_session: AsyncSession) -> None:
    """O ponto de bônus soma ao total, mas não vira acerto de critério."""
    pool, season, _fixture, casa, _fora, m_dono, _m = await _cenario(db_session, 2069)
    await bonus_service.save_bonus(
        db_session,
        membership=m_dono,
        kind=BonusKind.CHAMPION,
        team_ids=[casa.id],
        locks_at=datetime.now(UTC) + timedelta(days=1),
    )
    season.outcome = {"champion_team_id": casa.id, "top4_team_ids": [], "relegated_team_ids": []}
    await db_session.flush()
    await bonus_service.settle_season_bonuses(db_session, pool.id)

    await settlement_service.recompute_standings(db_session, pool.id)

    standing = await db_session.scalar(
        select(Standing).where(Standing.pool_id == pool.id, Standing.membership_id == m_dono.id)
    )
    assert standing is not None
    assert standing.points == 20
    assert standing.criterion_hits == {}


async def test_knockout_fixtures_lista_so_o_mata_mata(db_session: AsyncSession) -> None:
    _pool, season, _fixture, casa, fora, _m1, _m2 = await _cenario(db_session, 2070)

    fase = Stage(season_id=season.id, name="Semifinal", order_index=1, is_knockout=True)
    db_session.add(fase)
    await db_session.flush()

    rodada_mata_mata = await make_round(db_session, season, number=99)
    rodada_mata_mata.stage_id = fase.id
    await db_session.flush()
    eliminatorio = await make_fixture(
        db_session, season, rodada_mata_mata, fora, casa, kickoff_in=timedelta(days=5)
    )

    jogos = await bonus_service.knockout_fixtures(db_session, season.id)

    assert [jogo.id for jogo in jogos] == [eliminatorio.id]


async def test_config_de_pontuacao_guarda_os_valores_de_bonus(
    db_session: AsyncSession,
) -> None:
    pool, _season, _fixture, _casa, _fora, _m1, _m2 = await _cenario(db_session, 2071)

    config = await db_session.scalar(select(ScoringConfig).where(ScoringConfig.pool_id == pool.id))

    assert config is not None
    assert (config.knockout_advance_points, config.champion_points) == (6, 20)
    assert (config.top4_points, config.relegated_points) == (5, 4)


async def test_temporada_nasce_sem_desfecho(db_session: AsyncSession) -> None:
    _pool, season, *_ = await _cenario(db_session, 2072)

    fresca = await db_session.get(Season, season.id)

    assert fresca is not None
    assert fresca.outcome == {}
