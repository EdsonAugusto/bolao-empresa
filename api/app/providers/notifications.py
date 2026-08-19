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
import json
from dataclasses import dataclass

import httpx
from starlette.concurrency import run_in_threadpool

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

    expirados: tuple[str, ...] = ()
    """Endereços que o destino declarou mortos, para quem chamou apagar.

    Existe por causa do push: o serviço do navegador responde 404 ou 410 quando
    a pessoa desinstalou o app ou limpou os dados, e essa inscrição nunca mais
    vai funcionar. Sem devolver isso, o canal continuaria tentando entregar
    para aparelhos que não existem — a cada aviso, para sempre. Quem apaga é o
    serviço, que tem a sessão; o provedor não fala com o banco.
    """


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


class WebPushChannel(NotificationChannel):
    """Notificação do navegador — a que chega com o app fechado.

    Por que é o canal que faltava
    -----------------------------
    O aviso dentro da plataforma só alcança quem abre a plataforma, e quem
    esqueceu de palpitar é exatamente quem não abriu. O Telegram alcança de
    fora, mas exige a pessoa achar o bot e colar um código.

    Web Push não exige nada disso e não custa nada: é padrão do navegador. A
    chave VAPID é gerada uma vez na instalação, e o servidor fala direto com o
    serviço de push do fabricante — sem conta em nuvem, sem serviço a
    contratar, o que é a regra deste projeto.

    O que ele NÃO faz
    -----------------
    Não funciona sem HTTPS, e portanto não funciona na instalação de rede
    local. Isso não é limitação daqui: o navegador se recusa a assinar. Por
    isso o canal simplesmente não se constrói quando não há chave configurada,
    em vez de falhar na hora de enviar.

    Uma pessoa, vários aparelhos
    ----------------------------
    ``address`` chega como a lista de inscrições em JSON, montada por quem tem
    a sessão. O provedor não fala com o banco — é a regra de camada — então as
    inscrições mortas voltam em ``DeliveryResult.expirados`` para o serviço
    apagar.
    """

    kind = "push"

    def __init__(self, public_key: str, private_key: str, *, subject: str = "") -> None:
        self._public_key = public_key
        self._private_key = private_key
        # O serviço de push exige saber a quem reclamar. Sem `mailto:` ou URL,
        # alguns provedores recusam a requisição inteira.
        self._subject = subject or "mailto:admin@localhost"

    async def send(self, address: str | None, message: NotificationMessage) -> DeliveryResult:
        if not address:
            return DeliveryResult(delivered=False, detail="nenhum aparelho inscrito")

        try:
            inscricoes = json.loads(address)
        except ValueError:
            return DeliveryResult(delivered=False, detail="lista de inscrições ilegível")

        if not inscricoes:
            return DeliveryResult(delivered=False, detail="nenhum aparelho inscrito")

        corpo = json.dumps(
            {
                "title": message.title,
                "body": message.body,
                "url": message.url or "/",
                "tag": message.dedup_key or "",
            },
            ensure_ascii=False,
        )

        entregues = 0
        expirados: list[str] = []
        ultimo_erro = ""
        pode_repetir = False

        for inscricao in inscricoes:
            resultado = await run_in_threadpool(self._enviar_um, inscricao, corpo)
            if resultado is None:
                entregues += 1
            elif resultado[0] == "expirado":
                expirados.append(inscricao.get("endpoint", ""))
            else:
                ultimo_erro = resultado[1]
                pode_repetir = pode_repetir or resultado[0] == "temporario"

        if entregues:
            return DeliveryResult(
                delivered=True,
                detail=f"{entregues} aparelho(s)",
                expirados=tuple(expirados),
            )

        # Só inscrições mortas não é falha de entrega que valha repetir: não há
        # para onde mandar, e insistir gastaria uma tentativa por aviso até o
        # teto. Some da fila e as inscrições são apagadas.
        if expirados and not ultimo_erro:
            return DeliveryResult(
                delivered=False,
                detail="todas as inscrições estavam expiradas",
                expirados=tuple(expirados),
            )

        return DeliveryResult(
            delivered=False,
            detail=ultimo_erro or "não consegui entregar",
            retryable=pode_repetir,
            expirados=tuple(expirados),
        )

    def _enviar_um(self, inscricao: dict, corpo: str) -> tuple[str, str] | None:
        """Devolve ``None`` quando entregou, ou (categoria, motivo)."""
        from pywebpush import WebPushException, webpush

        try:
            webpush(
                subscription_info={
                    "endpoint": inscricao["endpoint"],
                    "keys": {"p256dh": inscricao["p256dh"], "auth": inscricao["auth"]},
                },
                data=corpo,
                vapid_private_key=self._private_key,
                vapid_claims={"sub": self._subject},
                ttl=_TTL_PUSH,
            )
        except WebPushException as exc:
            codigo = getattr(exc.response, "status_code", None)
            # 404 e 410 são o navegador dizendo que esta inscrição acabou —
            # app desinstalado, dados limpos, permissão revogada. Repetir nunca
            # vai funcionar.
            if codigo in (404, 410):
                return ("expirado", "inscrição encerrada pelo navegador")
            # 429 e 5xx são do serviço de push, não da inscrição.
            if codigo is not None and (codigo == 429 or codigo >= 500):
                return ("temporario", f"serviço de push respondeu {codigo}")
            return ("permanente", f"push recusado ({codigo or 'sem código'})")
        except (KeyError, TypeError) as exc:
            return ("permanente", f"inscrição malformada: {exc}")
        return None


#: Quanto tempo o serviço de push guarda a mensagem se o aparelho estiver
#: desligado. Lembrete de palpite envelhece: entregar "faltam 30 minutos" seis
#: horas depois é pior do que não entregar.
_TTL_PUSH = 3600


def build_channels(
    names: list[str],
    *,
    telegram_bot_token: str = "",
    vapid_public_key: str = "",
    vapid_private_key: str = "",
    vapid_subject: str = "",
) -> list[NotificationChannel]:
    channels: list[NotificationChannel] = []
    for name in names:
        if name == "in_app":
            channels.append(InAppChannel())
        elif name == "telegram" and telegram_bot_token:
            channels.append(TelegramChannel(telegram_bot_token))
        elif name == "push" and vapid_public_key and vapid_private_key:
            channels.append(
                WebPushChannel(vapid_public_key, vapid_private_key, subject=vapid_subject)
            )
        elif name == "log":
            channels.append(LogChannel())
        else:
            log.warning("notificacao.canal_ignorado", canal=name)
    return channels or [InAppChannel()]
