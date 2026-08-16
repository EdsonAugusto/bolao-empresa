"""Canais de notificação.

Interface primeiro, implementação depois — e nenhum service importa um cliente
concreto. Trocar de canal é configuração, não refatoração.

Por que WhatsApp ficou de fora: a Cloud API oficial cobra por conversa, exige
template aprovado e **não envia mensagem para grupo**; as bibliotecas
não-oficiais mandam em grupo mas violam os termos de uso e derrubam o número.
Nenhuma das duas cabe em "custo zero, entre amigos".

O que sobrou, e por quê:

- ``InAppChannel`` — central de avisos dentro da própria plataforma. Sempre
  funciona, não depende de nada, e na LAN é o que as pessoas realmente veem.
- ``TelegramChannel`` — gratuito, e o envio é uma chamada HTTP de saída, então
  funciona atrás de qualquer roteador doméstico sem IP fixo nem webhook.
- ``LogChannel`` — desenvolvimento.
"""

from __future__ import annotations

import abc
from dataclasses import dataclass

import httpx

from app.core.logging import get_logger

log = get_logger(__name__)


@dataclass(frozen=True, slots=True)
class NotificationMessage:
    """Mensagem já renderizada, pronta para sair."""

    title: str
    body: str
    url: str | None = None
    #: Chave de deduplicação, para o canal que precisar dela.
    dedup_key: str | None = None


@dataclass(frozen=True, slots=True)
class DeliveryResult:
    delivered: bool
    detail: str = ""
    retryable: bool = False


class NotificationChannel(abc.ABC):
    kind: str

    @abc.abstractmethod
    async def send(self, address: str | None, message: NotificationMessage) -> DeliveryResult:
        """Entrega a mensagem.

        ``address`` é o endereço do canal (chat do Telegram, por exemplo).
        ``None`` quando o canal não precisa — o in-app grava no banco e o
        destinatário já está na linha da notificação.
        """

    async def aclose(self) -> None:
        return None


class InAppChannel(NotificationChannel):
    """Entrega é a própria linha em ``notifications``, já gravada pelo service.

    Parece um no-op e é: o valor está em existir como canal, para que a regra
    de opt-out, silêncio noturno e deduplicação valha igual para todos.
    """

    kind = "in_app"

    async def send(self, address: str | None, message: NotificationMessage) -> DeliveryResult:
        return DeliveryResult(delivered=True, detail="registrado na central de avisos")


class LogChannel(NotificationChannel):
    kind = "log"

    async def send(self, address: str | None, message: NotificationMessage) -> DeliveryResult:
        log.info("notificacao", destino=address, titulo=message.title, corpo=message.body)
        return DeliveryResult(delivered=True, detail="escrito no log")


class TelegramChannel(NotificationChannel):
    """Bot do Telegram.

    Só saída: a plataforma chama ``sendMessage``, o Telegram não precisa
    alcançar a plataforma. É por isso que funciona numa máquina de casa sem IP
    fixo e sem abrir porta no roteador.

    Para o participante se conectar, ele manda ``/start`` para o bot e cola o
    ``chat_id`` no perfil — ou usa o link de vínculo que a plataforma gera.
    """

    kind = "telegram"

    def __init__(self, bot_token: str, *, client: httpx.AsyncClient | None = None) -> None:
        self._token = bot_token
        self._client = client or httpx.AsyncClient(timeout=httpx.Timeout(15.0))

    async def send(self, address: str | None, message: NotificationMessage) -> DeliveryResult:
        if not self._token:
            return DeliveryResult(delivered=False, detail="bot do Telegram não configurado")
        if not address:
            return DeliveryResult(delivered=False, detail="usuário sem chat do Telegram vinculado")

        text = f"*{_escape(message.title)}*\n{_escape(message.body)}"
        if message.url:
            text += f"\n{message.url}"

        try:
            response = await self._client.post(
                f"https://api.telegram.org/bot{self._token}/sendMessage",
                json={
                    "chat_id": address,
                    "text": text,
                    "parse_mode": "MarkdownV2",
                    "disable_web_page_preview": True,
                },
            )
        except httpx.HTTPError as exc:
            return DeliveryResult(delivered=False, detail=str(exc), retryable=True)

        if response.status_code == 429:
            return DeliveryResult(delivered=False, detail="rate limit do Telegram", retryable=True)
        if response.status_code >= 500:
            return DeliveryResult(delivered=False, detail="Telegram indisponível", retryable=True)
        if response.status_code >= 400:
            # 403 = o usuário bloqueou o bot. Insistir não adianta.
            return DeliveryResult(
                delivered=False, detail=f"Telegram recusou: {response.text[:160]}", retryable=False
            )

        return DeliveryResult(delivered=True)

    async def aclose(self) -> None:
        await self._client.aclose()


#: MarkdownV2 do Telegram quebra se estes caracteres não vierem escapados.
_SPECIAL = r"_*[]()~`>#+-=|{}.!"


def _escape(text: str) -> str:
    return "".join(f"\\{char}" if char in _SPECIAL else char for char in text)


def build_channels(names: list[str], *, telegram_bot_token: str = "") -> list[NotificationChannel]:
    channels: list[NotificationChannel] = []
    for name in names:
        if name == "in_app":
            channels.append(InAppChannel())
        elif name == "telegram" and telegram_bot_token:
            channels.append(TelegramChannel(telegram_bot_token))
        elif name == "log":
            channels.append(LogChannel())
        else:
            log.warning("notificacao.canal_ignorado", canal=name)
    return channels or [InAppChannel()]
