"""Testes do coletor de calendário de copa pela ESPN.

O que importa aqui é o que já deu errado, ou daria, numa importação de
mata-mata: janela truncada engolindo metade da temporada, pênalti de desempate
somado ao placar do tempo normal, e fase virando `None` — que faz o jogo entrar
no banco sem rodada, isto é, invisível para montar bolão.
"""

from __future__ import annotations

import json

import httpx
import pytest

from app.models.enums import FixtureStatus
from app.providers.espn import EspnCalendario, EspnError


def _evento(
    identificador: str,
    *,
    data: str,
    casa: tuple[str, str],
    fora: tuple[str, str],
    fase: str,
    estado: str = "STATUS_SCHEDULED",
    placar: tuple[str, str] | None = None,
    penaltis: tuple[str, str] | None = None,
) -> dict:
    def competidor(papel: str, time: tuple[str, str], indice: int) -> dict:
        dados: dict = {
            "homeAway": papel,
            "team": {"id": time[0], "displayName": time[1]},
        }
        if placar is not None:
            dados["score"] = placar[indice]
        if penaltis is not None:
            dados["shootoutScore"] = penaltis[indice]
        return dados

    return {
        "id": identificador,
        "date": data,
        "season": {"year": 2026, "slug": fase},
        "competitions": [
            {
                "status": {"type": {"name": estado, "state": "pre", "completed": False}},
                "venue": {"fullName": "Maracanã"},
                "competitors": [competidor("home", casa, 0), competidor("away", fora, 1)],
            }
        ],
    }


#: Um jogo em cada mês diferente, de propósito: a coleta é mês a mês, e um
#: coletor que só olhasse a primeira janela passaria em qualquer teste que
#: pusesse tudo no mesmo mês. Foi exatamente essa a falha real — pedir o ano
#: inteiro devolvia jogos até abril enquanto as oitavas de agosto existiam.
POR_MES: dict[int, list[dict]] = {
    2: [
        _evento(
            "1",
            data="2026-02-17T23:00Z",
            casa=("100", "Sampaio Corrêa"),
            fora=("200", "Ferroviária"),
            fase="first-round",
            estado="STATUS_FINAL_PEN",
            placar=("1", "1"),
            penaltis=("1", "3"),
        )
    ],
    8: [
        _evento(
            "2",
            data="2026-08-01T20:30Z",
            casa=("300", "Vasco da Gama"),
            fora=("400", "Fluminense"),
            fase="round-of-16",
            estado="STATUS_FULL_TIME",
            placar=("2", "0"),
        )
    ],
    9: [
        _evento(
            "3",
            data="2026-09-02T00:00Z",
            casa=("500", "Atlético-MG"),
            fora=("600", "Cruzeiro"),
            fase="quarterfinals",
        )
    ],
    11: [
        _evento(
            "4",
            data="2026-11-10T22:00Z",
            casa=("300", "Vasco da Gama"),
            fora=("500", "Atlético-MG"),
            fase="fase-inventada",
        )
    ],
}


def provider_falso(por_mes: dict[int, list[dict]] | None = None) -> EspnCalendario:
    dados = POR_MES if por_mes is None else por_mes

    def responder(request: httpx.Request) -> httpx.Response:
        # `dates=YYYYMMDD-YYYYMMDD`. A janela real começa três dias antes do
        # mês; o mês pedido é o do FIM menos um, e é mais simples devolver tudo
        # que cai dentro do intervalo — como a ESPN faz.
        janela = request.url.params.get("dates", "")
        de, _, ate = janela.partition("-")
        eventos = []
        for lista in dados.values():
            for evento in lista:
                dia = evento["date"][:10].replace("-", "")
                if de <= dia <= ate:
                    eventos.append(evento)
        return httpx.Response(200, text=json.dumps({"events": eventos}))

    return EspnCalendario(client=httpx.AsyncClient(transport=httpx.MockTransport(responder)))


@pytest.mark.anyio
async def test_coleta_junta_os_doze_meses() -> None:
    provider = provider_falso()
    try:
        snapshot = await provider.import_season("bra.copa_do_brazil", 2026)
    finally:
        await provider.aclose()

    assert len(snapshot.fixtures) == 4
    assert len(snapshot.teams) == 6
    # Ordenados por horário: a tela e a montagem de rodada contam com isso.
    assert [j.kickoff_at.isoformat() for j in snapshot.fixtures] == sorted(
        j.kickoff_at.isoformat() for j in snapshot.fixtures
    )


@pytest.mark.anyio
async def test_penalti_nao_entra_no_placar_do_tempo_normal() -> None:
    """Pênalti de desempate não pontua. Somá-lo ao placar mudaria o resultado
    de um jogo que terminou empatado — e a apuração inteira junto."""
    provider = provider_falso()
    try:
        snapshot = await provider.import_season("bra.copa_do_brazil", 2026)
    finally:
        await provider.aclose()

    jogo = next(j for j in snapshot.fixtures if j.external_id.endswith("-1"))
    assert (jogo.home_ft, jogo.away_ft) == (1, 1)
    assert (jogo.home_pen, jogo.away_pen) == (1, 3)


@pytest.mark.anyio
async def test_toda_fase_vira_rodada_inclusive_a_desconhecida() -> None:
    provider = provider_falso()
    try:
        snapshot = await provider.import_season("bra.copa_do_brazil", 2026)
    finally:
        await provider.aclose()

    assert all(jogo.round is not None for jogo in snapshot.fixtures)

    por_id = {jogo.external_id[-1]: jogo for jogo in snapshot.fixtures}
    assert por_id["1"].round is not None
    assert por_id["1"].round.name == "Primeira fase"
    assert por_id["1"].round.is_knockout is False
    assert por_id["2"].round is not None
    assert por_id["2"].round.name == "Oitavas de final"
    assert por_id["2"].round.is_knockout is True

    # Fase que a ESPN inventar não pode fazer o jogo sumir.
    desconhecida = por_id["4"].round
    assert desconhecida is not None
    assert desconhecida.number is None
    assert desconhecida.name == "Fase inventada"


@pytest.mark.anyio
async def test_jogo_futuro_mantem_horario_e_fica_agendado() -> None:
    provider = provider_falso()
    try:
        snapshot = await provider.import_season("bra.copa_do_brazil", 2026)
    finally:
        await provider.aclose()

    futuro = next(j for j in snapshot.fixtures if j.external_id.endswith("-3"))
    assert futuro.status is FixtureStatus.SCHEDULED
    assert futuro.kickoff_at.isoformat() == "2026-09-02T00:00:00+00:00"
    assert futuro.kickoff_at.tzinfo is not None


@pytest.mark.anyio
async def test_temporada_vazia_explica_em_vez_de_gravar_nada() -> None:
    """Copa não sorteada devolve zero jogo. Gravar uma temporada vazia deixaria
    um campeonato fantasma na tela; a mensagem diz o que houve."""
    provider = provider_falso({})
    try:
        with pytest.raises(EspnError, match="não tem nenhum jogo"):
            await provider.import_season("bra.copa_do_brazil", 2026)
    finally:
        await provider.aclose()


@pytest.mark.anyio
async def test_liga_desconhecida_diz_qual_e_o_codigo_certo() -> None:
    """400 da ESPN quase sempre é o código da liga — e o da Copa do Brasil tem
    uma troca de letra que ninguém adivinha."""

    def responder(request: httpx.Request) -> httpx.Response:
        return httpx.Response(400, text="")

    provider = EspnCalendario(client=httpx.AsyncClient(transport=httpx.MockTransport(responder)))
    try:
        with pytest.raises(EspnError, match=r"bra\.copa_do_brazil"):
            await provider.import_season("bra.copa_do_brasil", 2026)
    finally:
        await provider.aclose()


# ---------------------------------------------------------------------------
# Torneio partido em duas ligas
#
# A Champions começa em julho, pelas eliminatórias, que vivem em
# `uefa.champions_qual`. Quem olha só `uefa.champions` vê zero jogo até o
# sorteio da fase de liga, no fim de agosto — e conclui que a temporada não
# existe, com noventa jogos coletáveis do outro lado.


QUALIFICACAO = [
    _evento(
        "q1",
        data="2026-07-07T19:00Z",
        casa=("10", "Shelbourne"),
        fora=("11", "Linfield"),
        fase="first-round",
    ),
    _evento(
        "q2",
        data="2026-08-18T19:00Z",
        casa=("12", "Fenerbahce"),
        fora=("13", "Lyon"),
        fase="playoff-round",
    ),
]

FASE_DE_LIGA = [
    _evento(
        "p1",
        data="2026-09-15T19:00Z",
        casa=("14", "Real Madrid"),
        fora=("15", "Arsenal"),
        fase="league-phase",
    ),
    _evento(
        "p2",
        data="2027-05-29T19:00Z",
        casa=("14", "Real Madrid"),
        fora=("16", "Bayern"),
        fase="final",
    ),
]


def provider_de_duas_ligas() -> EspnCalendario:
    por_liga = {
        "uefa.champions_qual": QUALIFICACAO,
        "uefa.champions": FASE_DE_LIGA,
    }

    def responder(request: httpx.Request) -> httpx.Response:
        liga = request.url.path.split("/")[-2]
        janela = request.url.params.get("dates", "")
        de, _, ate = janela.partition("-")
        eventos = [
            evento
            for evento in por_liga.get(liga, [])
            if de <= evento["date"][:10].replace("-", "") <= ate
        ]
        return httpx.Response(200, text=json.dumps({"events": eventos}))

    return EspnCalendario(client=httpx.AsyncClient(transport=httpx.MockTransport(responder)))


@pytest.mark.anyio
async def test_junta_qualificacao_e_fase_de_liga_num_torneio_so() -> None:
    provider = provider_de_duas_ligas()
    try:
        snapshot = await provider.import_season(
            ("uefa.champions_qual", "uefa.champions"),
            2027,
            virada=True,
            identidade="champions-league",
        )
    finally:
        await provider.aclose()

    assert len(snapshot.fixtures) == 4

    # A identidade vem de fora: derivá-la da primeira liga faria reordenar a
    # lista criar uma competição paralela no import seguinte.
    assert snapshot.competition.external_id == "champions-league"
    assert snapshot.season.competition_external_id == "champions-league"
    assert all(jogo.external_id.startswith("champions-league-") for jogo in snapshot.fixtures)


@pytest.mark.anyio
async def test_janela_de_virada_alcanca_julho_do_ano_anterior() -> None:
    """A temporada 2026-27 é gravada como 2027 e começa em julho de 2026. Uma
    janela de ano civil perderia a eliminatória inteira."""
    provider = provider_de_duas_ligas()
    try:
        snapshot = await provider.import_season(
            ("uefa.champions_qual", "uefa.champions"), 2027, virada=True
        )
    finally:
        await provider.aclose()

    datas = sorted(jogo.kickoff_at.isoformat()[:10] for jogo in snapshot.fixtures)
    assert datas[0] == "2026-07-07"
    assert datas[-1] == "2027-05-29"


@pytest.mark.anyio
async def test_eliminatoria_nao_se_confunde_com_primeira_fase_de_copa() -> None:
    """`first-round` quer dizer coisas diferentes em ligas diferentes: na Copa
    do Brasil é a primeira fase do torneio; na qualificação da Champions é a
    primeira eliminatória, que acontece antes de tudo."""
    provider = provider_de_duas_ligas()
    try:
        snapshot = await provider.import_season(
            ("uefa.champions_qual", "uefa.champions"), 2027, virada=True
        )
    finally:
        await provider.aclose()

    por_nome = {jogo.round.name: jogo.round for jogo in snapshot.fixtures if jogo.round is not None}
    assert "1ª eliminatória" in por_nome
    assert "Primeira fase" not in por_nome
    assert por_nome["Play-off de acesso"].number == 8

    # A ordem tem que crescer ao longo da temporada, senão a tela lista a final
    # antes da eliminatória.
    ordens = [por_nome[n].number for n in ("1ª eliminatória", "Play-off de acesso", "Final")]
    assert ordens == sorted(ordens)
