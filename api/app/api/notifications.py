"""Central de avisos."""

from __future__ import annotations

from datetime import datetime
from typing import Annotated

from fastapi import APIRouter, Header, HTTPException, status
from pydantic import BaseModel, Field
from sqlalchemy import select

from app.api.deps import CurrentUser, SessionDep
from app.core.config import settings
from app.models import PushSubscription
from app.schemas.common import Message
from app.services import notifications as notify_service

router = APIRouter(prefix="/notifications", tags=["notificações"])


class NotificationOut(BaseModel):
    id: int
    template: str
    title: str
    body: str
    payload: dict
    created_at: datetime
    read_at: datetime | None


class MarkReadRequest(BaseModel):
    ids: list[int]


@router.get("", response_model=list[NotificationOut])
async def list_unread(session: SessionDep, user: CurrentUser) -> list[NotificationOut]:
    rows = await notify_service.unread_for_user(session, user.id)
    result = []
    for row in rows:
        message = notify_service.render(row.template, row.payload)
        result.append(
            NotificationOut(
                id=row.id,
                template=row.template,
                title=message.title,
                body=message.body,
                payload=row.payload,
                created_at=row.created_at,
                read_at=row.read_at,
            )
        )
    return result


@router.post("/read", response_model=Message)
async def mark_read(payload: MarkReadRequest, session: SessionDep, user: CurrentUser) -> Message:
    count = await notify_service.mark_read(session, user.id, payload.ids)
    await session.commit()
    return Message(detail=f"{count} aviso(s) marcado(s) como lido(s)")


# ---------------------------------------------------------------------------
# Notificação do navegador (Web Push)
#
# É o canal que alcança quem NÃO abriu o app — e quem esqueceu de palpitar é
# exatamente essa pessoa. Nada aqui funciona sem HTTPS: o navegador se recusa a
# assinar em conexão insegura, então na instalação de rede local estes
# endpoints existem e respondem que o recurso está desligado, em vez de fingir.


class ChavePublicaOut(BaseModel):
    """O que o navegador precisa para assinar, e se vale a pena tentar."""

    disponivel: bool
    chave_publica: str = ""


class InscricaoPushIn(BaseModel):
    endpoint: str = Field(max_length=2000)
    p256dh: str = Field(max_length=255)
    auth: str = Field(max_length=255)


@router.get("/push/chave", response_model=ChavePublicaOut)
async def chave_de_push(user: CurrentUser) -> ChavePublicaOut:
    """A chave pública VAPID. Sem ela configurada, o recurso não existe."""
    disponivel = bool(settings.vapid_public_key and settings.vapid_private_key)
    return ChavePublicaOut(
        disponivel=disponivel,
        chave_publica=settings.vapid_public_key if disponivel else "",
    )


@router.post("/push/inscrever", response_model=Message)
async def inscrever_push(
    payload: InscricaoPushIn,
    session: SessionDep,
    user: CurrentUser,
    user_agent: Annotated[str | None, Header()] = None,
) -> Message:
    """Registra este aparelho. Reinscrever o mesmo aparelho ATUALIZA.

    O navegador devolve o mesmo `endpoint` para o mesmo aparelho, mas troca as
    chaves de cifra quando a assinatura é refeita — depois de limpar dados, por
    exemplo. Gravar uma linha nova a cada vez encheria a tabela de inscrições
    mortas que ainda parecem vivas, e a pessoa receberia o mesmo aviso várias
    vezes no mesmo celular.
    """
    if not (settings.vapid_public_key and settings.vapid_private_key):
        raise HTTPException(
            status_code=status.HTTP_409_CONFLICT,
            detail="esta instalação não tem notificação do navegador configurada",
        )

    existente = await session.scalar(
        select(PushSubscription).where(PushSubscription.endpoint == payload.endpoint)
    )
    if existente is not None:
        # Inclusive quando o dono muda: o aparelho é de quem está usando agora.
        existente.user_id = user.id
        existente.p256dh = payload.p256dh
        existente.auth = payload.auth
        existente.user_agent = (user_agent or "")[:255] or None
    else:
        session.add(
            PushSubscription(
                user_id=user.id,
                endpoint=payload.endpoint,
                p256dh=payload.p256dh,
                auth=payload.auth,
                user_agent=(user_agent or "")[:255] or None,
            )
        )

    await session.commit()
    return Message(detail="aparelho inscrito para receber avisos")


@router.post("/push/cancelar", response_model=Message)
async def cancelar_push(
    payload: InscricaoPushIn, session: SessionDep, user: CurrentUser
) -> Message:
    """Tira este aparelho da lista.

    Filtra pelo dono também: sem isso, conhecer o `endpoint` de outra pessoa
    bastaria para desligar os avisos dela.
    """
    inscricao = await session.scalar(
        select(PushSubscription).where(
            PushSubscription.endpoint == payload.endpoint,
            PushSubscription.user_id == user.id,
        )
    )
    if inscricao is not None:
        await session.delete(inscricao)
        await session.commit()
    return Message(detail="aparelho removido dos avisos")
