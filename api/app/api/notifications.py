"""Central de avisos."""

from __future__ import annotations

from datetime import datetime

from fastapi import APIRouter
from pydantic import BaseModel

from app.api.deps import CurrentUser, SessionDep
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
