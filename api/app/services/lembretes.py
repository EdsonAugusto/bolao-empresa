"""Quem ainda não palpitou, e em quais jogos.

Por que isto existe separado
----------------------------
O lembrete que já havia era por RODADA de campeonato: olhava ``Round.starts_at``
e avisava 24h e 3h antes. Isso deixava dois buracos grandes.

O primeiro é a rodada montada à mão. Ela não tem ``Round`` nenhuma — o
organizador escolhe jogos avulsos — então nenhum aviso saía para o bolão que
mistura o clássico do Brasileirão com a volta da Libertadores, que é justamente
o formato que mais precisa de lembrete, porque não tem calendário previsível.

O segundo é o jogo solto. Uma rodada começa sábado às 16h e tem jogo domingo às
18h30; o lembrete de 3h antes do INÍCIO da rodada não ajuda em nada quem
esqueceu o jogo de domingo.

Aqui a pergunta é outra: dado um intervalo de horários de bola rolando, quais
jogos abrem para palpite em cada bolão, e quem não palpitou neles. A resposta
vale para os dois tipos de bolão porque quem decide o que conta em cada um é
``pool_service.included_fixture_ids``, que já sabe a diferença.
"""

from __future__ import annotations

from dataclasses import dataclass, field
from datetime import datetime

from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from app.models import (
    Fixture,
    FixtureStatus,
    Matchday,
    MatchdayFixture,
    Membership,
    Pool,
    PoolKind,
    PoolStatus,
    Prediction,
)
from app.services import pools as pool_service
from app.services import predictions as prediction_service

#: Bolão arquivado, encerrado ou ainda em rascunho não manda lembrete a
#: ninguém. Só quem está valendo.
STATUS_QUE_LEMBRAM = (PoolStatus.OPEN, PoolStatus.RUNNING)


@dataclass(slots=True)
class Pendencia:
    """O que falta a uma pessoa, num bolão, dentro da janela olhada."""

    pool: Pool
    membership: Membership
    fixture_ids: list[int] = field(default_factory=list)
    primeiro_kickoff: datetime | None = None

    @property
    def quantos(self) -> int:
        return len(self.fixture_ids)


async def jogos_abertos_por_bolao(
    session: AsyncSession, *, de: datetime, ate: datetime
) -> dict[int, tuple[Pool, list[Fixture]]]:
    """Por bolão em andamento, os jogos ainda agendados que começam na janela.

    Só ``SCHEDULED``: jogo em campo, encerrado, adiado ou cancelado não aceita
    palpite, e lembrar de palpitar nele seria pior do que não lembrar de nada.
    """
    jogos = (
        await session.scalars(
            select(Fixture)
            .where(
                Fixture.kickoff_at >= de,
                Fixture.kickoff_at <= ate,
                Fixture.status == FixtureStatus.SCHEDULED,
            )
            .order_by(Fixture.kickoff_at)
        )
    ).all()
    if not jogos:
        return {}

    ids = [jogo.id for jogo in jogos]
    temporadas = {jogo.season_id for jogo in jogos}

    # Bolão de campeonato: alcança os jogos da temporada dele.
    candidatos = list(
        (
            await session.scalars(
                select(Pool).where(
                    Pool.status.in_(STATUS_QUE_LEMBRAM),
                    Pool.kind != PoolKind.CUSTOM,
                    Pool.season_id.in_(temporadas),
                )
            )
        ).all()
    )

    # Bolão personalizado: alcança os jogos que alguém pôs numa rodada montada.
    montados = (
        await session.scalars(
            select(Pool)
            .join(Matchday, Matchday.pool_id == Pool.id)
            .join(MatchdayFixture, MatchdayFixture.matchday_id == Matchday.id)
            .where(
                Pool.status.in_(STATUS_QUE_LEMBRAM),
                MatchdayFixture.fixture_id.in_(ids),
            )
            .distinct()
        )
    ).all()
    candidatos.extend(montados)

    por_id: dict[int, tuple[Pool, list[Fixture]]] = {}
    for pool in candidatos:
        if pool.id in por_id:
            continue
        # Quem decide o que vale em cada bolão é esta função, e ela já conhece
        # a diferença entre campeonato e rodada montada. Repetir a regra aqui
        # seria a segunda cópia dela — e o dia em que divergissem, o lembrete
        # falaria de jogo que não pontua.
        incluidos = await pool_service.included_fixture_ids(session, pool.id, ids)
        do_bolao = [jogo for jogo in jogos if jogo.id in incluidos]
        if do_bolao:
            por_id[pool.id] = (pool, do_bolao)

    return por_id


async def quem_falta_palpitar(
    session: AsyncSession, *, de: datetime, ate: datetime
) -> list[Pendencia]:
    """Todas as pendências da janela, prontas para virar aviso.

    Uma linha por (pessoa, bolão) — e não por jogo. Três jogos esquecidos no
    mesmo bolão são um aviso dizendo "faltam 3", não três avisos.
    """
    pendencias: list[Pendencia] = []

    for pool, jogos in (await jogos_abertos_por_bolao(session, de=de, ate=ate)).values():
        ids = [jogo.id for jogo in jogos]
        quando = {jogo.id: jogo.kickoff_at for jogo in jogos}

        faltantes = await prediction_service.members_without_prediction(
            session, pool_id=pool.id, fixture_ids=ids
        )
        if not faltantes:
            continue

        # O que CADA pessoa já palpitou.
        #
        # `members_without_prediction` devolve quem não palpitou em todos —
        # certo para decidir a quem avisar, e insuficiente para o texto: dizer
        # "faltam 3 jogos" a quem palpitou dois de três é errado, e é o tipo de
        # erro que faz a pessoa parar de ler o aviso.
        ja_feitos: dict[int, set[int]] = {}
        linhas = await session.execute(
            select(Prediction.membership_id, Prediction.fixture_id)
            .join(Membership, Prediction.membership_id == Membership.id)
            .where(Membership.pool_id == pool.id, Prediction.fixture_id.in_(ids))
        )
        for membership_id, fixture_id in linhas:
            ja_feitos.setdefault(membership_id, set()).add(fixture_id)

        for membro in faltantes:
            feitos = ja_feitos.get(membro.id, set())
            faltam = [fid for fid in ids if fid not in feitos]
            if not faltam:
                continue
            pendencias.append(
                Pendencia(
                    pool=pool,
                    membership=membro,
                    fixture_ids=faltam,
                    primeiro_kickoff=min(quando[fid] for fid in faltam),
                )
            )

    return pendencias
