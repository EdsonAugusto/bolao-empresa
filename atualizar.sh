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
    readonly VERDE=$'\033[0;32m' VERMELHO=$'\033[0;31m' AMARELO=$'\033[0;33m'
    readonly FORTE=$'\033[1m' FIM=$'\033[0m'
else
    readonly VERDE='' VERMELHO='' AMARELO='' FORTE='' FIM=''
fi

ok()    { printf '  %s✓%s %s\n' "$VERDE" "$FIM" "$1"; }
erro()  { printf '\n  %s✗ %s%s\n\n' "$VERMELHO" "$1" "$FIM" >&2; exit 1; }
etapa() { printf '\n%s%s%s\n' "$FORTE" "$1" "$FIM"; }
# Nem tudo que dá errado justifica parar a atualização: o que só degrada um
# recurso opcional avisa e segue.
aviso() { printf '  %s!%s %s\n' "$AMARELO" "$FIM" "$1"; }

[[ $EUID -eq 0 ]] || erro "rode com sudo: sudo ./atualizar.sh"
[[ -f "$RAIZ/.env" ]] || erro "não achei o .env — rode ./instalar.sh primeiro"

cd "$RAIZ"

etapa "1. Backup antes de mexer"
# A razão da falha fica visível.
#
# `>/dev/null 2>&1` trocava mensagens acionáveis — "saiu vazio (312 bytes)",
# "Permission denied", "no such service: postgres" — por uma frase única que
# não distingue banco fora de disco cheio de permissão. E travava o deploy sem
# dar caminho de saída.
if ./backup.sh >/tmp/bolao-backup.log 2>&1; then
    ok "$(tail -1 /tmp/bolao-backup.log)"
else
    tail -20 /tmp/bolao-backup.log
    erro "o backup falhou (log acima e em /tmp/bolao-backup.log).
  Não vou atualizar sem rede de proteção."
fi

etapa "2. Reconstruindo"
$COMPOSE build --pull >/tmp/bolao-update.log 2>&1 \
    || { tail -30 /tmp/bolao-update.log; erro "construção falhou (log em /tmp/bolao-update.log)"; }
ok "imagens atualizadas"

# --- ajustes no .env de quem já estava no ar --------------------------------
#
# O `.env` não é reescrito por este script — e não deve ser, porque guarda
# escolhas do operador. Mas alguns valores gravados por instalações antigas
# quebram recursos novos, e deixá-los como estão faria a atualização parecer
# não ter funcionado.

# Trinta minutos obrigavam uma renovação de token em TODA abertura do
# aplicativo, e cada renovação é uma chance de a sessão cair. O valor do .env
# manda sobre o padrão do código, então trocar só o código não bastaria.
#
# A troca só acontece se o valor for exatamente o padrão antigo: quem escolheu
# outro número escolheu de propósito.
if grep -q '^ACCESS_TOKEN_TTL_MINUTES=30$' "$RAIZ/.env"; then
    sed -i 's/^ACCESS_TOKEN_TTL_MINUTES=30$/ACCESS_TOKEN_TTL_MINUTES=720/' "$RAIZ/.env"
    ok "validade do token de acesso ajustada (o app deixa de renovar a cada abertura)"
fi

# Sem chave VAPID o canal de push nem é construído: o lembrete aparece no
# sininho e nunca chega no celular, sem erro em log nenhum. Gerar aqui também —
# e não só no instalador — é o que faz o recurso valer para quem já tinha a
# plataforma no ar.
#
# Preservada quando já existe: trocá-la invalidaria toda inscrição feita, e cada
# pessoa teria de autorizar de novo.
if ! grep -q '^VAPID_PRIVATE_KEY=.' "$RAIZ/.env"; then
    if chaves_vapid="$($COMPOSE run --rm --no-deps api python -m app.cli gerar-vapid 2>/dev/null)" \
        && [[ "$chaves_vapid" == *VAPID_PUBLIC_KEY=* ]]; then
        acme="$(sed -n 's/^BOLAO_ACME_EMAIL=//p' "$RAIZ/.env" | head -1)"
        {
            echo
            echo "# --- Notificação do navegador (Web Push) ---------------------------------"
            printf '%s\n' "$chaves_vapid"
            echo "VAPID_SUBJECT=mailto:${acme:-admin@localhost}"
        } >> "$RAIZ/.env"
        ok "chaves de notificação geradas (o aviso passa a chegar no celular)"
    else
        aviso "não consegui gerar as chaves de notificação; os avisos ficam só na plataforma"
    fi
fi

# O Caddyfile é conferido ANTES de recriar a borda.
#
# Caddyfile inválido faz o container sair na subida, e aí não há site: a
# atualização que deveria corrigir alguma coisa derruba tudo. Validar custa um
# segundo e transforma "site fora do ar" em "não atualizei, e olha o motivo".
if ! saida_valida="$($COMPOSE run --rm --no-deps --entrypoint caddy caddy \
        validate --config /etc/caddy/Caddyfile --adapter caddyfile 2>&1)"; then
    printf '%s\n' "$saida_valida"
    erro "o Caddyfile não passou na validação (acima). Nada foi recriado."
fi
ok "Caddyfile válido"

etapa "3. Subindo"
# O serviço `migrate` roda antes da api por dependência declarada no compose.
#
# E é por isso que esta linha precisa de tratador: o compose HONRA
# `service_completed_successfully` e `service_healthy`, então migration que
# falha ou API que não fica saudável derrubam ESTE comando — os dois cenários
# que uma atualização mais produz. Com a saída em /dev/null e sem `|| erro`, o
# script morria mudo aqui e a etapa 4 inteira, escrita justamente para
# explicar isso, virava código inalcançável.
$COMPOSE up -d --remove-orphans >>/tmp/bolao-update.log 2>&1 || {
    $COMPOSE logs --tail 40 migrate api
    tail -20 /tmp/bolao-update.log
    erro "a subida falhou (log acima e em /tmp/bolao-update.log).
  O backup está em backups/."
}
ok "containers recriados"

etapa "4. Conferindo"
# `web` entra na conferência.
#
# A etapa só olhava a api, e a única prova externa batia em /api/health/live —
# que pelo Caddyfile vai direto para api:8000 e não passa pelo `web` em
# momento nenhum. Um Nuxt que sobe e estoura em toda renderização satisfazia o
# `up -d` (o caddy só exige `service_started`), passava pelos dois ✓ e pelo
# "Pronto", com todo visitante recebendo 502 na raiz.
saudavel=false
for _ in $(seq 1 40); do
    estados="$($COMPOSE ps --format '{{.Service}} {{.Health}}' 2>/dev/null || true)"
    if grep -q '^api healthy' <<<"$estados" && grep -q '^web healthy' <<<"$estados"; then
        saudavel=true
        break
    fi
    sleep 3
done

if [[ "$saudavel" != true ]]; then
    $COMPOSE logs --tail 40 api web migrate
    erro "a aplicação não voltou. Log acima; o backup está em backups/."
fi
ok "API e site saudáveis"

dominio="$(sed -n 's/^BOLAO_DOMINIO=//p' .env | head -1)"
# A raiz, e não só /api: é a URL que 100% das pessoas abrem, e a única que
# exercita caddy -> web -> api de ponta a ponta. E exigindo 200: sem `-L`,
# `curl -f` dá sucesso num 301, que é exatamente o que a Cloudflare em modo
# "Flexible" devolve num laço infinito de redirecionamento.
codigo="$(curl -sS -L --max-redirs 4 -o /dev/null -m 15 -w '%{http_code}' \
    "https://$dominio/" 2>/dev/null || echo 000)"
if [[ "$codigo" == 200 ]]; then
    ok "https://$dominio respondendo"
else
    erro "a aplicação está de pé mas o domínio devolveu $codigo.
  Se for 000, pode ser laço de redirecionamento: confira se o modo SSL/TLS
  da zona na Cloudflare é Full (strict).
  Veja também: $COMPOSE logs caddy"
fi

etapa "Pronto"
$COMPOSE ps --format 'table {{.Service}}\t{{.Status}}'

# Camadas antigas se acumulam a cada atualização e enchem o disco de um VPS
# pequeno sem aviso. Só imagens sem uso — volume nenhum é tocado.
docker image prune -f >/dev/null 2>&1 || true
echo
