"""Trava de palpite e blindagem.

Estes são os testes que sustentam a confiança no produto inteiro. Se um deles
cair, o bolão perde a graça: ou dá para palpitar depois do apito, ou dá para
espiar o palpite dos outros.
"""

from __future__ import annotations

from datetime import UTC, datetime, timedelta

import pytest
from sqlalchemy import text
from sqlalchemy.exc import DBAPIError
from sqlalchemy.ext.asyncio import AsyncSession

from app.models import FixtureStatus
from app.services import pools as pool_service
from app.services import predictions as prediction_service
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


async def _cenario(session: AsyncSession, kickoff_in: timedelta = timedelta(days=1)):
    """Bolão com dois participantes e um jogo."""
    dono = await make_user(session, "dono@teste.local", "Dono")
    rival = await make_user(session, "rival@teste.local", "Rival")
    season = await make_season(session)
    rodada = await make_round(session, season)
    casa = await make_team(session, season, "Time Casa")
    fora = await make_team(session, season, "Time Fora")
    fixture = await make_fixture(session, season, rodada, casa, fora, kickoff_in=kickoff_in)
    pool = await make_pool(session, dono, season, rounds=[rodada])

    m_dono = await pool_service.get_membership(session, pool.id, dono.id)
    assert m_dono is not None
    m_rival = await add_member(session, pool, rival, "Rival")
    return pool, fixture, m_dono, m_rival


# ---------------------------------------------------------------------------
# Trava de escrita — garantida pelo banco
# ---------------------------------------------------------------------------


async def test_palpite_e_aceito_antes_do_apito(db_session: AsyncSession) -> None:
    _, fixture, membro, _ = await _cenario(db_session)

    palpite = await prediction_service.upsert_prediction(
        db_session, membership=membro, fixture=fixture, home_goals=2, away_goals=1
    )

    assert palpite.home_goals == 2
    assert palpite.away_goals == 1


async def test_palpite_e_recusado_depois_do_apito(db_session: AsyncSession) -> None:
    _, fixture, membro, _ = await _cenario(db_session, kickoff_in=timedelta(minutes=-1))

    with pytest.raises(prediction_service.PredictionLocked):
        await prediction_service.upsert_prediction(
            db_session, membership=membro, fixture=fixture, home_goals=2, away_goals=1
        )


async def test_o_banco_recusa_mesmo_sem_passar_pelo_service(
    db_session: AsyncSession,
) -> None:
    """A trava não depende da aplicação.

    Este teste escreve SQL cru de propósito: é o cenário do script rodado à mão
    ou do endpoint novo que alguém esqueceu de proteger.
    """
    _, fixture, membro, _ = await _cenario(db_session, kickoff_in=timedelta(minutes=-5))

    with pytest.raises(DBAPIError) as exc:
        await db_session.execute(
            text(
                "INSERT INTO predictions (membership_id, fixture_id, home_goals, away_goals,"
                " created_at, updated_at) VALUES (:m, :f, 1, 0, now(), now())"
            ),
            {"m": membro.id, "f": fixture.id},
        )

    assert "palpite fechado" in str(exc.value.orig)


async def test_o_banco_recusa_edicao_depois_do_apito(db_session: AsyncSession) -> None:
    _pool, fixture, membro, _ = await _cenario(db_session, kickoff_in=timedelta(seconds=30))

    await prediction_service.upsert_prediction(
        db_session, membership=membro, fixture=fixture, home_goals=1, away_goals=1
    )
    # O jogo começa.
    fixture.kickoff_at = datetime.now(UTC) - timedelta(minutes=1)
    await db_session.flush()

    with pytest.raises(DBAPIError) as exc:
        await db_session.execute(
            text("UPDATE predictions SET home_goals = 5 WHERE membership_id = :m"),
            {"m": membro.id},
        )

    assert "palpite fechado" in str(exc.value.orig)


async def test_jogo_adiado_reabre_para_palpite(db_session: AsyncSession) -> None:
    """Adiamento não pode punir quem já palpitou nem travar quem não palpitou."""
    _, fixture, membro, _ = await _cenario(db_session, kickoff_in=timedelta(minutes=-10))
    fixture.status = FixtureStatus.POSTPONED
    await db_session.flush()

    palpite = await prediction_service.upsert_prediction(
        db_session, membership=membro, fixture=fixture, home_goals=3, away_goals=0
    )

    assert palpite.home_goals == 3


async def test_jogo_terminado_fecha_mesmo_com_kickoff_no_futuro(
    db_session: AsyncSession,
) -> None:
    """Regressão encontrada rodando o fluxo real.

    No modo manual o organizador lança o placar pela tela, e isso pode
    acontecer antes do horário marcado — ou a data pode ter vindo errada no
    CSV. Se a trava olhasse só o relógio, daria para palpitar num jogo já
    apurado, sabendo o resultado.
    """
    _pool, fixture, membro, _ = await _cenario(db_session, kickoff_in=timedelta(days=30))
    fixture.status = FixtureStatus.FINISHED
    fixture.home_ft, fixture.away_ft = 2, 1
    await db_session.flush()

    with pytest.raises(prediction_service.PredictionLocked):
        await prediction_service.upsert_prediction(
            db_session, membership=membro, fixture=fixture, home_goals=2, away_goals=1
        )


async def test_o_banco_recusa_palpite_em_jogo_terminado(db_session: AsyncSession) -> None:
    """A mesma regra, garantida pela trigger e não pela aplicação."""
    _pool, fixture, membro, _ = await _cenario(db_session, kickoff_in=timedelta(days=30))
    fixture.status = FixtureStatus.FINISHED
    await db_session.flush()

    with pytest.raises(DBAPIError) as exc:
        await db_session.execute(
            text(
                "INSERT INTO predictions (membership_id, fixture_id, home_goals, away_goals,"
                " created_at, updated_at) VALUES (:m, :f, 1, 0, now(), now())"
            ),
            {"m": membro.id, "f": fixture.id},
        )

    assert "palpite fechado" in str(exc.value.orig)


async def test_palpite_alheio_aparece_quando_o_jogo_termina_antes_da_hora(
    db_session: AsyncSession,
) -> None:
    """Blindagem e apuração não podem discordar.

    Um jogo pontuado com os palpites ainda escondidos deixa o ranking mexendo
    sem ninguém conseguir ver por quê.
    """
    pool, fixture, m_dono, m_rival = await _cenario(db_session, kickoff_in=timedelta(days=30))
    await prediction_service.upsert_prediction(
        db_session, membership=m_rival, fixture=fixture, home_goals=0, away_goals=3
    )

    fixture.status = FixtureStatus.FINISHED
    await db_session.flush()

    visiveis = await prediction_service.visible_predictions(
        db_session,
        pool_id=pool.id,
        fixture_ids=[fixture.id],
        viewer_membership_id=m_dono.id,
    )
    alheio = next(item for item in visiveis if item.membership_id == m_rival.id)

    assert alheio.is_hidden is False
    assert (alheio.home_goals, alheio.away_goals) == (0, 3)


async def test_um_palpite_por_participante_por_jogo(db_session: AsyncSession) -> None:
    _, fixture, membro, _ = await _cenario(db_session)

    primeiro = await prediction_service.upsert_prediction(
        db_session, membership=membro, fixture=fixture, home_goals=1, away_goals=0
    )
    segundo = await prediction_service.upsert_prediction(
        db_session, membership=membro, fixture=fixture, home_goals=2, away_goals=2
    )

    assert primeiro.id == segundo.id
    assert segundo.home_goals == 2


async def test_placar_negativo_e_implausivel_sao_recusados(db_session: AsyncSession) -> None:
    _, fixture, membro, _ = await _cenario(db_session)

    with pytest.raises(prediction_service.PredictionError):
        await prediction_service.upsert_prediction(
            db_session, membership=membro, fixture=fixture, home_goals=-1, away_goals=0
        )
    with pytest.raises(prediction_service.PredictionError):
        await prediction_service.upsert_prediction(
            db_session, membership=membro, fixture=fixture, home_goals=200, away_goals=0
        )


async def test_lote_salva_os_validos_e_recusa_so_os_travados(
    db_session: AsyncSession,
) -> None:
    """Quem preenche a rodada inteira não pode perder tudo por causa de um jogo."""
    from app.services import pools as pool_service

    dono = await make_user(db_session, "lote@teste.local", "Dono")
    season = await make_season(db_session, 2027)
    rodada = await make_round(db_session, season)
    a = await make_team(db_session, season, "Alfa")
    b = await make_team(db_session, season, "Beta")
    c = await make_team(db_session, season, "Gama")
    d = await make_team(db_session, season, "Delta")

    aberto = await make_fixture(db_session, season, rodada, a, b, kickoff_in=timedelta(days=1))
    fechado = await make_fixture(db_session, season, rodada, c, d, kickoff_in=timedelta(minutes=-5))
    pool = await make_pool(db_session, dono, season, rounds=[rodada])
    membro = await pool_service.get_membership(db_session, pool.id, dono.id)
    assert membro is not None

    salvos, recusados = await prediction_service.bulk_upsert(
        db_session,
        membership=membro,
        entries=[(aberto, 2, 1), (fechado, 0, 0)],
    )

    assert len(salvos) == 1
    assert salvos[0].fixture_id == aberto.id
    assert len(recusados) == 1
    assert recusados[0]["fixture_id"] == fechado.id


# ---------------------------------------------------------------------------
# Blindagem de leitura
# ---------------------------------------------------------------------------


async def test_palpite_alheio_fica_oculto_antes_do_apito(db_session: AsyncSession) -> None:
    pool, fixture, m_dono, m_rival = await _cenario(db_session)

    await prediction_service.upsert_prediction(
        db_session, membership=m_dono, fixture=fixture, home_goals=2, away_goals=1
    )
    await prediction_service.upsert_prediction(
        db_session, membership=m_rival, fixture=fixture, home_goals=0, away_goals=3
    )

    visiveis = await prediction_service.visible_predictions(
        db_session,
        pool_id=pool.id,
        fixture_ids=[fixture.id],
        viewer_membership_id=m_dono.id,
    )
    por_membro = {item.membership_id: item for item in visiveis}

    meu = por_membro[m_dono.id]
    assert (meu.home_goals, meu.away_goals) == (2, 1)
    assert meu.is_hidden is False

    alheio = por_membro[m_rival.id]
    assert alheio.home_goals is None
    assert alheio.away_goals is None
    assert alheio.is_hidden is True


async def test_palpite_alheio_aparece_depois_do_apito(db_session: AsyncSession) -> None:
    pool, fixture, m_dono, m_rival = await _cenario(db_session)

    await prediction_service.upsert_prediction(
        db_session, membership=m_rival, fixture=fixture, home_goals=0, away_goals=3
    )
    fixture.kickoff_at = datetime.now(UTC) - timedelta(minutes=1)
    await db_session.flush()

    visiveis = await prediction_service.visible_predictions(
        db_session,
        pool_id=pool.id,
        fixture_ids=[fixture.id],
        viewer_membership_id=m_dono.id,
    )
    alheio = next(item for item in visiveis if item.membership_id == m_rival.id)

    assert (alheio.home_goals, alheio.away_goals) == (0, 3)
    assert alheio.is_hidden is False


async def test_blindagem_e_por_jogo_e_nao_por_rodada(db_session: AsyncSession) -> None:
    """O jogo de sábado abre enquanto o de domingo continua fechado."""
    from app.services import pools as pool_service

    dono = await make_user(db_session, "sabado@teste.local", "Dono")
    rival = await make_user(db_session, "domingo@teste.local", "Rival")
    season = await make_season(db_session, 2028)
    rodada = await make_round(db_session, season)
    a = await make_team(db_session, season, "Alfa")
    b = await make_team(db_session, season, "Beta")
    c = await make_team(db_session, season, "Gama")
    d = await make_team(db_session, season, "Delta")

    ja_comecou = await make_fixture(
        db_session, season, rodada, a, b, kickoff_in=timedelta(minutes=-5)
    )
    ainda_nao = await make_fixture(db_session, season, rodada, c, d, kickoff_in=timedelta(days=1))

    pool = await make_pool(db_session, dono, season, rounds=[rodada])
    m_dono = await pool_service.get_membership(db_session, pool.id, dono.id)
    m_rival = await add_member(db_session, pool, rival)
    assert m_dono is not None

    # O rival palpitou no jogo aberto antes de ele começar.
    ja_comecou.kickoff_at = datetime.now(UTC) + timedelta(minutes=5)
    await db_session.flush()
    await prediction_service.upsert_prediction(
        db_session, membership=m_rival, fixture=ja_comecou, home_goals=1, away_goals=1
    )
    ja_comecou.kickoff_at = datetime.now(UTC) - timedelta(minutes=5)
    await db_session.flush()

    await prediction_service.upsert_prediction(
        db_session, membership=m_rival, fixture=ainda_nao, home_goals=2, away_goals=0
    )

    visiveis = await prediction_service.visible_predictions(
        db_session,
        pool_id=pool.id,
        fixture_ids=[ja_comecou.id, ainda_nao.id],
        viewer_membership_id=m_dono.id,
    )
    por_fixture = {item.fixture_id: item for item in visiveis}

    assert por_fixture[ja_comecou.id].is_hidden is False
    assert por_fixture[ainda_nao.id].is_hidden is True


async def test_blindagem_ignora_participante_de_outro_bolao(
    db_session: AsyncSession,
) -> None:
    """Dois bolões na mesma temporada não enxergam os palpites um do outro."""
    from app.services import pools as pool_service

    dono_a = await make_user(db_session, "a@teste.local", "Dono A")
    dono_b = await make_user(db_session, "b@teste.local", "Dono B")
    season = await make_season(db_session, 2029)
    rodada = await make_round(db_session, season)
    casa = await make_team(db_session, season, "Casa")
    fora = await make_team(db_session, season, "Fora")
    fixture = await make_fixture(db_session, season, rodada, casa, fora)

    pool_a = await make_pool(db_session, dono_a, season, rounds=[rodada], name="Bolão A")
    pool_b = await make_pool(db_session, dono_b, season, rounds=[rodada], name="Bolão B")

    m_a = await pool_service.get_membership(db_session, pool_a.id, dono_a.id)
    m_b = await pool_service.get_membership(db_session, pool_b.id, dono_b.id)
    assert m_a is not None and m_b is not None

    await prediction_service.upsert_prediction(
        db_session, membership=m_b, fixture=fixture, home_goals=4, away_goals=0
    )

    visiveis = await prediction_service.visible_predictions(
        db_session,
        pool_id=pool_a.id,
        fixture_ids=[fixture.id],
        viewer_membership_id=m_a.id,
    )

    assert visiveis == []


async def test_quem_nao_palpitou_aparece_na_lista_de_pendentes(
    db_session: AsyncSession,
) -> None:
    pool, fixture, m_dono, m_rival = await _cenario(db_session)

    await prediction_service.upsert_prediction(
        db_session, membership=m_dono, fixture=fixture, home_goals=1, away_goals=0
    )

    pendentes = await prediction_service.members_without_prediction(
        db_session, pool_id=pool.id, fixture_ids=[fixture.id]
    )

    assert [membro.id for membro in pendentes] == [m_rival.id]


async def test_cobertura_da_rodada(db_session: AsyncSession) -> None:
    pool, fixture, m_dono, _ = await _cenario(db_session)
    await prediction_service.upsert_prediction(
        db_session, membership=m_dono, fixture=fixture, home_goals=1, away_goals=0
    )

    cobertura = await prediction_service.prediction_coverage(
        db_session, pool_id=pool.id, fixture_ids=[fixture.id]
    )

    assert cobertura == {
        "members": 2,
        "fixtures": 1,
        "predictions": 1,
        "expected": 2,
        "percent": 50,
    }


async def test_o_dado_existe_no_banco_e_quem_protege_e_o_serializer(
    db_session: AsyncSession,
) -> None:
    """Deixa explícito onde mora a blindagem.

    A linha do palpite existe e está completa no banco — tem que existir, ou
    não daria para pontuar. O que impede o vazamento é `visible_predictions`,
    e é por isso que todo endpoint precisa passar por ela em vez de montar a
    resposta na mão.
    """
    pool, fixture, m_dono, m_rival = await _cenario(db_session)
    await prediction_service.upsert_prediction(
        db_session, membership=m_rival, fixture=fixture, home_goals=7, away_goals=7
    )

    no_banco = await db_session.scalar(
        text("SELECT count(*) FROM predictions WHERE fixture_id = :f").bindparams(f=fixture.id)
    )
    assert no_banco == 1

    visiveis = await prediction_service.visible_predictions(
        db_session,
        pool_id=pool.id,
        fixture_ids=[fixture.id],
        viewer_membership_id=m_dono.id,
    )
    assert all(item.home_goals is None for item in visiveis)
