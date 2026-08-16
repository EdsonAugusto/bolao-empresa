.DEFAULT_GOAL := help
COMPOSE := docker compose
API     := $(COMPOSE) exec -T api

.PHONY: help
help: ## Lista os alvos disponíveis
	@grep -hE '^[a-zA-Z_-]+:.*?## ' $(MAKEFILE_LIST) \
		| awk 'BEGIN{FS=":.*?## "}{printf "  \033[36m%-14s\033[0m %s\n", $$1, $$2}'

.PHONY: env
env: ## Cria .env a partir do .env.example se ainda não existir
	@test -f .env || (cp .env.example .env && echo "  .env criado a partir de .env.example")

.PHONY: build
build: env ## Constrói as imagens
	$(COMPOSE) build

.PHONY: up
up: env ## Sobe a stack completa
	$(COMPOSE) up -d --build
	@echo "  web    http://localhost:$${NGINX_HOST_PORT:-8080}"
	@echo "  api    http://localhost:$${NGINX_HOST_PORT:-8080}/api"
	@echo "  docs   http://localhost:$${NGINX_HOST_PORT:-8080}/api/docs"

.PHONY: down
down: ## Derruba a stack (mantém os volumes)
	$(COMPOSE) down

.PHONY: nuke
nuke: ## Derruba a stack e APAGA os volumes (banco incluído)
	$(COMPOSE) down -v

.PHONY: logs
logs: ## Segue os logs de todos os serviços
	$(COMPOSE) logs -f --tail=100

.PHONY: ps
ps: ## Estado dos serviços
	$(COMPOSE) ps

.PHONY: shell
shell: ## Shell no container da API
	$(COMPOSE) exec api bash

.PHONY: psql
psql: ## psql no banco de desenvolvimento
	$(COMPOSE) exec postgres psql -U $${POSTGRES_USER:-bolao} -d $${POSTGRES_DB:-bolao}

.PHONY: migrate
migrate: ## Aplica as migrations pendentes
	$(API) alembic upgrade head

.PHONY: downgrade
downgrade: ## Reverte a última migration
	$(API) alembic downgrade -1

.PHONY: revision
revision: ## Gera migration com autogenerate: make revision m="mensagem"
	$(API) alembic revision --autogenerate -m "$(m)"

.PHONY: seed
seed: ## Popula dados base (idempotente)
	$(API) python -m app.cli seed

.PHONY: test
test: test-api test-web ## Roda pytest + vitest

.PHONY: test-api
test-api: ## Testes do backend
	$(API) pytest

.PHONY: test-web
test-web: ## Testes do frontend
	$(COMPOSE) exec -T web npm run test

.PHONY: lint
lint: ## ruff + mypy + eslint
	$(API) ruff check app tests
	$(API) ruff format --check app tests
	$(API) mypy app/scoring app/services
	$(COMPOSE) exec -T web npm run lint

.PHONY: fmt
fmt: ## Formata o código
	$(API) ruff check --fix app tests
	$(API) ruff format app tests
