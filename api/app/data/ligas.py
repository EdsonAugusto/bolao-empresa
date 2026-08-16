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
