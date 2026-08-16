"""Worker arq.

Todo job é idempotente e registra o que fez em ``sync_runs``. Nenhum job
assume que é a primeira vez que roda.

Cadência pensada para custo zero: ``sync_live`` só acorda quando existe jogo em
andamento de fato, e o provedor manual nem chega a ser consultado. É isso que
impede um tier gratuito de estourar numa rodada de domingo.
"""

from __future__ import annotations

from collections.abc import Callable
from datetime import UTC, datetime, timedelta
from typing import Any, ClassVar

from arq import cron
from sqlalchemy import select

from app.core.config import settings
from app.core.db import SessionLocal
from app.core.logging import configure_logging, get_logger
from app.core.redis import TravaOcupada, arq_redis_settings, trava_de_job
from app.models import Fixture, FixtureStatus, Pool, PoolRound, Round
from app.providers.factory import build_provider
from app.services import catalog as catalog_service
from app.services import notifications as notify_service
from app.services import predictions as prediction_service
from app.services import settlement as settlement_service

log = get_logger(__name__)


async def ping(ctx: dict[str, Any]) -> str:
    return "pong"


# ---------------------------------------------------------------------------
# Ingestão
# ---------------------------------------------------------------------------


async def sync_fixtures(ctx: dict[str, Any]) -> dict[str, Any]:
    """Recoleta diária do calendário de cada competição ativa.

    Uma vez por dia, de madrugada, cada competição é recoletada pela **sua**
    fonte. É o que traz jogo remarcado, horário confirmado e a partida que
    ainda não tinha data — a rodada 21 do Brasileirão, por exemplo, tem quatro
    jogos que o GE publica sem data e que entram sozinhos quando a CBF marcar.

    Antes daqui existia um provedor global (``settings.football_provider``), o
    padrão era ``manual``, e este job saía na primeira linha todo dia. Nenhum
    calendário e nenhum placar jamais se atualizavam sozinhos.
    """
    from app.services import sync as sync_service

    started = datetime.now(UTC)
    total: dict[str, Any] = {"competitions": 0, "fixtures": 0, "failures": []}

    async with SessionLocal() as session:
        for competition, season, slug in await sync_service.temporadas_ativas(session):
            try:
                tocados, mudados = await sync_service.coletar_competicao(
                    session, competition, season, slug
                )
            except Exception as exc:  # noqa: BLE001 - um campeonato não derruba os outros
                total["failures"].append(f"{competition.name}: {exc}")
                log.warning("sync.falhou", competicao=competition.name, erro=str(exc))
                continue

            total["competitions"] += 1
            total["fixtures"] += tocados
            if mudados:
                total.setdefault("scores_changed", []).extend(mudados)

        await catalog_service.record_sync_run(
            session,
            kind="fixtures",
            status="failed" if total["failures"] else "success",
            started_at=started,
            stats=total,
            error="; ".join(total["failures"]) or None,
        )
        await session.commit()

    return total


async def sync_live(ctx: dict[str, Any]) -> dict[str, Any]:
    """Placar ao vivo, enquanto há jogo em campo.

    Roda de dois em dois minutos, mas quase sempre sai na primeira linha: a
    pergunta "tem jogo agora?" é uma consulta local, sem rede. Só quando há
    partida rolando é que a coleta acontece — e mesmo aí, só nas competições
    cuja fonte publica durante o jogo, e só nas rodadas em campo.

    Terminado o placar, apura na sequência. Sem isso o resultado apareceria na
    tela mas o ranking só mexeria no próximo quarto de hora, e a diferença
    entre as duas coisas é justamente o que se olha durante a rodada.
    """
    from app.services import sync as sync_service

    started = datetime.now(UTC)

    async with SessionLocal() as session:
        if not await sync_service.jogos_na_janela(session):
            return {"skipped": "nenhum jogo em campo"}

    try:
        # A trava é pega **depois** da consulta local: no minuto sem jogo o job
        # não toca no Redis, e é assim a maior parte do dia.
        async with trava_de_job("sync_live", segundos=110):
            return await _coletar_ao_vivo(ctx, started)
    except TravaOcupada:
        return {"skipped": "coleta anterior ainda rodando"}


async def _coletar_ao_vivo(ctx: dict[str, Any], started: datetime) -> dict[str, Any]:
    from app.services import placar as placar_service
    from app.services import sync as sync_service

    async with SessionLocal() as session:
        # Duas passadas, e elas se complementam. A primeira recoleta pela fonte
        # dona do calendário — é a que traz jogo remarcado e é a única com
        # placar ao vivo no Brasileirão. A segunda sobrepõe o placar da ESPN,
        # que é o que dá tempo real às ligas cujo calendário veio de um CSV
        # lento: sem ela, PSV x Fortuna Sittard ficava "em campo" horas depois
        # de acabar.
        resultado = await sync_service.coletar_ao_vivo(session)
        await session.commit()

        placares = await placar_service.aplicar_placares(
            session, await sync_service.jogos_na_janela(session)
        )
        await session.commit()

        if resultado.competicoes:
            await catalog_service.record_sync_run(
                session,
                kind="live",
                status="failed" if resultado.falhas else "success",
                started_at=started,
                stats=resultado.as_dict(),
                error="; ".join(resultado.falhas) or None,
            )
            await session.commit()

    apurados = await settle_fixtures(ctx)
    return {
        **resultado.as_dict(),
        "live_scores": placares.as_dict(),
        "settled": apurados.get("settled", 0),
    }


async def sync_results(ctx: dict[str, Any]) -> dict[str, Any]:
    """Varredura lenta: pega o resultado de quem já jogou e ainda não tem placar.

    É o caminho das fontes que só publicam depois do apito — o CSV das ligas
    europeias e a Wikipédia — e a rede de segurança de quem publica ao vivo: se
    a coleta durante o jogo falhou a tarde inteira, é aqui que o placar entra.

    Não gasta requisição à toa: competição com tudo apurado não é consultada.
    """
    started = datetime.now(UTC)
    try:
        async with trava_de_job("sync_results", segundos=1700):
            return await _coletar_resultados(ctx, started)
    except TravaOcupada:
        return {"skipped": "varredura anterior ainda rodando"}


async def _coletar_resultados(ctx: dict[str, Any], started: datetime) -> dict[str, Any]:
    from app.services import placar as placar_service
    from app.services import sync as sync_service

    async with SessionLocal() as session:
        resultado = await sync_service.coletar_resultados(session)
        await session.commit()

        # A ESPN também é a rede de segurança do que ficou sem placar: um jogo
        # de ontem que a fonte do calendário nunca publicou é resolvido aqui,
        # sem esperar o organizador lançar à mão.
        pendentes = await sync_service.jogos_sem_placar(session)
        placares = await placar_service.aplicar_placares(session, pendentes)
        await session.commit()

        if resultado.competicoes or resultado.falhas:
            await catalog_service.record_sync_run(
                session,
                kind="results",
                status="failed" if resultado.falhas else "success",
                started_at=started,
                stats=resultado.as_dict(),
                error="; ".join(resultado.falhas) or None,
            )
            await session.commit()

    if not resultado.competicoes and not placares.jogos_atualizados:
        return {"skipped": "nada pendente"}

    apurados = await settle_fixtures(ctx)
    return {
        **resultado.as_dict(),
        "live_scores": placares.as_dict(),
        "settled": apurados.get("settled", 0),
    }


async def settle_fixtures(ctx: dict[str, Any]) -> dict[str, Any]:
    """Apura tudo que terminou e ainda não foi pontuado.

    Também recolhe os jogos marcados como sujos por correção de placar.

    A trava não é zelo excessivo. Este job é chamado de cinco lugares — o cron
    próprio nos minutos 5/20/35/50, e mais de dentro de ``sync_live``,
    ``sync_results``, ``close_stale_fixtures`` e ``reconcile`` — e o arq roda
    cinco jobs em paralelo. Nos minutos 20 e 50 de qualquer rodada com jogo em
    campo, duas apurações do mesmo conjunto de jogos rodavam juntas; como
    ``settle_fixture`` apaga e reinsere ``prediction_scores``, a segunda travava
    no índice único e uma das duas morria no meio.
    """
    try:
        async with trava_de_job("settle_fixtures", segundos=560):
            return await _apurar(ctx)
    except TravaOcupada:
        return {"skipped": "apuração anterior ainda rodando"}


async def _apurar(ctx: dict[str, Any]) -> dict[str, Any]:
    async with SessionLocal() as session:
        results = await settlement_service.settle_pending(session)

        for result in results:
            if not result.settled:
                continue
            for change in result.position_changes:
                membership_id = int(change["membership_id"])  # type: ignore[arg-type]
                movement = int(change["movement"])  # type: ignore[arg-type]
                await notify_service.enqueue(
                    session,
                    membership_id=membership_id,
                    template="position_changed",
                    reference_id=result.fixture_id,
                    payload={
                        "direction": "subiu" if movement > 0 else "caiu",
                        "places": abs(movement),
                        "position": change["to"],
                        "pool": "seu bolão",
                    },
                )

        await session.commit()

    settled = sum(1 for item in results if item.settled)
    return {"checked": len(results), "settled": settled}


# ---------------------------------------------------------------------------
# Lembretes
# ---------------------------------------------------------------------------


async def prediction_reminders(ctx: dict[str, Any]) -> dict[str, Any]:
    """Avisa quem ainda não palpitou.

    Duas janelas: 24h e 3h antes do primeiro jogo da rodada. É o lembrete que
    mantém o bolão vivo — sem ele, metade do grupo esquece na segunda rodada.

    A deduplicação por ``(membership, template, reference_id)`` garante que
    rodar de minuto em minuto não vira spam.
    """
    now = datetime.now(UTC)
    windows = {
        "d1": (now + timedelta(hours=23), now + timedelta(hours=25)),
        "h3": (now + timedelta(hours=2, minutes=30), now + timedelta(hours=3, minutes=30)),
    }

    created = 0
    async with SessionLocal() as session:
        for label, (start, end) in windows.items():
            rounds = (
                await session.scalars(select(Round).where(Round.starts_at.between(start, end)))
            ).all()

            for round_obj in rounds:
                fixture_ids = list(
                    (
                        await session.scalars(
                            select(Fixture.id).where(Fixture.round_id == round_obj.id)
                        )
                    ).all()
                )
                if not fixture_ids:
                    continue

                pool_ids = (
                    await session.scalars(
                        select(PoolRound.pool_id).where(
                            PoolRound.round_id == round_obj.id, PoolRound.included.is_(True)
                        )
                    )
                ).all()

                for pool_id in pool_ids:
                    pool = await session.get(Pool, pool_id)
                    if pool is None:
                        continue
                    pending = await prediction_service.members_without_prediction(
                        session, pool_id=pool_id, fixture_ids=fixture_ids
                    )
                    for member in pending:
                        # reference_id combina rodada e janela: o lembrete de
                        # D-1 e o de H-3 são avisos diferentes.
                        reference = round_obj.id * 10 + (1 if label == "d1" else 2)
                        if await notify_service.enqueue(
                            session,
                            membership_id=member.id,
                            template="prediction_reminder",
                            reference_id=reference,
                            payload={
                                "pending": len(fixture_ids),
                                "round": round_obj.name,
                                "pool": pool.name,
                                "deadline": round_obj.starts_at.isoformat()
                                if round_obj.starts_at
                                else "breve",
                            },
                        ):
                            created += 1

        await session.commit()

    return {"reminders": created}


async def dispatch_notifications(ctx: dict[str, Any]) -> dict[str, int]:
    async with SessionLocal() as session:
        stats = await notify_service.dispatch_pending(session)
        await session.commit()
    return stats


async def close_predictions(ctx: dict[str, Any]) -> dict[str, int]:
    """Rede de segurança do fechamento de palpites, de minuto em minuto."""
    from app.services import sync as sync_service

    async with SessionLocal() as session:
        fechados = await sync_service.fechar_palpites(session)
        await session.commit()
    return {"closed": fechados}


async def close_stale_fixtures(ctx: dict[str, Any]) -> dict[str, Any]:
    """Destrava rodada: encerra jogo com placar que ficou preso em andamento.

    A regra mora em ``services.sync.encerrar_parados`` — inclusive o que ela
    deliberadamente **não** faz com jogo sem placar.
    """
    from app.services import sync as sync_service

    async with SessionLocal() as session:
        resultado = await sync_service.encerrar_parados(session)
        await session.commit()

    if resultado.encerrados:
        await settle_fixtures(ctx)

    return dict(resultado.as_dict())


async def reconcile(ctx: dict[str, Any]) -> dict[str, Any]:
    """Confere os últimos 7 dias contra o provedor e corrige divergência."""
    provider = build_provider()
    if not provider.supports_sync:
        return {"skipped": "provedor manual"}

    started = datetime.now(UTC)
    corrected: list[int] = []

    async with SessionLocal() as session:
        window_start = datetime.now(UTC) - timedelta(days=7)
        fixtures = (
            await session.scalars(
                select(Fixture).where(
                    Fixture.kickoff_at >= window_start,
                    Fixture.status == FixtureStatus.FINISHED,
                )
            )
        ).all()

        for fixture in fixtures:
            try:
                remote = await provider.get_fixture(fixture.external_id)
            except Exception as exc:  # noqa: BLE001
                log.warning("reconcile.falhou", fixture=fixture.id, erro=str(exc))
                continue
            if remote is None:
                continue
            if (remote.home_ft, remote.away_ft) != (fixture.home_ft, fixture.away_ft):
                log.warning(
                    "reconcile.divergencia",
                    fixture=fixture.id,
                    local=(fixture.home_ft, fixture.away_ft),
                    remoto=(remote.home_ft, remote.away_ft),
                )
                fixture.home_ft = remote.home_ft
                fixture.away_ft = remote.away_ft
                fixture.settlement_dirty = True
                corrected.append(fixture.id)

        await catalog_service.record_sync_run(
            session,
            kind="reconcile",
            status="success",
            started_at=started,
            stats={"checked": len(fixtures), "corrected": corrected},
        )
        await session.commit()

    await provider.aclose()
    if corrected:
        await settle_fixtures(ctx)
    return {"checked": len(fixtures), "corrected": len(corrected)}


#: Quantos clubes cabem numa passada. A seis segundos cada, 60 dão seis
#: minutos — folgado dentro do ``job_timeout`` de dez. O que sobrar vira a
#: próxima passada; passar do limite mataria o job e perderia o lote.
CRESTS_POR_PASSADA = 60


async def fill_crests(ctx: dict[str, Any], *, limit: int | None = None) -> dict[str, Any]:
    """Preenche escudo de clube que ainda não tem.

    Roda aqui e não no endpoint porque a fonte sem cadastro é limitada com
    rigor: são seis segundos entre buscas, e cem clubes dariam dez minutos —
    tempo que uma requisição HTTP não pode ficar aberta.

    Trabalha em lotes e **se reagenda** enquanto sobrar clube. Cada passada é
    idempotente: olha só quem continua sem escudo, então retomar depois de um
    bloqueio continua de onde parou em vez de refazer tudo.
    """
    from app.providers.thesportsdb import TheSportsDbCrests
    from app.services import crests as crest_service

    lote = min(limit or CRESTS_POR_PASSADA, CRESTS_POR_PASSADA)
    fonte = TheSportsDbCrests()
    try:
        async with SessionLocal() as session:
            resultado = await crest_service.preencher(
                session, buscador=fonte.escudo, limite_remoto=lote
            )
            await session.commit()
            faltando, _ = await crest_service.times_por_escudo(session)
    finally:
        await fonte.aclose()

    # Só continua se a passada rendeu: sem isso, uma lista de clubes que
    # nenhuma fonte conhece faria o job se reagendar para sempre.
    restante = max((limit or 0) - lote, 0) if limit else None
    if faltando and resultado.preenchidos:
        redis = ctx.get("redis")
        if redis is not None:
            await redis.enqueue_job("fill_crests", limit=restante)

    return {**resultado.as_dict(), "still_missing": len(faltando)}


# ---------------------------------------------------------------------------
# Configuração do worker
# ---------------------------------------------------------------------------


async def startup(ctx: dict[str, Any]) -> None:
    configure_logging()
    log.info("worker.startup", provedor=settings.football_provider)


async def shutdown(ctx: dict[str, Any]) -> None:
    from app.core.db import dispose_engine
    from app.core.redis import close_redis

    await dispose_engine()
    await close_redis()
    log.info("worker.shutdown")


class WorkerSettings:
    functions: ClassVar[list[Callable[..., Any]]] = [
        ping,
        sync_fixtures,
        sync_live,
        settle_fixtures,
        prediction_reminders,
        dispatch_notifications,
        close_predictions,
        close_stale_fixtures,
        reconcile,
        fill_crests,
        sync_results,
    ]

    cron_jobs: ClassVar[list[Any]] = [
        # Horários em UTC. 08:00 UTC = 05:00 em Brasília.
        cron(sync_fixtures, hour={8}, minute=0),
        # De dois em dois minutos, mas quase sempre sai sem tocar na rede: a
        # pergunta "tem jogo em campo?" é local.
        cron(sync_live, minute=set(range(0, 60, 2))),
        # Fecha o palpite no minuto do apito, independente da coleta.
        cron(close_predictions, minute=set(range(60))),
        # Varredura de resultado para as fontes que só publicam depois do jogo.
        cron(sync_results, minute={7, 37}),
        cron(settle_fixtures, minute={5, 20, 35, 50}),
        cron(prediction_reminders, minute={10, 40}),
        cron(dispatch_notifications, minute=set(range(0, 60, 5))),
        cron(reconcile, hour={6}, minute=0),
        # De hora em hora: fecha jogo que já tem placar mas ficou preso em
        # andamento, que é o que impede a rodada de concluir.
        cron(close_stale_fixtures, minute={25}),
    ]

    on_startup = startup
    on_shutdown = shutdown
    redis_settings = arq_redis_settings()
    max_jobs = 5
    job_timeout = 600
    keep_result = 3600
    health_check_interval = 60
