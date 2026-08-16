"""Clubes da Série A e geração de uma temporada completa.

**O que isto é e o que não é.** Aqui não está a tabela oficial da CBF — ela não
é pública em formato aberto, e sem provedor externo não há como obtê-la. O que
este módulo gera é uma temporada **estruturalmente correta**: 20 clubes reais,
turno e returno completos (cada time enfrenta todos os outros duas vezes, uma
em casa e uma fora), 38 rodadas, 380 jogos, com datas plausíveis.

Serve para o bolão existir hoje. Quando o calendário oficial sair, o organizador
reimporta por CSV ou liga o football-data.org — a ingestão é idempotente por
``(provider_id, external_id)``, então atualizar não duplica nada.

Os escudos são desenhados pela plataforma a partir das cores de cada clube. Os
oficiais são marca registrada, e apontar para imagem de terceiro quebraria o uso
offline, que é justamente a razão de o provedor manual ser o padrão.
"""

from __future__ import annotations

from dataclasses import dataclass
from datetime import UTC, datetime, time, timedelta


@dataclass(frozen=True, slots=True)
class Club:
    slug: str
    name: str
    code: str
    """Sigla de três letras, usada no escudo e nas telas estreitas."""

    primary: str
    secondary: str
    state: str


#: Participantes da Série A 2026, conferidos contra a página da temporada na
#: Wikipédia em 31/07/2026.
#:
#: Esta lista muda todo ano com acessos e rebaixamentos — é o primeiro lugar a
#: corrigir quando a temporada virar. Ela só é usada pelo calendário **gerado**;
#: importando do Globo, os clubes vêm de lá.
CLUBS: tuple[Club, ...] = (
    Club("athletico-pr", "Athletico Paranaense", "CAP", "#B4141E", "#111111", "PR"),
    Club("atletico-mg", "Atlético Mineiro", "CAM", "#111111", "#FFFFFF", "MG"),
    Club("bahia", "Bahia", "BAH", "#0057B8", "#E30613", "BA"),
    Club("botafogo", "Botafogo", "BOT", "#2B2B2B", "#E8E8E8", "RJ"),
    Club("bragantino", "Red Bull Bragantino", "BGT", "#C4122E", "#F2F2F2", "SP"),
    Club("chapecoense", "Chapecoense", "CHA", "#0F7A3D", "#FFFFFF", "SC"),
    Club("corinthians", "Corinthians", "COR", "#0B0B0B", "#FFFFFF", "SP"),
    Club("coritiba", "Coritiba", "CFC", "#0B6B3A", "#FFFFFF", "PR"),
    Club("cruzeiro", "Cruzeiro", "CRU", "#0A3B8C", "#F5F5F5", "MG"),
    Club("flamengo", "Flamengo", "FLA", "#C52613", "#1A1A1A", "RJ"),
    Club("fluminense", "Fluminense", "FLU", "#7A1122", "#0C7A45", "RJ"),
    Club("gremio", "Grêmio", "GRE", "#0D80BF", "#1A1A1A", "RS"),
    Club("internacional", "Internacional", "INT", "#E5050F", "#FFFFFF", "RS"),
    Club("mirassol", "Mirassol", "MIR", "#F5C518", "#0B7A3B", "SP"),
    Club("palmeiras", "Palmeiras", "PAL", "#006437", "#FFFFFF", "SP"),
    Club("remo", "Remo", "REM", "#12305B", "#FFFFFF", "PA"),
    Club("santos", "Santos", "SAN", "#111111", "#FFFFFF", "SP"),
    Club("sao-paulo", "São Paulo", "SAO", "#FE0000", "#111111", "SP"),
    Club("vasco", "Vasco da Gama", "VAS", "#1A1A1A", "#FFFFFF", "RJ"),
    Club("vitoria", "Vitória", "VIT", "#C8102E", "#1A1A1A", "BA"),
)

CLUBS_BY_SLUG: dict[str, Club] = {club.slug: club for club in CLUBS}

#: Horários usuais de Brasília. O último jogo de cada dia é o "clássico".
KICKOFF_SLOTS_SATURDAY = (time(16, 0), time(18, 30), time(21, 0))
KICKOFF_SLOTS_SUNDAY = (time(11, 0), time(16, 0), time(18, 30), time(20, 30))

BRASILIA_OFFSET = timedelta(hours=-3)


@dataclass(frozen=True, slots=True)
class GeneratedMatch:
    round_number: int
    home_slug: str
    away_slug: str
    kickoff_at: datetime
    """UTC."""


def round_robin(clubs: list[str]) -> list[list[tuple[str, str]]]:
    """Turno completo pelo método do círculo.

    Um time fica fixo e os outros giram. Com 20 clubes dá 19 rodadas de 10
    jogos, e todo mundo enfrenta todo mundo exatamente uma vez.

    O mando alterna a cada rodada para nenhum time acumular jogos seguidos em
    casa — que é o que aconteceria com a implementação ingênua.
    """
    if len(clubs) % 2 != 0:
        raise ValueError("o método do círculo exige um número par de times")

    fixo, giram = clubs[0], list(clubs[1:])
    metade = len(clubs) // 2
    rodadas: list[list[tuple[str, str]]] = []

    for indice in range(len(clubs) - 1):
        ordem = [fixo, *giram]
        casa = ordem[:metade]
        fora = ordem[metade:][::-1]

        rodada = [
            (mandante, visitante) if indice % 2 == 0 else (visitante, mandante)
            for mandante, visitante in zip(casa, fora, strict=True)
        ]
        rodadas.append(rodada)
        giram = [giram[-1], *giram[:-1]]

    return rodadas


def first_saturday_of_april(year: int) -> datetime:
    """Início natural do Brasileirão."""
    dia = datetime(year, 4, 1, tzinfo=UTC)
    while dia.weekday() != 5:  # 5 = sábado
        dia += timedelta(days=1)
    return dia


def next_saturday_on_or_after(momento: datetime) -> datetime:
    """Próximo sábado, contando o próprio dia se já for sábado."""
    dia = momento.replace(hour=0, minute=0, second=0, microsecond=0)
    while dia.weekday() != 5:
        dia += timedelta(days=1)
    return dia


def resolve_first_round(year: int, now: datetime) -> datetime:
    """Quando a rodada 1 acontece.

    Abril, salvo se abril já passou — aí começa no próximo sábado. Sem isto,
    criar o campeonato do ano corrente em agosto nasce com metade das rodadas
    já fechadas para palpite, e o bolão não serve para nada.
    """
    abril = first_saturday_of_april(year)
    if abril >= now:
        return abril
    return next_saturday_on_or_after(now)


def generate_season(
    year: int,
    *,
    first_round_saturday: datetime | None = None,
    clubs: tuple[Club, ...] = CLUBS,
) -> list[GeneratedMatch]:
    """Temporada inteira: turno, returno e datas.

    O returno repete o turno com o mando invertido, que é como o Brasileirão
    funciona.

    ``first_round_saturday`` é obrigatório na prática — quem chama decide a
    data, porque este módulo não olha o relógio. Sem ele, cai em abril do ano
    pedido.
    """
    if first_round_saturday is None:
        first_round_saturday = first_saturday_of_april(year)

    slugs = [club.slug for club in clubs]
    turno = round_robin(slugs)
    returno = [[(fora, casa) for casa, fora in rodada] for rodada in turno]
    todas = turno + returno

    partidas: list[GeneratedMatch] = []
    for indice, rodada in enumerate(todas):
        numero = indice + 1
        sabado = first_round_saturday + timedelta(weeks=indice)
        for posicao, (casa, fora) in enumerate(rodada):
            partidas.append(
                GeneratedMatch(
                    round_number=numero,
                    home_slug=casa,
                    away_slug=fora,
                    kickoff_at=_slot_for(sabado, posicao),
                )
            )

    return partidas


def _slot_for(saturday: datetime, position: int) -> datetime:
    """Espalha os 10 jogos da rodada entre sábado e domingo.

    A data é montada em horário de Brasília e convertida para UTC aqui — a
    conversão acontece uma vez, na borda, e nunca mais. Um jogo às 21h de
    sábado vira meia-noite de domingo em UTC, e é exatamente por isso que a
    conversão não pode ficar espalhada pelo código.
    """
    sabado_slots = len(KICKOFF_SLOTS_SATURDAY)

    if position < sabado_slots:
        dia = saturday
        hora = KICKOFF_SLOTS_SATURDAY[position]
    else:
        dia = saturday + timedelta(days=1)
        hora = KICKOFF_SLOTS_SUNDAY[(position - sabado_slots) % len(KICKOFF_SLOTS_SUNDAY)]

    local = datetime.combine(dia.date(), hora)
    return (local - BRASILIA_OFFSET).replace(tzinfo=UTC)
