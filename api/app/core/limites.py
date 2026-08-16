"""Limite de tentativas, em Redis.

Existe por um motivo concreto: verificar uma senha custa um Argon2 de 64 MiB
por tentativa. Sem limite, algumas centenas de requisições por segundo em
``/auth/login`` derrubam a plataforma inteira sem precisar acertar senha
nenhuma — e a mesma janela serve contra quem quer adivinhar a senha de alguém.

A contagem é por janela fixa, com ``INCR`` mais ``EXPIRE`` na primeira
ocorrência. Janela deslizante seria mais justa na virada, mas custa uma
estrutura ordenada por chave, e o ganho não paga num bolão entre amigos.

Falha aberta de propósito: se o Redis cair, a plataforma continua aceitando
login. Um limitador que derruba a autenticação quando a dependência dele some
troca um problema por outro pior.
"""

from __future__ import annotations

from dataclasses import dataclass

from fastapi import HTTPException, Request, status

from app.core.config import settings
from app.core.logging import get_logger
from app.core.redis import get_redis

log = get_logger(__name__)


@dataclass(frozen=True, slots=True)
class Estouro:
    """Quanto falta para a janela virar."""

    segundos: int


async def registrar(chave: str, *, teto: int, janela: int) -> Estouro | None:
    """Conta mais uma tentativa. Devolve ``Estouro`` quando passou do teto."""
    try:
        redis = get_redis()
        atual = await redis.incr(chave)
        if atual == 1:
            await redis.expire(chave, janela)
        if atual > teto:
            restante = await redis.ttl(chave)
            return Estouro(segundos=max(1, restante if restante and restante > 0 else janela))
    except Exception as exc:  # noqa: BLE001
        # Redis fora do ar não pode impedir alguém de entrar no próprio bolão.
        log.warning("limite.indisponivel", chave=chave, erro=str(exc))
    return None


async def limpar(chave: str) -> None:
    """Zera a contagem. Chamado quando a tentativa dá certo."""
    try:
        await get_redis().delete(chave)
    except Exception as exc:  # noqa: BLE001
        log.warning("limite.limpeza_falhou", chave=chave, erro=str(exc))


def ip_de(request: Request) -> str:
    """IP de quem chamou, respeitando o proxy da frente.

    Atrás do Caddy (e da Cloudflare antes dele) o socket sempre mostra o IP do
    container do proxy: sem ler cabeçalho, a internet inteira contaria como um
    visitante só e o limite bloquearia todo mundo junto.

    A ordem importa e não é arbitrária:

    1. ``X-Real-IP`` — **escrito pela borda**, um valor só. O nginx o preenche
       com ``$remote_addr`` e o Caddy com ``{client_ip}``. É o único dos dois
       que o cliente não consegue plantar.
    2. ``X-Forwarded-For`` — só como último recurso, e lendo o **último**
       elemento. Ele é uma cadeia à qual cada proxy ANEXA: o primeiro item é
       justamente o que o cliente alega ser. Ler o primeiro, como fazíamos,
       deixava ``curl -H 'X-Forwarded-For: 1.2.3.4'`` criar um balde novo a
       cada requisição — e o teto de tentativas virava enfeite.
    """
    real = request.headers.get("x-real-ip", "").strip()
    if real:
        return real[:64]

    encaminhado = request.headers.get("x-forwarded-for", "")
    if encaminhado:
        return encaminhado.split(",")[-1].strip()[:64]

    return (request.client.host if request.client else "desconhecido")[:64]


async def barrar_login(request: Request, email: str) -> None:
    """Aplica os dois tetos do login. Levanta 429 quando qualquer um estoura.

    Dois eixos porque eles cobrem ataques diferentes: por conta segura quem
    tenta adivinhar a senha de uma pessoa; por IP segura quem varre muitas
    contas de uma vez. Um sozinho deixa passar o outro.
    """
    janela = settings.login_janela_segundos
    alvo = (email or "").strip().lower()[:120]

    estouros = [
        await registrar(
            f"limite:login:conta:{alvo}", teto=settings.login_max_por_conta, janela=janela
        ),
        await registrar(
            f"limite:login:ip:{ip_de(request)}", teto=settings.login_max_por_ip, janela=janela
        ),
    ]
    pior = max((e for e in estouros if e), key=lambda e: e.segundos, default=None)
    if pior is None:
        return

    minutos = max(1, round(pior.segundos / 60))
    raise HTTPException(
        status_code=status.HTTP_429_TOO_MANY_REQUESTS,
        detail=(
            f"muitas tentativas. Tente de novo em {minutos} "
            f"{'minuto' if minutos == 1 else 'minutos'}."
        ),
        headers={"Retry-After": str(pior.segundos)},
    )


async def limpar_login(email: str) -> None:
    """Login deu certo: a conta volta a ter as tentativas cheias.

    O contador por IP fica de pé. Senão, bastaria um acerto numa conta própria
    para zerar o limite e continuar tentando nas dos outros.
    """
    await limpar(f"limite:login:conta:{(email or '').strip().lower()[:120]}")


async def barrar_por_ip(request: Request, balde: str, *, teto: int, janela: int) -> None:
    """Teto por IP para um caminho caro (importação, upload, cadastro)."""
    estouro = await registrar(f"limite:{balde}:{ip_de(request)}", teto=teto, janela=janela)
    if estouro is None:
        return
    raise HTTPException(
        status_code=status.HTTP_429_TOO_MANY_REQUESTS,
        detail="muitas requisições seguidas. Espere um pouco e tente de novo.",
        headers={"Retry-After": str(estouro.segundos)},
    )
