"""Enfileiramento e entrega de notificações.

Regras que valem para todo canal, sem exceção:

- **Deduplicação** por ``(membership, template, reference_id)``. O job pode
  rodar de novo sem transformar "sua rodada fechou" em cinco mensagens.
- **Opt-out por usuário**, por canal.
- **Silêncio noturno**: nada sai entre ``quiet_hours_start`` e
  ``quiet_hours_end`` no fuso do destinatário. Bolão entre amigos não justifica
  acordar ninguém às 2h.
- **Retry com recuo exponencial**, e só para falha que vale retentar. Usuário
  que bloqueou o bot não é retentável.
"""

from __future__ import annotations

import json
from collections.abc import Sequence
from datetime import UTC, datetime, timedelta
from zoneinfo import ZoneInfo

from sqlalchemy import select
from sqlalchemy.exc import IntegrityError
from sqlalchemy.ext.asyncio import AsyncSession

from app.core.config import settings
from app.core.logging import get_logger
from app.models import Membership, Notification, NotificationStatus, PushSubscription, User
from app.providers.notifications import (
    NotificationChannel,
    NotificationMessage,
    build_channels,
)

log = get_logger(__name__)

MAX_ATTEMPTS = 5


TEMPLATES: dict[str, tuple[str, str]] = {
    "round_open": ("Rodada aberta", "A {round} do {pool} está aberta para palpites."),
    "prediction_reminder": (
        "Faltam seus palpites",
        "Você ainda não palpitou {pending} jogo(s) da {round} no {pool}. Fecha em {deadline}.",
    ),
    "palpites_do_dia": (
        "Tem jogo hoje",
        "Você ainda não palpitou {pending} jogo(s) de hoje no {pool}.",
    ),
    "ultima_chamada": (
        "Última chamada",
        "{fixture} começa em {minutes} minutos e você ainda não palpitou no {pool}.",
    ),
    "round_closed": ("Rodada fechada", "Os palpites da {round} no {pool} foram encerrados."),
    "fixture_settled": ("Jogo apurado", "{fixture} terminou {score}. Você fez {points} ponto(s)."),
    "round_settled": (
        "Rodada apurada",
        "A {round} do {pool} foi apurada. Você fez {points} ponto(s) e está em {position}º.",
    ),
    "position_changed": (
        "Você mexeu no ranking",
        "Você {direction} {places} posição(ões) no {pool} e está em {position}º.",
    ),
    "score_corrected": (
        "Placar corrigido",
        "O placar de {fixture} mudou e o ranking do {pool} foi recalculado.",
    ),
    "pool_finished": ("Bolão encerrado", "O {pool} acabou. Você terminou em {position}º."),
    "member_joined": ("Novo participante", "{name} entrou no {pool}."),
}


def render(template: str, payload: dict[str, object]) -> NotificationMessage:
    title, body = TEMPLATES.get(template, ("Aviso", "{detail}"))
    try:
        rendered_body = body.format(**payload)
    except KeyError as exc:
        log.warning("notificacao.payload_incompleto", template=template, faltando=str(exc))
        rendered_body = body
    return NotificationMessage(title=title, body=rendered_body, url=payload.get("url"))  # type: ignore[arg-type]


async def enqueue(
    session: AsyncSession,
    *,
    membership_id: int,
    template: str,
    reference_id: int = 0,
    payload: dict[str, object] | None = None,
    channel: str = "in_app",
) -> Notification | None:
    """Enfileira uma notificação. Devolve ``None`` se já existia (dedup).

    O ponto delicado é o que acontece quando a deduplicação dispara.
    ``session.rollback()`` desfaria a transação **inteira** — e quem chama esta
    função é a apuração, que já gravou pontuação, ranking e ``settled_at`` sem
    commitar. Uma notificação repetida (reapuração de placar corrigido é o caso
    comum) jogava fora a apuração toda, e ainda avisava o participante sobre uma
    subida de posição que não existia no banco.
    """
    notification = Notification(
        membership_id=membership_id,
        template=template,
        reference_id=reference_id,
        channel=channel,
        status=NotificationStatus.PENDING,
        payload=payload or {},
    )
    try:
        # O SAVEPOINT limita o estrago ao INSERT: se a chave de dedup bater, só
        # ele é desfeito, e o trabalho da transação externa continua de pé.
        async with session.begin_nested():
            session.add(notification)
            await session.flush()
    except IntegrityError:
        return None
    return notification


async def enqueue_many(
    session: AsyncSession,
    *,
    membership_ids: Sequence[int],
    template: str,
    reference_id: int = 0,
    payload: dict[str, object] | None = None,
) -> int:
    created = 0
    for membership_id in membership_ids:
        if await enqueue(
            session,
            membership_id=membership_id,
            template=template,
            reference_id=reference_id,
            payload=payload,
        ):
            created += 1
    return created


def within_quiet_hours(user: User, now: datetime) -> bool:
    """Está no horário de silêncio do usuário?

    A janela pode cruzar a meia-noite (23h → 8h), então não dá para comparar
    com um simples ``start <= hora < end``.
    """
    try:
        local_hour = now.astimezone(ZoneInfo(user.timezone)).hour
    except Exception:  # noqa: BLE001 - fuso inválido não pode travar o envio
        local_hour = now.astimezone(ZoneInfo("America/Sao_Paulo")).hour

    start, end = user.quiet_hours_start, user.quiet_hours_end
    if start == end:
        return False
    if start < end:
        return start <= local_hour < end
    return local_hour >= start or local_hour < end


def channels_for(user: User) -> list[str]:
    enabled = []
    if user.notify_in_app:
        enabled.append("in_app")
    # Sem `and tem inscrição` aqui de propósito: descobrir isso custa uma
    # consulta, e o despacho já precisa carregar as inscrições para montar o
    # endereço. Se não houver nenhuma, o canal responde "nenhum aparelho
    # inscrito" e o aviso segue pelos outros.
    if user.notify_push:
        enabled.append("push")
    if user.notify_telegram and user.telegram_chat_id:
        enabled.append("telegram")
    return enabled


async def inscricoes_de_push(session: AsyncSession, user_id: int) -> list[PushSubscription]:
    return list(
        (
            await session.scalars(
                select(PushSubscription).where(PushSubscription.user_id == user_id)
            )
        ).all()
    )


async def dispatch_pending(
    session: AsyncSession,
    *,
    limit: int = 100,
    now: datetime | None = None,
    channels: list[NotificationChannel] | None = None,
) -> dict[str, int]:
    """Entrega o que está na fila. Chamado pelo worker."""
    now = now or datetime.now(UTC)
    active = channels or build_channels(
        settings.notification_channels,
        telegram_bot_token=settings.telegram_bot_token,
        vapid_public_key=settings.vapid_public_key,
        vapid_private_key=settings.vapid_private_key,
        vapid_subject=settings.vapid_subject,
    )
    by_kind = {channel.kind: channel for channel in active}

    pending = (
        await session.scalars(
            select(Notification)
            .where(
                Notification.status == NotificationStatus.PENDING,
                Notification.attempts < MAX_ATTEMPTS,
            )
            .order_by(Notification.created_at)
            .limit(limit)
        )
    ).all()

    stats = {"sent": 0, "skipped": 0, "failed": 0, "deferred": 0}

    for notification in pending:
        membership = await session.get(Membership, notification.membership_id)
        user = await session.get(User, membership.user_id) if membership else None
        if user is None or not user.is_active:
            notification.status = NotificationStatus.SKIPPED
            notification.error = "destinatário indisponível"
            stats["skipped"] += 1
            continue

        wanted = channels_for(user)
        if not wanted:
            notification.status = NotificationStatus.SKIPPED
            notification.error = "usuário desativou as notificações"
            stats["skipped"] += 1
            continue

        # O in-app não acorda ninguém: fica no sininho até a pessoa abrir.
        if within_quiet_hours(user, now) and wanted != ["in_app"]:
            wanted = ["in_app"] if "in_app" in wanted else []
            if not wanted:
                stats["deferred"] += 1
                continue

        message = render(notification.template, notification.payload)
        delivered_any = False
        last_error = ""
        retryable = False

        for kind in wanted:
            channel = by_kind.get(kind)
            if channel is None:
                continue

            address: str | None = None
            if kind == "telegram":
                address = user.telegram_chat_id
            elif kind == "push":
                # A lista de aparelhos vai em JSON porque o provedor não fala
                # com o banco — quem tem sessão é este serviço.
                inscricoes = await inscricoes_de_push(session, user.id)
                address = json.dumps(
                    [
                        {"endpoint": i.endpoint, "p256dh": i.p256dh, "auth": i.auth}
                        for i in inscricoes
                    ]
                )

            outcome = await channel.send(address, message)

            # Aparelho que o navegador declarou morto sai da lista agora.
            # Mantê-lo faria toda notificação futura tentar entregar num
            # endereço que nunca mais vai responder.
            if outcome.expirados:
                await _apagar_inscricoes(session, outcome.expirados)

            if outcome.delivered:
                delivered_any = True
            else:
                last_error = outcome.detail
                retryable = retryable or outcome.retryable

        notification.attempts += 1
        if delivered_any:
            notification.status = NotificationStatus.SENT
            notification.sent_at = now
            notification.channel = ",".join(wanted)
            stats["sent"] += 1
        elif retryable and notification.attempts < MAX_ATTEMPTS:
            notification.error = last_error[:500]
            stats["deferred"] += 1
        else:
            notification.status = NotificationStatus.FAILED
            notification.error = last_error[:500]
            stats["failed"] += 1

    await session.flush()
    return stats


async def unread_for_user(
    session: AsyncSession, user_id: int, *, limit: int = 50
) -> Sequence[Notification]:
    return (
        await session.scalars(
            select(Notification)
            .join(Membership, Notification.membership_id == Membership.id)
            .where(Membership.user_id == user_id, Notification.read_at.is_(None))
            .order_by(Notification.created_at.desc())
            .limit(limit)
        )
    ).all()


async def mark_read(session: AsyncSession, user_id: int, notification_ids: Sequence[int]) -> int:
    rows = (
        await session.scalars(
            select(Notification)
            .join(Membership, Notification.membership_id == Membership.id)
            .where(
                Membership.user_id == user_id,
                Notification.id.in_(notification_ids),
                Notification.read_at.is_(None),
            )
        )
    ).all()
    now = datetime.now(UTC)
    for row in rows:
        row.read_at = now
    return len(rows)


def retry_delay(attempts: int) -> timedelta:
    """Recuo exponencial, com teto de uma hora."""
    return timedelta(seconds=min(3600, 30 * (2 ** max(0, attempts - 1))))


async def _apagar_inscricoes(session: AsyncSession, endpoints: Sequence[str]) -> int:
    """Tira da lista os aparelhos que o navegador encerrou (404/410)."""
    alvos = [e for e in endpoints if e]
    if not alvos:
        return 0

    mortas = (
        await session.scalars(select(PushSubscription).where(PushSubscription.endpoint.in_(alvos)))
    ).all()
    for inscricao in mortas:
        await session.delete(inscricao)
    if mortas:
        log.info("push.inscricoes_removidas", quantas=len(mortas))
    return len(mortas)
