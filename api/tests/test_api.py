"""Testes de API.

A parte crítica é a segunda metade: **teste de vazamento por endpoint**. Não
basta testar a função de blindagem — o que quebra na prática é alguém criar um
endpoint novo que monta a resposta na mão. Por isso a lista de endpoints que
tocam palpite é explícita aqui, e cada um é verificado.
"""

from __future__ import annotations

from datetime import UTC, datetime, timedelta

import pytest
from httpx import AsyncClient
from sqlalchemy.ext.asyncio import AsyncSession

from app.models import Fixture, Pool
from app.services import pools as pool_service
from app.services import predictions as prediction_service
from tests.factories import (
    add_member,
    make_fixture,
    make_pool,
    make_round,
    make_season,
    make_team,
    make_user,
)

pytestmark = pytest.mark.integration


async def _registrar(client: AsyncClient, email: str, nome: str) -> dict[str, str]:
    response = await client.post(
        "/v1/auth/register",
        json={
            "email": email,
            "password": "senha-bem-comprida",
            "display_name": nome,
            "accepted_terms": True,
        },
    )
    assert response.status_code == 201, response.text
    return response.json()


def _auth(tokens: dict[str, str]) -> dict[str, str]:
    return {"Authorization": f"Bearer {tokens['access_token']}"}


# ---------------------------------------------------------------------------
# Autenticação
# ---------------------------------------------------------------------------


async def test_cadastro_e_login(client: AsyncClient) -> None:
    tokens = await _registrar(client, "novo@teste.local", "Novo")
    assert tokens["access_token"] and tokens["refresh_token"]

    login = await client.post(
        "/v1/auth/login",
        json={"email": "novo@teste.local", "password": "senha-bem-comprida"},
    )
    assert login.status_code == 200

    perfil = await client.get("/v1/auth/me", headers=_auth(login.json()))
    assert perfil.status_code == 200
    assert perfil.json()["display_name"] == "Novo"


async def test_email_repetido_e_recusado(client: AsyncClient) -> None:
    await _registrar(client, "repetido@teste.local", "Um")
    resposta = await client.post(
        "/v1/auth/register",
        json={
            "email": "repetido@teste.local",
            "password": "outra-senha-longa",
            "display_name": "Dois",
        },
    )
    assert resposta.status_code == 409


async def test_senha_errada_nao_entra(client: AsyncClient) -> None:
    await _registrar(client, "senha@teste.local", "Alguém")
    resposta = await client.post(
        "/v1/auth/login",
        json={"email": "senha@teste.local", "password": "senha-errada-mesmo"},
    )
    assert resposta.status_code == 401


async def test_endpoint_protegido_exige_token(client: AsyncClient) -> None:
    assert (await client.get("/v1/auth/me")).status_code == 401
    assert (
        await client.get("/v1/auth/me", headers={"Authorization": "Bearer lixo"})
    ).status_code == 401


async def test_refresh_rotaciona_e_invalida_o_anterior(client: AsyncClient) -> None:
    tokens = await _registrar(client, "rot@teste.local", "Rot")

    primeiro = await client.post(
        "/v1/auth/refresh", json={"refresh_token": tokens["refresh_token"]}
    )
    assert primeiro.status_code == 200
    novo = primeiro.json()
    assert novo["refresh_token"] != tokens["refresh_token"]

    # O antigo não vale mais.
    reuso = await client.post("/v1/auth/refresh", json={"refresh_token": tokens["refresh_token"]})
    assert reuso.status_code == 401


async def test_reuso_de_refresh_derruba_a_familia_inteira(client: AsyncClient) -> None:
    """Token reutilizado é assinatura de roubo: encerra tudo daquela linhagem."""
    tokens = await _registrar(client, "roubo@teste.local", "Roubo")

    rotacionado = (
        await client.post("/v1/auth/refresh", json={"refresh_token": tokens["refresh_token"]})
    ).json()

    # O atacante usa a cópia antiga...
    await client.post("/v1/auth/refresh", json={"refresh_token": tokens["refresh_token"]})
    # ...e o token legítimo também morre.
    resposta = await client.post(
        "/v1/auth/refresh", json={"refresh_token": rotacionado["refresh_token"]}
    )
    assert resposta.status_code == 401


async def test_exclusao_de_conta_anonimiza(client: AsyncClient, db_session: AsyncSession) -> None:
    tokens = await _registrar(client, "lgpd@teste.local", "Quem Sai")

    resposta = await client.delete("/v1/auth/me", headers=_auth(tokens))
    assert resposta.status_code == 200

    from sqlalchemy import select

    from app.models import User

    usuario = await db_session.scalar(
        select(User).where(User.display_name == "Participante removido")
    )
    assert usuario is not None
    assert usuario.anonymized_at is not None
    assert "@invalido.local" in usuario.email


# ---------------------------------------------------------------------------
# Blindagem — um teste por endpoint que toca palpite
# ---------------------------------------------------------------------------


async def _cenario_http(
    client: AsyncClient, db_session: AsyncSession
) -> tuple[Pool, Fixture, dict[str, str]]:
    """Bolão com dois participantes; o rival já palpitou, o jogo não começou."""
    espiao_tokens = await _registrar(client, "espiao@teste.local", "Espião")
    perfil = await client.get("/v1/auth/me", headers=_auth(espiao_tokens))
    espiao_id = perfil.json()["id"]

    from app.models import User

    espiao = await db_session.get(User, espiao_id)
    assert espiao is not None

    rival = await make_user(db_session, "rival-http@teste.local", "Rival")
    season = await make_season(db_session, 2050)
    rodada = await make_round(db_session, season)
    casa = await make_team(db_session, season, "Casa HTTP")
    fora = await make_team(db_session, season, "Fora HTTP")
    fixture = await make_fixture(
        db_session, season, rodada, casa, fora, kickoff_in=timedelta(hours=6)
    )

    pool = await make_pool(db_session, espiao, season, rounds=[rodada], name="Bolão HTTP")
    m_rival = await add_member(db_session, pool, rival, "Rival")

    await prediction_service.upsert_prediction(
        db_session, membership=m_rival, fixture=fixture, home_goals=4, away_goals=0
    )
    await db_session.flush()

    return pool, fixture, _auth(espiao_tokens)


def _contem_placar_do_rival(payload: object) -> bool:
    """Procura o 4x0 do rival em qualquer lugar da resposta."""
    texto = repr(payload)
    return '"home_goals": 4' in texto or "'home_goals': 4" in texto or "4x0" in texto


async def test_vazamento_endpoint_palpites_do_jogo(
    client: AsyncClient, db_session: AsyncSession
) -> None:
    pool, fixture, headers = await _cenario_http(client, db_session)

    resposta = await client.get(
        f"/v1/pools/{pool.slug}/fixtures/{fixture.id}/predictions", headers=headers
    )

    assert resposta.status_code == 200
    corpo = resposta.json()
    assert len(corpo) == 1
    assert corpo[0]["is_hidden"] is True
    assert corpo[0]["home_goals"] is None
    assert corpo[0]["away_goals"] is None
    assert not _contem_placar_do_rival(corpo)


async def test_vazamento_endpoint_palpites_da_rodada(
    client: AsyncClient, db_session: AsyncSession
) -> None:
    pool, fixture, headers = await _cenario_http(client, db_session)

    resposta = await client.get(
        f"/v1/pools/{pool.slug}/rounds/{fixture.round_id}/predictions", headers=headers
    )

    assert resposta.status_code == 200
    assert all(item["is_hidden"] for item in resposta.json())
    assert not _contem_placar_do_rival(resposta.json())


async def test_vazamento_endpoint_meus_palpites_so_traz_os_meus(
    client: AsyncClient, db_session: AsyncSession
) -> None:
    pool, fixture, headers = await _cenario_http(client, db_session)

    resposta = await client.get(
        f"/v1/pools/{pool.slug}/rounds/{fixture.round_id}/my-predictions", headers=headers
    )

    assert resposta.status_code == 200
    # O espião não palpitou; o palpite do rival não pode aparecer aqui.
    assert resposta.json() == []


async def test_vazamento_endpoint_breakdown_recusa_antes_do_apito(
    client: AsyncClient, db_session: AsyncSession
) -> None:
    """O detalhamento revela palpite por natureza — só depois do apito."""
    pool, fixture, headers = await _cenario_http(client, db_session)

    resposta = await client.get(
        f"/v1/pools/{pool.slug}/fixtures/{fixture.id}/breakdown", headers=headers
    )

    assert resposta.status_code == 409
    assert "blindados" in resposta.json()["detail"]


async def test_breakdown_diz_quem_palpitou_depois_do_apito(
    client: AsyncClient, db_session: AsyncSession
) -> None:
    """Depois do apito a tela precisa dizer QUEM levou o quê.

    Sem o nome, "Como cada um pontuou" é uma lista de placares soltos: dá para
    ver que alguém levou 7 e alguém levou 0, e não dá para saber quem — que é
    justamente a pergunta que a tela existe para responder.
    """
    from app.models import FixtureStatus
    from app.services import settlement as settlement_service

    pool, fixture, headers = await _cenario_http(client, db_session)

    # O espião também palpita, para a tabela ter duas linhas.
    espiao = await pool_service.get_membership(
        db_session, pool.id, (await client.get("/v1/auth/me", headers=headers)).json()["id"]
    )
    assert espiao is not None
    await prediction_service.upsert_prediction(
        db_session, membership=espiao, fixture=fixture, home_goals=1, away_goals=0
    )

    # O jogo acontece e é apurado.
    fixture.home_ft, fixture.away_ft = 4, 0
    fixture.status = FixtureStatus.FINISHED
    await db_session.flush()
    await settlement_service.settle_fixture(db_session, fixture.id)
    await db_session.commit()

    resposta = await client.get(
        f"/v1/pools/{pool.slug}/fixtures/{fixture.id}/breakdown", headers=headers
    )
    assert resposta.status_code == 200

    linhas = resposta.json()
    assert len(linhas) == 2

    por_nome = {linha["display_name"]: linha for linha in linhas}
    assert set(por_nome) == {"Espião", "Rival"}

    # O rival cravou 4x0 e leva mais; o espião errou.
    assert por_nome["Rival"]["prediction"] == "4x0"
    assert por_nome["Rival"]["final_points"] > por_nome["Espião"]["final_points"]

    # Cada um se reconhece na tabela sem a tela comparar id.
    assert por_nome["Espião"]["is_me"] is True
    assert por_nome["Rival"]["is_me"] is False

    # E a ordem é por pontos, decrescente — quem mais pontuou vem primeiro.
    assert [linha["final_points"] for linha in linhas] == sorted(
        (linha["final_points"] for linha in linhas), reverse=True
    )


async def test_vazamento_endpoint_jogos_nao_traz_palpite(
    client: AsyncClient, db_session: AsyncSession
) -> None:
    pool, fixture, headers = await _cenario_http(client, db_session)

    resposta = await client.get(
        f"/v1/pools/{pool.slug}/rounds/{fixture.round_id}/fixtures", headers=headers
    )

    assert resposta.status_code == 200
    assert not _contem_placar_do_rival(resposta.json())


async def test_vazamento_endpoint_ranking_nao_traz_palpite(
    client: AsyncClient, db_session: AsyncSession
) -> None:
    pool, _fixture, headers = await _cenario_http(client, db_session)

    resposta = await client.get(f"/v1/pools/{pool.slug}/standings", headers=headers)

    assert resposta.status_code == 200
    assert not _contem_placar_do_rival(resposta.json())


async def test_vazamento_endpoint_detalhe_do_bolao_nao_traz_palpite(
    client: AsyncClient, db_session: AsyncSession
) -> None:
    pool, _fixture, headers = await _cenario_http(client, db_session)

    resposta = await client.get(f"/v1/pools/{pool.slug}", headers=headers)

    assert resposta.status_code == 200
    assert not _contem_placar_do_rival(resposta.json())


async def test_vazamento_endpoint_membros_nao_traz_palpite(
    client: AsyncClient, db_session: AsyncSession
) -> None:
    pool, _fixture, headers = await _cenario_http(client, db_session)

    resposta = await client.get(f"/v1/pools/{pool.slug}/members", headers=headers)

    assert resposta.status_code == 200
    assert not _contem_placar_do_rival(resposta.json())


async def test_palpite_aparece_para_todos_depois_do_apito(
    client: AsyncClient, db_session: AsyncSession
) -> None:
    pool, fixture, headers = await _cenario_http(client, db_session)

    fixture.kickoff_at = datetime.now(UTC) - timedelta(minutes=1)
    await db_session.flush()

    resposta = await client.get(
        f"/v1/pools/{pool.slug}/fixtures/{fixture.id}/predictions", headers=headers
    )

    corpo = resposta.json()
    assert corpo[0]["is_hidden"] is False
    assert corpo[0]["home_goals"] == 4
    assert corpo[0]["away_goals"] == 0


async def test_quem_nao_e_membro_nao_ve_o_bolao_privado(
    client: AsyncClient, db_session: AsyncSession
) -> None:
    pool, _fixture, _headers = await _cenario_http(client, db_session)
    estranho = await _registrar(client, "estranho@teste.local", "Estranho")

    resposta = await client.get(f"/v1/pools/{pool.slug}", headers=_auth(estranho))

    assert resposta.status_code == 403


async def test_quem_nao_e_membro_nao_lista_palpites(
    client: AsyncClient, db_session: AsyncSession
) -> None:
    pool, fixture, _headers = await _cenario_http(client, db_session)
    estranho = await _registrar(client, "estranho2@teste.local", "Estranho")

    resposta = await client.get(
        f"/v1/pools/{pool.slug}/fixtures/{fixture.id}/predictions", headers=_auth(estranho)
    )

    assert resposta.status_code == 403


# ---------------------------------------------------------------------------
# Fluxo de palpite pela API
# ---------------------------------------------------------------------------


async def test_salvar_palpite_pela_api(client: AsyncClient, db_session: AsyncSession) -> None:
    pool, fixture, headers = await _cenario_http(client, db_session)

    resposta = await client.put(
        f"/v1/pools/{pool.slug}/predictions",
        headers=headers,
        json={"predictions": [{"fixture_id": fixture.id, "home_goals": 2, "away_goals": 1}]},
    )

    assert resposta.status_code == 200
    corpo = resposta.json()
    assert len(corpo["saved"]) == 1
    assert corpo["saved"][0]["home_goals"] == 2
    assert corpo["rejected"] == []


async def test_api_recusa_palpite_depois_do_apito(
    client: AsyncClient, db_session: AsyncSession
) -> None:
    pool, fixture, headers = await _cenario_http(client, db_session)
    fixture.kickoff_at = datetime.now(UTC) - timedelta(minutes=1)
    await db_session.flush()

    resposta = await client.put(
        f"/v1/pools/{pool.slug}/predictions",
        headers=headers,
        json={"predictions": [{"fixture_id": fixture.id, "home_goals": 2, "away_goals": 1}]},
    )

    assert resposta.status_code == 200
    corpo = resposta.json()
    assert corpo["saved"] == []
    assert len(corpo["rejected"]) == 1
    assert "começou" in corpo["rejected"][0]["reason"]


async def test_entrar_no_bolao_por_codigo(client: AsyncClient, db_session: AsyncSession) -> None:
    pool, _fixture, _headers = await _cenario_http(client, db_session)
    novato = await _registrar(client, "novato@teste.local", "Novato")

    resposta = await client.post(
        "/v1/pools/join",
        headers=_auth(novato),
        json={"invite_code": pool.invite_code, "display_name": "Novato"},
    )

    assert resposta.status_code == 200
    assert resposta.json()["slug"] == pool.slug


async def test_codigo_invalido(client: AsyncClient) -> None:
    tokens = await _registrar(client, "semcodigo@teste.local", "Sem Código")

    resposta = await client.post(
        "/v1/pools/join", headers=_auth(tokens), json={"invite_code": "NAOEXISTE"}
    )

    assert resposta.status_code == 404


async def test_codigo_de_convite_so_aparece_para_quem_organiza(
    client: AsyncClient, db_session: AsyncSession
) -> None:
    pool, _fixture, headers_dono = await _cenario_http(client, db_session)
    novato = await _registrar(client, "curioso@teste.local", "Curioso")
    await client.post(
        "/v1/pools/join", headers=_auth(novato), json={"invite_code": pool.invite_code}
    )

    como_dono = await client.get(f"/v1/pools/{pool.slug}", headers=headers_dono)
    como_jogador = await client.get(f"/v1/pools/{pool.slug}", headers=_auth(novato))

    assert como_dono.json()["invite_code"] == pool.invite_code
    assert como_jogador.json()["invite_code"] is None


async def test_pontuacao_do_bolao_e_exposta_com_explicacao(
    client: AsyncClient, db_session: AsyncSession
) -> None:
    pool, _fixture, headers = await _cenario_http(client, db_session)

    resposta = await client.get(f"/v1/pools/{pool.slug}/scoring", headers=headers)

    assert resposta.status_code == 200
    corpo = resposta.json()
    assert corpo["mode"] == "classic"
    assert corpo["max_points"] == 10
    # A ordem de avaliação é derivada dos pontos, e a tela mostra isso.
    assert corpo["evaluation_order"][0] == "exact"
    assert all(item["label"] for item in corpo["criteria"])


async def test_info_da_rede_local(client: AsyncClient) -> None:
    tokens = await _registrar(client, "lan@teste.local", "LAN")

    resposta = await client.get("/v1/system/lan", headers=_auth(tokens))

    assert resposta.status_code == 200
    corpo = resposta.json()
    assert corpo["port"] == 8080
    assert isinstance(corpo["addresses"], list)
