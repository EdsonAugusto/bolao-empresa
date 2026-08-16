"""Retrospecto dos últimos jogos.

O caso que quase passa despercebido está no fim do arquivo: um clube tem uma
linha por provedor, e agrupar por ``team_id`` mostraria o Flamengo da
Libertadores sem retrospecto nenhum enquanto o Flamengo do Brasileirão tem
vinte e seis jogos. Parece bug para quem olha, e é.
"""

from __future__ import annotations

from datetime import UTC, datetime, timedelta

from sqlalchemy.ext.asyncio import AsyncSession

from app.models import FixtureStatus, Provider, Team
from app.services.form import retrospecto_de, retrospectos
from tests.factories import make_fixture, make_round, make_season, make_team

AGORA = datetime.now(UTC)


async def _partida(session: AsyncSession, season, casa, fora, *, gols_casa, gols_fora, dias_atras):
    """Uma partida encerrada, `dias_atras` dias no passado.

    Cada partida ganha a própria rodada: `make_fixture` monta o identificador
    externo com o par de times mais o número da rodada, e dois jogos do mesmo
    par na mesma rodada colidiriam no índice único.
    """
    rodada = await make_round(session, season, number=dias_atras)
    jogo = await make_fixture(
        session, season, rodada, casa, fora, kickoff_in=timedelta(days=-dias_atras)
    )
    jogo.home_ft, jogo.away_ft = gols_casa, gols_fora
    jogo.status = FixtureStatus.FINISHED
    await session.flush()
    return jogo


# ---------------------------------------------------------------------------
# Leitura do resultado
# ---------------------------------------------------------------------------


async def test_vitoria_empate_e_derrota_dos_dois_lados(db_session: AsyncSession) -> None:
    season = await make_season(db_session, 5100)
    casa = await make_team(db_session, season, "Casa 5100")
    fora = await make_team(db_session, season, "Fora 5100")

    await _partida(db_session, season, casa, fora, gols_casa=3, gols_fora=0, dias_atras=3)
    await _partida(db_session, season, casa, fora, gols_casa=1, gols_fora=1, dias_atras=2)
    await _partida(db_session, season, casa, fora, gols_casa=0, gols_fora=2, dias_atras=1)

    achados = await retrospectos(db_session, [casa.id, fora.id])

    # Do mais recente para o mais antigo: derrota, empate, vitória.
    assert achados[casa.id].resumo == "DEV"
    assert achados[fora.id].resumo == "VED"


async def test_ordem_e_do_mais_recente_para_o_mais_antigo(db_session: AsyncSession) -> None:
    season = await make_season(db_session, 5101)
    casa = await make_team(db_session, season, "Casa 5101")
    fora = await make_team(db_session, season, "Fora 5101")

    await _partida(db_session, season, casa, fora, gols_casa=1, gols_fora=0, dias_atras=10)
    await _partida(db_session, season, casa, fora, gols_casa=0, gols_fora=1, dias_atras=1)

    retrospecto = await retrospecto_de(db_session, casa.id)

    assert retrospecto.resumo == "DV"
    assert retrospecto.jogos[0].adversario == "Fora 5101"
    assert retrospecto.jogos[0].em_casa is True


async def test_para_em_cinco_jogos(db_session: AsyncSession) -> None:
    season = await make_season(db_session, 5102)
    casa = await make_team(db_session, season, "Casa 5102")
    fora = await make_team(db_session, season, "Fora 5102")

    for dia in range(1, 9):
        await _partida(db_session, season, casa, fora, gols_casa=2, gols_fora=0, dias_atras=dia)

    assert len((await retrospecto_de(db_session, casa.id)).jogos) == 5


# ---------------------------------------------------------------------------
# O que não entra
# ---------------------------------------------------------------------------


async def test_jogo_sem_placar_nao_entra(db_session: AsyncSession) -> None:
    """Jogo marcado como encerrado mas sem placar não vira resultado nenhum."""
    season = await make_season(db_session, 5103)
    casa = await make_team(db_session, season, "Casa 5103")
    fora = await make_team(db_session, season, "Fora 5103")

    rodada = await make_round(db_session, season)
    jogo = await make_fixture(db_session, season, rodada, casa, fora, kickoff_in=timedelta(days=-1))
    jogo.status = FixtureStatus.FINISHED
    await db_session.flush()

    assert (await retrospecto_de(db_session, casa.id)).resumo == ""


async def test_jogo_futuro_nao_entra(db_session: AsyncSession) -> None:
    season = await make_season(db_session, 5104)
    casa = await make_team(db_session, season, "Casa 5104")
    fora = await make_team(db_session, season, "Fora 5104")

    rodada = await make_round(db_session, season)
    await make_fixture(db_session, season, rodada, casa, fora, kickoff_in=timedelta(days=3))

    assert (await retrospecto_de(db_session, casa.id)).resumo == ""


async def test_corte_por_data_mostra_como_o_time_chegou_naquele_dia(
    db_session: AsyncSession,
) -> None:
    """Na tela de um jogo antigo, o retrospecto não pode incluir o futuro dele."""
    season = await make_season(db_session, 5105)
    casa = await make_team(db_session, season, "Casa 5105")
    fora = await make_team(db_session, season, "Fora 5105")

    await _partida(db_session, season, casa, fora, gols_casa=1, gols_fora=0, dias_atras=10)
    await _partida(db_session, season, casa, fora, gols_casa=0, gols_fora=5, dias_atras=1)

    corte = AGORA - timedelta(days=5)
    achados = await retrospectos(db_session, [casa.id], ate=corte)

    assert achados[casa.id].resumo == "V"


async def test_time_sem_historico_devolve_vazio(db_session: AsyncSession) -> None:
    season = await make_season(db_session, 5106)
    novato = await make_team(db_session, season, "Novato 5106")

    assert (await retrospecto_de(db_session, novato.id)).resumo == ""


async def test_lista_vazia_nao_consulta_nada(db_session: AsyncSession) -> None:
    assert await retrospectos(db_session, []) == {}


# ---------------------------------------------------------------------------
# Identidade do clube — o caso que motivou o serviço
# ---------------------------------------------------------------------------


async def test_historico_junta_as_linhas_do_mesmo_clube(db_session: AsyncSession) -> None:
    """O mesmo clube tem uma linha por provedor, e o histórico é um só.

    O upsert é por ``(provider_id, external_id)``, então o Flamengo do GE e o
    da Wikipédia são ``teams`` diferentes. Sem juntar por nome, a tela da
    Libertadores mostraria o clube estreando na vida.
    """
    season = await make_season(db_session, 5107)
    flamengo = await make_team(db_session, season, "Flamengo 5107")
    rival = await make_team(db_session, season, "Rival 5107")

    await _partida(db_session, season, flamengo, rival, gols_casa=2, gols_fora=0, dias_atras=2)

    # Mesmo clube, outro provedor — como a Wikipédia cria ao importar mata-mata.
    outro_provedor = Provider(slug="outro-provedor-5107", name="Outro")
    db_session.add(outro_provedor)
    await db_session.flush()

    gemeo = Team(
        provider_id=outro_provedor.id,
        external_id="flamengo-5107",
        name="Flamengo 5107",
        slug="flamengo-5107-b",
    )
    db_session.add(gemeo)
    await db_session.flush()

    achados = await retrospectos(db_session, [flamengo.id, gemeo.id])

    assert achados[gemeo.id].resumo == "V"
    assert achados[gemeo.id].resumo == achados[flamengo.id].resumo


async def test_nome_com_forma_juridica_diferente_conta_como_o_mesmo(
    db_session: AsyncSession,
) -> None:
    """``FC Porto 5108`` e ``Porto 5108`` são o mesmo clube."""
    season = await make_season(db_session, 5108)
    porto = await make_team(db_session, season, "FC Porto 5108")
    rival = await make_team(db_session, season, "Rival 5108")

    await _partida(db_session, season, porto, rival, gols_casa=0, gols_fora=1, dias_atras=1)

    outro = Provider(slug="outro-provedor-5108", name="Outro")
    db_session.add(outro)
    await db_session.flush()
    gemeo = Team(
        provider_id=outro.id,
        external_id="porto-5108",
        name="Porto 5108",
        slug="porto-5108-b",
    )
    db_session.add(gemeo)
    await db_session.flush()

    achados = await retrospectos(db_session, [gemeo.id])

    assert achados[gemeo.id].resumo == "D"


async def test_clubes_diferentes_com_nome_parecido_nao_se_misturam(
    db_session: AsyncSession,
) -> None:
    """``Vitória`` (BA) e ``Vitória SC`` (Guimarães) são clubes diferentes.

    O par existe no banco de verdade, e ``chave()`` funde os dois porque ``sc``
    está na lista de ruído. Sem esta separação o português exibiria o
    retrospecto do baiano — errado, e convincente o bastante para ninguém
    desconfiar.
    """
    brasil = await make_season(db_session, 5200)
    vitoria_ba = await make_team(db_session, brasil, "Vitória 5200")
    rival = await make_team(db_session, brasil, "Rival 5200")
    await _partida(db_session, brasil, vitoria_ba, rival, gols_casa=3, gols_fora=0, dias_atras=1)
    vitoria_ba.country = "Brasil"

    portugal = Provider(slug="provedor-pt-5200", name="PT")
    db_session.add(portugal)
    await db_session.flush()
    vitoria_sc = Team(
        provider_id=portugal.id,
        external_id="vitoria-sc-5200",
        name="Vitória 5200 SC",
        slug="vitoria-sc-5200",
        country="Portugal",
    )
    db_session.add(vitoria_sc)
    await db_session.flush()

    achados = await retrospectos(db_session, [vitoria_ba.id, vitoria_sc.id])

    assert achados[vitoria_ba.id].resumo == "V"
    assert achados[vitoria_sc.id].resumo == ""


async def test_pais_ausente_nao_impede_a_fusao(db_session: AsyncSession) -> None:
    """Ausência de país não é discordância de país."""
    season = await make_season(db_session, 5201)
    porto = await make_team(db_session, season, "FC Porto 5201")
    rival = await make_team(db_session, season, "Rival 5201")
    await _partida(db_session, season, porto, rival, gols_casa=1, gols_fora=1, dias_atras=1)
    porto.country = "Portugal"

    outro = Provider(slug="provedor-x-5201", name="X")
    db_session.add(outro)
    await db_session.flush()
    gemeo = Team(
        provider_id=outro.id,
        external_id="porto-5201",
        name="Porto 5201",
        slug="porto-5201-b",
        country=None,
    )
    db_session.add(gemeo)
    await db_session.flush()

    assert (await retrospectos(db_session, [gemeo.id]))[gemeo.id].resumo == "E"


async def test_clube_com_muitos_jogos_nao_encurta_o_retrospecto_do_outro(
    db_session: AsyncSession,
) -> None:
    """Um teto global cortaria o histórico do clube menos ativo, em silêncio."""
    season = await make_season(db_session, 5202)
    veterano = await make_team(db_session, season, "Veterano 5202")
    parceiro = await make_team(db_session, season, "Parceiro 5202")
    novato = await make_team(db_session, season, "Novato 5202")
    outro = await make_team(db_session, season, "Outro 5202")

    for dia in range(1, 41):
        await _partida(
            db_session, season, veterano, parceiro, gols_casa=1, gols_fora=0, dias_atras=dia
        )
    for dia in range(50, 56):
        await _partida(db_session, season, novato, outro, gols_casa=0, gols_fora=1, dias_atras=dia)

    achados = await retrospectos(db_session, [veterano.id, novato.id])

    assert len(achados[veterano.id].jogos) == 5
    assert len(achados[novato.id].jogos) == 5
