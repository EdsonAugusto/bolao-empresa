"""Conclusão de rodada: do apito ao ranking.

O pedido do usuário foi "a rodada já passou e os jogos continuam abertos". A
cadeia que resolve isso tem quatro elos, e cada um pode quebrar sozinho:

    palpite fecha -> placar entra -> jogo encerra -> apuração pontua

Estes testes cobrem os dois elos que faltavam: encerrar o jogo quando o placar
chegou mas a fonte não disse que acabou, e **não** encerrar quando não há placar
— porque as duas saídas fáceis desse caso quebram coisas piores.
"""

from __future__ import annotations

from datetime import UTC, datetime, timedelta

from sqlalchemy.ext.asyncio import AsyncSession

from app.models import FixtureStatus
from app.services import predictions as prediction_service
from app.services import sync as sync_service
from tests.factories import make_fixture, make_round, make_season, make_team


async def _jogo(session: AsyncSession, marca: int, *, horas_atras: float, status, placar=None):
    season = await make_season(session, marca)
    rodada = await make_round(session, season)
    casa = await make_team(session, season, f"Casa {marca}")
    fora = await make_team(session, season, f"Fora {marca}")
    jogo = await make_fixture(session, season, rodada, casa, fora, kickoff_in=timedelta(days=1))
    jogo.kickoff_at = datetime.now(UTC) - timedelta(hours=horas_atras)
    jogo.status = status
    if placar is not None:
        jogo.home_ft, jogo.away_ft = placar
    await session.flush()
    return jogo


# ---------------------------------------------------------------------------
# O elo que faltava: encerrar quando o placar chegou
# ---------------------------------------------------------------------------


async def test_jogo_com_placar_preso_em_andamento_e_encerrado(db_session: AsyncSession) -> None:
    """O GE não manda campo de status: o placar chega e o jogo fica "em andamento".

    Sem este fechamento a rodada nunca conclui — a apuração só pega `FINISHED`.
    """
    jogo = await _jogo(db_session, 7100, horas_atras=5, status=FixtureStatus.LIVE, placar=(2, 1))

    resultado = (await sync_service.encerrar_parados(db_session)).as_dict()

    await db_session.flush()
    assert resultado["finished"] == 1
    assert jogo.status is FixtureStatus.FINISHED


async def test_jogo_ainda_em_andamento_nao_e_encerrado_cedo(db_session: AsyncSession) -> None:
    """Uma hora de bola rolando é jogo, não jogo preso."""
    jogo = await _jogo(db_session, 7101, horas_atras=1, status=FixtureStatus.LIVE, placar=(1, 0))

    await sync_service.encerrar_parados(db_session)

    await db_session.flush()
    assert jogo.status is FixtureStatus.LIVE


# ---------------------------------------------------------------------------
# O caso sem placar: as duas saídas fáceis quebram coisas piores
# ---------------------------------------------------------------------------


async def test_jogo_sem_placar_continua_travado_para_palpite(db_session: AsyncSession) -> None:
    """Não vira ``POSTPONED``.

    Adiado **destrava** o palpite: um jogo de duas semanas atrás voltaria a
    aceitar palpite de quem já sabe o resultado. Fica ``LIVE``, travado.
    """
    jogo = await _jogo(db_session, 7102, horas_atras=48, status=FixtureStatus.LIVE)

    resultado = (await sync_service.encerrar_parados(db_session)).as_dict()

    await db_session.flush()
    assert jogo.status is FixtureStatus.LIVE
    assert prediction_service.is_locked(jogo, datetime.now(UTC)) is True
    assert jogo.id in resultado["awaiting_score"]


async def test_jogo_sem_placar_nao_vira_encerrado(db_session: AsyncSession) -> None:
    """Não vira ``FINISHED`` vazio.

    Encerrado sem placar entra na fila de apuração e nunca sai dela: a cada
    quinze minutos o job tenta apurar um jogo que não tem o que apurar.
    """
    jogo = await _jogo(db_session, 7103, horas_atras=48, status=FixtureStatus.LIVE)

    await sync_service.encerrar_parados(db_session)

    await db_session.flush()
    assert jogo.status is not FixtureStatus.FINISHED
    assert jogo.settled_at is None


async def test_rodar_duas_vezes_da_o_mesmo_resultado(db_session: AsyncSession) -> None:
    """Idempotente: o job roda de hora em hora e não pode acumular efeito."""
    jogo = await _jogo(db_session, 7104, horas_atras=5, status=FixtureStatus.LIVE, placar=(0, 0))

    primeira = (await sync_service.encerrar_parados(db_session)).as_dict()
    segunda = (await sync_service.encerrar_parados(db_session)).as_dict()

    await db_session.flush()
    assert primeira["finished"] == 1
    assert segunda["finished"] == 0
    assert jogo.status is FixtureStatus.FINISHED


# ---------------------------------------------------------------------------
# Fechamento do palpite pelo relógio
# ---------------------------------------------------------------------------


async def test_jogo_cujo_horario_passou_fecha_para_palpite(db_session: AsyncSession) -> None:
    """A rede de segurança: mesmo sem coleta, o apito fecha o palpite."""
    jogo = await _jogo(db_session, 7105, horas_atras=0.5, status=FixtureStatus.SCHEDULED)

    fechados = await sync_service.fechar_palpites(db_session)

    await db_session.flush()
    assert fechados >= 1
    assert jogo.status is FixtureStatus.LIVE
    assert prediction_service.is_locked(jogo, datetime.now(UTC)) is True


async def test_jogo_futuro_continua_aberto(db_session: AsyncSession) -> None:
    jogo = await _jogo(db_session, 7106, horas_atras=-3, status=FixtureStatus.SCHEDULED)

    await sync_service.fechar_palpites(db_session)

    await db_session.flush()
    assert jogo.status is FixtureStatus.SCHEDULED
    assert prediction_service.is_locked(jogo, datetime.now(UTC)) is False


# ---------------------------------------------------------------------------
# Placar de minuto 20 não é resultado final
# ---------------------------------------------------------------------------


async def test_placar_parcial_de_jogo_abandonado_pela_fonte_nao_vira_final(
    db_session: AsyncSession,
) -> None:
    """A coleta ao vivo grava o placar **durante** a partida.

    Se a fonte cair no primeiro tempo com 1x0, três horas depois esse 1x0
    continua no banco. Promovê-lo a resultado final apuraria a rodada inteira em
    cima de um jogo que terminou 1x3 — e o ranking sairia errado com toda a
    aparência de estar certo.
    """
    jogo = await _jogo(db_session, 7200, horas_atras=5, status=FixtureStatus.LIVE, placar=(1, 0))
    # Última confirmação da fonte foi durante a partida.
    jogo.synced_at = jogo.kickoff_at + timedelta(minutes=20)
    await db_session.flush()

    resultado = (await sync_service.encerrar_parados(db_session)).as_dict()

    assert resultado["finished"] == 0
    assert jogo.status is FixtureStatus.LIVE


async def test_placar_confirmado_depois_do_fim_vira_final(db_session: AsyncSession) -> None:
    """A fonte confirmou o placar depois de o jogo certamente ter acabado."""
    jogo = await _jogo(db_session, 7201, horas_atras=6, status=FixtureStatus.LIVE, placar=(2, 1))
    jogo.synced_at = jogo.kickoff_at + timedelta(hours=4)
    await db_session.flush()

    resultado = (await sync_service.encerrar_parados(db_session)).as_dict()

    assert resultado["finished"] == 1
    assert jogo.status is FixtureStatus.FINISHED


async def test_placar_posto_a_mao_encerra_o_jogo(db_session: AsyncSession) -> None:
    """Sem coleta nenhuma, quem lançou sabia o que estava fazendo."""
    jogo = await _jogo(db_session, 7202, horas_atras=5, status=FixtureStatus.LIVE, placar=(0, 3))
    jogo.synced_at = None
    jogo.provider_updated_at = None
    await db_session.flush()

    resultado = (await sync_service.encerrar_parados(db_session)).as_dict()

    assert resultado["finished"] == 1
    assert jogo.status is FixtureStatus.FINISHED
