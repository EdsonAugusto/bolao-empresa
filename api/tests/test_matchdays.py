"""Rodadas montadas pelo organizador, com jogos de campeonatos diferentes.

O que mais importa aqui é a apuração encontrar o bolão personalizado. Antes,
`settle_fixture` achava os bolões por `pool.season_id == fixture.season_id` — um
bolão sem temporada nunca seria apurado, e o sintoma seria ranking parado sem
erro nenhum no log.
"""

from __future__ import annotations

from datetime import UTC, datetime, timedelta

import pytest
from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from app.models import (
    Fixture,
    FixtureStatus,
    Matchday,
    MatchdayFixture,
    Membership,
    Pool,
    PoolKind,
    Prediction,
    PredictionScore,
    ScoringConfig,
    Standing,
)
from app.scoring import CLASSIC, ScoringMode
from app.services import matchdays as matchday_service
from app.services import pools as pool_service
from app.services import predictions as prediction_service
from app.services import settlement as settlement_service
from tests.factories import make_fixture, make_round, make_season, make_team, make_user

pytestmark = pytest.mark.integration


async def _bolao_personalizado(session: AsyncSession, marca: int):
    """Bolão sem temporada, e jogos de dois campeonatos diferentes."""
    dono = await make_user(session, f"dono{marca}@teste.local", "Dono")
    rival = await make_user(session, f"rival{marca}@teste.local", "Rival")

    brasileirao = await make_season(session, 3000 + marca)
    premier = await make_season(session, 4000 + marca)

    r_br = await make_round(session, brasileirao)
    r_pl = await make_round(session, premier)

    pal = await make_team(session, brasileirao, f"Palmeiras {marca}")
    cor = await make_team(session, brasileirao, f"Corinthians {marca}")
    ars = await make_team(session, premier, f"Arsenal {marca}")
    che = await make_team(session, premier, f"Chelsea {marca}")

    jogo_br = await make_fixture(
        session, brasileirao, r_br, pal, cor, kickoff_in=timedelta(hours=5)
    )
    jogo_pl = await make_fixture(session, premier, r_pl, ars, che, kickoff_in=timedelta(hours=8))

    pool = await pool_service.create_pool(
        session,
        owner=dono,
        name=f"Rodada da semana {marca}",
        kind=PoolKind.CUSTOM,
        mode=ScoringMode.CLASSIC,
    )

    m_dono = await pool_service.get_membership(session, pool.id, dono.id)
    assert m_dono is not None
    m_rival = await pool_service.join_pool(session, pool=pool, user=rival, display_name="Rival")

    return pool, dono, jogo_br, jogo_pl, m_dono, m_rival


# ---------------------------------------------------------------------------
# Criação
# ---------------------------------------------------------------------------


async def test_bolao_personalizado_nasce_sem_temporada(db_session: AsyncSession) -> None:
    pool, *_ = await _bolao_personalizado(db_session, 1)

    assert pool.kind is PoolKind.CUSTOM
    assert pool.season_id is None


async def test_bolao_de_campeonato_ainda_exige_temporada(db_session: AsyncSession) -> None:
    dono = await make_user(db_session, "exige@teste.local", "Dono")

    with pytest.raises(pool_service.PoolError, match="precisa de uma temporada"):
        await pool_service.create_pool(
            db_session, owner=dono, name="Sem temporada", kind=PoolKind.SEASON
        )


async def test_rodadas_sao_numeradas_em_sequencia(db_session: AsyncSession) -> None:
    pool, dono, *_ = await _bolao_personalizado(db_session, 2)

    primeira = await matchday_service.create_matchday(db_session, pool=pool, actor=dono)
    segunda = await matchday_service.create_matchday(
        db_session, pool=pool, actor=dono, name="Semana do clássico"
    )

    assert (primeira.number, primeira.name) == (1, "Rodada 1")
    assert (segunda.number, segunda.name) == (2, "Semana do clássico")


async def test_bolao_de_campeonato_recusa_rodada_montada(db_session: AsyncSession) -> None:
    dono = await make_user(db_session, "campeonato@teste.local", "Dono")
    season = await make_season(db_session, 3500)
    pool = await pool_service.create_pool(
        db_session, owner=dono, name="Do campeonato", season_id=season.id
    )

    with pytest.raises(matchday_service.MatchdayError, match="rodadas do campeonato"):
        await matchday_service.create_matchday(db_session, pool=pool, actor=dono)


# ---------------------------------------------------------------------------
# Seleção de jogos
# ---------------------------------------------------------------------------


async def test_rodada_junta_jogos_de_campeonatos_diferentes(
    db_session: AsyncSession,
) -> None:
    """O caso de uso inteiro: Brasileirão e Premier League na mesma rodada."""
    pool, dono, jogo_br, jogo_pl, _m1, _m2 = await _bolao_personalizado(db_session, 3)
    rodada = await matchday_service.create_matchday(db_session, pool=pool, actor=dono)

    mudanca = await matchday_service.set_fixtures(
        db_session, pool=pool, actor=dono, matchday=rodada, fixture_ids=[jogo_br.id, jogo_pl.id]
    )

    assert sorted(mudanca.added) == sorted([jogo_br.id, jogo_pl.id])
    assert mudanca.rejected == []
    assert jogo_br.season_id != jogo_pl.season_id


async def test_jogo_que_ja_comecou_nao_entra(db_session: AsyncSession) -> None:
    """Colocar jogo em andamento pediria palpite de resultado conhecido."""
    pool, dono, jogo_br, _jogo_pl, _m1, _m2 = await _bolao_personalizado(db_session, 4)
    rodada = await matchday_service.create_matchday(db_session, pool=pool, actor=dono)

    jogo_br.kickoff_at = datetime.now(UTC) - timedelta(minutes=5)
    await db_session.flush()

    mudanca = await matchday_service.set_fixtures(
        db_session, pool=pool, actor=dono, matchday=rodada, fixture_ids=[jogo_br.id]
    )

    assert mudanca.added == []
    assert len(mudanca.rejected) == 1
    assert "já começou" in str(mudanca.rejected[0]["reason"])


async def test_jogo_em_andamento_nao_pode_ser_tirado(db_session: AsyncSession) -> None:
    """Tirar apagaria os palpites de quem já respondeu."""
    pool, dono, jogo_br, jogo_pl, _m1, _m2 = await _bolao_personalizado(db_session, 5)
    rodada = await matchday_service.create_matchday(db_session, pool=pool, actor=dono)
    await matchday_service.set_fixtures(
        db_session, pool=pool, actor=dono, matchday=rodada, fixture_ids=[jogo_br.id, jogo_pl.id]
    )

    jogo_br.kickoff_at = datetime.now(UTC) - timedelta(minutes=5)
    await db_session.flush()

    # Tenta deixar só o jogo da Premier.
    mudanca = await matchday_service.set_fixtures(
        db_session, pool=pool, actor=dono, matchday=rodada, fixture_ids=[jogo_pl.id]
    )

    assert mudanca.removed == []
    assert len(mudanca.rejected) == 1
    restantes = await matchday_service.matchday_fixture_ids(db_session, rodada.id)
    assert sorted(restantes) == sorted([jogo_br.id, jogo_pl.id])


async def test_substituir_a_lista_remove_o_que_saiu(db_session: AsyncSession) -> None:
    pool, dono, jogo_br, jogo_pl, _m1, _m2 = await _bolao_personalizado(db_session, 6)
    rodada = await matchday_service.create_matchday(db_session, pool=pool, actor=dono)
    await matchday_service.set_fixtures(
        db_session, pool=pool, actor=dono, matchday=rodada, fixture_ids=[jogo_br.id, jogo_pl.id]
    )

    mudanca = await matchday_service.set_fixtures(
        db_session, pool=pool, actor=dono, matchday=rodada, fixture_ids=[jogo_pl.id]
    )

    assert mudanca.removed == [jogo_br.id]
    assert await matchday_service.matchday_fixture_ids(db_session, rodada.id) == [jogo_pl.id]


async def test_jogo_inexistente_e_recusado(db_session: AsyncSession) -> None:
    pool, dono, *_ = await _bolao_personalizado(db_session, 7)
    rodada = await matchday_service.create_matchday(db_session, pool=pool, actor=dono)

    mudanca = await matchday_service.set_fixtures(
        db_session, pool=pool, actor=dono, matchday=rodada, fixture_ids=[999_999]
    )

    assert mudanca.added == []
    assert "não existe" in str(mudanca.rejected[0]["reason"])


# ---------------------------------------------------------------------------
# Palpite e apuração
# ---------------------------------------------------------------------------


async def test_so_jogo_da_rodada_aceita_palpite(db_session: AsyncSession) -> None:
    pool, dono, jogo_br, jogo_pl, _m_dono, _m2 = await _bolao_personalizado(db_session, 8)
    rodada = await matchday_service.create_matchday(db_session, pool=pool, actor=dono)
    await matchday_service.set_fixtures(
        db_session, pool=pool, actor=dono, matchday=rodada, fixture_ids=[jogo_br.id]
    )

    assert await pool_service.fixture_is_included(db_session, pool.id, jogo_br) is True
    assert await pool_service.fixture_is_included(db_session, pool.id, jogo_pl) is False


async def test_apuracao_encontra_o_bolao_personalizado(db_session: AsyncSession) -> None:
    """A regressão que a mudança de arquitetura poderia introduzir.

    A busca por temporada nunca acharia um bolão sem temporada, e o ranking
    ficaria parado sem erro nenhum no log.
    """
    pool, dono, jogo_br, jogo_pl, m_dono, m_rival = await _bolao_personalizado(db_session, 9)
    rodada = await matchday_service.create_matchday(db_session, pool=pool, actor=dono)
    await matchday_service.set_fixtures(
        db_session, pool=pool, actor=dono, matchday=rodada, fixture_ids=[jogo_br.id, jogo_pl.id]
    )

    await prediction_service.upsert_prediction(
        db_session, membership=m_dono, fixture=jogo_br, home_goals=2, away_goals=1
    )
    await prediction_service.upsert_prediction(
        db_session, membership=m_rival, fixture=jogo_br, home_goals=1, away_goals=0
    )

    jogo_br.home_ft, jogo_br.away_ft = 2, 1
    jogo_br.status = FixtureStatus.FINISHED
    await db_session.flush()

    resultado = await settlement_service.settle_fixture(db_session, jogo_br.id)

    assert resultado.settled
    assert pool.id in resultado.pools_touched

    pontos = {
        linha.membership_id: linha.final_points
        for linha in (
            await db_session.scalars(
                select(PredictionScore).where(PredictionScore.pool_id == pool.id)
            )
        ).all()
    }
    assert pontos[m_dono.id] == 10  # cravou
    assert pontos[m_rival.id] == 5  # só o vencedor


async def test_pontos_ficam_ligados_a_rodada_do_bolao(db_session: AsyncSession) -> None:
    pool, dono, jogo_br, _jogo_pl, m_dono, _m2 = await _bolao_personalizado(db_session, 10)
    rodada = await matchday_service.create_matchday(db_session, pool=pool, actor=dono)
    await matchday_service.set_fixtures(
        db_session, pool=pool, actor=dono, matchday=rodada, fixture_ids=[jogo_br.id]
    )
    await prediction_service.upsert_prediction(
        db_session, membership=m_dono, fixture=jogo_br, home_goals=2, away_goals=1
    )

    jogo_br.home_ft, jogo_br.away_ft = 2, 1
    jogo_br.status = FixtureStatus.FINISHED
    await db_session.flush()
    await settlement_service.settle_fixture(db_session, jogo_br.id)

    linha = await db_session.scalar(
        select(PredictionScore).where(PredictionScore.pool_id == pool.id)
    )
    assert linha is not None
    assert linha.matchday_id == rodada.id
    assert linha.round_id is None

    ranking = await settlement_service.round_standings(db_session, pool.id, matchday_id=rodada.id)
    assert ranking[0]["points"] == 10


async def test_multiplicador_da_rodada_montada(db_session: AsyncSession) -> None:
    pool, dono, jogo_br, _jogo_pl, m_dono, _m2 = await _bolao_personalizado(db_session, 11)
    rodada = await matchday_service.create_matchday(
        db_session, pool=pool, actor=dono, name="Rodada decisiva", multiplier=3
    )
    await matchday_service.set_fixtures(
        db_session, pool=pool, actor=dono, matchday=rodada, fixture_ids=[jogo_br.id]
    )
    await prediction_service.upsert_prediction(
        db_session, membership=m_dono, fixture=jogo_br, home_goals=2, away_goals=1
    )

    jogo_br.home_ft, jogo_br.away_ft = 2, 1
    jogo_br.status = FixtureStatus.FINISHED
    await db_session.flush()
    await settlement_service.settle_fixture(db_session, jogo_br.id)

    linha = await db_session.scalar(
        select(PredictionScore).where(PredictionScore.pool_id == pool.id)
    )
    assert linha is not None
    assert (linha.base_points, linha.multiplier, linha.final_points) == (10, 3, 30)


async def test_bolao_de_campeonato_nao_e_afetado_pelo_personalizado(
    db_session: AsyncSession,
) -> None:
    """Os dois tipos convivem sem um pontuar jogo do outro."""
    pool_custom, dono, jogo_br, _pl, m_custom, _m = await _bolao_personalizado(db_session, 12)
    rodada = await matchday_service.create_matchday(db_session, pool=pool_custom, actor=dono)
    await matchday_service.set_fixtures(
        db_session, pool=pool_custom, actor=dono, matchday=rodada, fixture_ids=[jogo_br.id]
    )

    outro_dono = await make_user(db_session, "outro12@teste.local", "Outro")
    pool_season = await pool_service.create_pool(
        db_session, owner=outro_dono, name="Do campeonato 12", season_id=jogo_br.season_id
    )
    m_season = await pool_service.get_membership(db_session, pool_season.id, outro_dono.id)
    assert m_season is not None

    await prediction_service.upsert_prediction(
        db_session, membership=m_custom, fixture=jogo_br, home_goals=2, away_goals=1
    )
    await prediction_service.upsert_prediction(
        db_session, membership=m_season, fixture=jogo_br, home_goals=0, away_goals=3
    )

    jogo_br.home_ft, jogo_br.away_ft = 2, 1
    jogo_br.status = FixtureStatus.FINISHED
    await db_session.flush()
    resultado = await settlement_service.settle_fixture(db_session, jogo_br.id)

    assert sorted(resultado.pools_touched) == sorted([pool_custom.id, pool_season.id])

    por_bolao = {
        (linha.pool_id, linha.membership_id): linha.final_points
        for linha in (await db_session.scalars(select(PredictionScore))).all()
    }
    assert por_bolao[(pool_custom.id, m_custom.id)] == 10
    assert por_bolao[(pool_season.id, m_season.id)] == 0


async def test_reapurar_o_bolao_personalizado(db_session: AsyncSession) -> None:
    pool, dono, jogo_br, jogo_pl, m_dono, _m2 = await _bolao_personalizado(db_session, 13)
    rodada = await matchday_service.create_matchday(db_session, pool=pool, actor=dono)
    await matchday_service.set_fixtures(
        db_session, pool=pool, actor=dono, matchday=rodada, fixture_ids=[jogo_br.id, jogo_pl.id]
    )
    await prediction_service.upsert_prediction(
        db_session, membership=m_dono, fixture=jogo_br, home_goals=2, away_goals=1
    )

    jogo_br.home_ft, jogo_br.away_ft = 2, 1
    jogo_br.status = FixtureStatus.FINISHED
    await db_session.flush()

    resultado = await settlement_service.recompute_pool(db_session, pool.id)

    assert resultado["fixtures"] == 2
    assert resultado["settled"] == 1

    standing = await db_session.scalar(
        select(Standing).where(Standing.pool_id == pool.id, Standing.membership_id == m_dono.id)
    )
    assert standing is not None
    assert standing.points == 10


async def test_apagar_a_rodada_leva_os_palpites(db_session: AsyncSession) -> None:
    pool, dono, jogo_br, _pl, _m_dono, _m2 = await _bolao_personalizado(db_session, 14)
    rodada = await matchday_service.create_matchday(db_session, pool=pool, actor=dono)
    await matchday_service.set_fixtures(
        db_session, pool=pool, actor=dono, matchday=rodada, fixture_ids=[jogo_br.id]
    )

    await matchday_service.delete_matchday(db_session, pool=pool, actor=dono, matchday=rodada)

    assert await db_session.get(Matchday, rodada.id) is None
    restantes = (
        await db_session.scalars(
            select(MatchdayFixture).where(MatchdayFixture.matchday_id == rodada.id)
        )
    ).all()
    assert restantes == []


async def test_contagem_de_jogos_do_bolao_personalizado(db_session: AsyncSession) -> None:
    pool, dono, jogo_br, jogo_pl, _m1, _m2 = await _bolao_personalizado(db_session, 15)
    rodada = await matchday_service.create_matchday(db_session, pool=pool, actor=dono)
    await matchday_service.set_fixtures(
        db_session, pool=pool, actor=dono, matchday=rodada, fixture_ids=[jogo_br.id, jogo_pl.id]
    )

    total = await pool_service.included_fixture_count(db_session, pool.id, None)

    assert total == 2


async def test_config_de_pontuacao_e_criada_igual(db_session: AsyncSession) -> None:
    """Personalizado usa o mesmo motor: nada muda na pontuação."""
    pool, *_ = await _bolao_personalizado(db_session, 16)

    config = await db_session.scalar(select(ScoringConfig).where(ScoringConfig.pool_id == pool.id))

    assert config is not None
    assert config.criteria == CLASSIC.to_payload()


async def test_so_organizador_monta_a_rodada(db_session: AsyncSession) -> None:
    pool, _dono, _br, _pl, _m1, m_rival = await _bolao_personalizado(db_session, 17)
    from app.models import User

    jogador = await db_session.get(User, m_rival.user_id)
    assert jogador is not None

    with pytest.raises(pool_service.NotAuthorized):
        await matchday_service.create_matchday(db_session, pool=pool, actor=jogador)


async def test_rodada_de_outro_bolao_e_recusada(db_session: AsyncSession) -> None:
    pool_a, dono_a, jogo, _pl, _m1, _m2 = await _bolao_personalizado(db_session, 18)
    pool_b, dono_b, *_ = await _bolao_personalizado(db_session, 19)
    rodada_a = await matchday_service.create_matchday(db_session, pool=pool_a, actor=dono_a)

    with pytest.raises(pool_service.NotAuthorized, match="outro bolão"):
        await matchday_service.set_fixtures(
            db_session, pool=pool_b, actor=dono_b, matchday=rodada_a, fixture_ids=[jogo.id]
        )


async def test_palpite_e_aceito_no_bolao_sem_temporada(db_session: AsyncSession) -> None:
    """Regressão encontrada rodando o fluxo real.

    O endpoint de palpite filtrava os jogos por `season_id` do bolão. Num bolão
    personalizado esse campo é nulo, então **nenhum** jogo casava e todos os
    palpites voltavam recusados com "jogo não é deste bolão" — a rodada existia,
    aparecia na tela e não aceitava nada.
    """
    pool, dono, jogo_br, jogo_pl, m_dono, _m2 = await _bolao_personalizado(db_session, 20)
    rodada = await matchday_service.create_matchday(db_session, pool=pool, actor=dono)
    await matchday_service.set_fixtures(
        db_session, pool=pool, actor=dono, matchday=rodada, fixture_ids=[jogo_br.id, jogo_pl.id]
    )

    salvos, recusados = await prediction_service.bulk_upsert(
        db_session, membership=m_dono, entries=[(jogo_br, 2, 1), (jogo_pl, 0, 0)]
    )

    assert len(salvos) == 2
    assert recusados == []


async def test_agenda_nao_oferece_jogo_ja_encerrado(db_session: AsyncSession) -> None:
    """Regressão encontrada rodando o fluxo real.

    A agenda filtrava só pelo relógio. Um jogo com placar lançado antes da data
    marcada continua com `kickoff_at` no futuro, então aparecia para montar
    rodada — e depois era recusado por já ter começado, deixando a rodada
    vazia sem explicação.
    """
    _pool, _dono, jogo_br, _pl, _m1, _m2 = await _bolao_personalizado(db_session, 21)
    agora = datetime.now(UTC)

    assert prediction_service.is_locked(jogo_br, agora) is False

    jogo_br.status = FixtureStatus.FINISHED
    jogo_br.home_ft, jogo_br.away_ft = 1, 0
    await db_session.flush()

    # Data ainda no futuro, mas o jogo acabou.
    assert jogo_br.kickoff_at > agora
    assert prediction_service.is_locked(jogo_br, agora) is True


async def test_janela_sugerida_alcanca_o_proximo_jogo(db_session: AsyncSession) -> None:
    """Regressão relatada de uso real.

    A tela abria em "hoje + 7 dias". O campeonato estava em pausa e o próximo
    jogo era daí a 8 dias — resultado: agenda vazia e a impressão de que a
    busca não funcionava. A janela agora parte do próximo jogo.
    """
    from app.api.catalog import agenda_window

    season = await make_season(db_session, 5000)
    rodada = await make_round(db_session, season)
    casa = await make_team(db_session, season, "Casa 5000")
    fora = await make_team(db_session, season, "Fora 5000")
    # Fora de qualquer janela de uma semana a partir de hoje.
    await make_fixture(db_session, season, rodada, casa, fora, kickoff_in=timedelta(days=12))

    janela = await agenda_window(db_session, user=None, dias=7)  # type: ignore[arg-type]

    assert janela.next_fixture_at is not None
    assert janela.total_upcoming >= 1
    assert janela.suggested_to is not None
    assert janela.suggested_to >= janela.next_fixture_at.date()


async def test_janela_sem_jogo_futuro_e_explicita(db_session: AsyncSession) -> None:
    """Sem jogo nenhum à frente, a tela precisa dizer isso — não 'amplie as datas'."""
    from app.api.catalog import agenda_window

    janela = await agenda_window(db_session, user=None, dias=7)  # type: ignore[arg-type]

    assert janela.next_fixture_at is None
    assert janela.total_upcoming == 0
    assert janela.suggested_from is None


async def test_pools_existentes_continuam_de_campeonato(db_session: AsyncSession) -> None:
    """A migration marcou tudo que existia como bolão de campeonato."""
    dono = await make_user(db_session, "legado@teste.local", "Dono")
    season = await make_season(db_session, 3600)
    pool = await pool_service.create_pool(
        db_session, owner=dono, name="Legado", season_id=season.id
    )

    recarregado = await db_session.get(Pool, pool.id)

    assert recarregado is not None
    assert recarregado.kind is PoolKind.SEASON
    assert recarregado.season_id == season.id


# ---------------------------------------------------------------------------
# Listagem de rodadas — o endpoint responde pelos dois tipos de bolão
# ---------------------------------------------------------------------------


async def test_rodadas_montadas_aparecem_na_listagem_de_rodadas(
    db_session: AsyncSession,
) -> None:
    """`/rounds` tem que devolver a rodada montada, não lista vazia.

    A tela decidia o endereço olhando o tipo do bolão, que ainda não tinha
    carregado: caía no caminho de campeonato, recebia `[]` e anunciava "este
    bolão ainda não tem rodadas" com a rodada montada no banco. Como o erro é
    uma lista vazia e não uma exceção, ninguém vê — só o organizador, que já
    tinha escolhido os jogos.
    """
    pool, dono, jogo_br, jogo_pl, *_ = await _bolao_personalizado(db_session, 40)

    rodada = await matchday_service.create_matchday(db_session, pool=pool, actor=dono)
    await matchday_service.set_fixtures(
        db_session, pool=pool, actor=dono, matchday=rodada, fixture_ids=[jogo_br.id, jogo_pl.id]
    )
    await db_session.commit()

    from app.api.pools import _rodadas_montadas

    listadas = await _rodadas_montadas(db_session, pool)

    assert len(listadas) == 1
    assert listadas[0]["id"] == rodada.id
    assert listadas[0]["kind"] == "custom"
    assert listadas[0]["fixtures"] == 2
    assert listadas[0]["is_open"] is True


async def test_rodada_montada_vazia_aparece_mas_fechada(db_session: AsyncSession) -> None:
    """Rodada recém-criada precisa aparecer para o organizador ir enchê-la."""
    pool, dono, *_ = await _bolao_personalizado(db_session, 41)

    rodada = await matchday_service.create_matchday(db_session, pool=pool, actor=dono)
    await db_session.commit()

    from app.api.pools import _rodadas_montadas

    listadas = await _rodadas_montadas(db_session, pool)

    assert [item["id"] for item in listadas] == [rodada.id]
    assert listadas[0]["fixtures"] == 0
    assert listadas[0]["is_open"] is False


async def test_rodada_montada_fecha_quando_todo_jogo_travou(db_session: AsyncSession) -> None:
    pool, dono, jogo_br, jogo_pl, *_ = await _bolao_personalizado(db_session, 42)

    rodada = await matchday_service.create_matchday(db_session, pool=pool, actor=dono)
    await matchday_service.set_fixtures(
        db_session, pool=pool, actor=dono, matchday=rodada, fixture_ids=[jogo_br.id, jogo_pl.id]
    )
    # Empurra os dois para o passado: rodada sem jogo aberto está fechada.
    passado = datetime.now(UTC) - timedelta(hours=2)
    jogo_br.kickoff_at = passado
    jogo_pl.kickoff_at = passado
    await db_session.commit()

    from app.api.pools import _rodadas_montadas

    listadas = await _rodadas_montadas(db_session, pool)

    assert listadas[0]["is_open"] is False


# ---------------------------------------------------------------------------
# Campeonatos do bolão — filtro de garimpo, não regra
# ---------------------------------------------------------------------------


async def test_filtro_de_campeonatos_nasce_vazio_e_significa_todos(
    db_session: AsyncSession,
) -> None:
    pool, *_ = await _bolao_personalizado(db_session, 50)

    assert await pool_service.pool_competition_ids(db_session, pool.id) == []


async def test_filtro_recusa_campeonato_inexistente(db_session: AsyncSession) -> None:
    """Id errado tem que falhar na hora, não virar filtro que esconde tudo."""
    pool, *_ = await _bolao_personalizado(db_session, 51)

    with pytest.raises(pool_service.PoolError, match="não encontrado"):
        await pool_service.set_pool_competitions(db_session, pool=pool, competition_ids=[999_999])


async def test_tirar_campeonato_do_filtro_nao_desfaz_rodada_montada(
    db_session: AsyncSession,
) -> None:
    """O filtro encurta a busca; a rodada é o que o organizador marcou.

    Se mexer no filtro apagasse jogo de rodada montada, o palpite de todo mundo
    naquele jogo iria junto — e ninguém espera isso ao mexer numa caixinha.
    """
    pool, dono, jogo_br, jogo_pl, *_ = await _bolao_personalizado(db_session, 52)

    rodada = await matchday_service.create_matchday(db_session, pool=pool, actor=dono)
    await matchday_service.set_fixtures(
        db_session, pool=pool, actor=dono, matchday=rodada, fixture_ids=[jogo_br.id, jogo_pl.id]
    )
    await pool_service.set_pool_competitions(db_session, pool=pool, competition_ids=[])
    await db_session.commit()

    ainda = await matchday_service.matchday_fixture_ids(db_session, rodada.id)
    assert sorted(ainda) == sorted([jogo_br.id, jogo_pl.id])


async def test_bolao_de_campeonato_nao_filtra_campeonatos(db_session: AsyncSession) -> None:
    dono = await make_user(db_session, "semfiltro@teste.local", "Dono")
    season = await make_season(db_session, 3500)
    pool = await pool_service.create_pool(
        db_session, owner=dono, name="Segue a tabela", kind=PoolKind.SEASON, season_id=season.id
    )

    with pytest.raises(pool_service.PoolError, match="rodada montada"):
        await pool_service.set_pool_competitions(db_session, pool=pool, competition_ids=[])


# ---------------------------------------------------------------------------
# Exclusão do bolão
# ---------------------------------------------------------------------------


async def test_so_o_dono_exclui_o_bolao(db_session: AsyncSession) -> None:
    """Administrador gerencia a rodada, mas não desfaz o bolão de outro."""
    pool, *_ = await _bolao_personalizado(db_session, 53)
    intruso = await make_user(db_session, "intruso@teste.local", "Intruso")

    with pytest.raises(pool_service.PoolError, match="só o dono"):
        await pool_service.delete_pool(db_session, pool=pool, actor=intruso)

    assert await pool_service.get_pool_by_slug(db_session, pool.slug) is not None


async def test_excluir_leva_junto_rodada_palpite_e_ranking(db_session: AsyncSession) -> None:
    """Cascata de verdade: nada pode sobreviver apontando para um bolão morto."""
    pool, dono, jogo_br, jogo_pl, _m_dono, m_rival = await _bolao_personalizado(db_session, 54)

    rodada = await matchday_service.create_matchday(db_session, pool=pool, actor=dono)
    await matchday_service.set_fixtures(
        db_session, pool=pool, actor=dono, matchday=rodada, fixture_ids=[jogo_br.id, jogo_pl.id]
    )
    await prediction_service.upsert_prediction(
        db_session, membership=m_rival, fixture=jogo_br, home_goals=2, away_goals=1
    )
    await db_session.commit()

    pool_id, slug, palpite_id = pool.id, pool.slug, m_rival.id
    await pool_service.delete_pool(db_session, pool=pool, actor=dono)
    await db_session.commit()

    assert await pool_service.get_pool_by_slug(db_session, slug) is None
    assert (await db_session.scalar(select(Matchday).where(Matchday.pool_id == pool_id))) is None
    # Palpite pende do vínculo, e o vínculo pende do bolão: os dois têm que
    # cair juntos, senão sobra palpite órfão apontando para um bolão morto.
    assert (
        await db_session.scalar(select(Prediction).where(Prediction.membership_id == palpite_id))
    ) is None
    assert (
        await db_session.scalar(select(Membership).where(Membership.pool_id == pool_id))
    ) is None


async def test_excluir_um_bolao_nao_toca_no_outro(db_session: AsyncSession) -> None:
    primeiro, dono_um, *_ = await _bolao_personalizado(db_session, 55)
    segundo, *_ = await _bolao_personalizado(db_session, 56)
    await db_session.commit()

    await pool_service.delete_pool(db_session, pool=primeiro, actor=dono_um)
    await db_session.commit()

    assert await pool_service.get_pool_by_slug(db_session, segundo.slug) is not None


async def test_excluir_bolao_nao_apaga_o_jogo(db_session: AsyncSession) -> None:
    """Fixture é do campeonato, não do bolão. Outro bolão pode estar usando."""
    pool, dono, jogo_br, *_ = await _bolao_personalizado(db_session, 57)
    await db_session.commit()

    jogo_id = jogo_br.id
    await pool_service.delete_pool(db_session, pool=pool, actor=dono)
    await db_session.commit()

    assert await db_session.get(Fixture, jogo_id) is not None


async def test_cada_jogo_diz_de_que_campeonato_e(db_session: AsyncSession) -> None:
    """Numa rodada montada os jogos vêm de campeonatos diferentes, e lado a
    lado na tela nada os distinguia. Quem palpita precisa saber se aquele
    Palmeiras x Corinthians é do Brasileirão ou da Copa do Brasil — a aposta
    muda."""
    from app.api.pools import _com_campeonato
    from app.models import Competition, Season
    from app.schemas.common import FixtureOut

    _pool, _dono, jogo_br, jogo_pl, _m1, _m2 = await _bolao_personalizado(db_session, 91)

    # As fábricas nomeiam toda competição igual; aqui os nomes precisam diferir.
    for jogo, nome in ((jogo_br, "Brasileirão Série A"), (jogo_pl, "Premier League")):
        temporada = await db_session.get(Season, jogo.season_id)
        assert temporada is not None
        competicao = await db_session.get(Competition, temporada.competition_id)
        assert competicao is not None
        competicao.name = nome
    await db_session.flush()

    jogos = [jogo_br, jogo_pl]
    saida = [FixtureOut.model_validate(jogo) for jogo in jogos]
    resultado = await _com_campeonato(db_session, jogos, saida)

    por_id = {item.id: item for item in resultado}
    assert por_id[jogo_br.id].competition == "Brasileirão Série A"
    assert por_id[jogo_pl.id].competition == "Premier League"


async def test_campeonato_nao_dispara_consulta_por_jogo(db_session: AsyncSession) -> None:
    """Uma consulta para a lista inteira, não uma por jogo.

    A tela de palpite é a mais aberta do bolão e uma rodada tem dez jogos; o
    N+1 apareceria exatamente ali. Lista vazia nem consulta.
    """
    from app.api.pools import _com_campeonato

    assert await _com_campeonato(db_session, [], []) == []


async def test_estadio_do_jogo_chega_na_tela(db_session: AsyncSession) -> None:
    """O estádio já vinha de todos os seis coletores e morria no schema sem
    ninguém mostrar. É a informação que diz se o jogo é em casa de verdade."""
    from app.schemas.common import FixtureOut

    _pool, _dono, jogo_br, _jogo_pl, _m1, _m2 = await _bolao_personalizado(db_session, 92)
    jogo_br.venue = "Allianz Parque"
    await db_session.flush()

    assert FixtureOut.model_validate(jogo_br).venue == "Allianz Parque"
