"""Placar ao vivo sobreposto ao calendário.

O risco aqui é o mesmo do escudo, com consequência pior: casar o jogo errado
grava um placar errado, e placar errado apura pontos errados. Por isso a maior
parte destes testes verifica **recusa**, e o casamento exige o confronto
inteiro — mandante, visitante, liga e horário.
"""

from __future__ import annotations

from datetime import UTC, datetime, timedelta

from sqlalchemy.ext.asyncio import AsyncSession

from app.models import Competition, FixtureStatus
from app.providers.espn import PlacarAoVivo
from app.services import placar as placar_service
from tests.factories import make_fixture, make_round, make_season, make_team

KICKOFF = datetime(2026, 8, 8, 18, 0, tzinfo=UTC)


def _placar(casa: str, fora: str, **kwargs) -> PlacarAoVivo:
    base = {
        "kickoff_at": KICKOFF,
        "status": FixtureStatus.FINISHED,
        "home_ft": 2,
        "away_ft": 2,
        "minuto": 90,
        "encerrado": True,
    }
    base.update(kwargs)
    return PlacarAoVivo(casa=casa, fora=fora, **base)


# ---------------------------------------------------------------------------
# Comparação de nome — tolerante de propósito
# ---------------------------------------------------------------------------


def test_nomes_reais_que_precisam_casar() -> None:
    """Os três que a comparação estrita perdia na medição real."""
    assert placar_service.parecido("PSV", "PSV Eindhoven")
    assert placar_service.parecido("AZ", "AZ Alkmaar")
    assert placar_service.parecido("N.E.C. Nijmegen", "NEC Nijmegen")
    assert placar_service.parecido("Marítimo M.", "Maritimo")
    assert placar_service.parecido("Estoril Praia", "Estoril")


def test_nomes_que_nao_podem_casar() -> None:
    assert not placar_service.parecido("Grêmio", "São Paulo")
    assert not placar_service.parecido("Flamengo", "Fluminense")
    assert not placar_service.parecido("", "PSV")


# ---------------------------------------------------------------------------
# Casamento do confronto — é o que segura a tolerância acima
# ---------------------------------------------------------------------------


async def _jogo(session: AsyncSession, marca: int, casa: str, fora: str, *, kickoff=KICKOFF):
    season = await make_season(session, marca)
    rodada = await make_round(session, season)
    time_casa = await make_team(session, season, casa)
    time_fora = await make_team(session, season, fora)
    jogo = await make_fixture(
        session, season, rodada, time_casa, time_fora, kickoff_in=timedelta(days=1)
    )
    jogo.kickoff_at = kickoff
    jogo.status = FixtureStatus.LIVE
    await session.flush()
    return jogo


async def test_casa_o_confronto_certo(db_session: AsyncSession) -> None:
    jogo = await _jogo(db_session, 8100, "PSV", "Fortuna Sittard")

    achado, ambiguo = placar_service.casar(
        jogo,
        "PSV",
        "Fortuna Sittard",
        [
            _placar("AZ Alkmaar", "ADO Den Haag"),
            _placar("PSV Eindhoven", "Fortuna Sittard"),
        ],
    )

    assert ambiguo is False
    assert achado is not None
    assert achado.casa == "PSV Eindhoven"


async def test_meio_confronto_nao_basta(db_session: AsyncSession) -> None:
    """O mandante bate, o visitante não. É outro jogo."""
    jogo = await _jogo(db_session, 8101, "PSV", "Fortuna Sittard")

    achado, _ = placar_service.casar(
        jogo, "PSV", "Fortuna Sittard", [_placar("PSV Eindhoven", "Ajax")]
    )

    assert achado is None


async def test_confronto_em_outro_dia_nao_basta(db_session: AsyncSession) -> None:
    """O mesmo par joga duas vezes na temporada. O horário separa."""
    jogo = await _jogo(db_session, 8102, "PSV", "Fortuna Sittard")

    achado, _ = placar_service.casar(
        jogo,
        "PSV",
        "Fortuna Sittard",
        [_placar("PSV Eindhoven", "Fortuna Sittard", kickoff_at=KICKOFF + timedelta(days=90))],
    )

    assert achado is None


async def test_empate_de_proximidade_e_recusado(db_session: AsyncSession) -> None:
    """Dois candidatos igualmente próximos: não dá para escolher, então não escolhe."""
    jogo = await _jogo(db_session, 8103, "PSV", "Fortuna Sittard")

    achado, ambiguo = placar_service.casar(
        jogo,
        "PSV",
        "Fortuna Sittard",
        [
            _placar("PSV Eindhoven", "Fortuna Sittard", kickoff_at=KICKOFF + timedelta(hours=1)),
            _placar("PSV", "Fortuna Sittard", kickoff_at=KICKOFF - timedelta(hours=1)),
        ],
    )

    assert ambiguo is True
    assert achado is None


# ---------------------------------------------------------------------------
# Aplicação — as mesmas guardas da ingestão
# ---------------------------------------------------------------------------


async def test_placar_entra_e_encerra(db_session: AsyncSession) -> None:
    jogo = await _jogo(db_session, 8104, "PSV", "Fortuna Sittard")

    mudou = placar_service._aplicar(
        jogo, _placar("PSV Eindhoven", "Fortuna Sittard"), datetime.now(UTC)
    )

    assert mudou is True
    assert (jogo.home_ft, jogo.away_ft) == (2, 2)
    assert jogo.status is FixtureStatus.FINISHED


async def test_placar_em_branco_nao_apaga(db_session: AsyncSession) -> None:
    jogo = await _jogo(db_session, 8105, "PSV", "Fortuna Sittard")
    jogo.home_ft, jogo.away_ft = 3, 1
    await db_session.flush()

    placar_service._aplicar(
        jogo,
        _placar("PSV Eindhoven", "Fortuna Sittard", home_ft=None, away_ft=None),
        datetime.now(UTC),
    )

    assert (jogo.home_ft, jogo.away_ft) == (3, 1)


async def test_encerrado_nao_regride(db_session: AsyncSession) -> None:
    jogo = await _jogo(db_session, 8106, "PSV", "Fortuna Sittard")
    jogo.status = FixtureStatus.FINISHED
    jogo.home_ft, jogo.away_ft = 2, 2
    await db_session.flush()

    placar_service._aplicar(
        jogo,
        _placar("PSV Eindhoven", "Fortuna Sittard", status=FixtureStatus.SCHEDULED),
        datetime.now(UTC),
    )

    assert jogo.status is FixtureStatus.FINISHED


async def test_em_campo_nao_volta_a_agendado(db_session: AsyncSession) -> None:
    jogo = await _jogo(db_session, 8107, "PSV", "Fortuna Sittard")

    placar_service._aplicar(
        jogo,
        _placar(
            "PSV Eindhoven",
            "Fortuna Sittard",
            status=FixtureStatus.SCHEDULED,
            home_ft=None,
            away_ft=None,
        ),
        datetime.now(UTC),
    )

    assert jogo.status is FixtureStatus.LIVE


async def test_nada_a_mudar_nao_marca_como_tocado(db_session: AsyncSession) -> None:
    """Idempotência: a passada roda de 2 em 2 minutos e não pode sujar tudo."""
    jogo = await _jogo(db_session, 8108, "PSV", "Fortuna Sittard")
    jogo.status = FixtureStatus.FINISHED
    jogo.home_ft, jogo.away_ft, jogo.minute = 2, 2, 90
    await db_session.flush()

    mudou = placar_service._aplicar(
        jogo, _placar("PSV Eindhoven", "Fortuna Sittard"), datetime.now(UTC)
    )

    assert mudou is False


# ---------------------------------------------------------------------------
# Competição sem mapeamento
# ---------------------------------------------------------------------------


async def test_competicao_sem_liga_da_espn_e_ignorada(db_session: AsyncSession) -> None:
    """Não é erro: campeonato cadastrado à mão simplesmente não tem essa fonte."""
    season = await make_season(db_session, 8109)
    competition = await db_session.get(Competition, season.competition_id)
    assert competition is not None

    assert placar_service.liga_espn_de(competition) is None

    competition.provider_config = {"espn_league": "ned.1"}
    assert placar_service.liga_espn_de(competition) == "ned.1"
