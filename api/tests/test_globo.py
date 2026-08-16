"""Coletor do GE Globo.

A chamada de rede não é testada aqui — o que se testa é a **tradução**, que é
onde o erro sai caro: data no fuso errado coloca o jogo no dia errado, status
mal mapeado apura um jogo que não acabou, e um campo que sumiu importaria meia
rodada em silêncio.

Todos os testes usam um transporte falso do httpx com payloads no formato que o
GE devolve.
"""

from __future__ import annotations

from datetime import UTC, datetime, timedelta
from typing import Any
from zoneinfo import ZoneInfo

import httpx
import pytest

from app.models.enums import FixtureStatus
from app.providers.globo import GloboProvider, GloboScrapeError, SemDataDefinida

BRASILIA = ZoneInfo("America/Sao_Paulo")


#: Instante fixo, para os testes de status não dependerem do relógio.
AGORA = datetime(2026, 7, 31, 12, 0, tzinfo=UTC)


def jogo(
    *,
    mandante: str = "Palmeiras",
    visitante: str = "Corinthians",
    data: str | None = "2026-08-15T16:00",
    hora: str | None = "16:00",
    casa: int | None = None,
    fora: int | None = None,
    comecou: bool | None = None,
    identificador: str = "abc123",
) -> dict[str, Any]:
    """Um jogo no formato que o GE devolve de verdade.

    Conferido contra a resposta real da rodada 4 de 2026: não há campo de
    status, ``data_realizacao`` é ISO com hora, e ``sede`` pode vir nula.
    """
    return {
        "id": identificador,
        "data_realizacao": data,
        "hora_realizacao": hora,
        "jogo_ja_comecou": comecou,
        "placar_oficial_mandante": casa,
        "placar_oficial_visitante": fora,
        "placar_penaltis_mandante": None,
        "placar_penaltis_visitante": None,
        "sede": {"nome_popular": "Allianz Parque"},
        "transmissao": None,
        "equipes": {
            # O id vem do nome para que times diferentes não colidam no
            # catálogo — como colidiriam se o helper fixasse o mesmo número.
            "mandante": _equipe(mandante),
            "visitante": _equipe(visitante),
        },
    }


def _equipe(nome: str) -> dict[str, Any]:
    return {
        "id": abs(hash(nome)) % 100_000,
        "nome_popular": nome,
        "sigla": nome[:3].upper(),
        "escudo": f"https://s.sde.globo.com/media/organizations/{nome.lower()}.svg",
    }


def provider_com(rodadas: dict[int, Any], *, status: int = 200) -> GloboProvider:
    """Provedor apontando para um GE falso."""

    def responder(request: httpx.Request) -> httpx.Response:
        numero = int(request.url.path.rstrip("/").split("/")[-2])
        conteudo = rodadas.get(numero, [])
        if status != 200:
            return httpx.Response(status, json={})
        if isinstance(conteudo, str):
            return httpx.Response(200, text=conteudo)
        return httpx.Response(200, json=conteudo)

    client = httpx.AsyncClient(transport=httpx.MockTransport(responder))
    return GloboProvider(client=client, delay_seconds=0)


# ---------------------------------------------------------------------------
# Fuso horário
# ---------------------------------------------------------------------------


def test_iso_com_hora_e_lido_como_brasilia_e_gravado_em_utc() -> None:
    """Formato real do GE: ``2026-01-28T19:00``, local, sem fuso.

    Ler isso como UTC atrasaria a plataforma inteira em três horas.
    """
    fixture = GloboProvider()._fixture(jogo(data="2026-01-28T19:00"), 2026, 1, now=AGORA)

    assert fixture.kickoff_at == datetime(2026, 1, 28, 22, 0, tzinfo=UTC)
    assert fixture.kickoff_at.astimezone(BRASILIA).hour == 19


def test_data_sem_hora_usa_hora_realizacao() -> None:
    """Formato que o GE já usou e pode voltar a usar."""
    fixture = GloboProvider()._fixture(jogo(data="2026-08-15", hora="16:00:00"), 2026, 1, now=AGORA)

    assert fixture.kickoff_at.astimezone(BRASILIA).hour == 16


def test_jogo_de_sabado_a_noite_vira_domingo_em_utc() -> None:
    """A armadilha de sempre: 21h30 de sábado é meia-noite e meia de domingo."""
    fixture = GloboProvider()._fixture(jogo(data="2026-08-15T21:30"), 2026, 1, now=AGORA)

    assert fixture.kickoff_at == datetime(2026, 8, 16, 0, 30, tzinfo=UTC)
    assert fixture.kickoff_at.astimezone(BRASILIA).day == 15


def test_jogo_sem_hora_definida_cai_as_16h() -> None:
    """Sem hora, meia-noite jogaria a partida para o dia anterior na tela."""
    fixture = GloboProvider()._fixture(jogo(data="2026-08-15", hora=None), 2026, 1, now=AGORA)

    assert fixture.kickoff_at.astimezone(BRASILIA).hour == 16


def test_hora_malformada_nao_derruba_a_importacao() -> None:
    fixture = GloboProvider()._fixture(
        jogo(data="2026-08-15", hora="a definir"), 2026, 1, now=AGORA
    )

    assert fixture.kickoff_at.astimezone(BRASILIA).hour == 16


def test_jogo_sem_data_marcada_e_sinalizado_e_nao_inventado() -> None:
    """Acontece de verdade: 5 dos 380 jogos de 2026 estavam assim.

    Chutar uma data faria o palpite fechar na hora errada. O jogo é pulado e
    volta na próxima coleta, quando a CBF marcar o horário.
    """
    with pytest.raises(SemDataDefinida):
        GloboProvider()._fixture(jogo(data=None, hora=None), 2026, 4, now=AGORA)


def test_data_em_formato_estranho_falha_alto() -> None:
    with pytest.raises(GloboScrapeError, match="não é ISO"):
        GloboProvider()._fixture(jogo(data="15/08/2026"), 2026, 1, now=AGORA)


# ---------------------------------------------------------------------------
# Times e placares
# ---------------------------------------------------------------------------


def test_times_trazem_escudo_oficial_e_sigla() -> None:
    provider = GloboProvider()
    time = provider._team(jogo()["equipes"]["mandante"], "mandante", 1)

    assert time.name == "Palmeiras"
    assert time.slug == "palmeiras"
    assert time.short_name == "PAL"
    assert time.crest_url is not None
    # O GE serve os escudos oficiais do CDN dele.
    assert time.crest_url.startswith("https://s.sde.globo.com/")


def test_time_ausente_falha_alto() -> None:
    with pytest.raises(GloboScrapeError, match="sem o time visitante"):
        GloboProvider()._team(None, "visitante", 7)


def test_time_sem_nome_falha_alto() -> None:
    with pytest.raises(GloboScrapeError, match="sem nome"):
        GloboProvider()._team({"id": 1}, "mandante", 3)


def test_placar_e_transportado() -> None:
    fixture = GloboProvider()._fixture(
        jogo(data="2026-05-10T16:00", casa=3, fora=1), 2026, 5, now=AGORA
    )

    assert (fixture.home_ft, fixture.away_ft) == (3, 1)
    assert fixture.status is FixtureStatus.FINISHED


def test_jogo_futuro_vem_sem_placar() -> None:
    fixture = GloboProvider()._fixture(jogo(data="2026-11-20T16:00"), 2026, 30, now=AGORA)

    assert fixture.home_ft is None
    assert fixture.status is FixtureStatus.SCHEDULED


def test_estadio_e_transportado() -> None:
    assert GloboProvider()._fixture(jogo(), 2026, 1, now=AGORA).venue == "Allianz Parque"


def test_sede_nula_nao_quebra() -> None:
    """O GE manda ``sede: null`` em jogo sem estádio definido."""
    sem_sede = jogo()
    sem_sede["sede"] = None

    assert GloboProvider()._fixture(sem_sede, 2026, 4, now=AGORA).venue is None


# ---------------------------------------------------------------------------
# Status
# ---------------------------------------------------------------------------


@pytest.mark.parametrize(
    ("cru", "esperado"),
    [
        ("pre-jogo", FixtureStatus.SCHEDULED),
        ("em-andamento", FixtureStatus.LIVE),
        ("intervalo", FixtureStatus.HT),
        ("encerrado", FixtureStatus.FINISHED),
        ("adiado", FixtureStatus.POSTPONED),
        ("cancelado", FixtureStatus.CANCELLED),
        ("suspenso", FixtureStatus.SUSPENDED),
    ],
)
def test_traducao_de_status_se_o_ge_voltar_a_mandar(cru: str, esperado: FixtureStatus) -> None:
    """Hoje o GE não manda status nesta rota, mas a tradução fica pronta."""
    kickoff = datetime(2026, 8, 15, 19, tzinfo=UTC)

    assert GloboProvider()._status({"jogo_status": cru}, kickoff, AGORA) is esperado


def test_sem_campo_de_status_o_placar_decide() -> None:
    """Caso real: a rodada inteira vem sem `jogo_status`."""
    passado = datetime(2026, 5, 10, 19, tzinfo=UTC)
    status = GloboProvider()._status(
        {"placar_oficial_mandante": 2, "placar_oficial_visitante": 0}, passado, AGORA
    )

    assert status is FixtureStatus.FINISHED


def test_jogo_futuro_sem_status_fica_agendado() -> None:
    futuro = datetime(2026, 11, 20, 19, tzinfo=UTC)

    assert GloboProvider()._status({}, futuro, AGORA) is FixtureStatus.SCHEDULED


def test_jogo_em_andamento_nao_e_marcado_como_encerrado() -> None:
    """Marcar como encerrado no intervalo apuraria a rodada e revelaria os
    palpites antes da hora."""
    comecou_ha_uma_hora = AGORA - timedelta(hours=1)
    status = GloboProvider()._status(
        {"jogo_ja_comecou": True, "placar_oficial_mandante": 1, "placar_oficial_visitante": 0},
        comecou_ha_uma_hora,
        AGORA,
    )

    assert status is FixtureStatus.LIVE


def test_depois_de_tres_horas_o_jogo_acabou() -> None:
    ha_quatro_horas = AGORA - timedelta(hours=4)
    status = GloboProvider()._status(
        {"jogo_ja_comecou": True, "placar_oficial_mandante": 1, "placar_oficial_visitante": 0},
        ha_quatro_horas,
        AGORA,
    )

    assert status is FixtureStatus.FINISHED


def test_status_desconhecido_cai_na_heuristica() -> None:
    passado = datetime(2026, 5, 10, 19, tzinfo=UTC)
    status = GloboProvider()._status(
        {
            "jogo_status": "status-que-nao-existia",
            "placar_oficial_mandante": 2,
            "placar_oficial_visitante": 0,
        },
        passado,
        AGORA,
    )

    assert status is FixtureStatus.FINISHED


# ---------------------------------------------------------------------------
# Identidade externa
# ---------------------------------------------------------------------------


def test_id_do_ge_vira_external_id() -> None:
    fixture = GloboProvider()._fixture(jogo(identificador="xyz789"), 2026, 1, now=AGORA)

    assert fixture.external_id == "xyz789"


def test_sem_id_monta_chave_estavel_pelo_confronto() -> None:
    """O upsert depende do external_id para não duplicar na próxima coleta."""
    sem_id = jogo()
    del sem_id["id"]

    primeira = GloboProvider()._fixture(dict(sem_id), 2026, 12, now=AGORA)
    segunda = GloboProvider()._fixture(dict(sem_id), 2026, 12, now=AGORA)

    assert primeira.external_id == segunda.external_id
    assert primeira.external_id == "ge-2026-r12-palmeiras-corinthians"


# ---------------------------------------------------------------------------
# Coleta
# ---------------------------------------------------------------------------


async def test_coleta_percorre_as_rodadas_e_junta_os_times() -> None:
    provider = provider_com(
        {
            1: [jogo(mandante="Palmeiras", visitante="Corinthians", identificador="j1")],
            2: [jogo(mandante="Corinthians", visitante="Palmeiras", identificador="j2")],
        }
    )

    snapshot = await provider.import_season("brasileirao-serie-a", 2026)
    await provider.aclose()

    assert len(snapshot.fixtures) == 2
    assert len(snapshot.teams) == 2
    assert {time.name for time in snapshot.teams} == {"Palmeiras", "Corinthians"}


async def test_rodada_ainda_nao_publicada_nao_quebra() -> None:
    """Em temporada em andamento, as últimas rodadas ainda não saíram."""
    provider = provider_com({1: [jogo(identificador="j1")], 2: []})

    snapshot = await provider.import_season("brasileirao-serie-a", 2026)
    await provider.aclose()

    assert len(snapshot.fixtures) == 1


async def test_jogo_sem_data_e_pulado_e_reportado() -> None:
    """Os outros jogos da rodada entram normalmente."""
    provider = provider_com(
        {
            1: [
                jogo(identificador="ok", mandante="Palmeiras", visitante="Santos"),
                jogo(
                    identificador="sem-data",
                    data=None,
                    hora=None,
                    mandante="Flamengo",
                    visitante="Mirassol",
                ),
            ]
        }
    )

    snapshot = await provider.import_season("brasileirao-serie-a", 2026)
    await provider.aclose()

    assert len(snapshot.fixtures) == 1
    assert snapshot.fixtures[0].external_id == "ok"
    # Os times do jogo pulado continuam no catálogo — eles existem.
    assert {time.name for time in snapshot.teams} >= {"Flamengo", "Mirassol"}
    assert provider.last_collection is not None
    assert provider.last_collection.undated == ["R1: Flamengo x Mirassol"]


async def test_temporada_sem_nenhum_jogo_falha_alto() -> None:
    """Silêncio total é sinal de endpoint mudado, não de campeonato vazio."""
    provider = provider_com({})

    with pytest.raises(GloboScrapeError, match="nenhum jogo encontrado"):
        await provider.import_season("brasileirao-serie-a", 2026)
    await provider.aclose()


async def test_bloqueio_do_ge_e_reportado_com_clareza() -> None:
    provider = provider_com({1: []}, status=403)

    with pytest.raises(GloboScrapeError, match="recusou a requisição"):
        await provider.import_season("brasileirao-serie-a", 2026)
    await provider.aclose()


async def test_resposta_que_nao_e_json_falha_alto() -> None:
    provider = provider_com({1: "<html>manutenção</html>"})

    with pytest.raises(GloboScrapeError, match="não veio em JSON"):
        await provider.import_season("brasileirao-serie-a", 2026)
    await provider.aclose()


async def test_json_no_formato_errado_falha_alto() -> None:
    def responder(request: httpx.Request) -> httpx.Response:
        return httpx.Response(200, json={"jogos": []})

    provider = GloboProvider(
        client=httpx.AsyncClient(transport=httpx.MockTransport(responder)), delay_seconds=0
    )

    with pytest.raises(GloboScrapeError, match="esperava uma lista"):
        await provider.import_season("brasileirao-serie-a", 2026)
    await provider.aclose()


async def test_snapshot_traz_o_intervalo_da_temporada() -> None:
    provider = provider_com(
        {
            1: [jogo(data="2026-04-11T16:00", identificador="j1")],
            38: [jogo(data="2026-12-06T16:00", identificador="j2")],
        }
    )

    snapshot = await provider.import_season("brasileirao-serie-a", 2026)
    await provider.aclose()

    assert snapshot.season.start_date is not None
    assert snapshot.season.start_date.month == 4
    assert snapshot.season.end_date is not None
    assert snapshot.season.end_date.month == 12


def test_url_da_rodada() -> None:
    url = GloboProvider()._round_url(2026, 7)

    assert "fase-unica-campeonato-brasileiro-2026" in url
    assert url.endswith("/rodada/7/jogos/")
