"""Testes do coletor de calendário em CSV.

O que importa aqui é o que já deu errado antes em importação de calendário:
fuso trocado, jogo sem data virando data inventada, e placar transformando em
`FINISHED` um jogo que ainda nem começou — o que travaria o palpite antes da
hora.
"""

from __future__ import annotations

from datetime import UTC, datetime

import httpx
import pytest

from app.models.enums import FixtureStatus
from app.providers.fixturedownload import FixtureDownloadError, FixtureDownloadProvider

CABECALHO = "Match Number,Round Number,Date,Location,Home Team,Away Team,Result"

CSV_OK = f"""{CABECALHO}
1,1,21/08/2026 19:00,Emirates Stadium,Arsenal,Coventry,
2,1,22/08/2026 11:30,MKM Stadium,Hull,Man Utd,
3,2,29/08/2026 14:00,Anfield,Liverpool,Arsenal,2 - 1
"""


def provider_com(conteudo: str, *, status_code: int = 200) -> FixtureDownloadProvider:
    def responder(request: httpx.Request) -> httpx.Response:
        if status_code != 200:
            return httpx.Response(status_code, text="")
        return httpx.Response(200, text=conteudo)

    return FixtureDownloadProvider(
        client=httpx.AsyncClient(transport=httpx.MockTransport(responder))
    )


# ---------------------------------------------------------------------------
# Fuso
# ---------------------------------------------------------------------------


async def test_data_do_arquivo_utc_e_gravada_em_utc() -> None:
    """O arquivo é ``-UTC.csv``: ler como Brasília atrasaria tudo em 3 horas."""
    provider = provider_com(CSV_OK)
    jogos = await provider.list_fixtures("epl", 2026)

    primeiro = min(jogos, key=lambda jogo: jogo.kickoff_at)
    assert primeiro.kickoff_at == datetime(2026, 8, 21, 19, 0, tzinfo=UTC)


async def test_todo_kickoff_tem_fuso() -> None:
    provider = provider_com(CSV_OK)
    for jogo in await provider.list_fixtures("epl", 2026):
        assert jogo.kickoff_at.tzinfo is not None


# ---------------------------------------------------------------------------
# Status
# ---------------------------------------------------------------------------


async def test_placar_no_passado_vira_encerrado() -> None:
    csv = f"{CABECALHO}\n1,1,01/01/2020 15:00,Anfield,Liverpool,Arsenal,2 - 1\n"
    jogos = await provider_com(csv).list_fixtures("epl", 2026)

    assert jogos[0].status is FixtureStatus.FINISHED
    assert (jogos[0].home_ft, jogos[0].away_ft) == (2, 1)


async def test_placar_em_jogo_futuro_nao_encerra_o_jogo() -> None:
    """Data errada na fonte não pode travar o palpite antes do apito.

    Foi o mesmo defeito que já apareceu na trava de palpite: `FINISHED` com
    kickoff no futuro fecha a rodada cedo e ainda esconde o palpite de todos.
    """
    csv = f"{CABECALHO}\n1,1,01/01/2099 15:00,Anfield,Liverpool,Arsenal,2 - 1\n"
    jogos = await provider_com(csv).list_fixtures("epl", 2026)

    assert jogos[0].status is FixtureStatus.SCHEDULED


async def test_placar_ilegivel_nao_derruba_a_importacao() -> None:
    csv = f"{CABECALHO}\n1,1,01/01/2020 15:00,Anfield,Liverpool,Arsenal,W.O.\n"
    jogos = await provider_com(csv).list_fixtures("epl", 2026)

    assert len(jogos) == 1
    assert jogos[0].home_ft is None
    assert jogos[0].status is FixtureStatus.SCHEDULED


# ---------------------------------------------------------------------------
# Jogo sem data
# ---------------------------------------------------------------------------


async def test_jogo_sem_data_e_pulado_e_reportado() -> None:
    """Sem horário não dá para travar o palpite. Pular é melhor que inventar."""
    csv = (
        f"{CABECALHO}\n"
        "1,1,,Anfield,Liverpool,Arsenal,\n"
        "2,1,21/08/2026 19:00,Emirates,Arsenal,Hull,\n"
    )
    provider = provider_com(csv)
    jogos = await provider.list_fixtures("epl", 2026)

    assert len(jogos) == 1
    assert provider.ultimas_sem_data == ["Liverpool x Arsenal"]


# ---------------------------------------------------------------------------
# Falhas de formato — alto e específico, nunca meia tabela
# ---------------------------------------------------------------------------


async def test_coluna_faltando_falha_com_o_nome_da_coluna() -> None:
    csv = "Match Number,Date,Home Team,Away Team\n1,21/08/2026 19:00,Arsenal,Hull\n"
    with pytest.raises(FixtureDownloadError, match="Round Number"):
        await provider_com(csv).list_fixtures("epl", 2026)


async def test_data_em_formato_estranho_falha() -> None:
    csv = f"{CABECALHO}\n1,1,2026-08-21T19:00,Emirates,Arsenal,Hull,\n"
    with pytest.raises(FixtureDownloadError, match="dd/mm/aaaa"):
        await provider_com(csv).list_fixtures("epl", 2026)


async def test_temporada_inexistente_explica_a_convencao_de_ano() -> None:
    with pytest.raises(FixtureDownloadError, match="2026 é a 2026-27"):
        await provider_com("", status_code=404).list_fixtures("epl", 2030)


# ---------------------------------------------------------------------------
# Identidade e ano
# ---------------------------------------------------------------------------


async def test_external_id_e_estavel_entre_coletas() -> None:
    """Reimportar tem que cair na mesma linha, não criar jogo novo."""
    primeira = await provider_com(CSV_OK).list_fixtures("epl", 2026)
    segunda = await provider_com(CSV_OK).list_fixtures("epl", 2026)

    assert [jogo.external_id for jogo in primeira] == [jogo.external_id for jogo in segunda]
    assert len({jogo.external_id for jogo in primeira}) == len(primeira)


async def test_liga_de_virada_e_gravada_com_o_ano_em_que_termina() -> None:
    """``epl-2026`` é a temporada 2026-27, e o banco a chama de 2027.

    Sem isso ela entraria como 2026 e colidiria com a 2025-26, que já está no
    banco com o mesmo nome de competição.
    """
    snapshot = await provider_com(CSV_OK).import_season("epl", 2026)
    assert snapshot.season.year == 2027


async def test_liga_de_ano_civil_mantem_o_proprio_ano() -> None:
    snapshot = await provider_com(CSV_OK).import_season("mls", 2026)
    assert snapshot.season.year == 2026


async def test_time_herda_o_pais_da_liga() -> None:
    times = await provider_com(CSV_OK).list_teams("epl", 2026)
    assert {time.country for time in times} == {"Inglaterra"}


# ---------------------------------------------------------------------------
# Limite do identificador externo
# ---------------------------------------------------------------------------


async def test_nome_longo_nao_estoura_o_limite_da_coluna() -> None:
    """``fixtures.external_id`` é varchar(64).

    Clube inglês de nome comprido em duas pontas passava de 64 e derrubava a
    importação no meio, com metade da temporada já gravada.
    """
    csv = (
        f"{CABECALHO}\n"
        "1,1,21/08/2026 19:00,Estádio,"
        "Wolverhampton Wanderers Football Club,"
        "Brighton and Hove Albion Football Club,\n"
    )
    jogos = await provider_com(csv).list_fixtures("scottish-premiership", 2026)

    assert len(jogos[0].external_id) <= 64


async def test_identificador_cortado_continua_unico() -> None:
    """Cortar não pode fundir dois jogos diferentes na mesma linha."""
    csv = (
        f"{CABECALHO}\n"
        "1,1,21/08/2026 19:00,E,"
        "Wolverhampton Wanderers Football Club,Brighton and Hove Albion FC,\n"
        "2,1,22/08/2026 19:00,E,"
        "Wolverhampton Wanderers Football Club,Brighton and Hove Albion United,\n"
    )
    jogos = await provider_com(csv).list_fixtures("scottish-premiership", 2026)

    assert len({jogo.external_id for jogo in jogos}) == 2


# ---------------------------------------------------------------------------
# Fases eliminatórias
#
# A Champions traz 45 dos seus 189 jogos com `Round Number` NÃO numérico —
# `R16 Game 1`, `SF Game 2`, `Final`. O `int()` estourava, a rodada virava
# None, e jogo sem rodada entra no banco com `round_id` nulo: existe, mas não
# aparece em rodada nenhuma para montar bolão. Some justamente o mata-mata,
# que é o que as pessoas mais querem palpitar.


CSV_MATA_MATA = f"""{CABECALHO}
1,8,28/01/2027 20:00,Emirates Stadium,Arsenal,Inter,1 - 0
2,Play-off Game 1,17/02/2027 20:00,San Siro,Milan,Porto,2 - 1
3,R16 Game 2,10/03/2027 20:00,Camp Nou,Barcelona,Bayern,
4,QF Game 1,07/04/2027 20:00,Anfield,Liverpool,Real Madrid,
5,SF Game 2,05/05/2027 20:00,Allianz Arena,Bayern,Arsenal,
6,Final,29/05/2027 19:00,Wembley,Arsenal,Barcelona,
"""


@pytest.mark.anyio
async def test_fase_eliminatoria_vira_rodada_com_nome_e_ordem() -> None:
    provider = provider_com(CSV_MATA_MATA)
    try:
        jogos = await provider.list_fixtures("champions-league", 2026)
    finally:
        await provider.aclose()

    por_nome = {jogo.round.name: jogo.round for jogo in jogos if jogo.round is not None}

    # Nenhum jogo pode ficar sem rodada: é isso que o tornaria invisível.
    assert all(jogo.round is not None for jogo in jogos)
    assert len(jogos) == 6

    assert por_nome["Rodada 8"].number == 8
    assert por_nome["Rodada 8"].is_knockout is False

    # A ordem continua a contagem da fase de liga, para a tela ordenar sozinha.
    assert por_nome["Play-off (ida)"].number == 9
    assert por_nome["Oitavas (volta)"].number == 12
    assert por_nome["Quartas (ida)"].number == 13
    assert por_nome["Semifinal (volta)"].number == 16
    assert por_nome["Final"].number == 17

    assert all(
        por_nome[nome].is_knockout
        for nome in ("Play-off (ida)", "Oitavas (volta)", "Quartas (ida)", "Final")
    )


@pytest.mark.anyio
async def test_rotulo_de_fase_desconhecido_ainda_vira_rodada() -> None:
    """Formato novo não pode fazer jogo sumir.

    Se a UEFA inventar uma fase, o nome cru vira rodada — feio na tela, mas
    presente. Sumir em silêncio é a única saída que não se pode escolher.
    """
    csv_novo = f"""{CABECALHO}
1,Repescagem Game 1,17/02/2027 20:00,San Siro,Milan,Porto,
"""
    provider = provider_com(csv_novo)
    try:
        jogos = await provider.list_fixtures("champions-league", 2026)
    finally:
        await provider.aclose()

    assert len(jogos) == 1
    rodada = jogos[0].round
    assert rodada is not None
    assert rodada.name == "Repescagem Game 1"
    assert rodada.number is None
