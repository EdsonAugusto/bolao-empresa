"""Painel de pessoas: quem tem conta, em que nível, com quais permissões.

Uma decisão que atravessa o arquivo inteiro: **o painel devolve, junto de cada
conta, o que quem está olhando pode fazer com ela** (``pode_gerenciar``, e a
lista de níveis atribuíveis). A alternativa — a tela deduzir isso comparando
níveis — colocaria a regra de hierarquia em dois lugares, e o dia em que os dois
divergirem a tela vai oferecer um botão que a API recusa.
"""

from __future__ import annotations

import secrets
from datetime import datetime
from typing import Annotated

from fastapi import APIRouter, File, HTTPException, Response, UploadFile, status
from pydantic import BaseModel, Field
from sqlalchemy import func, select

from app.api.deps import CurrentUser, Requer, SessionDep
from app.core.permissoes import (
    Nivel,
    Permissao,
    catalogo,
    escada,
    niveis_que_pode_conceder,
)
from app.core.security import hash_password
from app.data.avatares import catalogo as catalogo_de_avatares
from app.models import PermissionGroup, User
from app.schemas.common import Message
from app.services import auth as auth_service
from app.services import avatares as avatar_service
from app.services import permissoes as permissao_service

router = APIRouter(prefix="/usuarios", tags=["pessoas"])

PodeVer = Requer(Permissao.USUARIOS_VER)
PodeGerenciar = Requer(Permissao.USUARIOS_GERENCIAR)
PodeGrupos = Requer(Permissao.GRUPOS_GERENCIAR)


# --------------------------------------------------------------------- saída


class ContaOut(BaseModel):
    id: int
    email: str
    display_name: str
    nivel: str
    nivel_rotulo: str
    is_active: bool
    avatar_url: str | None = None
    titulos: int = 0
    permissoes: list[str]
    grupos: list[str]
    concedidas: list[str]
    """Permissões dadas à pessoa além do que o nível e os grupos trazem."""

    revogadas: list[str]
    """Permissões tiradas da pessoa apesar do nível e dos grupos."""

    pode_gerenciar: bool
    """Se **quem está olhando** pode alterar esta conta."""

    niveis_possiveis: list[str]
    """Níveis que quem está olhando pode atribuir a esta conta."""

    created_at: datetime
    last_login_at: datetime | None


class GrupoOut(BaseModel):
    id: int
    slug: str
    name: str
    description: str
    permissions: list[str]
    is_system: bool
    membros: int


class VocabularioOut(BaseModel):
    """O catálogo inteiro, para a tela desenhar o painel sem repetir texto."""

    niveis: list[dict]
    permissoes: list[dict]
    meu_nivel: str
    minhas_permissoes: list[str]


# -------------------------------------------------------------------- entrada


class NivelIn(BaseModel):
    nivel: Nivel


class PermissaoIn(BaseModel):
    permissao: Permissao
    estado: bool | None = Field(
        default=None,
        description="true concede, false revoga, ausente volta ao padrão do nível",
    )
    motivo: str = Field(default="", max_length=500)


class GruposIn(BaseModel):
    grupos: list[str] = Field(default_factory=list, max_length=30)


class AtivaIn(BaseModel):
    ativa: bool


class GrupoIn(BaseModel):
    nome: str = Field(min_length=3, max_length=80)
    descricao: str = Field(default="", max_length=500)
    permissoes: list[str] = Field(default_factory=list, max_length=60)


class GrupoPatch(BaseModel):
    nome: str | None = Field(default=None, max_length=80)
    descricao: str | None = Field(default=None, max_length=500)
    permissoes: list[str] | None = Field(default=None, max_length=60)


# ------------------------------------------------------------------ ajudantes


async def _conta_out(
    session: SessionDep, alvo: User, *, quem: permissao_service.Acesso, quem_id: int
) -> ContaOut:
    from app.core.permissoes import ROTULOS

    acesso = await permissao_service.acesso_de(session, alvo)

    # A MESMA função que a recusa usa. Recalcular aqui — nem que fosse a mesma
    # expressão — abriria espaço para o painel oferecer um botão que a API
    # recusa. Foi assim que a própria linha de quem olhava ganhou um seletor de
    # nível que devolvia 403 ao ser usado.
    manda = permissao_service.pode_gerenciar(
        de_quem=quem, quem_id=quem_id, do_alvo=acesso, alvo_id=alvo.id
    )

    return ContaOut(
        id=alvo.id,
        email=alvo.email,
        display_name=alvo.display_name,
        nivel=str(acesso.nivel),
        nivel_rotulo=ROTULOS[acesso.nivel][0],
        is_active=alvo.is_active,
        avatar_url=alvo.avatar_url,
        titulos=alvo.titulos,
        permissoes=sorted(str(item) for item in acesso.permissoes),
        grupos=list(acesso.grupos),
        concedidas=sorted(str(item) for item in acesso.concedidas),
        revogadas=sorted(str(item) for item in acesso.revogadas),
        pode_gerenciar=manda,
        niveis_possiveis=(
            [str(nivel) for nivel in niveis_que_pode_conceder(quem.nivel)] if manda else []
        ),
        created_at=alvo.created_at,
        last_login_at=alvo.last_login_at,
    )


async def _alvo(session: SessionDep, user_id: int) -> User:
    alvo = await session.get(User, user_id)
    if alvo is None:
        raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail="conta não encontrada")
    return alvo


def _erro(exc: permissao_service.PermissaoError) -> HTTPException:
    codigo = (
        status.HTTP_403_FORBIDDEN
        if isinstance(exc, permissao_service.NaoManda)
        else status.HTTP_400_BAD_REQUEST
    )
    return HTTPException(status_code=codigo, detail=str(exc))


# -------------------------------------------------------------------- rotas


@router.get("/vocabulario", response_model=VocabularioOut)
async def vocabulario(session: SessionDep, user: PodeVer) -> VocabularioOut:
    """Níveis e permissões existentes, mais o que quem chamou tem.

    A tela precisa das duas coisas juntas para saber o que desenhar cinza: uma
    permissão que quem está olhando não tem não pode ser oferecida a ninguém.
    """
    acesso = await permissao_service.acesso_de(session, user)
    return VocabularioOut(
        niveis=escada(),
        permissoes=catalogo(),
        meu_nivel=str(acesso.nivel),
        minhas_permissoes=sorted(str(item) for item in acesso.permissoes),
    )


@router.get("", response_model=list[ContaOut])
async def listar(
    session: SessionDep,
    user: PodeVer,
    busca: str = "",
    limite: int = 200,
) -> list[ContaOut]:
    consulta = select(User).order_by(User.display_name).limit(min(limite, 500))
    if busca.strip():
        alvo = f"%{busca.strip().lower()}%"
        consulta = consulta.where(
            func.lower(User.display_name).like(alvo) | func.lower(User.email).like(alvo)
        )

    contas = (await session.scalars(consulta)).all()
    quem = await permissao_service.acesso_de(session, user)
    return [await _conta_out(session, conta, quem=quem, quem_id=user.id) for conta in contas]


@router.get("/eu", response_model=ContaOut)
async def eu(session: SessionDep, user: CurrentUser) -> ContaOut:
    """O próprio acesso. Sem exigir permissão: todo mundo pode ver o que pode."""
    quem = await permissao_service.acesso_de(session, user)
    return await _conta_out(session, user, quem=quem, quem_id=user.id)


@router.patch("/{user_id}/nivel", response_model=ContaOut)
async def mudar_nivel(
    user_id: int, payload: NivelIn, session: SessionDep, user: PodeGerenciar
) -> ContaOut:
    alvo = await _alvo(session, user_id)
    try:
        await permissao_service.definir_nivel(session, quem=user, alvo=alvo, nivel=payload.nivel)
    except permissao_service.PermissaoError as exc:
        raise _erro(exc) from exc

    await session.commit()
    await session.refresh(alvo)
    return await _conta_out(
        session, alvo, quem=await permissao_service.acesso_de(session, user), quem_id=user.id
    )


@router.patch("/{user_id}/permissao", response_model=ContaOut)
async def mudar_permissao(
    user_id: int, payload: PermissaoIn, session: SessionDep, user: PodeGerenciar
) -> ContaOut:
    alvo = await _alvo(session, user_id)
    try:
        await permissao_service.ajustar_permissao(
            session,
            quem=user,
            alvo=alvo,
            permissao=payload.permissao,
            estado=payload.estado,
            motivo=payload.motivo,
        )
    except permissao_service.PermissaoError as exc:
        raise _erro(exc) from exc

    await session.commit()
    return await _conta_out(
        session, alvo, quem=await permissao_service.acesso_de(session, user), quem_id=user.id
    )


@router.put("/{user_id}/grupos", response_model=ContaOut)
async def mudar_grupos(
    user_id: int, payload: GruposIn, session: SessionDep, user: PodeGerenciar
) -> ContaOut:
    alvo = await _alvo(session, user_id)
    try:
        await permissao_service.definir_grupos(session, quem=user, alvo=alvo, slugs=payload.grupos)
    except permissao_service.PermissaoError as exc:
        raise _erro(exc) from exc

    await session.commit()
    return await _conta_out(
        session, alvo, quem=await permissao_service.acesso_de(session, user), quem_id=user.id
    )


@router.patch("/{user_id}/acesso", response_model=ContaOut)
async def mudar_acesso(
    user_id: int, payload: AtivaIn, session: SessionDep, user: PodeGerenciar
) -> ContaOut:
    """Liga ou desliga a entrada de alguém.

    Desativar, e não apagar: a conta apagada levaria junto palpites e ranking, e
    uma temporada inteira mudaria de números porque alguém saiu do grupo.
    """
    alvo = await _alvo(session, user_id)
    try:
        await permissao_service.definir_ativa(session, quem=user, alvo=alvo, ativa=payload.ativa)
    except permissao_service.PermissaoError as exc:
        raise _erro(exc) from exc

    await session.commit()
    await session.refresh(alvo)
    return await _conta_out(
        session, alvo, quem=await permissao_service.acesso_de(session, user), quem_id=user.id
    )


# -------------------------------------------------------------------- grupos


@router.get("/grupos/todos", response_model=list[GrupoOut])
async def listar_grupos(session: SessionDep, user: PodeVer) -> list[GrupoOut]:
    from app.models import UserGroup

    grupos = await permissao_service.listar_grupos(session)
    contagem = dict(
        (
            await session.execute(
                select(UserGroup.group_id, func.count()).group_by(UserGroup.group_id)
            )
        ).all()
    )
    return [
        GrupoOut(
            id=grupo.id,
            slug=grupo.slug,
            name=grupo.name,
            description=grupo.description,
            permissions=list(grupo.permissions or []),
            is_system=grupo.is_system,
            membros=int(contagem.get(grupo.id, 0)),
        )
        for grupo in grupos
    ]


@router.post("/grupos", response_model=GrupoOut, status_code=status.HTTP_201_CREATED)
async def criar_grupo(payload: GrupoIn, session: SessionDep, user: PodeGrupos) -> GrupoOut:
    try:
        grupo = await permissao_service.criar_grupo(
            session,
            quem=user,
            nome=payload.nome,
            descricao=payload.descricao,
            permissoes=payload.permissoes,
        )
    except permissao_service.PermissaoError as exc:
        raise _erro(exc) from exc

    await session.commit()
    return GrupoOut(
        id=grupo.id,
        slug=grupo.slug,
        name=grupo.name,
        description=grupo.description,
        permissions=list(grupo.permissions or []),
        is_system=grupo.is_system,
        membros=0,
    )


async def _grupo(session: SessionDep, grupo_id: int) -> PermissionGroup:
    grupo = await session.get(PermissionGroup, grupo_id)
    if grupo is None:
        raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail="grupo não encontrado")
    return grupo


@router.patch("/grupos/{grupo_id}", response_model=GrupoOut)
async def editar_grupo(
    grupo_id: int, payload: GrupoPatch, session: SessionDep, user: PodeGrupos
) -> GrupoOut:
    grupo = await _grupo(session, grupo_id)
    try:
        await permissao_service.editar_grupo(
            session,
            quem=user,
            grupo=grupo,
            nome=payload.nome,
            descricao=payload.descricao,
            permissoes=payload.permissoes,
        )
    except permissao_service.PermissaoError as exc:
        raise _erro(exc) from exc

    await session.commit()
    return GrupoOut(
        id=grupo.id,
        slug=grupo.slug,
        name=grupo.name,
        description=grupo.description,
        permissions=list(grupo.permissions or []),
        is_system=grupo.is_system,
        membros=0,
    )


@router.delete("/grupos/{grupo_id}", response_model=Message)
async def apagar_grupo(grupo_id: int, session: SessionDep, user: PodeGrupos) -> Message:
    grupo = await _grupo(session, grupo_id)
    try:
        await permissao_service.apagar_grupo(session, quem=user, grupo=grupo)
    except permissao_service.PermissaoError as exc:
        raise _erro(exc) from exc

    await session.commit()
    return Message(detail=f'grupo "{grupo.name}" apagado')


# ---------------------------------------------------------------------------
# Foto de perfil
# ---------------------------------------------------------------------------


class AvatarOut(BaseModel):
    id: str
    url: str


@router.get("/avatares", response_model=list[AvatarOut])
async def avatares_prontos(user: CurrentUser) -> list[AvatarOut]:
    """Os avatares desenhados pela plataforma, para o seletor do perfil."""
    return [AvatarOut(**item) for item in catalogo_de_avatares()]


@router.post("/avatar", response_model=Message)
async def enviar_avatar(
    session: SessionDep,
    user: CurrentUser,
    file: Annotated[UploadFile, File(description="Imagem de perfil")],
) -> Message:
    """Troca a foto de perfil por uma imagem enviada.

    O tipo sai da assinatura do arquivo, nunca do ``Content-Type`` que o
    navegador mandou — é entrada do cliente como qualquer outra, e confiar nela
    é como upload de imagem vira XSS armazenado.

    A foto anterior é apagada do disco: sem isso, cada troca deixaria a antiga
    ali para sempre, e um grupo grande enche o disco de retratos que ninguém vê.
    """
    dados = await file.read(avatar_service.MAX_BYTES + 1)
    try:
        nome, _formato = avatar_service.gravar(dados)
    except avatar_service.AvatarInvalido as exc:
        raise HTTPException(
            status_code=status.HTTP_422_UNPROCESSABLE_ENTITY, detail=str(exc)
        ) from exc

    anterior = user.avatar_url
    user.avatar_url = f"{avatar_service.PREFIXO_ENVIADO}{nome}"
    await session.commit()

    # Depois do commit: se a gravação falhar, a foto velha ainda é a válida.
    avatar_service.apagar(anterior)
    return Message(detail="foto de perfil atualizada")


@router.get("/avatar/{nome}")
async def ver_avatar(nome: str, user: CurrentUser) -> Response:
    """Serve uma foto enviada.

    Exige sessão porque a foto é de quem divide bolão, não da internet — e
    responde com `nosniff` mais uma CSP de sandbox, pelo mesmo motivo do anexo
    de relato: um arquivo que o navegador resolva interpretar como HTML rodaria
    na origem da plataforma.
    """
    try:
        caminho = avatar_service.caminho_de(nome)
    except avatar_service.AvatarInvalido as exc:
        raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail="não achei") from exc

    if not caminho.is_file():
        raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail="não achei")

    tipos = {"png": "image/png", "jpg": "image/jpeg", "gif": "image/gif", "webp": "image/webp"}
    return Response(
        content=caminho.read_bytes(),
        media_type=tipos.get(caminho.suffix.lstrip("."), "application/octet-stream"),
        headers={
            "X-Content-Type-Options": "nosniff",
            "Content-Security-Policy": "sandbox; default-src 'none'",
            # O nome tem UUID, então o conteúdo nunca muda para o mesmo nome.
            "Cache-Control": "private, max-age=86400",
        },
    )


# ---------------------------------------------------------------------------
# O que quem administra pode fazer na conta de outra pessoa
# ---------------------------------------------------------------------------


class SenhaIn(BaseModel):
    nova_senha: str | None = Field(
        default=None,
        min_length=10,
        max_length=128,
        description="Em branco, uma senha forte é gerada e devolvida uma vez.",
    )


class SenhaOut(BaseModel):
    detail: str
    senha: str | None = None
    """Só quando foi gerada aqui. Aparece uma vez e não é guardada em claro."""


@router.post("/{user_id}/senha", response_model=SenhaOut)
async def redefinir_senha(
    user_id: int, payload: SenhaIn, session: SessionDep, user: PodeGerenciar
) -> SenhaOut:
    """Redefine a senha de outra pessoa.

    Quem faz isso passa a conhecer a senha da conta, e por isso a conta nasce
    obrigada a trocá-la no próximo acesso: senha escolhida por um terceiro não
    pode continuar valendo depois que a dona entrar.

    Todas as sessões abertas daquela conta caem junto. Se a redefinição foi
    porque alguém perdeu o acesso, deixar as sessões vivas não resolveria nada;
    se foi porque alguém entrou indevidamente, deixá-las vivas manteria o
    invasor lá dentro.
    """
    alvo = await _alvo(session, user_id)

    motivo = permissao_service.motivo_para_nao_gerenciar(
        de_quem=await permissao_service.acesso_de(session, user),
        quem_id=user.id,
        do_alvo=await permissao_service.acesso_de(session, alvo),
        alvo_id=alvo.id,
        nome_do_alvo=alvo.display_name,
    )
    if motivo:
        raise HTTPException(status_code=status.HTTP_403_FORBIDDEN, detail=motivo)

    senha = payload.nova_senha or secrets.token_urlsafe(12)
    alvo.password_hash = hash_password(senha)
    alvo.must_change_password = True
    await auth_service.revoke_all_sessions(session, alvo.id)
    await session.commit()

    return SenhaOut(
        detail=f"senha de {alvo.display_name} redefinida; será pedida a troca no próximo acesso",
        senha=senha if payload.nova_senha is None else None,
    )


class TitulosIn(BaseModel):
    titulos: int = Field(ge=0, le=99)


@router.patch("/{user_id}/titulos", response_model=ContaOut)
async def definir_titulos(
    user_id: int, payload: TitulosIn, session: SessionDep, user: PodeGerenciar
) -> ContaOut:
    """Registra quantas vezes a pessoa foi campeã.

    Informado, não apurado: o bolão do grupo é mais antigo que a plataforma e
    esse histórico não está em banco nenhum. A plataforma apura o que acontece
    dentro dela; isto é a memória do que veio antes.
    """
    alvo = await _alvo(session, user_id)

    motivo = permissao_service.motivo_para_nao_gerenciar(
        de_quem=await permissao_service.acesso_de(session, user),
        quem_id=user.id,
        do_alvo=await permissao_service.acesso_de(session, alvo),
        alvo_id=alvo.id,
        nome_do_alvo=alvo.display_name,
    )
    if motivo:
        raise HTTPException(status_code=status.HTTP_403_FORBIDDEN, detail=motivo)

    alvo.titulos = payload.titulos
    await session.commit()
    await session.refresh(alvo)
    return await _conta_out(
        session, alvo, quem=await permissao_service.acesso_de(session, user), quem_id=user.id
    )
