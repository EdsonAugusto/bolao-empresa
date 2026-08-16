"""Escolha do provedor.

Único lugar do projeto que conhece as implementações concretas. Qualquer outro
módulo pede um ``FootballDataProvider`` e não sabe qual chegou.
"""

from __future__ import annotations

from app.core.config import settings
from app.core.logging import get_logger
from app.core.redis import get_redis
from app.providers.base import FootballDataProvider, ProviderError
from app.providers.manual import ManualProvider

log = get_logger(__name__)


def build_provider(slug: str | None = None) -> FootballDataProvider:
    """Devolve o provedor pedido, ou o configurado.

    Cai para o manual quando o provedor externo não está utilizável — falta de
    chave não pode derrubar a plataforma inteira, porque o manual sempre
    funciona.
    """
    chosen = slug or settings.football_provider

    if chosen in {"manual", "fake"}:
        return ManualProvider()

    if chosen == "football_data_org":
        from app.providers.football_data_org import FootballDataOrgProvider

        try:
            return FootballDataOrgProvider(
                api_key=settings.football_data_org_key,
                requests_per_minute=settings.football_data_org_rate_limit_per_minute,
                cache=get_redis(),
            )
        except ProviderError as exc:
            log.warning("provider.indisponivel", provider=chosen, motivo=str(exc))
            return ManualProvider()

    if chosen == "globo":
        from app.providers.globo import GloboProvider

        return GloboProvider()

    if chosen == "api_football":
        from app.providers.api_football import ApiFootballProvider

        try:
            return ApiFootballProvider(
                api_key=settings.api_football_key,
                base_url=settings.api_football_base_url,
                requests_per_minute=settings.api_football_rate_limit_per_minute,
                cache=get_redis(),
            )
        except ProviderError as exc:
            log.warning("provider.indisponivel", provider=chosen, motivo=str(exc))
            return ManualProvider()

    raise ProviderError(f"provedor desconhecido: {chosen}")


def available_providers() -> list[dict[str, object]]:
    """Para a tela de administração: o que dá para usar nesta instalação."""
    return [
        {
            "slug": "manual",
            "name": "Cadastro manual",
            "ready": True,
            "detail": "Sempre disponível. Você cadastra os jogos e lança os placares.",
        },
        {
            "slug": "globo",
            "name": "GE Globo (coleta)",
            "ready": True,
            "detail": (
                "Calendário e resultados reais do Brasileirão, direto do site do GE. "
                "Não é API pública: os termos do Globo não autorizam coleta "
                "automatizada, e o endpoint pode mudar sem aviso."
            ),
        },
        {
            "slug": "football_data_org",
            "name": "football-data.org",
            "ready": bool(settings.football_data_org_key),
            "detail": (
                "Chave gratuita, mas o Brasileirão Série A está fora do plano free "
                "— só nas assinaturas pagas."
            ),
        },
        {
            "slug": "api_football",
            "name": "API-Football",
            "ready": bool(settings.api_football_key),
            "detail": (
                "Cobre Brasileirão, estaduais e Copinha. Free de 100 requisições/dia "
                "dá para importar a temporada e sincronizar resultados algumas vezes ao dia."
            ),
        },
    ]
