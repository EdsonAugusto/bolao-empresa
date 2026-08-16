"""Cliente Redis compartilhado e settings de conexão do arq."""

from __future__ import annotations

import secrets

from arq.connections import RedisSettings
from redis.asyncio import Redis

from app.core.config import settings

_client: Redis | None = None


def get_redis() -> Redis:
    """Cliente Redis singleton (decode_responses ligado — tudo é texto/JSON)."""
    global _client
    if _client is None:
        _client = Redis(
            host=settings.redis_host,
            port=settings.redis_port,
            db=settings.redis_db,
            decode_responses=True,
            health_check_interval=30,
        )
    return _client


async def close_redis() -> None:
    global _client
    if _client is not None:
        await _client.aclose()
        _client = None


def arq_redis_settings() -> RedisSettings:
    """Conexão usada pelo worker e pelo enfileirador. DB separado do cache."""
    return RedisSettings(
        host=settings.redis_host,
        port=settings.redis_port,
        database=settings.redis_db,
    )


class TravaOcupada(Exception):
    """Outra execução do mesmo job ainda está rodando."""


class trava_de_job:  # noqa: N801 - é um gerenciador de contexto, não uma classe comum
    """Impede que duas execuções do mesmo job se atropelem.

    O cron do arq não junta disparos atrasados: se uma coleta demorar mais que
    o intervalo, o próximo tick sobe uma segunda execução por cima. Duas
    coletas simultâneas da mesma competição dobram as requisições numa fonte
    que não é pública, e ainda brigam pelo mesmo ``UPDATE``.

    A trava expira sozinha (``EX``), então um worker morto no meio não deixa o
    job travado para sempre — no pior caso ele espera um ciclo.
    """

    #: Só apaga a trava se ela ainda for a minha. Sem esta comparação, uma
    #: execução que passou do tempo apagaria, ao terminar, a trava que a
    #: execução seguinte acabou de pegar — e aí as duas rodam juntas, que é
    #: exatamente o que a trava existia para impedir.
    _SOLTAR = """
    if redis.call('get', KEYS[1]) == ARGV[1] then
        return redis.call('del', KEYS[1])
    end
    return 0
    """

    def __init__(self, nome: str, *, segundos: int) -> None:
        self._chave = f"lock:job:{nome}"
        self._segundos = segundos
        self._token = secrets.token_hex(8)
        self._tenho = False

    async def __aenter__(self) -> trava_de_job:
        redis = get_redis()
        self._tenho = bool(await redis.set(self._chave, self._token, nx=True, ex=self._segundos))
        if not self._tenho:
            raise TravaOcupada(self._chave)
        return self

    async def __aexit__(self, *_: object) -> None:
        if self._tenho:
            await get_redis().eval(self._SOLTAR, 1, self._chave, self._token)


async def enqueue_job(nome: str, **kwargs: object) -> bool:
    """Manda um job para o worker. Devolve se foi aceito.

    Existe para o endpoint não segurar uma requisição HTTP durante um trabalho
    longo. Não derruba a resposta se o worker estiver fora do ar: quem chamou
    já fez a parte que dava para fazer na hora, e falar "não deu para
    enfileirar" é melhor do que devolver erro em cima de trabalho concluído.
    """
    from arq import create_pool

    try:
        pool = await create_pool(arq_redis_settings())
    except OSError:
        return False

    try:
        return await pool.enqueue_job(nome, **kwargs) is not None
    finally:
        await pool.aclose()
