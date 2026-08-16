"""Coleta de resultado por competição.

O que está sendo protegido aqui é a economia de requisição e a honestidade da
janela. Uma coleta ao vivo que baixa a temporada inteira a cada dois minutos
seria bloqueada pela fonte no primeiro domingo; uma janela apertada demais
perde o resultado e a rodada não conclui.
"""

from __future__ import annotations

from datetime import UTC, datetime, timedelta

import pytest
from sqlalchemy.ext.asyncio import AsyncSession

from app.models import Competition, FixtureStatus, Season
from app.services import sync as sync_service
from tests.factories import make_fixture, make_round, make_season, make_team

AGORA = datetime(2026, 8, 8, 20, 0, tzinfo=UTC)


async def _competicao(session: AsyncSession, season: Season) -> Competition:
    competition = await session.get(Competition, season.competition_id)
    assert competition is not None
    return competition


async def _jogo(session: AsyncSession, season, *, offset: timedelta, status=None):
    rodada = await make_round(session, season, number=abs(int(offset.total_seconds())) % 900 + 1)
    casa = await make_team(session, season, f"Casa {season.year}-{rodada.number}")
    fora = await make_team(session, season, f"Fora {season.year}-{rodada.number}")
    jogo = await make_fixture(session, season, rodada, casa, fora, kickoff_in=timedelta(days=1))
    jogo.kickoff_at = AGORA + offset
    if status is not None:
        jogo.status = status
    await session.flush()
    return jogo


# ---------------------------------------------------------------------------
# Janela de coleta
# ---------------------------------------------------------------------------


async def test_jogo_em_campo_entra_na_janela(db_session: AsyncSession) -> None:
    season = await make_season(db_session, 6100)
    await _jogo(db_session, season, offset=-timedelta(minutes=30), status=FixtureStatus.LIVE)

    assert len(await sync_service.jogos_na_janela(db_session, now=AGORA)) == 1


async def test_jogo_prestes_a_comecar_entra(db_session: AsyncSession) -> None:
    """Dez minutos de antecedência: a fonte muda o estado antes do apito."""
    season = await make_season(db_session, 6101)
    await _jogo(db_session, season, offset=timedelta(minutes=5))

    assert len(await sync_service.jogos_na_janela(db_session, now=AGORA)) == 1


async def test_jogo_de_daqui_a_uma_hora_fica_de_fora(db_session: AsyncSession) -> None:
    season = await make_season(db_session, 6102)
    await _jogo(db_session, season, offset=timedelta(hours=1))

    assert await sync_service.jogos_na_janela(db_session, now=AGORA) == []


async def test_jogo_de_ontem_fica_de_fora(db_session: AsyncSession) -> None:
    """Três horas cobrem acréscimo e prorrogação; um dia não é jogo em campo."""
    season = await make_season(db_session, 6103)
    await _jogo(db_session, season, offset=-timedelta(hours=26), status=FixtureStatus.LIVE)

    assert await sync_service.jogos_na_janela(db_session, now=AGORA) == []


async def test_jogo_ja_encerrado_nao_gasta_requisicao(db_session: AsyncSession) -> None:
    season = await make_season(db_session, 6104)
    await _jogo(db_session, season, offset=-timedelta(minutes=30), status=FixtureStatus.FINISHED)

    assert await sync_service.jogos_na_janela(db_session, now=AGORA) == []


async def test_rodadas_em_jogo_agrupa_por_temporada(db_session: AsyncSession) -> None:
    """É o que permite coletar uma rodada em vez de trinta e oito."""
    from app.models import Round

    season = await make_season(db_session, 6105)
    jogo = await _jogo(db_session, season, offset=-timedelta(minutes=10), status=FixtureStatus.LIVE)
    rodada = await db_session.get(Round, jogo.round_id)
    assert rodada is not None

    agrupado = await sync_service.rodadas_em_jogo(db_session, [jogo])

    assert agrupado == {season.id: {rodada.number}}


# ---------------------------------------------------------------------------
# Reconstrução do coletor
# ---------------------------------------------------------------------------


async def test_globo_sem_fase_falha_com_instrucao(db_session: AsyncSession) -> None:
    """Sem a fase não há coleta, e a mensagem diz o que fazer."""
    season = await make_season(db_session, 6106)
    competition = await _competicao(db_session, season)
    competition.provider_config = {"table_id": "abc"}

    with pytest.raises(sync_service.SyncError, match="Reimporte"):
        sync_service.provider_para(competition, "globo")


async def test_globo_com_config_completa_monta_o_coletor(db_session: AsyncSession) -> None:
    season = await make_season(db_session, 6107)
    competition = await _competicao(db_session, season)
    competition.provider_config = {
        "table_id": "d1a37fa4-e948-43a6-ba53-ab24ab3a45b1",
        "phase": "fase-unica-campeonato-brasileiro-2026",
        "rounds": 38,
    }

    provider = sync_service.provider_para(competition, "globo")

    assert provider.supports_live is True
    assert provider.supports_partial is True


async def test_provedor_desconhecido_falha(db_session: AsyncSession) -> None:
    season = await make_season(db_session, 6108)
    competition = await _competicao(db_session, season)

    with pytest.raises(sync_service.SyncError, match="não coleta sozinho"):
        sync_service.provider_para(competition, "manual")


async def test_liga_de_virada_pede_o_ano_do_arquivo(db_session: AsyncSession) -> None:
    """A Premier 2026-27 está gravada como 2027, e o arquivo se chama `epl-2026`.

    Pedir 2027 baixaria a temporada seguinte, que ainda não existe — e a coleta
    voltaria vazia todo dia sem ninguém entender por quê.
    """
    season = await make_season(db_session, 2027)
    competition = await _competicao(db_session, season)
    competition.provider_config = {"league": "epl"}

    assert sync_service._ano_do_arquivo(competition, "fixturedownload", season) == 2026


async def test_liga_de_ano_civil_pede_o_proprio_ano(db_session: AsyncSession) -> None:
    season = await make_season(db_session, 2026)
    competition = await _competicao(db_session, season)
    competition.provider_config = {"league": "mls"}

    assert sync_service._ano_do_arquivo(competition, "fixturedownload", season) == 2026


# ---------------------------------------------------------------------------
# Quem entra na coleta ao vivo
# ---------------------------------------------------------------------------


async def test_fonte_que_nao_publica_ao_vivo_nao_e_consultada(
    db_session: AsyncSession, monkeypatch
) -> None:
    """Bater num CSV de temporada inteira a cada dois minutos é reler o mesmo arquivo.

    O teste falha se alguém tornar a coleta ao vivo indiscriminada: aí a
    requisição sairia e o contador subiria.
    """
    season = await make_season(db_session, 6109)
    competition = await _competicao(db_session, season)
    competition.provider_config = {"league": "epl"}
    await _jogo(db_session, season, offset=-timedelta(minutes=20), status=FixtureStatus.LIVE)
    await db_session.flush()

    chamadas: list[str] = []

    async def _nunca(*args, **kwargs):
        chamadas.append("coletou")
        raise AssertionError("fonte sem tempo real não devia ser consultada")

    monkeypatch.setattr(sync_service, "coletar_competicao", _nunca)

    # A temporada de teste nasce com provedor próprio; força o caminho do CSV.
    async def _temporadas(_session, **_kwargs):
        return [(competition, season, "fixturedownload")]

    monkeypatch.setattr(sync_service, "temporadas_ativas", _temporadas)

    resultado = await sync_service.coletar_ao_vivo(db_session, now=AGORA)

    assert chamadas == []
    assert resultado.competicoes == 0


async def test_falha_de_uma_competicao_nao_derruba_as_outras(
    db_session: AsyncSession, monkeypatch
) -> None:
    season = await make_season(db_session, 6110)
    competition = await _competicao(db_session, season)
    competition.provider_config = {
        "table_id": "abc",
        "phase": "fase-teste-2026",
        "rounds": 3,
    }
    await _jogo(db_session, season, offset=-timedelta(minutes=20), status=FixtureStatus.LIVE)
    await db_session.flush()

    from app.providers.base import ProviderError

    async def _explode(*args, **kwargs):
        raise ProviderError("o GE recusou a requisição")

    async def _temporadas(_session, **_kwargs):
        return [(competition, season, "globo")]

    monkeypatch.setattr(sync_service, "coletar_competicao", _explode)
    monkeypatch.setattr(sync_service, "temporadas_ativas", _temporadas)

    resultado = await sync_service.coletar_ao_vivo(db_session, now=AGORA)

    assert resultado.competicoes == 0
    assert len(resultado.falhas) == 1
    assert "recusou" in resultado.falhas[0]


# ---------------------------------------------------------------------------
# Varredura lenta
# ---------------------------------------------------------------------------


async def test_sem_jogo_pendente_nao_consulta_ninguem(
    db_session: AsyncSession, monkeypatch
) -> None:
    """Competição com tudo apurado não gasta requisição."""
    season = await make_season(db_session, 6111)
    await _jogo(db_session, season, offset=-timedelta(days=2), status=FixtureStatus.FINISHED)
    await db_session.flush()

    async def _nunca(*args, **kwargs):
        raise AssertionError("não devia coletar nada")

    monkeypatch.setattr(sync_service, "coletar_competicao", _nunca)

    resultado = await sync_service.coletar_resultados(db_session, now=AGORA)

    assert resultado.competicoes == 0


async def test_jogo_antigo_sem_placar_entra_na_varredura(
    db_session: AsyncSession, monkeypatch
) -> None:
    season = await make_season(db_session, 6112)
    competition = await _competicao(db_session, season)
    await _jogo(db_session, season, offset=-timedelta(days=2), status=FixtureStatus.LIVE)
    await db_session.flush()

    coletadas: list[str] = []

    async def _coleta(_session, comp, _season, _slug, **kwargs):
        coletadas.append(comp.name)
        return 1, []

    async def _temporadas(_session, **_kwargs):
        return [(competition, season, "fixturedownload")]

    monkeypatch.setattr(sync_service, "coletar_competicao", _coleta)
    monkeypatch.setattr(sync_service, "temporadas_ativas", _temporadas)

    resultado = await sync_service.coletar_resultados(db_session, now=AGORA)

    assert coletadas == [competition.name]
    assert resultado.competicoes == 1
