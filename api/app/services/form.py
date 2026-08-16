"""Retrospecto: como o clube vem jogando.

Cinco letras — ``V``, ``E``, ``D`` — do mais recente para o mais antigo. É o que
dá contexto na hora de palpitar sem precisar abrir outra aba.

A pegadinha aqui é identidade de clube
--------------------------------------
Um clube tem **uma linha por provedor**: o Flamengo do GE e o Flamengo da
Wikipédia são ``teams`` diferentes, porque o upsert é por
``(provider_id, external_id)`` — e tem que ser, senão dois provedores brigariam
pela mesma linha.

Consequência: retrospecto por ``team_id`` sai partido. No banco de hoje o
Flamengo do GE tem 26 jogos encerrados e o da Wikipédia tem zero, então a tela
da Libertadores mostraria o Flamengo sem retrospecto nenhum — o que parece bug
para quem olha, e é.

Por isso o agrupamento é pelo **nome comparável** (:func:`app.core.names.chave`),
o mesmo critério que casa escudo. Um clube = todas as linhas com aquele nome.
"""

from __future__ import annotations

from collections.abc import Iterable, Sequence
from dataclasses import dataclass

from sqlalchemy import or_, select
from sqlalchemy.ext.asyncio import AsyncSession

from app.core.names import chave, normalizar
from app.models import Fixture, FixtureStatus, Team

#: Cinco é o padrão do futebol, e cabe na linha do celular sem quebrar.
JOGOS_NO_RETROSPECTO = 5


@dataclass(frozen=True, slots=True)
class JogoDoRetrospecto:
    """Um jogo passado, do ponto de vista de um clube."""

    fixture_id: int
    resultado: str
    """``V``, ``E`` ou ``D``."""

    marcou: int
    sofreu: int
    adversario: str
    em_casa: bool
    kickoff_at: object
    """``datetime`` — a tela formata no fuso de quem lê."""


@dataclass(frozen=True, slots=True)
class Retrospecto:
    team_id: int
    jogos: list[JogoDoRetrospecto]

    @property
    def resumo(self) -> str:
        """``VVEDV``, do mais recente para o mais antigo."""
        return "".join(jogo.resultado for jogo in self.jogos)

    def as_dict(self) -> dict[str, object]:
        return {
            "summary": self.resumo,
            "matches": [
                {
                    "fixture_id": jogo.fixture_id,
                    "result": jogo.resultado,
                    "scored": jogo.marcou,
                    "conceded": jogo.sofreu,
                    "opponent": jogo.adversario,
                    "home": jogo.em_casa,
                    "kickoff_at": jogo.kickoff_at,
                }
                for jogo in self.jogos
            ],
        }


def _mesmo_clube(um: Team, outro: Team) -> bool:
    """Duas linhas de ``teams`` são o mesmo clube do mundo real?

    Juntar demais é pior do que não juntar. No banco de hoje existe o par
    ``Vitória`` (Brasil, Série A) e ``Vitória SC`` (Portugal, Primeira Liga):
    são clubes diferentes, e ``chave()`` funde os dois porque ``sc`` está na
    lista de ruído. O resultado seria o português exibindo o retrospecto do
    baiano — errado, e convincente o bastante para ninguém desconfiar.

    Então a fusão exige uma das duas evidências:

    1. **Nome idêntico** depois de tirar acento e pontuação. É o caso de todas
       as 29 duplicatas reais, que existem porque o upsert é por provedor: o
       Flamengo do GE e o da Wikipédia se escrevem igual.
    2. **Nome equivalente e país compatível.** Cobre ``FC Porto`` contra
       ``Porto`` sem cobrir ``Vitória`` contra ``Vitória SC``, que discordam
       no país.

    País ausente não impede a fusão — ausência não é discordância.
    """
    if um.id == outro.id:
        return True

    if normalizar(um.name) == normalizar(outro.name):
        return True

    if chave(um.name) != chave(outro.name):
        return False

    return not (um.country and outro.country and um.country != outro.country)


def _resultado(marcou: int, sofreu: int) -> str:
    if marcou > sofreu:
        return "V"
    return "E" if marcou == sofreu else "D"


async def retrospectos(
    session: AsyncSession,
    team_ids: Iterable[int],
    *,
    limite: int = JOGOS_NO_RETROSPECTO,
    ate: object | None = None,
) -> dict[int, Retrospecto]:
    """Últimos jogos de cada clube pedido, indexado por ``team_id``.

    ``ate`` corta o retrospecto num instante — serve para a tela de um jogo
    passado mostrar como os times chegaram **naquele** dia, em vez de misturar
    resultados que só aconteceram depois.

    Uma consulta para os clubes, uma para os jogos. Não faz N+1 mesmo com uma
    rodada inteira na tela.
    """
    pedidos = list(dict.fromkeys(team_ids))
    if not pedidos:
        return {}

    alvos = (await session.scalars(select(Team).where(Team.id.in_(pedidos)))).all()
    if not alvos:
        return {}

    # Só as três colunas que o casamento usa, em vez de hidratar o objeto ORM
    # inteiro: `teams` guarda uma linha por provedor, então uma instalação com
    # muitas competições importadas tem centenas de linhas aqui, e nenhuma delas
    # precisa de escudo, slug ou datas para responder "é o mesmo clube?".
    linhas = (await session.execute(select(Team.id, Team.name, Team.country))).all()
    nome_por_id = {tid: nome for tid, nome, _ in linhas}

    # A identidade de cada linha é calculada UMA vez. Antes, `_mesmo_clube`
    # normalizava os dois nomes a cada par comparado: com 20 clubes na tela e
    # 500 no banco, eram 20 mil normalizações por abertura de rodada, todas
    # recalculando as mesmas poucas centenas de strings.
    identidade = {tid: (normalizar(nome), chave(nome), pais) for tid, nome, pais in linhas}

    def sao_o_mesmo(a: int, b: int) -> bool:
        """``_mesmo_clube`` sobre a identidade pré-calculada. Mesma regra."""
        if a == b:
            return True
        nome_a, chave_a, pais_a = identidade[a]
        nome_b, chave_b, pais_b = identidade[b]
        if nome_a == nome_b:
            return True
        if chave_a != chave_b:
            return False
        return not (pais_a and pais_b and pais_a != pais_b)

    # Irmãos de cada clube pedido: as outras linhas que são o mesmo clube.
    grupos: dict[int, set[int]] = {
        alvo.id: {tid for tid in identidade if sao_o_mesmo(alvo.id, tid)} for alvo in alvos
    }

    # Uma consulta por grupo distinto de irmãos, cada uma com `LIMIT limite`.
    #
    # A alternativa — uma consulta só, com teto calculado — trunca em silêncio:
    # basta um clube da tela ter jogado muito mais que os outros para o corte
    # global cair antes de completar cinco jogos de alguém, e o retrospecto sai
    # curto sem nada indicar isso. Aqui cada grupo pede exatamente o que precisa.
    #
    # Grupos repetidos são consultados uma vez: numa rodada os dois times de um
    # jogo têm grupos diferentes, mas o mesmo clube em dois jogos, não.
    por_grupo: dict[frozenset[int], list[JogoDoRetrospecto]] = {}

    for grupo in {frozenset(itens) for itens in grupos.values() if itens}:
        consulta = (
            select(Fixture)
            .where(
                Fixture.status == FixtureStatus.FINISHED,
                Fixture.home_ft.is_not(None),
                Fixture.away_ft.is_not(None),
                or_(
                    Fixture.home_team_id.in_(grupo),
                    Fixture.away_team_id.in_(grupo),
                ),
            )
            .order_by(Fixture.kickoff_at.desc())
            .limit(limite)
        )
        if ate is not None:
            consulta = consulta.where(Fixture.kickoff_at < ate)

        coletado: list[JogoDoRetrospecto] = []
        for jogo in (await session.scalars(consulta)).all():
            casa, fora = jogo.home_ft, jogo.away_ft
            # A consulta já exige placar, mas o par pode chegar meio preenchido
            # se alguém editar o jogo à mão. Sem os dois números não há resultado.
            if casa is None or fora is None:
                continue

            em_casa = jogo.home_team_id in grupo
            marcou, sofreu = (casa, fora) if em_casa else (fora, casa)
            adversario_id = jogo.away_team_id if em_casa else jogo.home_team_id

            coletado.append(
                JogoDoRetrospecto(
                    fixture_id=jogo.id,
                    resultado=_resultado(marcou, sofreu),
                    marcou=marcou,
                    sofreu=sofreu,
                    adversario=nome_por_id.get(adversario_id, "—"),
                    em_casa=em_casa,
                    kickoff_at=jogo.kickoff_at,
                )
            )
        por_grupo[grupo] = coletado

    return {
        alvo.id: Retrospecto(
            team_id=alvo.id,
            jogos=por_grupo.get(frozenset(grupos[alvo.id]), []),
        )
        for alvo in alvos
    }


async def retrospecto_de(
    session: AsyncSession, team_id: int, *, limite: int = JOGOS_NO_RETROSPECTO
) -> Retrospecto:
    encontrados = await retrospectos(session, [team_id], limite=limite)
    return encontrados.get(team_id, Retrospecto(team_id=team_id, jogos=[]))


def resumos(retrospecto: Sequence[JogoDoRetrospecto]) -> str:
    return "".join(jogo.resultado for jogo in retrospecto)
