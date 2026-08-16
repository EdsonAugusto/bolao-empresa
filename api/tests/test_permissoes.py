"""Hierarquia, permissões e grupos.

O que estes testes protegem, em uma frase: **a hierarquia não pode ser
contornada**. Cada furo aqui é alguém virando administrador da instalação de
outra pessoa sem que ninguém tenha decidido isso.
"""

from __future__ import annotations

import pytest
from httpx import AsyncClient
from sqlalchemy.ext.asyncio import AsyncSession

from app.core.permissoes import (
    Nivel,
    Permissao,
    catalogo,
    efetivas,
    escada,
    manda_em,
    niveis_que_pode_conceder,
)
from app.core.security import create_access_token
from app.models import PermissionGroup, User
from app.services import permissoes as servico
from tests.factories import make_user

asincrono = pytest.mark.asyncio


async def _conta(session: AsyncSession, marca: int, nivel: Nivel = Nivel.JOGADOR) -> User:
    user = await make_user(
        session, email=f"perm{marca}@teste.local", display_name=f"Pessoa {marca}"
    )
    user.nivel = str(nivel)
    user.is_superuser = nivel in (Nivel.DONO, Nivel.ADMIN)
    await session.flush()
    return user


def _cabecalho(user: User) -> dict[str, str]:
    return {"Authorization": f"Bearer {create_access_token(user.id)}"}


# ------------------------------------------------------------ catálogo (puro)


def test_a_escada_esta_ordenada_e_sem_empate() -> None:
    """Peso repetido faria dois níveis não mandarem um no outro — nem em si."""
    pesos = [item["peso"] for item in escada()]
    assert pesos == sorted(pesos, reverse=True)
    assert len(set(pesos)) == len(pesos)


def test_ninguem_fora_do_topo_manda_em_si_mesmo() -> None:
    """Igual não vale: dois administradores se rebaixariam mutuamente."""
    for nivel in Nivel:
        if nivel not in (Nivel.DEV, Nivel.DONO):
            assert manda_em(nivel, nivel) is False


def test_topo_manda_no_proprio_nivel() -> None:
    """É o que permite transferir a posição sem linha de comando no servidor."""
    for topo in (Nivel.DEV, Nivel.DONO):
        assert manda_em(topo, topo) is True


def test_o_dono_nao_alcanca_quem_constroi_a_plataforma() -> None:
    """Alcançar o dev daria ao dono um caminho para o nível acima do próprio."""
    assert manda_em(Nivel.DONO, Nivel.DEV) is False
    assert Nivel.DEV not in niveis_que_pode_conceder(Nivel.DONO)

    # E o dev alcança todo mundo, inclusive outro dev.
    for nivel in Nivel:
        assert manda_em(Nivel.DEV, nivel) is True


def test_so_o_dev_manda_no_dev() -> None:
    for nivel in Nivel:
        if nivel is not Nivel.DEV:
            assert manda_em(nivel, Nivel.DEV) is False


def test_so_da_para_conceder_nivel_abaixo_do_proprio() -> None:
    for nivel in Nivel:
        if nivel in (Nivel.DEV, Nivel.DONO):
            continue  # os topos transferem a própria posição; ver acima
        for concedivel in niveis_que_pode_conceder(nivel):
            assert manda_em(nivel, concedivel)
            assert concedivel is not nivel


def test_todas_as_permissoes_tem_texto() -> None:
    """Permissão sem descrição vira caixa de marcar sem legenda no painel."""
    descritas = {item["chave"] for item in catalogo()}
    assert descritas == {str(item) for item in Permissao}
    for item in catalogo():
        assert item["rotulo"] and item["ajuda"] and item["area"]


def test_revogacao_vence_nivel_e_grupo() -> None:
    resultado = efetivas(
        Nivel.ORGANIZADOR,
        de_grupos=frozenset({Permissao.CAMPEONATOS_PLACAR}),
        revogadas=frozenset({Permissao.CAMPEONATOS_PLACAR}),
    )
    assert Permissao.CAMPEONATOS_PLACAR not in resultado


@pytest.mark.parametrize("topo", [Nivel.DEV, Nivel.DONO])
def test_topo_nao_perde_permissao_nem_revogada(topo: Nivel) -> None:
    """Revogar de quem administra trancaria todo mundo para fora do painel."""
    assert efetivas(topo, revogadas=frozenset(Permissao)) == frozenset(Permissao)


# ------------------------------------------------------------------- serviço


@asincrono
async def test_nivel_desconhecido_cai_para_o_mais_restrito(db_session: AsyncSession) -> None:
    """Falhar fechado: valor estranho no banco não pode virar acesso extra."""
    user = await _conta(db_session, 1)
    user.nivel = "imperador"
    assert servico.nivel_de(user) is Nivel.JOGADOR


@asincrono
async def test_permissao_desconhecida_no_banco_e_ignorada(db_session: AsyncSession) -> None:
    """Chave órfã não pode derrubar o carregamento da sessão de todo mundo."""
    from app.models import UserPermission

    user = await _conta(db_session, 2)
    db_session.add(
        UserPermission(user_id=user.id, permission="permissao.que.nao.existe", granted=True)
    )
    await db_session.flush()

    acesso = await servico.acesso_de(db_session, user)
    assert acesso.nivel is Nivel.JOGADOR


@asincrono
async def test_nao_da_para_mexer_em_quem_esta_no_mesmo_nivel(db_session: AsyncSession) -> None:
    um = await _conta(db_session, 3, Nivel.ADMIN)
    outro = await _conta(db_session, 4, Nivel.ADMIN)

    with pytest.raises(servico.NaoManda, match="mesmo nível"):
        await servico.definir_nivel(db_session, quem=um, alvo=outro, nivel=Nivel.JOGADOR)


@asincrono
async def test_nao_da_para_mexer_em_si_mesmo(db_session: AsyncSession) -> None:
    admin = await _conta(db_session, 5, Nivel.ADMIN)
    with pytest.raises(servico.NaoManda, match="próprio"):
        await servico.definir_nivel(db_session, quem=admin, alvo=admin, nivel=Nivel.DONO)


@asincrono
async def test_nao_da_para_promover_alguem_ao_proprio_nivel(db_session: AsyncSession) -> None:
    """Senão o promovido pode rebaixar quem o promoveu no minuto seguinte."""
    admin = await _conta(db_session, 6, Nivel.ADMIN)
    jogador = await _conta(db_session, 7)

    with pytest.raises(servico.NaoManda, match="abaixo do seu"):
        await servico.definir_nivel(db_session, quem=admin, alvo=jogador, nivel=Nivel.ADMIN)


@asincrono
async def test_moderador_gerencia_quem_esta_abaixo(db_session: AsyncSession) -> None:
    moderador = await _conta(db_session, 8, Nivel.MODERADOR)
    jogador = await _conta(db_session, 9)

    await servico.definir_nivel(db_session, quem=moderador, alvo=jogador, nivel=Nivel.ORGANIZADOR)
    assert servico.nivel_de(jogador) is Nivel.ORGANIZADOR


@asincrono
async def test_nao_da_para_conceder_o_que_nao_se_tem(db_session: AsyncSession) -> None:
    """Sem isto a hierarquia é decorativa: basta se dar o que falta."""
    moderador = await _conta(db_session, 10, Nivel.MODERADOR)
    jogador = await _conta(db_session, 11)

    with pytest.raises(servico.NaoManda, match="não tem"):
        await servico.ajustar_permissao(
            db_session,
            quem=moderador,
            alvo=jogador,
            permissao=Permissao.CAMPEONATOS_IMPORTAR,
            estado=True,
        )


@asincrono
async def test_ajuste_volta_ao_padrao_quando_estado_e_none(db_session: AsyncSession) -> None:
    dono = await _conta(db_session, 12, Nivel.DONO)
    alvo = await _conta(db_session, 13, Nivel.ORGANIZADOR)

    await servico.ajustar_permissao(
        db_session,
        quem=dono,
        alvo=alvo,
        permissao=Permissao.CAMPEONATOS_PLACAR,
        estado=False,
    )
    acesso = await servico.acesso_de(db_session, alvo)
    assert Permissao.CAMPEONATOS_PLACAR not in acesso.permissoes

    await servico.ajustar_permissao(
        db_session,
        quem=dono,
        alvo=alvo,
        permissao=Permissao.CAMPEONATOS_PLACAR,
        estado=None,
    )
    acesso = await servico.acesso_de(db_session, alvo)
    assert Permissao.CAMPEONATOS_PLACAR in acesso.permissoes
    assert not acesso.revogadas


@asincrono
async def test_is_superuser_acompanha_o_nivel(db_session: AsyncSession) -> None:
    """A coluna é usada em consulta; duas fontes de verdade divergiriam."""
    dono = await _conta(db_session, 14, Nivel.DONO)
    alvo = await _conta(db_session, 15)

    await servico.definir_nivel(db_session, quem=dono, alvo=alvo, nivel=Nivel.ADMIN)
    assert alvo.is_superuser is True

    await servico.definir_nivel(db_session, quem=dono, alvo=alvo, nivel=Nivel.MODERADOR)
    assert alvo.is_superuser is False


@asincrono
async def test_dono_transfere_a_posse(db_session: AsyncSession) -> None:
    """Sem isto a instalação fica presa na primeira conta para sempre."""
    dono = await _conta(db_session, 16, Nivel.DONO)
    sucessor = await _conta(db_session, 17)

    await servico.definir_nivel(db_session, quem=dono, alvo=sucessor, nivel=Nivel.DONO)
    assert servico.nivel_de(sucessor) is Nivel.DONO

    # Com dois donos, rebaixar um é legítimo.
    await servico.definir_nivel(db_session, quem=sucessor, alvo=dono, nivel=Nivel.ADMIN)
    assert servico.nivel_de(dono) is Nivel.ADMIN


@asincrono
async def test_o_ultimo_dono_nao_pode_ser_rebaixado(db_session: AsyncSession) -> None:
    """Sem dono ninguém alcança o topo da escada de novo pela tela.

    A invariante se sustenta pela estrutura, e é isso que este teste afirma:
    com um dono só, **nenhum caminho** o rebaixa. Ele mesmo esbarra na regra de
    não mexer na própria conta; qualquer outra pessoa esbarra na hierarquia; e
    outro dono, que poderia, não existe — é justamente o que "último" quer
    dizer. A checagem explícita no serviço fica como rede, para o dia em que
    alguma dessas três deixar de valer.
    """
    dono = await _conta(db_session, 40, Nivel.DONO)
    segundo = await _conta(db_session, 41, Nivel.DONO)
    admin = await _conta(db_session, 45, Nivel.ADMIN)

    await servico.definir_nivel(db_session, quem=dono, alvo=segundo, nivel=Nivel.ADMIN)
    assert await servico.contar_donos(db_session) == 1

    with pytest.raises(servico.NaoManda, match="mesmo nível"):
        await servico.definir_nivel(db_session, quem=admin, alvo=dono, nivel=Nivel.JOGADOR)

    with pytest.raises(servico.NaoManda, match="próprio"):
        await servico.definir_nivel(db_session, quem=dono, alvo=dono, nivel=Nivel.JOGADOR)

    assert await servico.contar_donos(db_session) == 1


# -------------------------------------------------------------------- grupos


@asincrono
async def test_grupo_nao_pode_carregar_permissao_que_quem_cria_nao_tem(
    db_session: AsyncSession,
) -> None:
    """Senão bastava criar o grupo e entrar nele para se promover."""
    dono = await _conta(db_session, 44, Nivel.DONO)
    moderador = await _conta(db_session, 19, Nivel.MODERADOR)

    # Ele ganha só o direito de mexer em grupo, e nada além disso.
    await servico.ajustar_permissao(
        db_session,
        quem=dono,
        alvo=moderador,
        permissao=Permissao.GRUPOS_GERENCIAR,
        estado=True,
    )

    with pytest.raises(servico.NaoManda, match="não tem"):
        await servico.criar_grupo(
            db_session,
            quem=moderador,
            nome="Atalho",
            permissoes=[str(Permissao.CAMPEONATOS_IMPORTAR)],
        )


@asincrono
async def test_nao_da_para_atribuir_grupo_com_permissao_que_nao_se_tem(
    db_session: AsyncSession,
) -> None:
    moderador = await _conta(db_session, 20, Nivel.MODERADOR)
    jogador = await _conta(db_session, 21)

    grupo = PermissionGroup(
        slug="curadoria-teste",
        name="Curadoria",
        permissions=[str(Permissao.CAMPEONATOS_IMPORTAR)],
    )
    db_session.add(grupo)
    await db_session.flush()

    with pytest.raises(servico.NaoManda, match="não pode atribuí-lo"):
        await servico.definir_grupos(
            db_session, quem=moderador, alvo=jogador, slugs=["curadoria-teste"]
        )


@asincrono
async def test_grupo_da_plataforma_nao_e_apagavel(db_session: AsyncSession) -> None:
    """Apagar o grupo que dá acesso ao painel não teria volta pela tela."""
    dono = await _conta(db_session, 22, Nivel.DONO)
    grupo = PermissionGroup(slug="sistema-teste", name="Sistema", is_system=True)
    db_session.add(grupo)
    await db_session.flush()

    with pytest.raises(servico.PermissaoError, match="não pode ser apagado"):
        await servico.apagar_grupo(db_session, quem=dono, grupo=grupo)


@asincrono
async def test_grupo_soma_permissao_ao_nivel(db_session: AsyncSession) -> None:
    dono = await _conta(db_session, 23, Nivel.DONO)
    jogador = await _conta(db_session, 24)

    grupo = await servico.criar_grupo(
        db_session,
        quem=dono,
        nome="Curadoria do time",
        permissoes=[str(Permissao.CAMPEONATOS_IMPORTAR)],
    )
    await servico.definir_grupos(db_session, quem=dono, alvo=jogador, slugs=[grupo.slug])

    acesso = await servico.acesso_de(db_session, jogador)
    assert Permissao.CAMPEONATOS_IMPORTAR in acesso.permissoes
    assert acesso.grupos == (grupo.slug,)


# ---------------------------------------------------------------------- HTTP


@asincrono
async def test_painel_recusa_quem_nao_pode_ver(
    client: AsyncClient, db_session: AsyncSession
) -> None:
    jogador = await _conta(db_session, 25)
    await db_session.commit()

    resposta = await client.get("/api/v1/usuarios", headers=_cabecalho(jogador))
    assert resposta.status_code == 403


@asincrono
async def test_qualquer_conta_ve_o_proprio_acesso(
    client: AsyncClient, db_session: AsyncSession
) -> None:
    """Saber o que se pode fazer não é privilégio de ninguém."""
    jogador = await _conta(db_session, 26)
    await db_session.commit()

    resposta = await client.get("/api/v1/usuarios/eu", headers=_cabecalho(jogador))
    assert resposta.status_code == 200
    corpo = resposta.json()
    assert corpo["nivel"] == "jogador"
    assert str(Permissao.BOLOES_CRIAR) in corpo["permissoes"]


@asincrono
async def test_lista_diz_em_quem_da_para_mexer(
    client: AsyncClient, db_session: AsyncSession
) -> None:
    """A tela não recalcula hierarquia; ela lê `pode_gerenciar` daqui."""
    moderador = await _conta(db_session, 27, Nivel.MODERADOR)
    await _conta(db_session, 28)
    await _conta(db_session, 29, Nivel.ADMIN)
    await db_session.commit()

    resposta = await client.get("/api/v1/usuarios", headers=_cabecalho(moderador))
    assert resposta.status_code == 200

    por_email = {conta["email"]: conta for conta in resposta.json()}
    assert por_email["perm28@teste.local"]["pode_gerenciar"] is True
    assert por_email["perm29@teste.local"]["pode_gerenciar"] is False
    assert por_email["perm27@teste.local"]["pode_gerenciar"] is False


@asincrono
async def test_permissao_concedida_abre_endpoint_de_verdade(
    client: AsyncClient, db_session: AsyncSession
) -> None:
    """O painel não é enfeite: o ajuste muda o que a API aceita."""
    dono = await _conta(db_session, 30, Nivel.DONO)
    jogador = await _conta(db_session, 31)
    await db_session.commit()

    barrado = await client.post(
        "/api/v1/catalog/ligas/import",
        headers=_cabecalho(jogador),
        json={"slug": "premier-league"},
    )
    assert barrado.status_code == 403

    concedido = await client.patch(
        f"/api/v1/usuarios/{jogador.id}/permissao",
        headers=_cabecalho(dono),
        json={"permissao": str(Permissao.CAMPEONATOS_IMPORTAR), "estado": True},
    )
    assert concedido.status_code == 200

    passou = await client.post(
        "/api/v1/catalog/ligas/import",
        headers=_cabecalho(jogador),
        json={"slug": "liga-que-nao-existe"},
    )
    # Passou da permissão e parou na validação do pedido — que é o esperado.
    assert passou.status_code != 403


@asincrono
async def test_conta_desativada_perde_o_acesso(
    client: AsyncClient, db_session: AsyncSession
) -> None:
    dono = await _conta(db_session, 32, Nivel.DONO)
    alvo = await _conta(db_session, 33)
    await db_session.commit()

    desativa = await client.patch(
        f"/api/v1/usuarios/{alvo.id}/acesso",
        headers=_cabecalho(dono),
        json={"ativa": False},
    )
    assert desativa.status_code == 200

    resposta = await client.get("/api/v1/usuarios/eu", headers=_cabecalho(alvo))
    assert resposta.status_code == 401


@asincrono
async def test_dono_nao_promove_ninguem_a_dev(db_session: AsyncSession) -> None:
    """Senão o dono teria caminho para subir ao nível acima do próprio."""
    dono = await _conta(db_session, 50, Nivel.DONO)
    alvo = await _conta(db_session, 51, Nivel.ADMIN)

    with pytest.raises(servico.NaoManda, match="abaixo do seu"):
        await servico.definir_nivel(db_session, quem=dono, alvo=alvo, nivel=Nivel.DEV)


@asincrono
async def test_dono_nao_mexe_em_quem_e_dev(db_session: AsyncSession) -> None:
    dono = await _conta(db_session, 52, Nivel.DONO)
    dev = await _conta(db_session, 53, Nivel.DEV)

    with pytest.raises(servico.NaoManda, match="mesmo nível que você ou acima"):
        await servico.definir_nivel(db_session, quem=dono, alvo=dev, nivel=Nivel.JOGADOR)


@asincrono
async def test_dev_transfere_a_propria_posicao(db_session: AsyncSession) -> None:
    dev = await _conta(db_session, 54, Nivel.DEV)
    sucessor = await _conta(db_session, 55)

    await servico.definir_nivel(db_session, quem=dev, alvo=sucessor, nivel=Nivel.DEV)
    assert servico.nivel_de(sucessor) is Nivel.DEV

    await servico.definir_nivel(db_session, quem=sucessor, alvo=dev, nivel=Nivel.DONO)
    assert servico.nivel_de(dev) is Nivel.DONO


@asincrono
async def test_o_ultimo_de_cada_topo_e_protegido(db_session: AsyncSession) -> None:
    """Sem ninguém naquele topo, o nível fica inalcançável pela tela.

    A proteção vem da estrutura, e é isso que este teste afirma: com um dev só,
    nenhum caminho o rebaixa. Ele mesmo esbarra na regra de não mexer na própria
    conta; todo o resto esbarra na hierarquia; e outro dev, que poderia, não
    existe — é o que "último" quer dizer. A contagem explícita no serviço fica
    como rede, para o dia em que alguma dessas deixar de valer.
    """
    dev = await _conta(db_session, 56, Nivel.DEV)
    segundo = await _conta(db_session, 57, Nivel.DEV)

    await servico.definir_nivel(db_session, quem=dev, alvo=segundo, nivel=Nivel.DONO)
    assert await servico.contar_no_nivel(db_session, Nivel.DEV) == 1

    with pytest.raises(servico.NaoManda, match="mesmo nível que você ou acima"):
        await servico.definir_nivel(db_session, quem=segundo, alvo=dev, nivel=Nivel.ADMIN)

    with pytest.raises(servico.NaoManda, match="próprio"):
        await servico.definir_nivel(db_session, quem=dev, alvo=dev, nivel=Nivel.ADMIN)

    assert await servico.contar_no_nivel(db_session, Nivel.DEV) == 1


@asincrono
async def test_a_propria_linha_nunca_vem_gerenciavel(
    client: AsyncClient, db_session: AsyncSession
) -> None:
    """O painel não pode oferecer um botão que a API recusa.

    Os dois topos mandam no próprio NÍVEL — é assim que a posição se transfere.
    Mas mandar em quem é igual não é mandar em si: a própria conta continua
    fora de alcance, e a lista precisa dizer isso, senão o seletor de nível
    aparece na sua linha e devolve 403 ao ser usado.
    """
    for marca, nivel in ((60, Nivel.DEV), (61, Nivel.DONO), (62, Nivel.MODERADOR)):
        quem = await _conta(db_session, marca, nivel)
        await db_session.commit()

        resposta = await client.get("/api/v1/usuarios", headers=_cabecalho(quem))
        assert resposta.status_code == 200

        minha = next(c for c in resposta.json() if c["id"] == quem.id)
        assert minha["pode_gerenciar"] is False, f"{nivel} viu a própria linha como editável"
        assert minha["niveis_possiveis"] == []
