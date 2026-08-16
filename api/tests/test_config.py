"""Configuração — erro aqui aparece só em produção, então testamos cedo."""

from __future__ import annotations

import pytest
from pydantic import ValidationError

from app.core.config import Settings


def test_database_url_usa_driver_async() -> None:
    settings = Settings(
        postgres_user="u",
        postgres_password="p",
        postgres_db="d",
        postgres_host="h",
        postgres_port=5432,
    )

    assert settings.database_url.startswith("postgresql+asyncpg://")
    assert settings.database_url.endswith("/d")
    assert settings.sync_database_url.startswith("postgresql://")


def test_cors_origins_aceita_lista_separada_por_virgula() -> None:
    settings = Settings(cors_origins="http://a.test, http://b.test ,")  # type: ignore[arg-type]

    assert settings.cors_origins == ["http://a.test", "http://b.test"]


def test_cors_origins_vindo_do_ambiente(monkeypatch: pytest.MonkeyPatch) -> None:
    """Regressão: o caminho por variável de ambiente é outro código.

    O pydantic-settings faz JSON-decode de campos de lista antes de qualquer
    validator. Sem `NoDecode` no campo, `CORS_ORIGINS=http://a,http://b`
    derruba a aplicação na subida — e um teste que passa a string por
    argumento não enxerga isso, porque não percorre o mesmo caminho.
    """
    monkeypatch.setenv("CORS_ORIGINS", "http://a.test,http://b.test")

    settings = Settings()

    assert settings.cors_origins == ["http://a.test", "http://b.test"]


def test_cors_origins_usa_o_padrao_quando_ausente(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.delenv("CORS_ORIGINS", raising=False)

    assert Settings().cors_origins == ["http://localhost:8080"]


def test_redis_url_inclui_o_banco() -> None:
    settings = Settings(redis_host="r", redis_port=6379, redis_db=3)

    assert settings.redis_url == "redis://r:6379/3"


#: Valores mínimos para uma `Settings` de produção passar pelo endurecimento.
DE_PRODUCAO = {
    "environment": "production",
    "debug": False,
    "secret_key": "u" * 64,
    "postgres_password": "senha-que-nao-esta-no-repositorio",
}


def test_is_production_so_em_production() -> None:
    assert Settings(**DE_PRODUCAO).is_production is True
    assert Settings(**{**DE_PRODUCAO, "environment": "staging"}).is_production is False
    assert Settings(environment="development").is_production is False


def test_staging_tambem_e_endurecido() -> None:
    """Staging fica na internet do mesmo jeito que produção.

    Endurecer só por igualdade com ``production`` deixava a chave do
    repositório assinando token de verdade num ambiente público — o modo de
    falhar silencioso que este validador existe para fechar.
    """
    with pytest.raises(ValidationError, match="SECRET_KEY"):
        Settings(
            environment="staging", secret_key="troque-esta-chave-em-producao-ela-e-so-para-dev"
        )


def test_producao_recusa_a_chave_de_exemplo() -> None:
    """A chave do repositório assina token; deixá-la valer é entregar a sessão."""
    with pytest.raises(ValidationError, match="SECRET_KEY"):
        Settings(**{**DE_PRODUCAO, "secret_key": "troque-esta-chave-em-producao-ela-e-so-para-dev"})


def test_producao_recusa_chave_curta() -> None:
    with pytest.raises(ValidationError, match="SECRET_KEY"):
        Settings(**{**DE_PRODUCAO, "secret_key": "curta-demais"})


def test_producao_recusa_a_senha_de_exemplo_do_banco() -> None:
    with pytest.raises(ValidationError, match="POSTGRES_PASSWORD"):
        Settings(**{**DE_PRODUCAO, "postgres_password": "bolao_dev_password"})


def test_producao_recusa_debug_ligado() -> None:
    with pytest.raises(ValidationError, match="DEBUG"):
        Settings(**{**DE_PRODUCAO, "debug": True})


def test_producao_desliga_origem_de_rede_privada_sozinha() -> None:
    """`Origin` é escolhido por quem chama: na internet aberta não prova nada."""
    assert (
        Settings(**DE_PRODUCAO, allow_private_network_origins=True).allow_private_network_origins
        is False
    )


def test_desenvolvimento_nao_e_endurecido() -> None:
    """Em rede local os valores de exemplo são o caminho normal, não um erro."""
    local = Settings(environment="development", debug=True)
    assert local.allow_private_network_origins is True
