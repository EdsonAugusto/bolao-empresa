# syntax=docker/dockerfile:1

# ---------------------------------------------------------------------------
# Base: dependências instaladas
# ---------------------------------------------------------------------------
FROM node:24-alpine AS base

WORKDIR /app
ENV NUXT_TELEMETRY_DISABLED=1

COPY package.json package-lock.json* ./
# Sem lockfile no primeiro build (ele nasce aqui); depois que o
# package-lock.json for commitado, `npm ci` passa a valer e é reprodutível.
RUN if [ -f package-lock.json ]; then npm ci; else npm install; fi

# ---------------------------------------------------------------------------
# Dev: código vem por bind mount; o compose reinstala se o volume estiver vazio
# ---------------------------------------------------------------------------
FROM base AS dev

COPY . .
EXPOSE 3000
CMD ["npm", "run", "dev", "--", "--host", "0.0.0.0", "--port", "3000"]

# ---------------------------------------------------------------------------
# Build de produção
# ---------------------------------------------------------------------------
FROM base AS build

COPY . .
RUN npm run build

# ---------------------------------------------------------------------------
# Prod: só o output do Nitro, sem node_modules de desenvolvimento
# ---------------------------------------------------------------------------
FROM node:24-alpine AS prod

WORKDIR /app
ENV NODE_ENV=production \
    NITRO_HOST=0.0.0.0 \
    NITRO_PORT=3000

RUN addgroup -g 10001 app && adduser -u 10001 -G app -D app
COPY --from=build --chown=app:app /app/.output ./.output

USER app
EXPOSE 3000
CMD ["node", ".output/server/index.mjs"]
