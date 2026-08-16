#!/usr/bin/env bash
#
# Backup do banco e dos anexos de relato.
#
#   ./backup.sh                    grava em backups/
#   ./backup.sh /mnt/externo       grava onde você mandar
#
# Para rodar todo dia às 3h da manhã:
#   sudo crontab -e
#   0 3 * * * cd /caminho/do/bolao && ./backup.sh >> /var/log/bolao-backup.log 2>&1

set -Eeuo pipefail

RAIZ="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
readonly RAIZ
readonly COMPOSE="docker compose -f $RAIZ/docker-compose.prod.yml"
readonly DESTINO="${1:-$RAIZ/backups}"
readonly QUANTOS_MANTER="${BOLAO_BACKUPS_MANTER:-14}"

carimbo="$(date +%Y%m%d-%H%M%S)"
mkdir -p "$DESTINO"

usuario="$(sed -n 's/^POSTGRES_USER=//p' "$RAIZ/.env" | head -1)"
banco="$(sed -n 's/^POSTGRES_DB=//p' "$RAIZ/.env" | head -1)"
: "${usuario:=bolao}" "${banco:=bolao}"

# --- banco ------------------------------------------------------------------
# `--clean --if-exists` deixa o arquivo restaurável sobre um banco existente
# sem precisar derrubá-lo antes.
arquivo_banco="$DESTINO/bolao-$carimbo.sql.gz"
$COMPOSE exec -T postgres pg_dump -U "$usuario" -d "$banco" --clean --if-exists \
    | gzip -9 > "$arquivo_banco"

# Dump que falha no meio deixa um .gz pequeno e válido — o tamanho é o que
# denuncia. Menos de 1 KB não é um banco com bolão nenhum.
tamanho=$(stat -c%s "$arquivo_banco" 2>/dev/null || stat -f%z "$arquivo_banco")
if (( tamanho < 1024 )); then
    rm -f "$arquivo_banco"
    echo "backup do banco saiu vazio ($tamanho bytes) — abortado" >&2
    exit 1
fi

# --- anexos -----------------------------------------------------------------
arquivo_anexos="$DESTINO/anexos-$carimbo.tar.gz"
$COMPOSE exec -T api tar -czf - -C /data uploads > "$arquivo_anexos" 2>/dev/null || {
    # Instalação sem nenhum relato ainda: a pasta pode nem existir.
    rm -f "$arquivo_anexos"
    arquivo_anexos=""
}

# --- limpeza ----------------------------------------------------------------
# Mantém os N mais recentes de cada tipo. Backup que enche o disco derruba a
# aplicação que ele deveria proteger.
for padrao in "bolao-*.sql.gz" "anexos-*.tar.gz"; do
    # `|| true` porque `ls` sai com 1 quando o glob nao casa nada — o normal no
    # primeiro backup — e `set -e` derrubaria o script inteiro. Como
    # `atualizar.sh` recusa atualizar se o backup falhar, isso travava o deploy
    # de uma correcao justamente na instalacao nova.
    # SC2012: nomes sao gerados aqui, sem espaco nem quebra de linha.
    # SC2086: o glob PRECISA expandir — aspas o transformariam em nome literal.
    # shellcheck disable=SC2012,SC2086
    velhos="$(ls -1t "$DESTINO"/$padrao 2>/dev/null | tail -n "+$((QUANTOS_MANTER + 1))" || true)"
    if [[ -n "$velhos" ]]; then
        echo "$velhos" | xargs -r rm -f
    fi
done

echo "$(date -Is)  banco $(du -h "$arquivo_banco" | cut -f1)${arquivo_anexos:+  anexos $(du -h "$arquivo_anexos" | cut -f1)}  em $DESTINO"
