"""Interface de provedor de dados esportivos.

Nenhum módulo de domínio importa um cliente concreto. O contrato é este arquivo
e os DTOs abaixo — que são deliberadamente pobres: só o que a plataforma
precisa, sem nada do formato de nenhum provedor específico.

A tradução do status é responsabilidade de cada implementação. O status cru do
provedor nunca chega ao banco.
"""

from __future__ import annotations

import abc
import hashlib
from collections.abc import Sequence
from dataclasses import dataclass, field
from datetime import date, datetime

from app.models.enums import CompetitionType, FixtureStatus


@dataclass(frozen=True, slots=True)
class ProviderCompetition:
    external_id: str
    slug: str
    name: str
    country: str | None = None
    type: CompetitionType = CompetitionType.LEAGUE
    logo_url: str | None = None

    config: dict[str, object] = field(default_factory=dict)
    """O que o coletor precisa para voltar aqui sozinho.

    Sem isto a recoleta automática não existe: o ``external_id`` não reconstrói
    o coletor do GE, que também exige a fase. Cada provedor preenche o que é
    seu, e ``competitions.provider_config`` guarda.

    Vazio significa "o ``external_id`` basta" — é o caso do CSV e da Wikipédia.
    """


@dataclass(frozen=True, slots=True)
class ProviderSeason:
    external_id: str | None
    competition_external_id: str
    year: int
    start_date: date | None = None
    end_date: date | None = None
    is_current: bool = False


@dataclass(frozen=True, slots=True)
class ProviderTeam:
    external_id: str
    name: str
    slug: str
    short_name: str | None = None
    crest_url: str | None = None
    country: str | None = None


@dataclass(frozen=True, slots=True)
class ProviderRound:
    name: str
    number: int | None = None
    stage_name: str | None = None
    is_knockout: bool = False


@dataclass(frozen=True, slots=True)
class ProviderFixture:
    external_id: str
    home_team_external_id: str
    away_team_external_id: str
    kickoff_at: datetime
    """Sempre em UTC. Converter é obrigação da implementação."""

    status: FixtureStatus = FixtureStatus.SCHEDULED
    round: ProviderRound | None = None
    minute: int | None = None
    venue: str | None = None

    home_ft: int | None = None
    away_ft: int | None = None
    home_et: int | None = None
    away_et: int | None = None
    home_pen: int | None = None
    away_pen: int | None = None

    provider_updated_at: datetime | None = None

    def __post_init__(self) -> None:
        if self.kickoff_at.tzinfo is None:
            raise ValueError(
                f"kickoff_at de {self.external_id} veio sem timezone; "
                "o provedor precisa devolver UTC explícito"
            )


@dataclass(frozen=True, slots=True)
class ProviderSnapshot:
    """Tudo que uma temporada precisa, em uma tacada."""

    competition: ProviderCompetition
    season: ProviderSeason
    teams: Sequence[ProviderTeam] = field(default_factory=tuple)
    fixtures: Sequence[ProviderFixture] = field(default_factory=tuple)


#: ``fixtures.external_id`` é ``varchar(64)``. Nome de clube inglês somado a
#: nome de fase estoura isso com folga — e o erro só aparece na hora do INSERT,
#: com metade da temporada já gravada.
EXTERNAL_ID_MAX = 64


def build_external_id(*partes: str, limite: int = EXTERNAL_ID_MAX) -> str:
    """Identificador externo legível e garantidamente dentro do limite.

    Mantém o começo legível (ajuda a depurar olhando o banco) e fecha com um
    resumo das partes inteiras, para que dois jogos diferentes nunca colidam
    depois do corte. É determinístico: reimportar cai na mesma linha, que é o
    que faz a ingestão ser idempotente.
    """
    inteiro = "-".join(parte.strip("-") for parte in partes if parte)
    if len(inteiro) <= limite:
        return inteiro

    resumo = hashlib.blake2b(inteiro.encode(), digest_size=4).hexdigest()
    return f"{inteiro[: limite - len(resumo) - 1]}-{resumo}"


class ProviderError(Exception):
    """Falha ao falar com o provedor. Nunca sobe crua para o endpoint."""


class ProviderQuotaExceeded(ProviderError):
    """Cota do plano estourada. O job deve recuar, não insistir."""


class FootballDataProvider(abc.ABC):
    """Contrato que todo provedor cumpre.

    Implementações existentes:

    - ``ManualProvider`` — o organizador cadastra tudo. Zero rede, zero custo,
      funciona offline. É o padrão.
    - ``FootballDataOrgProvider`` — tier gratuito do football-data.org.
    - ``ApiFootballProvider`` — api-sports.io, para quem tiver chave.
    """

    slug: str
    name: str
    #: Provedores manuais não têm o que sincronizar.
    supports_sync: bool = True

    #: A fonte publica placar **enquanto a bola rola**, não só depois do apito.
    #: Só quem marca isto entra na coleta de dois em dois minutos; para os
    #: outros, bater durante o jogo é reler o mesmo arquivo à toa.
    supports_live: bool = False

    #: A fonte sabe entregar rodadas isoladas, sem baixar a temporada inteira.
    #: É o que torna a coleta ao vivo barata: uma requisição cobre os dez jogos
    #: da rodada em andamento, em vez das trinta e oito da temporada.
    supports_partial: bool = False

    async def import_rounds(
        self, competition_external_id: str, year: int, rounds: Sequence[int]
    ) -> ProviderSnapshot:
        """Retrato só das rodadas pedidas.

        Só faz sentido em quem declara ``supports_partial``. O padrão recai na
        temporada inteira, que é correto — apenas caro.
        """
        return await self.import_season(competition_external_id, year)

    @abc.abstractmethod
    async def list_competitions(self) -> Sequence[ProviderCompetition]: ...

    @abc.abstractmethod
    async def list_seasons(self, competition_external_id: str) -> Sequence[ProviderSeason]: ...

    @abc.abstractmethod
    async def list_teams(
        self, competition_external_id: str, year: int
    ) -> Sequence[ProviderTeam]: ...

    @abc.abstractmethod
    async def list_fixtures(
        self, competition_external_id: str, year: int
    ) -> Sequence[ProviderFixture]: ...

    @abc.abstractmethod
    async def get_fixture(self, external_id: str) -> ProviderFixture | None: ...

    async def import_season(self, competition_external_id: str, year: int) -> ProviderSnapshot:
        """Monta o retrato completo de uma temporada.

        O padrão serve para qualquer provedor; quem tiver um endpoint que traz
        tudo de uma vez sobrescreve e economiza requisição.
        """
        competitions = await self.list_competitions()
        competition = next(
            (item for item in competitions if item.external_id == competition_external_id), None
        )
        if competition is None:
            raise ProviderError(f"competição {competition_external_id} não existe em {self.slug}")

        seasons = await self.list_seasons(competition_external_id)
        season = next((item for item in seasons if item.year == year), None)
        if season is None:
            raise ProviderError(
                f"temporada {year} de {competition_external_id} não existe em {self.slug}"
            )

        return ProviderSnapshot(
            competition=competition,
            season=season,
            teams=await self.list_teams(competition_external_id, year),
            fixtures=await self.list_fixtures(competition_external_id, year),
        )

    async def aclose(self) -> None:
        """Libera recursos. Sem efeito em provedores sem conexão."""
        return None
