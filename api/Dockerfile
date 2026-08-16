# syntax=docker/dockerfile:1

# ---------------------------------------------------------------------------
# Base: dependências de runtime
# ---------------------------------------------------------------------------
FROM python:3.12-slim AS base

ENV PYTHONUNBUFFERED=1 \
    PYTHONDONTWRITEBYTECODE=1 \
    PIP_NO_CACHE_DIR=1 \
    PIP_DISABLE_PIP_VERSION_CHECK=1 \
    TZ=UTC

WORKDIR /app

RUN apt-get update \
    && apt-get install -y --no-install-recommends \
        build-essential \
        libpq5 \
        curl \
    && rm -rf /var/lib/apt/lists/*

COPY pyproject.toml ./
# Instala só as dependências de runtime resolvendo pelo pyproject.
RUN pip install --upgrade pip setuptools wheel \
    && pip install .

# ---------------------------------------------------------------------------
# Dev: acrescenta ferramentas de teste/lint. O código vem por bind mount.
# ---------------------------------------------------------------------------
FROM base AS dev

RUN pip install ".[dev]"
COPY . .
EXPOSE 8000
CMD ["uvicorn", "app.main:app", "--host", "0.0.0.0", "--port", "8000", "--reload"]

# ---------------------------------------------------------------------------
# Prod: imagem enxuta, sem toolchain de compilação, usuário sem privilégio
# ---------------------------------------------------------------------------
FROM python:3.12-slim AS prod

ENV PYTHONUNBUFFERED=1 \
    PYTHONDONTWRITEBYTECODE=1 \
    TZ=UTC

WORKDIR /app

# O `mkdir` de /data/uploads não é detalhe: um volume nomeado herda o dono do
# ponto de montagem quando ele já existe na imagem, e cria como root quando não
# existe. Como esta etapa roda sem privilégio (`USER appuser`), sem isto gravar
# anexo de relato falha com "permission denied" — e só em produção, porque a
# imagem de desenvolvimento roda como root.
RUN apt-get update \
    && apt-get install -y --no-install-recommends libpq5 \
    && rm -rf /var/lib/apt/lists/* \
    && useradd --create-home --uid 10001 appuser \
    && mkdir -p /data/uploads \
    && chown -R appuser:appuser /data

COPY --from=base /usr/local/lib/python3.12/site-packages /usr/local/lib/python3.12/site-packages
COPY --from=base /usr/local/bin /usr/local/bin
COPY --chown=appuser:appuser . .

USER appuser
EXPOSE 8000
CMD ["uvicorn", "app.main:app", "--host", "0.0.0.0", "--port", "8000"]
