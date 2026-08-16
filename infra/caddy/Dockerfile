# syntax=docker/dockerfile:1

# ---------------------------------------------------------------------------
# Caddy com o módulo de DNS da Cloudflare.
#
# A imagem oficial não traz nenhum provedor de DNS, e sem um deles o Caddy só
# consegue emitir certificado pelo desafio HTTP-01 — que exige a porta 80
# aberta e alcançável, e **não funciona com o proxy da Cloudflare ligado**
# (a nuvem laranja intercepta o desafio).
#
# Com o desafio DNS-01 o certificado é emitido criando um registro TXT no
# domínio. Funciona com o proxy ligado, funciona com a porta 80 fechada, e é o
# único jeito de obter certificado curinga. Por isso a imagem é construída
# aqui em vez de puxada pronta.
# ---------------------------------------------------------------------------
FROM caddy:2.10-builder-alpine AS builder

# São dois módulos com o mesmo apelido e propósitos diferentes — confundi-los
# custa um deploy inteiro:
#
#   caddy-dns/cloudflare        → `dns.providers.cloudflare`, o desafio DNS-01
#                                 do bloco `tls`.
#   WeidiDeng/caddy-cloudflare-ip → `http.ip_sources.cloudflare`, a lista de
#                                 faixas da Cloudflare do `trusted_proxies`.
#
# Sem o segundo, o Caddy nem chega a subir: a adaptação do Caddyfile falha com
# `module not registered` e o container entra em laço de reinício, sem servir
# nada nas portas 80 e 443.
RUN xcaddy build \
    --with github.com/caddy-dns/cloudflare \
    --with github.com/WeidiDeng/caddy-cloudflare-ip

FROM caddy:2.10-alpine

COPY --from=builder /usr/bin/caddy /usr/bin/caddy

# Confere os DOIS módulos, nomeando o que faltou. Conferir só um dava build
# verde com falsa confiança — foi exatamente assim que a ausência do
# `http.ip_sources.cloudflare` passou despercebida até o servidor.
RUN for modulo in dns.providers.cloudflare http.ip_sources.cloudflare; do \
        caddy list-modules | grep -q "$modulo" \
        || { echo "módulo $modulo não entrou no build do Caddy"; exit 1; }; \
    done
