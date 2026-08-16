"""Lê e altera quem pode o quê.

O vocabulário e as regras de comparação estão em ``app.core.permissoes``, que é
puro. Aqui mora o que toca o banco.

Duas invariantes que este módulo mantém, e que nenhum endpoint deve reimplementar:

1. **Ninguém mexe em quem não está abaixo de si.** Vale para nível, permissão,
   grupo e desativação. Sem isso dois administradores se rebaixam mutuamente.
2. **Ninguém concede o que não tem.** Senão a hierarquia é decorativa: bastaria
   um moderador se dar `usuarios.gerenciar` para virar administrador.

E uma que o banco não garante sozinho: **a instalação nunca fica sem dono.**
"""

from __future__ import annotations

from collections.abc import Sequence
from dataclasses import dataclass

from slugify import slugify
from sqlalchemy import delete, func, select
from sqlalchemy.ext.asyncio import AsyncSession

from app.core.logging import get_logger
from app.core.permissoes import (
    NIVEIS_DE_ADMINISTRACAO,
    ROTULOS,
    TOPOS,
    Nivel,
    Permissao,
    efetivas,
    manda_em,
)
from app.models import (
    AuditAction,
    AuditLog,
    PermissionGroup,
    User,
    UserGroup,
    UserPermission,
)

log = get_logger(__name__)


class PermissaoError(Exception):
    """Regra de permissão violada. A mensagem vai para a tela."""


class NaoManda(PermissaoError):
    """Quem pediu não está acima de quem seria afetado."""


def nivel_de(user: User) -> Nivel:
    """Nível da conta, tolerante a valor desconhecido no banco.

    Um `nivel` que não existe mais no código — porque alguém renomeou, ou
    porque o banco veio de uma versão futura — vira `JOGADOR`, o mais restrito.
    Falhar fechado aqui é a diferença entre "essa tela não abre" e "essa conta
    virou administradora por acidente".
    """
    try:
        return Nivel(user.nivel)
    except ValueError:
        log.warning("permissoes.nivel_desconhecido", user=user.id, valor=user.nivel)
        return Nivel.JOGADOR


@dataclass(frozen=True, slots=True)
class Acesso:
    """Foto do que uma conta pode, com a origem de cada coisa.

    A origem importa na tela: sem ela, o painel mostra caixas marcadas e ninguém
    sabe se aquilo veio do nível, de um grupo ou de um ajuste — e portanto o que
    acontece ao desmarcar.
    """

    nivel: Nivel
    permissoes: frozenset[Permissao]
    grupos: tuple[str, ...]
    concedidas: frozenset[Permissao]
    revogadas: frozenset[Permissao]

    def pode(self, permissao: Permissao) -> bool:
        return permissao in self.permissoes


def _conhecidas(chaves: Sequence[str]) -> frozenset[Permissao]:
    """Filtra o que não é permissão conhecida em vez de explodir.

    Uma chave órfã no banco — permissão removida numa versão nova — não pode
    derrubar o carregamento da sessão de todo mundo.
    """
    validas = set()
    for chave in chaves:
        try:
            validas.add(Permissao(chave))
        except ValueError:
            log.warning("permissoes.chave_desconhecida", chave=chave)
    return frozenset(validas)


async def acesso_de(session: AsyncSession, user: User) -> Acesso:
    """Monta o acesso efetivo de uma conta. Três consultas, sempre."""
    nivel = nivel_de(user)

    grupos = (
        await session.execute(
            select(PermissionGroup.slug, PermissionGroup.permissions)
            .join(UserGroup, UserGroup.group_id == PermissionGroup.id)
            .where(UserGroup.user_id == user.id)
            .order_by(PermissionGroup.name)
        )
    ).all()

    ajustes = (
        await session.execute(
            select(UserPermission.permission, UserPermission.granted).where(
                UserPermission.user_id == user.id
            )
        )
    ).all()

    de_grupos = _conhecidas([chave for _, lista in grupos for chave in (lista or [])])
    concedidas = _conhecidas([chave for chave, granted in ajustes if granted])
    revogadas = _conhecidas([chave for chave, granted in ajustes if not granted])

    return Acesso(
        nivel=nivel,
        permissoes=efetivas(nivel, de_grupos=de_grupos, concedidas=concedidas, revogadas=revogadas),
        grupos=tuple(slug for slug, _ in grupos),
        concedidas=concedidas,
        revogadas=revogadas,
    )


async def pode(session: AsyncSession, user: User, permissao: Permissao) -> bool:
    return (await acesso_de(session, user)).pode(permissao)


# --------------------------------------------------------------------- mexer


def motivo_para_nao_gerenciar(
    *, de_quem: Acesso, quem_id: int, do_alvo: Acesso, alvo_id: int, nome_do_alvo: str = ""
) -> str | None:
    """Por que ``quem`` não pode mexer em ``alvo`` — ou ``None`` se pode.

    Uma implementação só, dois usos: a recusa (que precisa da frase) e o
    ``pode_gerenciar`` que o painel recebe pronto. Duplicar isso deixaria a tela
    oferecendo um botão que a API recusa — que é justamente o que o painel
    existe para não fazer.
    """
    if not de_quem.pode(Permissao.USUARIOS_GERENCIAR):
        return "você não pode gerenciar contas"

    if quem_id == alvo_id:
        # Mexer na própria conta é o caminho mais curto para se trancar para
        # fora — e para se promover, se a checagem de nível fosse frouxa.
        # Vale inclusive para os topos, que mandam no próprio nível: mandar em
        # quem é igual não é o mesmo que mandar em si.
        return "você não pode alterar o próprio nível ou permissões"

    if not manda_em(de_quem.nivel, do_alvo.nivel):
        quem_e = nome_do_alvo or "essa conta"
        return f"{quem_e} está no mesmo nível que você ou acima; só quem está acima pode alterar"

    return None


def pode_gerenciar(*, de_quem: Acesso, quem_id: int, do_alvo: Acesso, alvo_id: int) -> bool:
    return (
        motivo_para_nao_gerenciar(
            de_quem=de_quem, quem_id=quem_id, do_alvo=do_alvo, alvo_id=alvo_id
        )
        is None
    )


async def _exigir_comando(
    session: AsyncSession, *, quem: User, alvo: User
) -> tuple[Acesso, Acesso]:
    """Confere que ``quem`` está acima de ``alvo`` e pode gerenciar contas."""
    de_quem = await acesso_de(session, quem)
    do_alvo = await acesso_de(session, alvo)

    motivo = motivo_para_nao_gerenciar(
        de_quem=de_quem,
        quem_id=quem.id,
        do_alvo=do_alvo,
        alvo_id=alvo.id,
        nome_do_alvo=alvo.display_name,
    )
    if motivo is not None:
        raise NaoManda(motivo)

    return de_quem, do_alvo


def _sincronizar_superuser(user: User, nivel: Nivel) -> None:
    """Mantém ``is_superuser`` em acordo com o nível.

    A coluna continua existindo porque é usada em consulta e por código que veio
    antes da hierarquia. Ela nunca é escrita em outro lugar.
    """
    user.nivel = str(nivel)
    user.is_superuser = nivel in NIVEIS_DE_ADMINISTRACAO


async def contar_no_nivel(session: AsyncSession, nivel: Nivel) -> int:
    total = await session.scalar(
        select(func.count()).select_from(User).where(User.nivel == str(nivel))
    )
    return int(total or 0)


async def contar_donos(session: AsyncSession) -> int:
    """Mantido pelo nome antigo; hoje é um caso de :func:`contar_no_nivel`."""
    return await contar_no_nivel(session, Nivel.DONO)


async def definir_nivel(session: AsyncSession, *, quem: User, alvo: User, nivel: Nivel) -> User:
    """Move alguém na escada."""
    de_quem, _ = await _exigir_comando(session, quem=quem, alvo=alvo)

    if not manda_em(de_quem.nivel, nivel):
        raise NaoManda(
            "você só pode atribuir níveis abaixo do seu — "
            "promover alguém ao seu nível criaria quem pudesse rebaixar você"
        )

    anterior = nivel_de(alvo)
    if anterior is nivel:
        return alvo

    # Ceder a posição é caso legítimo; a instalação ficar sem ninguém naquele
    # topo não é — sem ele, ninguém alcança o nível de novo pela tela.
    if anterior in TOPOS and await contar_no_nivel(session, anterior) <= 1:
        rotulo = ROTULOS[anterior][0].lower()
        raise PermissaoError(
            f"esta é a única conta com nível {rotulo} da instalação. "
            f"Promova outra pessoa a {rotulo} antes."
        )

    _sincronizar_superuser(alvo, nivel)
    session.add(
        AuditLog(
            action=AuditAction.USER_LEVEL_CHANGED,
            actor_user_id=quem.id,
            entity="user",
            entity_id=alvo.id,
            payload={"de": str(anterior), "para": str(nivel)},
        )
    )
    await session.flush()
    log.info("permissoes.nivel", alvo=alvo.id, de=str(anterior), para=str(nivel), por=quem.id)
    return alvo


async def ajustar_permissao(
    session: AsyncSession,
    *,
    quem: User,
    alvo: User,
    permissao: Permissao,
    estado: bool | None,
    motivo: str = "",
) -> Acesso:
    """Concede, revoga ou volta ao padrão do nível.

    ``estado=None`` apaga o ajuste — a permissão volta a ser o que o nível e os
    grupos dizem. É a terceira opção que falta em quase todo painel de
    permissão, e sem ela não há como desfazer um ajuste sem adivinhar o padrão.
    """
    de_quem, _ = await _exigir_comando(session, quem=quem, alvo=alvo)

    if not de_quem.pode(permissao):
        raise NaoManda(
            "você não pode conceder uma permissão que não tem — "
            "senão a hierarquia não significaria nada"
        )

    existente = await session.scalar(
        select(UserPermission).where(
            UserPermission.user_id == alvo.id, UserPermission.permission == str(permissao)
        )
    )

    if estado is None:
        if existente is not None:
            await session.delete(existente)
    elif existente is not None:
        existente.granted = estado
        existente.granted_by_id = quem.id
        existente.reason = motivo.strip()[:500]
    else:
        session.add(
            UserPermission(
                user_id=alvo.id,
                permission=str(permissao),
                granted=estado,
                granted_by_id=quem.id,
                reason=motivo.strip()[:500],
            )
        )

    await session.flush()
    log.info(
        "permissoes.ajuste",
        alvo=alvo.id,
        permissao=str(permissao),
        estado=estado,
        por=quem.id,
    )
    return await acesso_de(session, alvo)


async def definir_grupos(
    session: AsyncSession, *, quem: User, alvo: User, slugs: Sequence[str]
) -> Acesso:
    """Troca os grupos de alguém pelos informados."""
    de_quem, _ = await _exigir_comando(session, quem=quem, alvo=alvo)

    pedidos = list(dict.fromkeys(slug.strip().lower() for slug in slugs if slug.strip()))
    grupos = (
        (
            await session.scalars(select(PermissionGroup).where(PermissionGroup.slug.in_(pedidos)))
        ).all()
        if pedidos
        else []
    )

    achados = {grupo.slug for grupo in grupos}
    faltando = [slug for slug in pedidos if slug not in achados]
    if faltando:
        raise PermissaoError(f"grupo não encontrado: {faltando[0]}")

    # Um grupo carrega permissões; entregá-lo é conceder cada uma delas.
    for grupo in grupos:
        for permissao in _conhecidas(grupo.permissions or []):
            if not de_quem.pode(permissao):
                raise NaoManda(
                    f'o grupo "{grupo.name}" dá uma permissão que você não tem '
                    f"({permissao}), então você não pode atribuí-lo"
                )

    await session.execute(delete(UserGroup).where(UserGroup.user_id == alvo.id))
    for grupo in grupos:
        session.add(UserGroup(user_id=alvo.id, group_id=grupo.id))

    await session.flush()
    log.info("permissoes.grupos", alvo=alvo.id, grupos=sorted(achados), por=quem.id)
    return await acesso_de(session, alvo)


async def definir_ativa(session: AsyncSession, *, quem: User, alvo: User, ativa: bool) -> User:
    """Liga ou desliga o acesso de alguém, sem apagar o histórico.

    Desativar em vez de apagar é escolha de produto: a conta apagada levaria
    junto os palpites e o ranking passado, e uma temporada inteira mudaria de
    números porque alguém saiu do grupo.
    """
    await _exigir_comando(session, quem=quem, alvo=alvo)

    if alvo.is_active == ativa:
        return alvo

    alvo.is_active = ativa
    session.add(
        AuditLog(
            action=AuditAction.USER_ACCESS_CHANGED,
            actor_user_id=quem.id,
            entity="user",
            entity_id=alvo.id,
            payload={"ativa": ativa},
        )
    )
    await session.flush()
    return alvo


# -------------------------------------------------------------------- grupos


async def listar_grupos(session: AsyncSession) -> Sequence[PermissionGroup]:
    return (await session.scalars(select(PermissionGroup).order_by(PermissionGroup.name))).all()


async def criar_grupo(
    session: AsyncSession,
    *,
    quem: User,
    nome: str,
    descricao: str = "",
    permissoes: Sequence[str] = (),
) -> PermissionGroup:
    de_quem = await acesso_de(session, quem)
    if not de_quem.pode(Permissao.GRUPOS_GERENCIAR):
        raise NaoManda("você não pode gerenciar grupos")

    titulo = nome.strip()
    if len(titulo) < 3:
        raise PermissaoError("dê um nome de pelo menos 3 letras ao grupo")

    escolhidas = _conhecidas(list(permissoes))
    negadas = escolhidas - de_quem.permissoes
    if negadas:
        raise NaoManda(f"você não pode pôr num grupo uma permissão que não tem ({min(negadas)})")

    slug = slugify(titulo)[:48] or "grupo"
    if await session.scalar(select(PermissionGroup.id).where(PermissionGroup.slug == slug)):
        raise PermissaoError(f'já existe um grupo com o nome "{titulo}"')

    grupo = PermissionGroup(
        slug=slug,
        name=titulo[:80],
        description=descricao.strip()[:500],
        permissions=sorted(str(item) for item in escolhidas),
    )
    session.add(grupo)
    await session.flush()
    return grupo


async def editar_grupo(
    session: AsyncSession,
    *,
    quem: User,
    grupo: PermissionGroup,
    nome: str | None = None,
    descricao: str | None = None,
    permissoes: Sequence[str] | None = None,
) -> PermissionGroup:
    de_quem = await acesso_de(session, quem)
    if not de_quem.pode(Permissao.GRUPOS_GERENCIAR):
        raise NaoManda("você não pode gerenciar grupos")

    if nome is not None and nome.strip():
        grupo.name = nome.strip()[:80]
    if descricao is not None:
        grupo.description = descricao.strip()[:500]

    if permissoes is not None:
        escolhidas = _conhecidas(list(permissoes))
        negadas = escolhidas - de_quem.permissoes
        if negadas:
            raise NaoManda(
                f"você não pode pôr num grupo uma permissão que não tem ({min(negadas)})"
            )
        grupo.permissions = sorted(str(item) for item in escolhidas)

    await session.flush()
    return grupo


async def apagar_grupo(session: AsyncSession, *, quem: User, grupo: PermissionGroup) -> None:
    de_quem = await acesso_de(session, quem)
    if not de_quem.pode(Permissao.GRUPOS_GERENCIAR):
        raise NaoManda("você não pode gerenciar grupos")

    if grupo.is_system:
        raise PermissaoError(
            f'"{grupo.name}" é um grupo da própria plataforma e não pode ser apagado. '
            "Se ele não serve, tire as permissões dele."
        )

    await session.delete(grupo)
    await session.flush()
