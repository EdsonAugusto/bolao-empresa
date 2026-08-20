"""Configuração da aplicação.

Fonte única de verdade para tudo que vem do ambiente. Nenhum módulo lê
``os.environ`` diretamente — importa ``settings`` daqui.
"""

from __future__ import annotations

from functools import lru_cache
from typing import Annotated, Literal

from pydantic import Field, PostgresDsn, RedisDsn, field_validator, model_validator
from pydantic_settings import BaseSettings, NoDecode, SettingsConfigDict

Environment = Literal["development", "test", "staging", "production"]

#: Valores que existem no repositório e portanto não são segredo de ninguém.
_CHAVES_DE_EXEMPLO = frozenset(
    {
        "troque-esta-chave-em-producao-ela-e-so-para-dev",
        "chave-de-ci-nao-usada-em-producao",
        "",
    }
)
_SENHAS_DE_EXEMPLO = frozenset({"bolao_dev_password", "bolao_ci_password", ""})


class Settings(BaseSettings):
    model_config = SettingsConfigDict(
        env_file=".env",
        env_file_encoding="utf-8",
        extra="ignore",
        case_sensitive=False,
    )

    # --- Ambiente ---------------------------------------------------------
    environment: Environment = "development"
    debug: bool = False
    log_level: str = "INFO"
    project_name: str = "Plataforma de Bolões"

    # --- Postgres ---------------------------------------------------------
    postgres_user: str = "bolao"
    postgres_password: str = "bolao_dev_password"
    postgres_db: str = "bolao"
    postgres_host: str = "postgres"
    postgres_port: int = 5432

    # --- Redis ------------------------------------------------------------
    redis_host: str = "redis"
    redis_port: int = 6379
    redis_db: int = 0

    # --- Segurança --------------------------------------------------------
    secret_key: str = "troque-esta-chave-em-producao-ela-e-so-para-dev"
    #: Quanto o token de acesso vale antes de precisar ser renovado.
    #:
    #: Trinta minutos obrigavam uma renovação em toda abertura do aplicativo —
    #: ninguém abre um bolão de meia em meia hora. E cada renovação é uma
    #: chance de falhar: rede oscilando, resposta perdida, rotação lida como
    #: reuso. Doze horas fazem o caminho comum não rotacionar nada.
    #:
    #: O prazo não é a defesa contra token roubado — a defesa é a rotação do
    #: refresh com derrubada de família. Este número é a troca entre quantas
    #: vezes a renovação pode dar errado e quanto tempo um acesso copiado
    #: sobrevive.
    access_token_ttl_minutes: int = 720
    #: Quanto tempo o refresh vale sem ser usado.
    #:
    #: Cada renovação emite um refresh novo com prazo cheio, então na prática
    #: isto é "quanto tempo o app pode ficar fechado". Trinta dias deslogava
    #: quem viaja ou some numa pausa do campeonato; seis meses cobre uma
    #: temporada inteira. A defesa contra token roubado não é o prazo — é a
    #: rotação com derrubada de família a cada reuso.
    refresh_token_ttl_days: int = 180
    # `NoDecode` é obrigatório aqui: sem ele o pydantic-settings tenta fazer
    # JSON-decode do valor vindo do ambiente ANTES de qualquer validator, e
    # `CORS_ORIGINS=http://a,http://b` explode na subida do container.
    cors_origins: Annotated[list[str], NoDecode] = Field(
        default_factory=lambda: ["http://localhost:8080"]
    )

    # --- Provedor de dados esportivos -------------------------------------
    # `manual` é o padrão e não depende de nada externo.
    football_provider: Literal["manual", "globo", "football_data_org", "api_football"] = "manual"

    football_data_org_key: str = ""
    football_data_org_rate_limit_per_minute: int = 10

    #: Onde os anexos de relato ficam. Fora de `/app` de propósito: `/app` é
    #: um bind-mount do repositório, e anexo de usuário não entra no código.
    uploads_dir: str = "/data/uploads"

    api_football_key: str = ""
    api_football_base_url: str = "https://v3.football.api-sports.io"
    api_football_rate_limit_per_minute: int = 10
    api_football_daily_quota: int = 100

    # --- Notificações -----------------------------------------------------
    # `in_app` é o padrão: não depende de serviço externo nenhum.
    #: `push` entra por padrão e não faz mal onde não serve: o canal só é
    #: construído se houver chave VAPID, e ela só existe em instalação com
    #: HTTPS. Na rede local a lista vira `in_app` sozinha.
    notification_channels: Annotated[list[str], NoDecode] = Field(
        default_factory=lambda: ["in_app", "push"]
    )
    telegram_bot_token: str = ""

    # --- Rede local -------------------------------------------------------
    # Quando ligado, a API aceita qualquer Origin de rede privada. É o que
    # permite abrir a plataforma pelo IP do host no celular de quem chegou.
    #
    # Num servidor público isto não prova nada: `Origin` é escolhido por quem
    # chama, então uma página hostil pode se anunciar como `http://10.0.0.9` e
    # receber `Access-Control-Allow-Credentials: true`. O validador abaixo
    # desliga a opção sozinho em produção.
    allow_private_network_origins: bool = True

    # --- Quem pode criar conta --------------------------------------------
    #: ``aberto`` — qualquer pessoa se cadastra. É o padrão em rede local, onde
    #: estar na rede já é o convite.
    #: ``convite`` — o cadastro exige o código de um bolão. É o padrão que o
    #: instalador de servidor escreve, porque um formulário de cadastro na
    #: internet aberta enche de conta que ninguém convidou.
    #: ``fechado`` — ninguém se cadastra; contas nascem pela CLI.
    registration_mode: Literal["aberto", "convite", "fechado"] = "aberto"

    # --- Limite de tentativas ---------------------------------------------
    #: Tentativas de login por conta e por IP dentro da janela. Generoso de
    #: propósito: quem erra a senha do bolão da firma três vezes seguidas é o
    #: dono da conta, não um atacante.
    login_max_por_conta: int = 10
    login_max_por_ip: int = 30
    login_janela_segundos: int = 900

    # --- Apresentação -----------------------------------------------------
    # O banco guarda UTC. Este é o fuso usado para renderizar datas ao usuário
    # e para agendar jobs "às 04:00" no horário de Brasília.
    display_timezone: str = "America/Sao_Paulo"

    # --- Web Push -----------------------------------------------------------
    #: Par de chaves VAPID, gerado uma vez pelo instalador.
    #:
    #: Vazio desativa o canal de push por completo, sem erro: a instalação de
    #: rede local não tem HTTPS e o navegador não deixaria assinar de qualquer
    #: forma. Quem tem domínio ganha o recurso; quem não tem continua com o
    #: aviso dentro do app.
    vapid_public_key: str = ""
    vapid_private_key: str = ""

    #: Quem é o dono deste servidor, para o serviço de push do navegador saber
    #: a quem reclamar. `mailto:` ou uma URL.
    vapid_subject: str = ""

    @field_validator("cors_origins", "notification_channels", mode="before")
    @classmethod
    def _split_csv(cls, value: object) -> object:
        if isinstance(value, str):
            return [item.strip() for item in value.split(",") if item.strip()]
        return value

    @model_validator(mode="after")
    def _endurecer_producao(self) -> Settings:
        """Recusa subir em produção com configuração de desenvolvimento.

        A alternativa seria confiar em quem faz o deploy lembrar de trocar cada
        item. Não lembra — e o modo de falhar é silencioso: a plataforma sobe,
        funciona, e a chave que assina os tokens é a mesma que está publicada
        no repositório. Qualquer pessoa com o `.env.example` forja a sessão de
        quem quiser.
        """
        # Por "não é rede local", e não por igualdade com production: staging
        # fica na internet do mesmo jeito, e passar batido ali significa a
        # chave publicada no repositório assinando token de verdade.
        if self.environment in ("development", "test"):
            return self

        problemas: list[str] = []

        if self.secret_key in _CHAVES_DE_EXEMPLO or len(self.secret_key) < 32:
            problemas.append(
                "SECRET_KEY está com o valor de exemplo (ou é curta demais). "
                'Gere uma: python -c "import secrets; print(secrets.token_urlsafe(64))"'
            )

        if self.postgres_password in _SENHAS_DE_EXEMPLO:
            problemas.append("POSTGRES_PASSWORD está com a senha de exemplo.")

        if self.debug:
            problemas.append("DEBUG=true em produção expõe detalhe de erro a quem chamar.")

        if problemas:
            raise ValueError(
                "configuração insegura para ENVIRONMENT=production:\n  - "
                + "\n  - ".join(problemas)
            )

        # `Origin` é escolhido por quem chama; numa rede local isso é um atalho
        # aceitável, num servidor público é só uma string. Desligamos em vez de
        # exigir que alguém se lembre — o custo de errar aqui é alto demais.
        if self.allow_private_network_origins:
            self.allow_private_network_origins = False

        return self

    @property
    def is_production(self) -> bool:
        return self.environment == "production"

    @property
    def database_url(self) -> str:
        """DSN async (asyncpg), usada pela aplicação."""
        return str(
            PostgresDsn.build(
                scheme="postgresql+asyncpg",
                username=self.postgres_user,
                password=self.postgres_password,
                host=self.postgres_host,
                port=self.postgres_port,
                path=self.postgres_db,
            )
        )

    @property
    def sync_database_url(self) -> str:
        """DSN sync (psycopg/pg8000-free). Usada só por ferramentas offline."""
        return str(
            PostgresDsn.build(
                scheme="postgresql",
                username=self.postgres_user,
                password=self.postgres_password,
                host=self.postgres_host,
                port=self.postgres_port,
                path=self.postgres_db,
            )
        )

    @property
    def redis_url(self) -> str:
        return str(
            RedisDsn.build(
                scheme="redis",
                host=self.redis_host,
                port=self.redis_port,
                path=str(self.redis_db),
            )
        )


@lru_cache
def get_settings() -> Settings:
    return Settings()


settings = get_settings()
