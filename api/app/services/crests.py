"""Preenchimento de escudo de clube.

Por que isso não é trivial
--------------------------
Cada fonte de calendário chama o mesmo clube de um jeito: ``Man Utd`` no CSV,
``Manchester United`` no GE, ``FC Bayern München`` num, ``Bayern de Munique`` no
outro. E as fontes que trazem calendário completo em geral **não** trazem
escudo. Então casar escudo com time é um problema de nome, não de imagem.

A ordem das fontes é de propósito
---------------------------------
1. **O que já está no banco.** O coletor do GE traz o escudo oficial em SVG.
   Se o mesmo clube já entrou por outro campeonato, o escudo dele já está aqui
   — de graça, sem rede, e no mesmo padrão visual dos outros.
2. **Uma fonte externa**, para o que sobrar.

Casar errado é pior do que não casar: escudo trocado num jogo é um erro que a
pessoa vê na hora e não sabe explicar. Por isso o casamento remoto só aceita
resultado da liga esperada, e nunca "o primeiro parecido".
"""

from __future__ import annotations

from collections.abc import Awaitable, Callable
from dataclasses import dataclass, field

from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from app.core.logging import get_logger
from app.core.names import chave
from app.models import Competition, Fixture, Season, Team

log = get_logger(__name__)


@dataclass
class Resultado:
    """O que a passada fez. Serve para a tela e para o log do job."""

    preenchidos_local: int = 0
    preenchidos_remoto: int = 0
    ja_tinham: int = 0
    sem_escudo: list[str] = field(default_factory=list)
    """Times que sobraram. Continuam com o escudo desenhado pela plataforma."""

    @property
    def preenchidos(self) -> int:
        return self.preenchidos_local + self.preenchidos_remoto

    def as_dict(self) -> dict[str, object]:
        return {
            "filled_local": self.preenchidos_local,
            "filled_remote": self.preenchidos_remoto,
            "already_had": self.ja_tinham,
            "missing": self.sem_escudo,
        }


@dataclass(frozen=True, slots=True)
class TimeSemEscudo:
    id: int
    name: str
    competition: str
    """Nome da competição, para restringir a busca remota à liga certa."""


async def times_por_escudo(session: AsyncSession) -> tuple[list[TimeSemEscudo], dict[str, str]]:
    """Quem está sem escudo, e o índice do que já temos.

    O índice mapeia :func:`chave` do nome para a URL do escudo. Quem já tem
    escudo é a fonte gratuita para quem não tem.
    """
    linhas = set()
    for lado in (Fixture.home_team_id, Fixture.away_team_id):
        resultado = await session.execute(
            select(Team.id, Team.name, Team.crest_url, Competition.name)
            # `select_from` explícito: com dois caminhos possíveis até Team
            # (mandante e visitante), o SQLAlchemy não adivinha o lado certo.
            .select_from(Fixture)
            .join(Season, Season.id == Fixture.season_id)
            .join(Competition, Competition.id == Season.competition_id)
            .join(Team, Team.id == lado)
            .distinct()
        )
        linhas |= set(resultado.all())

    indice: dict[str, str] = {}
    faltando: list[TimeSemEscudo] = []
    vistos: set[int] = set()

    for time_id, nome, escudo, competicao in sorted(linhas, key=lambda linha: linha[0]):
        if escudo:
            indice.setdefault(chave(nome), escudo)
        elif time_id not in vistos:
            vistos.add(time_id)
            faltando.append(TimeSemEscudo(id=time_id, name=nome, competition=competicao))

    return faltando, indice


#: Assinatura da busca externa. Recebe nome e competição, devolve URL ou nada.
#: É injetada para que o serviço seja testável sem rede.
Buscador = Callable[[str, str], Awaitable[str | None]]


async def preencher(
    session: AsyncSession,
    *,
    buscador: Buscador | None = None,
    limite_remoto: int | None = None,
    commit_a_cada: int = 10,
) -> Resultado:
    """Preenche ``teams.crest_url`` de quem está sem.

    Idempotente: rodar de novo só mexe em quem continua sem escudo. Não
    sobrescreve escudo existente — se um veio errado, apague-o primeiro.

    Grava de tempos em tempos em vez de tudo no fim. A busca sem cadastro leva
    seis segundos por clube, e uma passada de cem clubes passa do tempo limite
    de um job — commitando aos poucos, um corte no meio perde a última dezena,
    não a hora inteira.
    """
    faltando, indice = await times_por_escudo(session)
    resultado = Resultado(ja_tinham=len(indice))
    restantes = limite_remoto
    desde_o_commit = 0

    async def gravar(time_id: int, escudo: str) -> bool:
        nonlocal desde_o_commit
        time = await session.get(Team, time_id)
        if time is None:
            return False
        time.crest_url = escudo
        desde_o_commit += 1
        if commit_a_cada and desde_o_commit >= commit_a_cada:
            await session.commit()
            desde_o_commit = 0
        return True

    for item in faltando:
        local = indice.get(chave(item.name))
        if local:
            if await gravar(item.id, local):
                resultado.preenchidos_local += 1
            continue

        if buscador is None or (restantes is not None and restantes <= 0):
            resultado.sem_escudo.append(item.name)
            continue

        if restantes is not None:
            restantes -= 1

        achado = await buscador(item.name, item.competition)
        if achado is None:
            resultado.sem_escudo.append(item.name)
            continue

        if await gravar(item.id, achado):
            resultado.preenchidos_remoto += 1
            # Alimenta o índice: o mesmo clube pode aparecer em dois
            # campeonatos, e a segunda vez sai de graça.
            indice.setdefault(chave(item.name), achado)

    log.info(
        "escudos.preenchidos",
        local=resultado.preenchidos_local,
        remoto=resultado.preenchidos_remoto,
        faltando=len(resultado.sem_escudo),
    )
    return resultado
