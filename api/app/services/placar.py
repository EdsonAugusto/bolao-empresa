"""Placar ao vivo sobreposto ao calendário que já existe.

Isto **não** importa jogo. Pega o placar de uma fonte que publica durante a
partida e o aplica em cima dos jogos que já estão no banco, venham eles de onde
vierem. É o que faz uma liga cujo calendário veio de um CSV lento ganhar placar
em tempo real sem duplicar competição, time nem jogo.

O casamento é por par, não por time
-----------------------------------
Nomes de clube não batem entre fontes: ``PSV`` contra ``PSV Eindhoven``,
``N.E.C. Nijmegen`` contra ``NEC Nijmegen``, ``Vitória SC`` contra ``Vitória de
Guimaraes``. Afrouxar a comparação um a um seria perigoso — ``Vitória`` casaria
com dois clubes de países diferentes.

O que torna isso seguro é casar o **confronto inteiro**: mesma liga, horário
próximo, mandante parecido **e** visitante parecido. Três restrições juntas.
Numa medição contra os treze jogos reais de 07 e 08/08 isso deu treze acertos,
nenhum erro e nenhuma ambiguidade — enquanto a comparação time a time perdia
três.

Havendo mais de um candidato, o desempate é o horário mais próximo; havendo
empate também nisso, o jogo é deixado em paz e o caso é registrado. Placar
errado é pior do que placar ausente.
"""

from __future__ import annotations

from dataclasses import dataclass, field
from datetime import UTC, datetime, timedelta

from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from app.core.logging import get_logger
from app.core.names import normalizar
from app.models import Competition, Fixture, FixtureStatus, Season, Team
from app.providers.base import ProviderError
from app.providers.espn import EspnScores, PlacarAoVivo
from app.services.catalog import COMECOU

log = get_logger(__name__)

#: Diferença de horário tolerada entre a nossa agenda e a da fonte. Generosa
#: porque fonte remarca e arredonda, e apertada o bastante para não confundir
#: dois jogos do mesmo par — que não acontecem no mesmo dia.
TOLERANCIA = timedelta(hours=6)


@dataclass
class ResultadoDoPlacar:
    jogos_atualizados: int = 0
    encerrados: int = 0
    sem_par: list[str] = field(default_factory=list)
    ambiguos: list[str] = field(default_factory=list)
    falhas: list[str] = field(default_factory=list)

    def as_dict(self) -> dict[str, object]:
        return {
            "updated": self.jogos_atualizados,
            "finished": self.encerrados,
            "unmatched": self.sem_par,
            "ambiguous": self.ambiguos,
            "failures": self.falhas,
        }


def _tokens(nome: str) -> set[str]:
    return {parte for parte in normalizar(nome).split() if parte}


def parecido(um: str, outro: str) -> bool:
    """Dois nomes designam o mesmo clube?

    Deliberadamente tolerante, porque quem garante a segurança é o casamento do
    par inteiro — ver o cabeçalho do módulo. Aceita quando um nome está contido
    no outro (``PSV`` ⊂ ``PSV Eindhoven``) ou quando compartilham uma palavra
    longa (``Nijmegen``, ``Vitória``).
    """
    esquerda, direita = _tokens(um), _tokens(outro)
    if not esquerda or not direita:
        return False
    if esquerda == direita:
        return True

    comuns = esquerda & direita
    if not comuns:
        return False
    return (
        esquerda <= direita or direita <= esquerda or max(len(palavra) for palavra in comuns) >= 4
    )


def casar(
    jogo: Fixture, casa: str, fora: str, candidatos: list[PlacarAoVivo]
) -> tuple[PlacarAoVivo | None, bool]:
    """Acha o placar do jogo entre os candidatos da liga.

    Devolve ``(placar, ambiguo)``. Ambíguo significa que dois candidatos ficaram
    igualmente próximos — e aí nada é aplicado.
    """
    encontrados = [
        (abs((item.kickoff_at - jogo.kickoff_at).total_seconds()), item)
        for item in candidatos
        if abs((item.kickoff_at - jogo.kickoff_at).total_seconds()) <= TOLERANCIA.total_seconds()
        and parecido(casa, item.casa)
        and parecido(fora, item.fora)
    ]
    if not encontrados:
        return None, False

    encontrados.sort(key=lambda par: par[0])
    if len(encontrados) > 1 and encontrados[0][0] == encontrados[1][0]:
        return None, True
    return encontrados[0][1], False


def ligas_espn_de(competition: Competition) -> list[str]:
    """Ligas da ESPN desta competição, em ordem de tentativa.

    Vem de ``provider_config``, gravado na importação ou pelo backfill.
    Competição sem mapeamento simplesmente não recebe placar ao vivo por aqui —
    não é erro.

    **Mais de uma** porque um torneio pode estar partido lá. A Champions tem a
    qualificação em ``uefa.champions_qual`` e o resto em ``uefa.champions``, e
    guardar só a principal fazia o placar dos jogos de agosto — que são todos
    de qualificação — nunca ser encontrado: a consulta ia para a liga certa do
    torneio e errada do jogo, voltava vazia, e o jogo ficava 0 a 0 em campo
    para sempre. Sem erro nenhum, porque não achar jogo não é falha.

    ``espn_league`` no singular continua sendo lido: é o que está gravado nas
    competições importadas antes desta mudança.
    """
    config = competition.provider_config or {}

    varias = config.get("espn_leagues")
    if isinstance(varias, list):
        return [str(item) for item in varias if item]

    unica = config.get("espn_league")
    return [str(unica)] if unica else []


async def aplicar_placares(
    session: AsyncSession,
    jogos: list[Fixture],
    *,
    fonte: EspnScores | None = None,
    now: datetime | None = None,
) -> ResultadoDoPlacar:
    """Busca e aplica o placar dos jogos informados.

    Uma requisição por (liga, dia) — os dez jogos de uma rodada de sábado saem
    numa consulta só. O placar entra pelas mesmas guardas da ingestão: estado
    final não regride, e placar em branco não apaga placar que já existe.
    """
    resultado = ResultadoDoPlacar()
    if not jogos:
        return resultado

    agora = now or datetime.now(UTC)
    proprio = fonte is None
    fonte = fonte or EspnScores()

    try:
        contexto = await _contexto(session, jogos)
        cache: dict[tuple[str, object], list[PlacarAoVivo]] = {}

        for jogo in jogos:
            competicao, casa, fora = contexto.get(jogo.id, (None, None, None))
            if competicao is None or casa is None or fora is None:
                continue

            ligas = ligas_espn_de(competicao)
            if not ligas:
                continue

            # Percorre as ligas do torneio até uma reconhecer o confronto. Para
            # na primeira que acha: numa competição de uma liga só isso é
            # exatamente o que era antes, uma requisição por (liga, dia).
            achado = None
            ambiguo = False
            for liga in ligas:
                chave = (liga, jogo.kickoff_at.date())
                if chave not in cache:
                    try:
                        cache[chave] = await fonte.placares(liga, jogo.kickoff_at.date())
                    except ProviderError as exc:
                        cache[chave] = []
                        resultado.falhas.append(f"{competicao.name}: {exc}")
                        log.warning("placar.fonte_falhou", liga=liga, erro=str(exc))

                achado, ambiguo = casar(jogo, casa, fora, cache[chave])
                if achado is not None or ambiguo:
                    break

            if ambiguo:
                resultado.ambiguos.append(f"{casa} x {fora}")
                log.warning("placar.ambiguo", fixture=jogo.id, casa=casa, fora=fora)
                continue
            if achado is None:
                resultado.sem_par.append(f"{casa} x {fora}")
                continue

            if _aplicar(jogo, achado, agora):
                resultado.jogos_atualizados += 1
                if jogo.status is FixtureStatus.FINISHED:
                    resultado.encerrados += 1
    finally:
        if proprio:
            await fonte.aclose()

    log.info(
        "placar.aplicado",
        atualizados=resultado.jogos_atualizados,
        encerrados=resultado.encerrados,
        sem_par=len(resultado.sem_par),
    )
    return resultado


def _aplicar(jogo: Fixture, placar: PlacarAoVivo, agora: datetime) -> bool:
    """Grava o que mudou. Devolve se mexeu em alguma coisa.

    As guardas são as mesmas da ingestão, e pelos mesmos motivos: um estado
    final não regride (senão a trava de palpite reabre num jogo decidido), e
    placar em branco não apaga placar (senão uma passada ruim zera a rodada).
    """
    mudou = False

    tem_placar = placar.home_ft is not None and placar.away_ft is not None
    if tem_placar and (jogo.home_ft, jogo.away_ft) != (placar.home_ft, placar.away_ft):
        jogo.home_ft, jogo.away_ft = placar.home_ft, placar.away_ft
        mudou = True

    if placar.minuto is not None and jogo.minute != placar.minuto:
        jogo.minute = placar.minuto
        mudou = True

    novo = placar.status
    atual = jogo.status
    permitido = (
        atual is None
        or (not atual.is_final and not (atual in COMECOU and novo is FixtureStatus.SCHEDULED))
        or novo.is_final
    )
    if permitido and novo is not atual:
        jogo.status = novo
        mudou = True

    if mudou:
        jogo.synced_at = agora
        jogo.provider_updated_at = agora
    return mudou


async def _contexto(
    session: AsyncSession, jogos: list[Fixture]
) -> dict[int, tuple[Competition | None, str | None, str | None]]:
    """Competição e nomes dos times de cada jogo, em duas consultas."""
    ids_de_temporada = {jogo.season_id for jogo in jogos}
    linhas = (
        await session.execute(
            select(Season.id, Competition)
            .join(Competition, Competition.id == Season.competition_id)
            .where(Season.id.in_(ids_de_temporada))
        )
    ).all()
    # `dict(linhas)` seria mais curto, mas o mypy não infere o tipo a partir
    # de `Sequence[Row]` — a anotação explícita vale mais que a concisão.
    por_temporada: dict[int, Competition] = {}
    for season_id, competition in linhas:
        por_temporada[season_id] = competition

    ids_de_time = {jogo.home_team_id for jogo in jogos} | {jogo.away_team_id for jogo in jogos}
    nomes = {
        time.id: time.name
        for time in (await session.scalars(select(Team).where(Team.id.in_(ids_de_time)))).all()
    }

    return {
        jogo.id: (
            por_temporada.get(jogo.season_id),
            nomes.get(jogo.home_team_id),
            nomes.get(jogo.away_team_id),
        )
        for jogo in jogos
    }
