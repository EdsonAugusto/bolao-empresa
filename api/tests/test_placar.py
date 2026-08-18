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

    assert placar_service.ligas_espn_de(competition) == []

    # A chave antiga, no singular, continua valendo: é o que está gravado nas
    # competições importadas antes de o torneio poder ter mais de uma liga.
    competition.provider_config = {"espn_league": "ned.1"}
    assert placar_service.ligas_espn_de(competition) == ["ned.1"]

    # A nova manda, e preserva a ordem de tentativa.
    competition.provider_config = {
        "espn_league": "uefa.champions",
        "espn_leagues": ["uefa.champions_qual", "uefa.champions"],
    }
    assert placar_service.ligas_espn_de(competition) == [
        "uefa.champions_qual",
        "uefa.champions",
    ]


# ---------------------------------------------------------------------------
# Torneio partido em duas ligas na fonte
# ---------------------------------------------------------------------------


class _FontePorLiga:
    """ESPN de mentira que só conhece jogo na liga certa.

    É o comportamento real: pedir a qualificação da Champions em
    `uefa.champions` devolve lista vazia, não erro.
    """

    def __init__(self, por_liga: dict[str, list[PlacarAoVivo]]) -> None:
        self.por_liga = por_liga
        self.consultadas: list[str] = []

    async def placares(self, liga: str, dia: object) -> list[PlacarAoVivo]:
        self.consultadas.append(liga)
        return self.por_liga.get(liga, [])

    async def aclose(self) -> None:
        return None


async def test_placar_de_jogo_da_qualificacao_e_encontrado(db_session: AsyncSession) -> None:
    """O jogo de agosto da Champions é de qualificação e vive noutra liga.

    Guardando só `uefa.champions`, a consulta ia para a liga certa do torneio e
    errada do jogo, voltava vazia, e o jogo ficava 0 a 0 em campo para sempre —
    sem erro nenhum, porque não achar jogo não é falha.
    """
    season = await make_season(db_session, 2081)
    rodada = await make_round(db_session, season)
    casa = await make_team(db_session, season, "Dinamo Zagreb")
    fora = await make_team(db_session, season, "Viking FK")
    jogo = await make_fixture(
        db_session, season, rodada, casa, fora, kickoff_in=timedelta(hours=-1)
    )

    competicao = await db_session.get(Competition, season.competition_id)
    assert competicao is not None
    competicao.provider_config = {
        "espn_leagues": ["uefa.champions_qual", "uefa.champions"],
        "espn_league": "uefa.champions",
    }
    await db_session.flush()

    fonte = _FontePorLiga(
        {
            "uefa.champions_qual": [
                _placar(
                    "Dinamo Zagreb",
                    "Viking FK",
                    kickoff_at=jogo.kickoff_at,
                    home_ft=2,
                    away_ft=2,
                    status=FixtureStatus.HT,
                    encerrado=False,
                    minuto=45,
                )
            ],
            "uefa.champions": [],
        }
    )

    resultado = await placar_service.aplicar_placares(db_session, [jogo], fonte=fonte)

    assert resultado.falhas == []
    assert (jogo.home_ft, jogo.away_ft) == (2, 2)
    # Achou na primeira e não gastou requisição na segunda.
    assert fonte.consultadas == ["uefa.champions_qual"]


async def test_ordem_das_ligas_e_respeitada_e_a_segunda_e_tentada(
    db_session: AsyncSession,
) -> None:
    """Em setembro o jogo estará na liga principal, e a qualificação é que sai
    vazia. As duas precisam ser tentadas, na ordem."""
    season = await make_season(db_session, 2082)
    rodada = await make_round(db_session, season)
    casa = await make_team(db_session, season, "Real Madrid")
    fora = await make_team(db_session, season, "Arsenal")
    jogo = await make_fixture(
        db_session, season, rodada, casa, fora, kickoff_in=timedelta(hours=-1)
    )

    competicao = await db_session.get(Competition, season.competition_id)
    assert competicao is not None
    competicao.provider_config = {"espn_leagues": ["uefa.champions_qual", "uefa.champions"]}
    await db_session.flush()

    fonte = _FontePorLiga(
        {
            "uefa.champions_qual": [],
            "uefa.champions": [
                _placar("Real Madrid", "Arsenal", kickoff_at=jogo.kickoff_at, home_ft=3, away_ft=1)
            ],
        }
    )

    await placar_service.aplicar_placares(db_session, [jogo], fonte=fonte)

    assert (jogo.home_ft, jogo.away_ft) == (3, 1)
    assert fonte.consultadas == ["uefa.champions_qual", "uefa.champions"]


async def test_competicao_de_uma_liga_so_continua_com_uma_requisicao(
    db_session: AsyncSession,
) -> None:
    """A mudança não pode dobrar o tráfego de quem tem uma liga só."""
    season = await make_season(db_session, 2083)
    rodada = await make_round(db_session, season)
    casa = await make_team(db_session, season, "Palmeiras")
    fora = await make_team(db_session, season, "Corinthians")
    jogo = await make_fixture(
        db_session, season, rodada, casa, fora, kickoff_in=timedelta(hours=-1)
    )

    competicao = await db_session.get(Competition, season.competition_id)
    assert competicao is not None
    competicao.provider_config = {"espn_league": "bra.1"}
    await db_session.flush()

    fonte = _FontePorLiga(
        {"bra.1": [_placar("Palmeiras", "Corinthians", kickoff_at=jogo.kickoff_at)]}
    )

    await placar_service.aplicar_placares(db_session, [jogo], fonte=fonte)

    assert fonte.consultadas == ["bra.1"]
