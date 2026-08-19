"""Quem recebe o lembrete de palpite, e o que o lembrete diz.

O lembrete antigo era ancorado no início da RODADA de campeonato. Isso deixava
de fora a rodada montada à mão — que não tem rodada nenhuma — e o jogo de
domingo de uma rodada que começou no sábado. Aqui a pergunta é por janela de
horário de bola rolando, e vale para os dois tipos de bolão.

O que estes testes protegem, além de "avisa quem esqueceu": não avisar quem já
palpitou, não contar errado para quem palpitou metade, e não lembrar de jogo
que não aceita mais palpite — cada um desses erros ensina a pessoa a ignorar o
aviso, e aí o recurso todo deixa de servir.
"""

from __future__ import annotations

from datetime import UTC, datetime, timedelta

from sqlalchemy.ext.asyncio import AsyncSession

from app.models import FixtureStatus, Membership, Prediction
from app.services import lembretes as lembrete_service
from app.services import pools as pool_service
from tests.factories import make_fixture, make_pool, make_round, make_season, make_team, make_user

AGORA = datetime.now(UTC)


async def _bolao_com_dois_jogos(session: AsyncSession, marca: int, *, horas: int = 3):
    """Bolão de campeonato com dois jogos daqui a algumas horas."""
    dono = await make_user(session, f"dono{marca}@teste.local", "Dono")
    season = await make_season(session, 2500 + marca)
    rodada = await make_round(session, season)

    a = await make_team(session, season, f"Time A {marca}")
    b = await make_team(session, season, f"Time B {marca}")
    c = await make_team(session, season, f"Time C {marca}")
    d = await make_team(session, season, f"Time D {marca}")

    jogo1 = await make_fixture(session, season, rodada, a, b, kickoff_in=timedelta(hours=horas))
    jogo2 = await make_fixture(session, season, rodada, c, d, kickoff_in=timedelta(hours=horas + 1))

    pool = await make_pool(session, dono, season, rounds=[rodada], name=f"Bolão {marca}")
    membro = await pool_service.get_membership(session, pool.id, dono.id)
    await session.flush()
    return pool, membro, jogo1, jogo2


async def _palpitar(session: AsyncSession, membro: Membership, jogo, casa: int, fora: int) -> None:
    session.add(
        Prediction(
            membership_id=membro.id,
            fixture_id=jogo.id,
            home_goals=casa,
            away_goals=fora,
        )
    )
    await session.flush()


async def test_quem_nao_palpitou_nada_entra_com_todos_os_jogos(db_session: AsyncSession) -> None:
    _pool, membro, jogo1, jogo2 = await _bolao_com_dois_jogos(db_session, 1)

    pendencias = await lembrete_service.quem_falta_palpitar(
        db_session, de=AGORA, ate=AGORA + timedelta(hours=12)
    )

    minhas = [p for p in pendencias if p.membership.id == membro.id]
    assert len(minhas) == 1
    assert minhas[0].quantos == 2
    assert set(minhas[0].fixture_ids) == {jogo1.id, jogo2.id}


async def test_quem_palpitou_metade_e_contado_certo(db_session: AsyncSession) -> None:
    """Dizer "faltam 2" a quem já palpitou um é o tipo de erro que faz a pessoa
    parar de ler o aviso."""
    _pool, membro, jogo1, jogo2 = await _bolao_com_dois_jogos(db_session, 2)
    await _palpitar(db_session, membro, jogo1, 1, 0)

    pendencias = await lembrete_service.quem_falta_palpitar(
        db_session, de=AGORA, ate=AGORA + timedelta(hours=12)
    )

    minhas = [p for p in pendencias if p.membership.id == membro.id]
    assert len(minhas) == 1
    assert minhas[0].quantos == 1
    assert minhas[0].fixture_ids == [jogo2.id]


async def test_quem_palpitou_tudo_nao_e_incomodado(db_session: AsyncSession) -> None:
    _pool, membro, jogo1, jogo2 = await _bolao_com_dois_jogos(db_session, 3)
    await _palpitar(db_session, membro, jogo1, 1, 0)
    await _palpitar(db_session, membro, jogo2, 2, 2)

    pendencias = await lembrete_service.quem_falta_palpitar(
        db_session, de=AGORA, ate=AGORA + timedelta(hours=12)
    )

    assert [p for p in pendencias if p.membership.id == membro.id] == []


async def test_jogo_que_nao_aceita_mais_palpite_fica_de_fora(db_session: AsyncSession) -> None:
    """Lembrar de palpitar num jogo em campo é pior do que não lembrar de nada:
    a pessoa abre o app, tenta, e a trava do banco recusa."""
    _pool, membro, jogo1, jogo2 = await _bolao_com_dois_jogos(db_session, 4)
    jogo1.status = FixtureStatus.LIVE
    await db_session.flush()

    pendencias = await lembrete_service.quem_falta_palpitar(
        db_session, de=AGORA, ate=AGORA + timedelta(hours=12)
    )

    minhas = [p for p in pendencias if p.membership.id == membro.id]
    assert len(minhas) == 1
    assert minhas[0].fixture_ids == [jogo2.id]


async def test_jogo_fora_da_janela_nao_entra(db_session: AsyncSession) -> None:
    """A última chamada olha os trinta minutos seguintes, não o campeonato
    inteiro."""
    _pool, membro, _jogo1, _jogo2 = await _bolao_com_dois_jogos(db_session, 5, horas=10)

    pendencias = await lembrete_service.quem_falta_palpitar(
        db_session, de=AGORA, ate=AGORA + timedelta(hours=1)
    )

    assert [p for p in pendencias if p.membership.id == membro.id] == []


async def test_primeiro_kickoff_e_o_do_jogo_que_falta(db_session: AsyncSession) -> None:
    """O texto do aviso fala do prazo. Se a pessoa já palpitou o jogo mais
    cedo, o prazo dela é o do próximo — não o do primeiro da lista."""
    _pool, membro, jogo1, jogo2 = await _bolao_com_dois_jogos(db_session, 6)
    await _palpitar(db_session, membro, jogo1, 0, 0)

    pendencias = await lembrete_service.quem_falta_palpitar(
        db_session, de=AGORA, ate=AGORA + timedelta(hours=12)
    )

    minhas = [p for p in pendencias if p.membership.id == membro.id]
    assert minhas[0].primeiro_kickoff == jogo2.kickoff_at


async def test_rodada_montada_a_mao_tambem_gera_lembrete(db_session: AsyncSession) -> None:
    """É o buraco que motivou este serviço.

    O bolão personalizado não tem `Round`, então o lembrete antigo — ancorado
    em `Round.starts_at` — nunca saía para ele. E é justamente o formato que
    mais precisa: sem calendário previsível, ninguém lembra sozinho.
    """
    from app.services import matchdays as matchday_service
    from tests.test_matchdays import _bolao_personalizado

    pool, dono, jogo_br, jogo_pl, _m1, _m2 = await _bolao_personalizado(db_session, 77)
    rodada = await matchday_service.create_matchday(db_session, pool=pool, actor=dono)
    await matchday_service.set_fixtures(
        db_session, pool=pool, actor=dono, matchday=rodada, fixture_ids=[jogo_br.id, jogo_pl.id]
    )
    await db_session.flush()

    pendencias = await lembrete_service.quem_falta_palpitar(
        db_session, de=AGORA, ate=AGORA + timedelta(hours=24)
    )

    do_bolao = [p for p in pendencias if p.pool.id == pool.id]
    assert do_bolao, "bolão de rodada montada não gerou lembrete nenhum"
    assert all(set(p.fixture_ids) == {jogo_br.id, jogo_pl.id} for p in do_bolao)
