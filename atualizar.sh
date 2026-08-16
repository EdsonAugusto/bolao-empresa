#!/usr/bin/env bash
#
# Atualiza a plataforma depois de um `git pull`.
#
#   sudo ./atualizar.sh
#
# Faz backup do banco antes, reconstrói o que mudou, aplica migrations e sobe.
# Se a subida falhar, o backup está ali para voltar atrás.

set -Eeuo pipefail

RAIZ="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
readonly RAIZ
readonly COMPOSE="docker compose -f $RAIZ/docker-compose.prod.yml"

if [[ -t 1 ]]; then
    readonly VERDE=$'\033[0;32m' VERMELHO=$'\033[0;31m' FORTE=$'\033[1m' FIM=$'\033[0m'
else
    readonly VERDE='' VERMELHO='' FORTE='' FIM=''
fi

ok()    { printf '  %s✓%s %s\n' "$VERDE" "$FIM" "$1"; }
erro()  { printf '\n  %s✗ %s%s\n\n' "$VERMELHO" "$1" "$FIM" >&2; exit 1; }
etapa() { printf '\n%s%s%s\n' "$FORTE" "$1" "$FIM"; }

[[ $EUID -eq 0 ]] || erro "rode com sudo: sudo ./atualizar.sh"
[[ -f "$RAIZ/.env" ]] || erro "não achei o .env — rode ./instalar.sh primeiro"

cd "$RAIZ"

etapa "1. Backup antes de mexer"
if ./backup.sh >/dev/null 2>&1; then
    ok "banco salvo em backups/"
else
    erro "o backup falhou. Não vou atualizar sem rede de proteção."
fi

etapa "2. Reconstruindo"
$COMPOSE build --pull >/tmp/bolao-update.log 2>&1 \
    || { tail -30 /tmp/bolao-update.log; erro "construção falhou (log em /tmp/bolao-update.log)"; }
ok "imagens atualizadas"

etapa "3. Subindo"
# O serviço `migrate` roda antes da api por dependência declarada no compose.
$COMPOSE up -d --remove-orphans >/dev/null 2>&1
ok "containers recriados"

etapa "4. Conferindo"
saudavel=false
for _ in $(seq 1 40); do
    if $COMPOSE ps --format '{{.Service}} {{.Health}}' 2>/dev/null | grep -q '^api healthy'; then
        saudavel=true
        break
    fi
    sleep 3
done

if [[ "$saudavel" != true ]]; then
    $COMPOSE logs --tail 40 api migrate
    erro "a API não voltou. Log acima; o backup está em backups/."
fi
ok "API saudável"

dominio="$(sed -n 's/^BOLAO_DOMINIO=//p' .env | head -1)"
if curl -fsS --max-time 10 "https://$dominio/api/health/live" >/dev/null 2>&1; then
    ok "https://$dominio respondendo"
else
    erro "a API está de pé mas o domínio não responde. Veja: $COMPOSE logs caddy"
fi

etapa "Pronto"
$COMPOSE ps --format 'table {{.Service}}\t{{.Status}}'

# Camadas antigas se acumulam a cada atualização e enchem o disco de um VPS
# pequeno sem aviso. Só imagens sem uso — volume nenhum é tocado.
docker image prune -f >/dev/null 2>&1 || true
echo
