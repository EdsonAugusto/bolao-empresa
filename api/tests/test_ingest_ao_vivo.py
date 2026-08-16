"""Ingestão repetida durante a partida.

A coleta ao vivo roda de minuto em minuto enquanto a bola rola. Isso muda a
natureza do ``ingest_snapshot``: antes ele era chamado uma vez por dia, com um
retrato bom; agora é chamado dezenas de vezes por jogo, e **uma passada ruim
não pode desfazer uma boa**.

O estrago de deixar um jogo encerrado regredir é triplo e nem todo mundo é
óbvio: o jogo volta para a fila de apuração, o ranking oscila, e — o pior — a
trava de palpite reabre, deixando palpitar num jogo cujo placar todo mundo já
viu no telão.
"""

from __future__ import annotations

from datetime import UTC, datetime, timedelta

from sqlalchemy.ext.asyncio import AsyncSession

from app.models import FixtureStatus
from app.providers.base import (
    ProviderCompetition,
    ProviderFixture,
    ProviderRound,
    ProviderSeason,
    ProviderSnapshot,
    ProviderTeam,
)
from app.services import catalog as catalog_service

PROVEDOR = "teste-ao-vivo"
KICKOFF = datetime(2026, 8, 8, 19, 0, tzinfo=UTC)


def _snapshot(
    marca: int,
    *,
    status: FixtureStatus,
    home_ft: int | None = None,
    away_ft: int | None = None,
    minute: int | None = None,
) -> ProviderSnapshot:
    """Retrato de uma temporada de um jogo só, no estado pedido."""
    casa = ProviderTeam(external_id=f"casa{marca}", name=f"Casa {marca}", slug=f"casa-{marca}")
    fora = ProviderTeam(external_id=f"fora{marca}", name=f"Fora {marca}", slug=f"fora-{marca}")
    return ProviderSnapshot(
        competition=ProviderCompetition(
            external_id=f"camp{marca}", slug=f"camp-{marca}", name=f"Campeonato {marca}"
        ),
        season=ProviderSeason(
            external_id="2026", competition_external_id=f"camp{marca}", year=2026
        ),
        teams=(casa, fora),
        fixtures=(
            ProviderFixture(
                external_id=f"jogo{marca}",
                home_team_external_id=casa.external_id,
                away_team_external_id=fora.external_id,
                kickoff_at=KICKOFF,
                status=status,
                round=ProviderRound(name="Rodada 1", number=1),
                minute=minute,
                home_ft=home_ft,
                away_ft=away_ft,
            ),
        ),
    )


async def _ingerir(session: AsyncSession, snapshot: ProviderSnapshot):
    season, _ = await catalog_service.ingest_snapshot(session, PROVEDOR, "Teste", snapshot)
    await session.flush()
    return season


async def _jogo(session: AsyncSession, marca: int):
    from sqlalchemy import select

    from app.models import Fixture

    return await session.scalar(select(Fixture).where(Fixture.external_id == f"jogo{marca}"))


# ---------------------------------------------------------------------------
# Avanço normal do jogo
# ---------------------------------------------------------------------------


async def test_jogo_avanca_de_agendado_para_ao_vivo_e_encerrado(
    db_session: AsyncSession,
) -> None:
    await _ingerir(db_session, _snapshot(1, status=FixtureStatus.SCHEDULED))
    assert (await _jogo(db_session, 1)).status is FixtureStatus.SCHEDULED

    await _ingerir(
        db_session, _snapshot(1, status=FixtureStatus.LIVE, home_ft=1, away_ft=0, minute=23)
    )
    jogo = await _jogo(db_session, 1)
    assert jogo.status is FixtureStatus.LIVE
    assert (jogo.home_ft, jogo.away_ft, jogo.minute) == (1, 0, 23)

    await _ingerir(db_session, _snapshot(1, status=FixtureStatus.FINISHED, home_ft=2, away_ft=1))
    jogo = await _jogo(db_session, 1)
    assert jogo.status is FixtureStatus.FINISHED
    assert (jogo.home_ft, jogo.away_ft) == (2, 1)


async def test_placar_muda_durante_o_jogo(db_session: AsyncSession) -> None:
    """Gol no segundo tempo tem que aparecer, não ficar preso no 1x0."""
    await _ingerir(db_session, _snapshot(2, status=FixtureStatus.LIVE, home_ft=1, away_ft=0))
    await _ingerir(db_session, _snapshot(2, status=FixtureStatus.LIVE, home_ft=1, away_ft=1))

    jogo = await _jogo(db_session, 2)
    assert (jogo.home_ft, jogo.away_ft) == (1, 1)


# ---------------------------------------------------------------------------
# Passada ruim não desfaz passada boa
# ---------------------------------------------------------------------------


async def test_encerrado_nao_volta_a_ser_agendado(db_session: AsyncSession) -> None:
    """A fonte piscou e devolveu o jogo como futuro. Ignorar é o certo.

    Aceitar isso reabriria a trava de palpite num jogo já decidido.
    """
    await _ingerir(db_session, _snapshot(3, status=FixtureStatus.FINISHED, home_ft=3, away_ft=0))
    await _ingerir(db_session, _snapshot(3, status=FixtureStatus.SCHEDULED))

    jogo = await _jogo(db_session, 3)
    assert jogo.status is FixtureStatus.FINISHED
    assert (jogo.home_ft, jogo.away_ft) == (3, 0)


async def test_encerrado_nao_volta_a_ao_vivo(db_session: AsyncSession) -> None:
    await _ingerir(db_session, _snapshot(4, status=FixtureStatus.FINISHED, home_ft=1, away_ft=1))
    await _ingerir(db_session, _snapshot(4, status=FixtureStatus.LIVE, home_ft=1, away_ft=1))

    assert (await _jogo(db_session, 4)).status is FixtureStatus.FINISHED


async def test_coleta_sem_placar_nao_apaga_o_placar(db_session: AsyncSession) -> None:
    """Fonte que ainda não publicou o resultado devolve `None`.

    Gravar esse `None` zeraria um jogo já apurado e mandaria a rodada inteira
    para reapuração com placar vazio.
    """
    await _ingerir(db_session, _snapshot(5, status=FixtureStatus.FINISHED, home_ft=2, away_ft=1))
    await _ingerir(db_session, _snapshot(5, status=FixtureStatus.FINISHED))

    jogo = await _jogo(db_session, 5)
    assert (jogo.home_ft, jogo.away_ft) == (2, 1)


async def test_adiamento_depois_de_encerrado_e_ignorado(db_session: AsyncSession) -> None:
    """`POSTPONED` reabre a trava. Depois de encerrado, não pode."""
    await _ingerir(db_session, _snapshot(6, status=FixtureStatus.FINISHED, home_ft=0, away_ft=0))
    await _ingerir(db_session, _snapshot(6, status=FixtureStatus.POSTPONED))

    assert (await _jogo(db_session, 6)).status is FixtureStatus.FINISHED


# ---------------------------------------------------------------------------
# Correção legítima continua passando
# ---------------------------------------------------------------------------


async def test_placar_corrigido_depois_de_encerrado_e_aceito(db_session: AsyncSession) -> None:
    """VAR, W.O. e tribunal mudam placar de jogo encerrado. Isso tem que valer."""
    await _ingerir(db_session, _snapshot(7, status=FixtureStatus.FINISHED, home_ft=1, away_ft=0))
    await _ingerir(db_session, _snapshot(7, status=FixtureStatus.FINISHED, home_ft=0, away_ft=3))

    jogo = await _jogo(db_session, 7)
    assert (jogo.home_ft, jogo.away_ft) == (0, 3)


async def test_correcao_de_placar_marca_para_reapurar(db_session: AsyncSession) -> None:
    await _ingerir(db_session, _snapshot(8, status=FixtureStatus.FINISHED, home_ft=1, away_ft=0))
    jogo = await _jogo(db_session, 8)
    jogo.settled_at = datetime.now(UTC)
    await db_session.flush()

    await _ingerir(db_session, _snapshot(8, status=FixtureStatus.FINISHED, home_ft=2, away_ft=2))

    jogo = await _jogo(db_session, 8)
    assert jogo.settlement_dirty is True


# ---------------------------------------------------------------------------
# Idempotência — a base da coleta repetida
# ---------------------------------------------------------------------------


async def test_ingerir_o_mesmo_retrato_duas_vezes_nao_duplica(db_session: AsyncSession) -> None:
    from sqlalchemy import func, select

    from app.models import Fixture

    snapshot = _snapshot(9, status=FixtureStatus.FINISHED, home_ft=1, away_ft=1)
    await _ingerir(db_session, snapshot)
    await _ingerir(db_session, snapshot)

    total = await db_session.scalar(
        select(func.count()).select_from(Fixture).where(Fixture.external_id == "jogo9")
    )
    assert total == 1


async def test_reingerir_encerrado_nao_marca_como_sujo(db_session: AsyncSession) -> None:
    """Polling num jogo já encerrado não pode reapurar a rodada toda a cada minuto."""
    snapshot = _snapshot(10, status=FixtureStatus.FINISHED, home_ft=2, away_ft=0)
    await _ingerir(db_session, snapshot)
    jogo = await _jogo(db_session, 10)
    jogo.settled_at = datetime.now(UTC) - timedelta(hours=1)
    jogo.settlement_dirty = False
    await db_session.flush()

    await _ingerir(db_session, snapshot)

    assert (await _jogo(db_session, 10)).settlement_dirty is False


# ---------------------------------------------------------------------------
# Coleta parcial não pode falar pela temporada inteira
# ---------------------------------------------------------------------------


async def test_coleta_parcial_nao_encolhe_a_temporada(db_session: AsyncSession) -> None:
    """A coleta ao vivo traz uma rodada, não a temporada.

    Se ela disser "a temporada vai de 8 a 10 de agosto" — que é o intervalo dos
    jogos que ela trouxe — o intervalo real da temporada some, e volta a sumir a
    cada dois minutos enquanto a rodada acontece. É o tipo de corrupção que só
    aparece meses depois, quando alguém pergunta quando o campeonato começou.
    """
    from datetime import date

    inteira = _snapshot(20, status=FixtureStatus.SCHEDULED)
    season, _ = await catalog_service.ingest_snapshot(
        db_session,
        PROVEDOR,
        "Teste",
        ProviderSnapshot(
            competition=inteira.competition,
            season=ProviderSeason(
                external_id="2026",
                competition_external_id=inteira.competition.external_id,
                year=2026,
                start_date=date(2026, 4, 1),
                end_date=date(2026, 12, 20),
            ),
            teams=inteira.teams,
            fixtures=inteira.fixtures,
        ),
    )
    await db_session.flush()
    assert season.start_date == date(2026, 4, 1)

    # Agora a coleta parcial: sem datas, como o provedor manda quando é parcial.
    parcial = _snapshot(20, status=FixtureStatus.LIVE, home_ft=1, away_ft=0)
    season, _ = await catalog_service.ingest_snapshot(
        db_session,
        PROVEDOR,
        "Teste",
        ProviderSnapshot(
            competition=parcial.competition,
            season=ProviderSeason(
                external_id="2026",
                competition_external_id=parcial.competition.external_id,
                year=2026,
                start_date=None,
                end_date=None,
            ),
            teams=parcial.teams,
            fixtures=parcial.fixtures,
        ),
    )
    await db_session.flush()

    assert season.start_date == date(2026, 4, 1)
    assert season.end_date == date(2026, 12, 20)


async def test_globo_declara_parcial_sem_datas_de_temporada() -> None:
    """O coletor do GE tem que ser honesto sobre o que a coleta parcial sabe."""
    import inspect

    from app.providers.globo import GloboProvider

    fonte = inspect.getsource(GloboProvider.import_rounds)
    assert "parcial=True" in fonte


# ---------------------------------------------------------------------------
# Fonte que só conhece "tem placar" e "não tem"
# ---------------------------------------------------------------------------


async def test_em_campo_nao_volta_a_agendado_com_horario_no_passado(
    db_session: AsyncSession,
) -> None:
    """O CSV das ligas europeias só tem dois estados: com placar ou sem.

    Enquanto o resultado não é publicado — o que leva horas — ele afirma
    ``SCHEDULED`` sobre um jogo que já acabou. Aceitar isso faz o jogo pular
    entre "em campo" e "vai começar" a cada passada, e a tela mostra "vai
    começar" numa partida encerrada. Aconteceu de verdade com a Eredivisie.
    """
    passado = datetime.now(UTC) - timedelta(hours=4)

    def _com_kickoff(status: FixtureStatus) -> ProviderSnapshot:
        base = _snapshot(30, status=status)
        jogo = base.fixtures[0]
        return ProviderSnapshot(
            competition=base.competition,
            season=base.season,
            teams=base.teams,
            fixtures=(
                ProviderFixture(
                    external_id=jogo.external_id,
                    home_team_external_id=jogo.home_team_external_id,
                    away_team_external_id=jogo.away_team_external_id,
                    kickoff_at=passado,
                    status=status,
                    round=jogo.round,
                ),
            ),
        )

    await _ingerir(db_session, _com_kickoff(FixtureStatus.LIVE))
    await _ingerir(db_session, _com_kickoff(FixtureStatus.SCHEDULED))

    assert (await _jogo(db_session, 30)).status is FixtureStatus.LIVE


async def test_remarcacao_de_verdade_volta_a_agendado(db_session: AsyncSession) -> None:
    """Jogo remarcado para o futuro **pode** voltar a ser agendado.

    É o adiamento legítimo, e o sinal que o distingue da desinformação é o
    provedor mandar um horário que ainda não chegou.
    """
    passado = datetime.now(UTC) - timedelta(hours=4)
    futuro = datetime.now(UTC) + timedelta(days=7)

    def _com_kickoff(status: FixtureStatus, kickoff) -> ProviderSnapshot:
        base = _snapshot(31, status=status)
        jogo = base.fixtures[0]
        return ProviderSnapshot(
            competition=base.competition,
            season=base.season,
            teams=base.teams,
            fixtures=(
                ProviderFixture(
                    external_id=jogo.external_id,
                    home_team_external_id=jogo.home_team_external_id,
                    away_team_external_id=jogo.away_team_external_id,
                    kickoff_at=kickoff,
                    status=status,
                    round=jogo.round,
                ),
            ),
        )

    await _ingerir(db_session, _com_kickoff(FixtureStatus.LIVE, passado))
    await _ingerir(db_session, _com_kickoff(FixtureStatus.SCHEDULED, futuro))

    jogo = await _jogo(db_session, 31)
    assert jogo.status is FixtureStatus.SCHEDULED
    assert jogo.kickoff_at > datetime.now(UTC)


async def test_jogo_muda_de_temporada_sem_duplicar(db_session: AsyncSession) -> None:
    """A chave única é `(provider, external_id)` — sem a temporada.

    Se a busca do que já existe filtrar por temporada, um jogo cuja competição
    foi resolvida para outra linha não é encontrado, vira `INSERT`, e a
    restrição única derruba a coleta inteira. Foi assim que o Brasileirão ficou
    oito dias sem placar.
    """
    from sqlalchemy import func, select

    from app.models import Fixture

    base = _snapshot(32, status=FixtureStatus.SCHEDULED)
    await _ingerir(db_session, base)

    # Mesmo jogo, mesma competição, outra temporada.
    outra = ProviderSnapshot(
        competition=base.competition,
        season=ProviderSeason(
            external_id="2027",
            competition_external_id=base.competition.external_id,
            year=2027,
        ),
        teams=base.teams,
        fixtures=base.fixtures,
    )
    await _ingerir(db_session, outra)

    total = await db_session.scalar(
        select(func.count()).select_from(Fixture).where(Fixture.external_id == "jogo32")
    )
    assert total == 1
