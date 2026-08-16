"""O que muda quando a plataforma sai da rede local para a internet.

Cada teste aqui trava uma decisão que só passou a importar por existir um
domínio público na frente: em rede doméstica, estar na rede já era autorização
suficiente para quase tudo.
"""

from __future__ import annotations

import pytest
from httpx import AsyncClient
from sqlalchemy.ext.asyncio import AsyncSession

from app.core import limites
from app.core.config import settings
from app.core.security import create_access_token
from app.services import auth as auth_service
from tests.factories import (
    make_fixture,
    make_pool,
    make_round,
    make_season,
    make_team,
    make_user,
)

pytestmark = pytest.mark.asyncio


async def _conta(session: AsyncSession, marca: int, *, admin: bool = False):
    """Conta de teste.

    `nivel` e `is_superuser` sempre juntos: a coluna booleana é derivada do
    nível, e marcar só ela dá uma conta que passa em `is_superuser` mas não tem
    permissão nenhuma — que é o que a checagem de verdade consulta hoje.
    """
    from app.core.permissoes import Nivel

    user = await make_user(session, email=f"exp{marca}@teste.local", display_name=f"Pessoa {marca}")
    user.nivel = str(Nivel.DONO if admin else Nivel.JOGADOR)
    user.is_superuser = admin
    await session.flush()
    return user


def _cabecalho(user) -> dict[str, str]:  # type: ignore[no-untyped-def]
    return {"Authorization": f"Bearer {create_access_token(user.id)}"}


# --------------------------------------------------------------- permissão


@pytest.mark.parametrize(
    ("metodo", "caminho", "corpo"),
    [
        ("post", "/api/v1/catalog/brasileirao", {"year": 2026}),
        ("post", "/api/v1/catalog/ligas/import", {"slug": "premier-league"}),
        ("post", "/api/v1/catalog/escudos", {}),
        ("post", "/api/v1/catalog/ge-discover", {"url": "https://ge.globo.com/futebol/x/"}),
    ],
)
async def test_conta_comum_nao_mexe_no_catalogo(
    client: AsyncClient, db_session: AsyncSession, metodo: str, caminho: str, corpo: dict
) -> None:
    """Importar campeonato e lançar placar mexem no que todo mundo vê.

    Lançar placar é o mais grave: ele reapura o ranking de todos os bolões que
    incluem aquele jogo. Com cadastro aberto, isso era um botão de reescrever a
    pontuação alheia para qualquer pessoa da internet.
    """
    comum = await _conta(db_session, 1)
    await db_session.commit()

    resposta = await getattr(client, metodo)(caminho, headers=_cabecalho(comum), json=corpo)
    assert resposta.status_code == 403, resposta.text


async def test_administracao_passa_pelo_mesmo_caminho(
    client: AsyncClient, db_session: AsyncSession
) -> None:
    """A guarda é de permissão, não de rota quebrada."""
    admin = await _conta(db_session, 2, admin=True)
    await db_session.commit()

    resposta = await client.post(
        "/api/v1/catalog/ge-discover",
        headers=_cabecalho(admin),
        json={"url": "http://169.254.169.254/latest/meta-data/"},
    )
    # Passou da permissão e parou na validação do endereço — que é o esperado.
    assert resposta.status_code == 422


# --------------------------------------------------------------------- SSRF


@pytest.mark.parametrize(
    "endereco",
    [
        "http://169.254.169.254/latest/meta-data/",  # metadados da nuvem
        "http://api:8000/health/live",  # serviço interno do Compose
        "http://localhost:5432/",
        "https://evil.example.com/pagina",
        "https://ge.globo.com.evil.example.com/x",  # sufixo enganoso
        "file:///etc/passwd",
    ],
)
async def test_descoberta_recusa_endereco_que_nao_seja_do_ge(
    client: AsyncClient, db_session: AsyncSession, endereco: str
) -> None:
    """Sem esta lista o endpoint é um proxy: quem chama escolhe o que o servidor busca."""
    admin = await _conta(db_session, 3, admin=True)
    await db_session.commit()

    resposta = await client.post(
        "/api/v1/catalog/ge-discover", headers=_cabecalho(admin), json={"url": endereco}
    )
    assert resposta.status_code == 422, resposta.text


# ------------------------------------------------------------ limite de taxa


async def test_login_barra_depois_de_muitas_tentativas(client: AsyncClient) -> None:
    """Cada tentativa custa um Argon2 de 64 MiB; sem teto, um laço derruba a API."""
    tentativas = settings.login_max_por_conta
    corpo = {"email": "ninguem@teste.local", "password": "senha-errada-mesmo"}

    for _ in range(tentativas):
        resposta = await client.post("/api/v1/auth/login", json=corpo)
        assert resposta.status_code == 401

    barrado = await client.post("/api/v1/auth/login", json=corpo)
    assert barrado.status_code == 429
    assert "Retry-After" in barrado.headers
    assert "tente de novo em" in barrado.json()["detail"].lower()


async def test_login_certo_devolve_as_tentativas(
    client: AsyncClient, db_session: AsyncSession
) -> None:
    """Quem erra a senha duas vezes e acerta na terceira não fica marcado."""
    user = await make_user(db_session, email="volta@teste.local", password="senha-boa-de-teste")
    await db_session.commit()

    for _ in range(3):
        await client.post("/api/v1/auth/login", json={"email": user.email, "password": "errada"})

    entrou = await client.post(
        "/api/v1/auth/login", json={"email": user.email, "password": "senha-boa-de-teste"}
    )
    assert entrou.status_code == 200

    # O contador da conta foi zerado; sobrou espaço para errar de novo.
    de_novo = await client.post(
        "/api/v1/auth/login", json={"email": user.email, "password": "errada"}
    )
    assert de_novo.status_code == 401


async def test_limitador_falha_aberto_sem_redis(monkeypatch: pytest.MonkeyPatch) -> None:
    """Redis fora do ar não pode trancar ninguém para fora do próprio bolão."""

    def explode() -> None:
        raise RuntimeError("redis indisponível")

    monkeypatch.setattr(limites, "get_redis", explode)
    assert await limites.registrar("limite:teste", teto=1, janela=60) is None


# ------------------------------------------------------------------ cadastro


async def test_modo_convite_exige_codigo(
    db_session: AsyncSession, monkeypatch: pytest.MonkeyPatch
) -> None:
    """Num servidor público o formulário de cadastro fica visível para todo mundo."""
    await _conta(db_session, 4)  # já existe alguém: não é a primeira conta
    await db_session.flush()
    monkeypatch.setattr(settings, "registration_mode", "convite")

    with pytest.raises(auth_service.CadastroFechado, match="código de convite"):
        await auth_service.cadastro_permitido(db_session, convite="")

    with pytest.raises(auth_service.CadastroFechado, match="não encontrado"):
        await auth_service.cadastro_permitido(db_session, convite="XXXXXXXX")


async def test_primeira_conta_entra_em_qualquer_modo(
    db_session: AsyncSession, monkeypatch: pytest.MonkeyPatch
) -> None:
    """Sem a primeira conta não existe quem crie o bolão que gera o convite."""
    monkeypatch.setattr(settings, "registration_mode", "fechado")
    # Banco sem nenhuma conta: a fixture rola tudo para trás a cada teste.
    await auth_service.cadastro_permitido(db_session, convite="")


async def test_modo_aberto_e_o_padrao_em_rede_local(db_session: AsyncSession) -> None:
    """Em casa, estar na rede já é o convite — pedir código seria atrito à toa."""
    assert settings.registration_mode == "aberto"
    await _conta(db_session, 5)
    await db_session.flush()
    await auth_service.cadastro_permitido(db_session, convite="")


# ------------------------------------------------------- lançar placar


async def _cenario_com_jogo(session: AsyncSession, marca: int):
    """Um bolão com uma rodada e um jogo, mais alguém de fora."""
    dono = await make_user(session, email=f"dono{marca}@teste.local", display_name="Dono")
    estranho = await make_user(session, email=f"fora{marca}@teste.local", display_name="Fora")
    season = await make_season(session, year=2050 + marca)
    rodada = await make_round(session, season, number=1)
    casa = await make_team(session, season, f"Casa {marca}")
    fora = await make_team(session, season, f"Fora {marca}")
    jogo = await make_fixture(session, season, rodada, casa, fora)
    await make_pool(session, dono, season, name=f"Bolão {marca}", rounds=[rodada])
    await session.commit()
    return dono, estranho, jogo


async def test_quem_organiza_o_bolao_lanca_o_placar(
    client: AsyncClient, db_session: AsyncSession
) -> None:
    """Destravar um jogo que terminou sem placar é trabalho de organizador.

    Exigir administração da instalação fechava o único caminho de saída daquele
    estado: quem organiza um bolão entre amigos quase nunca é a primeira conta
    da instalação, que é a única que nasce com ``is_superuser``.
    """
    dono, _, jogo = await _cenario_com_jogo(db_session, 1)

    resposta = await client.put(
        f"/api/v1/catalog/fixtures/{jogo.id}/score",
        headers=_cabecalho(dono),
        json={"home_goals": 2, "away_goals": 1},
    )
    assert resposta.status_code == 200, resposta.text


async def test_quem_nao_organiza_nao_lanca_o_placar(
    client: AsyncClient, db_session: AsyncSession
) -> None:
    """Lançar placar reapura o ranking de todo bolão que inclui o jogo."""
    _, estranho, jogo = await _cenario_com_jogo(db_session, 2)

    resposta = await client.put(
        f"/api/v1/catalog/fixtures/{jogo.id}/score",
        headers=_cabecalho(estranho),
        json={"home_goals": 9, "away_goals": 0},
    )
    assert resposta.status_code == 403, resposta.text
    assert "organiza" in resposta.json()["detail"]


async def test_administracao_lanca_placar_de_qualquer_jogo(
    client: AsyncClient, db_session: AsyncSession
) -> None:
    from app.core.permissoes import Nivel

    _, estranho, jogo = await _cenario_com_jogo(db_session, 3)
    estranho.nivel = str(Nivel.DONO)
    estranho.is_superuser = True
    await db_session.commit()

    resposta = await client.put(
        f"/api/v1/catalog/fixtures/{jogo.id}/score",
        headers=_cabecalho(estranho),
        json={"home_goals": 1, "away_goals": 1},
    )
    assert resposta.status_code == 200, resposta.text
