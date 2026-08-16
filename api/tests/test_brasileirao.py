"""Geração da temporada e seleção de jogos.

A parte que mais importa aqui é a tabela: um turno-returno errado só aparece na
rodada 20, quando alguém percebe que dois times nunca se enfrentaram.
"""

from __future__ import annotations

from collections import Counter
from datetime import UTC, datetime, timedelta
from zoneinfo import ZoneInfo

import pytest
from sqlalchemy import func, select
from sqlalchemy.ext.asyncio import AsyncSession

from app.data.brasileirao import (
    CLUBS,
    first_saturday_of_april,
    generate_season,
    next_saturday_on_or_after,
    resolve_first_round,
    round_robin,
)
from app.data.crests import crest_path, crest_svg
from app.models import Fixture, PoolFixture, PredictionScore, Round, Team
from app.services import pools as pool_service
from app.services import predictions as prediction_service
from app.services import settlement as settlement_service
from app.services.seeding import seed_brasileirao
from tests.factories import add_member, make_pool, make_user

BRASILIA = ZoneInfo("America/Sao_Paulo")


# ---------------------------------------------------------------------------
# Tabela do campeonato (puro, sem banco)
# ---------------------------------------------------------------------------


def test_sao_vinte_clubes_com_sigla_unica() -> None:
    assert len(CLUBS) == 20
    assert len({club.slug for club in CLUBS}) == 20
    assert len({club.code for club in CLUBS}) == 20
    assert all(len(club.code) == 3 for club in CLUBS)


def test_turno_tem_dezenove_rodadas_de_dez_jogos() -> None:
    rodadas = round_robin([club.slug for club in CLUBS])

    assert len(rodadas) == 19
    assert all(len(rodada) == 10 for rodada in rodadas)


def test_no_turno_cada_time_joga_uma_vez_por_rodada() -> None:
    for rodada in round_robin([club.slug for club in CLUBS]):
        participantes = [time for confronto in rodada for time in confronto]
        assert len(participantes) == 20
        assert len(set(participantes)) == 20


def test_numero_impar_de_times_e_rejeitado() -> None:
    with pytest.raises(ValueError, match="par"):
        round_robin(["a", "b", "c"])


def test_temporada_tem_380_jogos_em_38_rodadas() -> None:
    partidas = generate_season(2026)

    assert len(partidas) == 380
    assert len({partida.round_number for partida in partidas}) == 38
    assert Counter(partida.round_number for partida in partidas).most_common(1)[0][1] == 10


def test_todo_mundo_enfrenta_todo_mundo_em_casa_e_fora() -> None:
    """A propriedade que define um turno-returno.

    Cada par de clubes se enfrenta exatamente duas vezes, e o mando é trocado
    entre os dois jogos. Se isso quebrar, o campeonato é injusto e ninguém
    percebe até o returno.
    """
    partidas = generate_season(2026)
    confrontos = Counter((partida.home_slug, partida.away_slug) for partida in partidas)

    assert all(total == 1 for total in confrontos.values())

    slugs = [club.slug for club in CLUBS]
    for casa in slugs:
        for fora in slugs:
            if casa == fora:
                continue
            assert confrontos[(casa, fora)] == 1, f"falta {casa} x {fora}"

    assert len(confrontos) == 20 * 19


def test_cada_time_tem_19_jogos_em_casa_e_19_fora() -> None:
    partidas = generate_season(2026)
    em_casa = Counter(partida.home_slug for partida in partidas)
    fora = Counter(partida.away_slug for partida in partidas)

    for club in CLUBS:
        assert em_casa[club.slug] == 19, club.name
        assert fora[club.slug] == 19, club.name


def test_ninguem_joga_contra_si_mesmo() -> None:
    assert all(partida.home_slug != partida.away_slug for partida in generate_season(2026))


def test_horarios_sao_utc_e_correspondem_a_brasilia() -> None:
    """A conversão acontece uma vez, na geração — não espalhada pelo código."""
    partidas = generate_season(2026)

    assert all(partida.kickoff_at.tzinfo is UTC for partida in partidas)

    horas_locais = {partida.kickoff_at.astimezone(BRASILIA).hour for partida in partidas}
    assert horas_locais <= {11, 16, 18, 20, 21}

    # O jogo das 21h de sábado é meia-noite de domingo em UTC — o caso que
    # coloca a partida no dia errado quando alguém agrupa pelo UTC.
    noturnos = [
        partida for partida in partidas if partida.kickoff_at.astimezone(BRASILIA).hour == 21
    ]
    assert noturnos
    assert all(partida.kickoff_at.hour == 0 for partida in noturnos)


def test_temporada_do_ano_corrente_nao_nasce_fechada() -> None:
    """Regressão encontrada criando o campeonato de verdade.

    Se a rodada 1 cai sempre em abril, quem monta o bolão em agosto recebe
    metade das rodadas já passadas — palpite fechado, bolão inútil. Abril vale
    quando ainda não chegou; depois disso, começa no próximo sábado.
    """
    agora = datetime(2026, 7, 31, 14, 0, tzinfo=UTC)

    inicio = resolve_first_round(2026, agora)

    assert inicio >= agora
    assert inicio.weekday() == 5
    assert (inicio - agora) < timedelta(days=8)


def test_temporada_criada_antes_de_abril_comeca_em_abril() -> None:
    inicio = resolve_first_round(2026, datetime(2026, 1, 10, tzinfo=UTC))

    assert inicio == first_saturday_of_april(2026)


def test_proximo_sabado_conta_o_proprio_dia() -> None:
    sabado = datetime(2026, 8, 1, 9, 0, tzinfo=UTC)  # é sábado

    assert next_saturday_on_or_after(sabado).date() == sabado.date()


def test_rodadas_sao_semanais_e_em_ordem() -> None:
    partidas = generate_season(2026)
    inicio = {}
    for partida in partidas:
        anterior = inicio.get(partida.round_number)
        if anterior is None or partida.kickoff_at < anterior:
            inicio[partida.round_number] = partida.kickoff_at

    ordenadas = [inicio[numero] for numero in sorted(inicio)]
    assert ordenadas == sorted(ordenadas)
    assert ordenadas[1] - ordenadas[0] == timedelta(days=7)


# ---------------------------------------------------------------------------
# Escudos
# ---------------------------------------------------------------------------


def test_escudo_de_cada_clube_e_svg_valido() -> None:
    for club in CLUBS:
        svg = crest_svg(club.slug)
        assert svg.startswith("<svg")
        assert svg.rstrip().endswith("</svg>")
        assert club.code in svg
        assert club.primary in svg


def test_escudo_de_clube_desconhecido_nao_quebra() -> None:
    svg = crest_svg("time-que-nao-existe")

    assert svg.startswith("<svg")
    assert "???" in svg


def test_sigla_tem_contraste_com_o_fundo() -> None:
    """Time de camisa clara precisa de sigla escura, e vice-versa."""
    mirassol = crest_svg("mirassol")  # amarelo
    corinthians = crest_svg("corinthians")  # preto

    assert 'fill="#111111"' in mirassol
    assert 'fill="#FFFFFF"' in corinthians


def test_caminho_do_escudo_passa_pelo_nginx() -> None:
    assert crest_path("palmeiras") == "/api/v1/catalog/teams/palmeiras/crest.svg"


# ---------------------------------------------------------------------------
# Banco
# ---------------------------------------------------------------------------

pytestmark_db = pytest.mark.integration


@pytest.mark.integration
async def test_seed_cria_a_temporada_inteira(db_session: AsyncSession) -> None:
    resultado = await seed_brasileirao(db_session, 2090)

    assert (resultado.teams, resultado.rounds, resultado.fixtures) == (20, 38, 380)

    jogos = await db_session.scalar(
        select(func.count()).select_from(Fixture).where(Fixture.season_id == resultado.season_id)
    )
    rodadas = await db_session.scalar(
        select(func.count()).select_from(Round).where(Round.season_id == resultado.season_id)
    )
    assert (jogos, rodadas) == (380, 38)


@pytest.mark.integration
async def test_seed_e_idempotente(db_session: AsyncSession) -> None:
    primeira = await seed_brasileirao(db_session, 2091)
    segunda = await seed_brasileirao(db_session, 2091)

    assert primeira.season_id == segunda.season_id
    jogos = await db_session.scalar(
        select(func.count()).select_from(Fixture).where(Fixture.season_id == primeira.season_id)
    )
    assert jogos == 380


@pytest.mark.integration
async def test_times_ficam_com_escudo_e_sigla(db_session: AsyncSession) -> None:
    await seed_brasileirao(db_session, 2092)

    palmeiras = await db_session.scalar(select(Team).where(Team.slug == "palmeiras"))

    assert palmeiras is not None
    assert palmeiras.short_name == "PAL"
    assert palmeiras.crest_url == "/api/v1/catalog/teams/palmeiras/crest.svg"


# ---------------------------------------------------------------------------
# Seleção de jogos pelo organizador
# ---------------------------------------------------------------------------


async def _bolao_do_brasileirao(session: AsyncSession, year: int):
    resultado = await seed_brasileirao(session, year)
    dono = await make_user(session, f"org{year}@teste.local", "Organizador")
    rival = await make_user(session, f"jog{year}@teste.local", "Jogador")

    from app.models import Season

    season = await session.get(Season, resultado.season_id)
    assert season is not None

    rodadas = (
        await session.scalars(
            select(Round).where(Round.season_id == season.id).order_by(Round.number)
        )
    ).all()
    pool = await make_pool(session, dono, season, rounds=list(rodadas), name=f"Brasileirão {year}")
    m_dono = await pool_service.get_membership(session, pool.id, dono.id)
    assert m_dono is not None
    m_rival = await add_member(session, pool, rival, "Jogador")
    return pool, season, list(rodadas), m_dono, m_rival, dono


@pytest.mark.integration
async def test_por_padrao_todos_os_jogos_valem(db_session: AsyncSession) -> None:
    pool, season, _rodadas, _m1, _m2, _dono = await _bolao_do_brasileirao(db_session, 2093)

    total = await pool_service.included_fixture_count(db_session, pool.id, season.id)

    assert total == 380


@pytest.mark.integration
async def test_excluir_um_jogo_tira_ele_do_bolao(db_session: AsyncSession) -> None:
    pool, season, rodadas, _m1, _m2, dono = await _bolao_do_brasileirao(db_session, 2094)
    jogo = await db_session.scalar(
        select(Fixture).where(Fixture.round_id == rodadas[0].id).limit(1)
    )
    assert jogo is not None

    await pool_service.set_fixture_inclusion(
        db_session, pool=pool, actor=dono, fixture_ids=[jogo.id], included=False
    )

    assert await pool_service.fixture_is_included(db_session, pool.id, jogo) is False
    assert await pool_service.included_fixture_count(db_session, pool.id, season.id) == 379


@pytest.mark.integration
async def test_jogo_excluido_recusa_palpite(db_session: AsyncSession) -> None:
    pool, _season, rodadas, _m_dono, _m2, dono = await _bolao_do_brasileirao(db_session, 2095)
    jogo = await db_session.scalar(
        select(Fixture).where(Fixture.round_id == rodadas[0].id).limit(1)
    )
    assert jogo is not None
    await pool_service.set_fixture_inclusion(
        db_session, pool=pool, actor=dono, fixture_ids=[jogo.id], included=False
    )

    incluido = await pool_service.fixture_is_included(db_session, pool.id, jogo)

    assert incluido is False


@pytest.mark.integration
async def test_jogo_excluido_nao_pontua(db_session: AsyncSession) -> None:
    """A apuração consulta a mesma regra do CRUD de palpite."""
    from app.models import FixtureStatus

    pool, _season, rodadas, m_dono, _m2, dono = await _bolao_do_brasileirao(db_session, 2096)
    jogos = (
        await db_session.scalars(
            select(Fixture).where(Fixture.round_id == rodadas[0].id).order_by(Fixture.id)
        )
    ).all()
    valendo, excluido = jogos[0], jogos[1]

    for jogo in (valendo, excluido):
        await prediction_service.upsert_prediction(
            db_session, membership=m_dono, fixture=jogo, home_goals=1, away_goals=0
        )

    await pool_service.set_fixture_inclusion(
        db_session, pool=pool, actor=dono, fixture_ids=[excluido.id], included=False
    )

    for jogo in (valendo, excluido):
        jogo.home_ft, jogo.away_ft = 1, 0
        jogo.status = FixtureStatus.FINISHED
    await db_session.flush()

    for jogo in (valendo, excluido):
        await settlement_service.settle_fixture(db_session, jogo.id)

    pontuados = list(
        (
            await db_session.scalars(
                select(PredictionScore.fixture_id).where(PredictionScore.pool_id == pool.id)
            )
        ).all()
    )

    assert pontuados == [valendo.id]


@pytest.mark.integration
async def test_excluir_a_rodada_inteira(db_session: AsyncSession) -> None:
    pool, season, rodadas, _m1, _m2, dono = await _bolao_do_brasileirao(db_session, 2097)

    await pool_service.set_round_inclusion(
        db_session, pool=pool, actor=dono, round_id=rodadas[0].id, included=False
    )

    assert await pool_service.included_fixture_count(db_session, pool.id, season.id) == 370


@pytest.mark.integration
async def test_reincluir_a_rodada_limpa_as_excecoes(db_session: AsyncSession) -> None:
    """Quem diz "a rodada inteira entra" não espera uma exclusão antiga sobreviver."""
    pool, season, rodadas, _m1, _m2, dono = await _bolao_do_brasileirao(db_session, 2098)
    jogo = await db_session.scalar(
        select(Fixture).where(Fixture.round_id == rodadas[0].id).limit(1)
    )
    assert jogo is not None

    await pool_service.set_fixture_inclusion(
        db_session, pool=pool, actor=dono, fixture_ids=[jogo.id], included=False
    )
    await pool_service.set_round_inclusion(
        db_session, pool=pool, actor=dono, round_id=rodadas[0].id, included=True
    )

    sobrou = await db_session.scalar(
        select(func.count()).select_from(PoolFixture).where(PoolFixture.pool_id == pool.id)
    )
    assert sobrou == 0
    assert await pool_service.included_fixture_count(db_session, pool.id, season.id) == 380


@pytest.mark.integration
async def test_bolao_so_de_classicos(db_session: AsyncSession) -> None:
    """O caso de uso real: tirar tudo e ligar meia dúzia de jogos."""
    pool, season, rodadas, _m1, _m2, dono = await _bolao_do_brasileirao(db_session, 2099)

    for rodada in rodadas:
        await pool_service.set_round_inclusion(
            db_session, pool=pool, actor=dono, round_id=rodada.id, included=False
        )

    escolhidos = list(
        (
            await db_session.scalars(
                select(Fixture.id).where(Fixture.season_id == season.id).limit(6)
            )
        ).all()
    )
    await pool_service.set_fixture_inclusion(
        db_session, pool=pool, actor=dono, fixture_ids=escolhidos, included=True
    )

    assert await pool_service.included_fixture_count(db_session, pool.id, season.id) == 6


@pytest.mark.integration
async def test_so_organizador_muda_a_selecao(db_session: AsyncSession) -> None:
    pool, _season, rodadas, _m1, m_rival, _dono = await _bolao_do_brasileirao(db_session, 2100)
    from app.models import User

    jogador = await db_session.get(User, m_rival.user_id)
    assert jogador is not None
    jogo = await db_session.scalar(
        select(Fixture).where(Fixture.round_id == rodadas[0].id).limit(1)
    )
    assert jogo is not None

    with pytest.raises(pool_service.NotAuthorized):
        await pool_service.set_fixture_inclusion(
            db_session, pool=pool, actor=jogador, fixture_ids=[jogo.id], included=False
        )
