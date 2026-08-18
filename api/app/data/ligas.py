"""Catálogo das ligas do mundo com calendário aberto.

O GE resolve o futebol brasileiro, mas não expõe as temporadas europeias novas:
a página de cada campeonato só publica o identificador da temporada corrente, e
até a virada de temporada o que está lá é a de trás. Foi exatamente isso que fez
a plataforma dizer "temporada encerrada" para a Premier League enquanto o Google
já mostrava a primeira rodada de 2026-27.

O ``fixturedownload.com`` publica a tabela completa de cada uma dessas ligas em
CSV, **em UTC**, sem chave e sem cota. É a fonte destas entradas.

Cada liga aqui foi baixada e conferida: o número de jogos e a data da primeira
rodada batem com o calendário oficial divulgado. A data está em
``verificado_em``.
"""

from __future__ import annotations

from dataclasses import dataclass


@dataclass(frozen=True, slots=True)
class Liga:
    slug: str
    """Identificador no fixturedownload (``epl``, ``la-liga``…)."""

    name: str
    country: str
    rounds: int
    fixtures: int
    """Quantos jogos a temporada inteira tem. Serve para conferir a coleta."""

    first_round: str
    """Data da primeira rodada, em ISO. Só para a tela mostrar."""

    verificado_em: str

    virada: bool = True
    """Temporada que atravessa o ano (agosto a maio). Falso na MLS, que é de
    ano civil."""

    def season_year(self, ano_do_arquivo: int) -> int:
        """Ano com que a temporada é gravada no banco.

        Liga de virada é referida pelo ano em que **termina** — é a convenção
        que o coletor do GE já usa (``...-2025-2026`` vira 2026). Sem isso a
        Premier 2026-27 entraria como "2026" e cairia em cima da 2025-26 que já
        está no banco, com nome igual e temporada diferente.
        """
        return ano_do_arquivo + 1 if self.virada else ano_do_arquivo


#: Ano da temporada publicada hoje pelo fixturedownload. Liga de virada é
#: identificada pelo ano em que **começa**: ``epl-2026`` é a 2026-27.
TEMPORADA = 2026

#: Conferidas em 31/07/2026 baixando o CSV de cada uma.
LIGAS: tuple[Liga, ...] = (
    Liga("epl", "Premier League", "Inglaterra", 38, 380, "2026-08-21", "2026-07-31"),
    Liga("la-liga", "LaLiga", "Espanha", 38, 380, "2026-08-15", "2026-07-31"),
    Liga("serie-a", "Serie A", "Itália", 38, 380, "2026-08-22", "2026-07-31"),
    Liga("bundesliga", "Bundesliga", "Alemanha", 34, 306, "2026-08-28", "2026-07-31"),
    Liga("ligue-1", "Ligue 1", "França", 34, 306, "2026-08-21", "2026-07-31"),
    Liga("primeira-liga", "Primeira Liga", "Portugal", 34, 306, "2026-08-07", "2026-07-31"),
    Liga("eredivisie", "Eredivisie", "Holanda", 34, 306, "2026-08-07", "2026-07-31"),
    Liga("mls", "MLS", "Estados Unidos", 34, 510, "2026-02-21", "2026-07-31", virada=False),
    Liga("championship", "Championship", "Inglaterra", 46, 552, "2026-08-14", "2026-07-31"),
    Liga("super-lig", "Süper Lig", "Turquia", 34, 306, "2026-08-15", "2026-07-31"),
    Liga(
        "scottish-premiership",
        "Scottish Premiership",
        "Escócia",
        33,
        198,
        "2026-07-31",
        "2026-07-31",
    ),
    Liga("ligue-2", "Ligue 2", "França", 34, 306, "2026-08-08", "2026-07-31"),
    Liga("efl-league-one", "EFL League One", "Inglaterra", 46, 552, "2026-08-15", "2026-07-31"),
)

LIGAS_POR_SLUG: dict[str, Liga] = {liga.slug: liga for liga in LIGAS}


@dataclass(frozen=True, slots=True)
class MataMata:
    """Torneio eliminatório, coletado da Wikipédia em português.

    Mata-mata não tem calendário em CSV: o chaveamento só existe depois do
    sorteio, e o GE não expõe as fases eliminatórias no endpoint de tabela.
    """

    slug: str
    name: str
    country: str
    artigo: str
    """Título exato do artigo na Wikipédia em português."""

    year: int
    verificado_em: str


#: Conferidos em 31/07/2026 contra o artigo.
MATA_MATAS: tuple[MataMata, ...] = (
    MataMata(
        slug="libertadores-mata-mata",
        name="Libertadores (mata-mata)",
        country="América do Sul",
        artigo="Fase final da Copa Libertadores da América de 2026",
        year=2026,
        verificado_em="2026-07-31",
    ),
)

MATA_MATAS_POR_SLUG: dict[str, MataMata] = {item.slug: item for item in MATA_MATAS}


@dataclass(frozen=True, slots=True)
class Copa:
    """Copa eliminatória coletada da ESPN.

    Mata-mata brasileiro não tem calendário em CSV nem chaveamento com horário
    na Wikipédia, e o GE devolve lista vazia para fase eliminatória. A ESPN
    publica data em UTC, mandante, placar, pênaltis e fase — inclusive de jogo
    ainda não realizado, que é o que um bolão precisa.
    """

    slug: str
    name: str
    country: str

    espn_leagues: tuple[str, ...]
    """Códigos da liga na ESPN, na ordem em que a temporada acontece.

    Mais de um porque um torneio pode estar partido lá: a Champions tem a
    qualificação em `uefa.champions_qual` e o resto em `uefa.champions`. São a
    mesma competição para quem palpita.

    O da Copa do Brasil é `bra.copa_do_brazil`, com **z** —
    `bra.copa_do_brasil` devolve HTTP 400.
    """

    year: int
    """Ano com que a temporada é gravada. Torneio de virada é referido pelo ano
    em que TERMINA, igual às ligas: a Champions 2026-27 entra como 2027."""

    verificado_em: str

    virada: bool = False
    """Temporada que atravessa o ano (agosto a maio). A coleta então vai de
    julho do ano anterior a junho deste."""

    @property
    def rotulo(self) -> str:
        """Como a temporada aparece na tela. `2026-27`, não `2027`."""
        return f"{self.year - 1}-{self.year % 100:02d}" if self.virada else str(self.year)


#: Conferidas em 17/08/2026 contra a API da ESPN.
#:
#: A Champions NÃO entra aqui: ela já vem pelo fixturedownload, em `LIGAS`, e
#: duas fontes para a mesma competição criariam duas competições lado a lado no
#: banco — com times e jogos duplicados.
COPAS: tuple[Copa, ...] = (
    Copa(
        slug="copa-do-brasil",
        name="Copa do Brasil",
        country="Brasil",
        espn_leagues=("bra.copa_do_brazil",),
        year=2026,
        verificado_em="2026-08-17",
    ),
    # A Champions começa em JULHO, pelas eliminatórias — e é aí que ela some de
    # quem olha só `uefa.champions`, que só passa a ter jogo depois do sorteio
    # da fase de liga, no fim de agosto. Em 17/08/2026 a qualificação tinha 90
    # jogos coletáveis, 14 deles ainda por vir, enquanto a liga principal
    # respondia zero. Foi assim que a Champions "não veio" na primeira
    # tentativa: a temporada existia, na outra porta.
    Copa(
        slug="champions-league",
        name="Champions League",
        country="Europa",
        espn_leagues=("uefa.champions_qual", "uefa.champions"),
        year=2027,
        verificado_em="2026-08-17",
        virada=True,
    ),
)

COPAS_POR_SLUG: dict[str, Copa] = {item.slug: item for item in COPAS}
