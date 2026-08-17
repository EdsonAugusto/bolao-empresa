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

do_env() { sed -n "s/^$1=//p" "$RAIZ/.env" 2>/dev/null | head -1; }

# O ambiente manda; depois o .env; por último o padrão.
#
# O DEPLOY.md dizia "ajuste com BOLAO_BACKUPS_MANTER" e o script só lia o
# ambiente — quem punha a variável no .env, o único lugar de configuração que
# a instalação tem, não mudava nada. O cron seguia imprimindo sucesso e
# apagando tudo além dos 14, e a descoberta vinha no dia de precisar.
QUANTOS_MANTER="${BOLAO_BACKUPS_MANTER:-$(do_env BOLAO_BACKUPS_MANTER)}"
: "${QUANTOS_MANTER:=14}"
readonly QUANTOS_MANTER

# `0` é o que se escreve quando se quer dizer "sem limite" — e virava
# `tail -n +1`, isto é, a lista inteira, incluindo o backup recém-criado.
# A rotação apagava os catorze dias E o dump de hoje, e o script saía com 0.
if ! [[ "$QUANTOS_MANTER" =~ ^[0-9]+$ ]] || (( QUANTOS_MANTER < 1 )); then
    echo "BOLAO_BACKUPS_MANTER precisa ser um inteiro >= 1 (recebi: $QUANTOS_MANTER)" >&2
    exit 1
fi

carimbo="$(date +%Y%m%d-%H%M%S)"
mkdir -p "$DESTINO"

usuario="$(do_env POSTGRES_USER)"
banco="$(do_env POSTGRES_DB)"
: "${usuario:=bolao}" "${banco:=bolao}"

# Nada meio-pronto sobrevive a uma falha.
parciais=()
limpar_parciais() { [[ ${#parciais[@]} -gt 0 ]] && rm -f "${parciais[@]}"; }
trap limpar_parciais ERR INT TERM

# --- banco ------------------------------------------------------------------
# `--clean --if-exists` deixa o arquivo restaurável sobre um banco existente
# sem precisar derrubá-lo antes.
arquivo_banco="$DESTINO/bolao-$carimbo.sql.gz"
parcial="$arquivo_banco.parcial"
parciais+=("$parcial")

# Grava com outro nome e só promove no fim.
#
# Antes o dump ia direto para o nome final. pg_dump morrendo no meio — OOM num
# VPS pequeno, disco cheio, container reiniciado — deixava um .gz truncado com
# nome e tamanho perfeitamente plausíveis, e o `rm` de segurança nunca rodava
# porque o `set -e` já havia matado o script. Semanas depois alguém restaurava
# esse arquivo: o `--clean` executava todos os DROP TABLE e só então o psql
# encontrava o fim truncado. Banco vivo destruído por um backup que ninguém
# sabia estar quebrado.
$COMPOSE exec -T postgres pg_dump -U "$usuario" -d "$banco" --clean --if-exists \
    | gzip -9 > "$parcial"

# Três conferências baratas, cada uma pegando o que a anterior não pega.
tamanho=$(stat -c%s "$parcial" 2>/dev/null || stat -f%z "$parcial")
if (( tamanho < 1024 )); then
    rm -f "$parcial"
    echo "backup do banco saiu vazio ($tamanho bytes) — abortado" >&2
    exit 1
fi
if ! gzip -t "$parcial" 2>/dev/null; then
    rm -f "$parcial"
    echo "backup do banco saiu corrompido (gzip -t reprovou) — abortado" >&2
    exit 1
fi
# pg_dump só escreve esta linha quando chegou ao fim. É a diferença entre
# "backup" e "os primeiros 40 MB de um backup".
if ! gzip -dc "$parcial" | tail -5 | grep -q 'PostgreSQL database dump complete'; then
    rm -f "$parcial"
    echo "backup do banco terminou no meio (sem a marca de conclusão) — abortado" >&2
    exit 1
fi
mv "$parcial" "$arquivo_banco"
parciais=()

# --- anexos -----------------------------------------------------------------
# Falha real não pode virar "ainda não há anexos".
#
# O `|| { rm; arquivo_anexos=""; }` anterior tratava disco cheio, api
# reiniciando e tar interrompido exatamente como "instalação nova" — em
# silêncio, e para sempre. Os anexos paravam de ser copiados sem uma linha de
# log, enquanto a rotação seguia descartando os bons que ainda restavam.
arquivo_anexos=""
if $COMPOSE exec -T api test -d /data/uploads 2>/dev/null; then
    arquivo_anexos="$DESTINO/anexos-$carimbo.tar.gz"
    parcial="$arquivo_anexos.parcial"
    parciais+=("$parcial")

    codigo=0
    $COMPOSE exec -T api tar -czf - -C /data uploads > "$parcial" || codigo=$?
    if (( codigo == 1 )); then
        # tar sai 1 em "file changed as we read it" — alguém subiu anexo
        # durante o backup. O arquivo continua utilizável.
        echo "aviso: anexos mudaram durante a cópia; o arquivo foi mantido" >&2
    elif (( codigo > 1 )); then
        rm -f "$parcial"
        echo "backup dos anexos falhou (tar saiu $codigo) — abortado" >&2
        exit 1
    fi
    if ! gzip -t "$parcial" 2>/dev/null; then
        rm -f "$parcial"
        echo "backup dos anexos saiu corrompido — abortado" >&2
        exit 1
    fi
    mv "$parcial" "$arquivo_anexos"
    parciais=()
else
    echo "sem pasta de anexos ainda — nada a copiar" >&2
fi

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
    # Nenhuma rotação pode apagar o que esta execução acabou de produzir.
    [[ -n "$arquivo_banco" ]] && velhos="$(printf '%s\n' "$velhos" | grep -vFx "$arquivo_banco" || true)"
    [[ -n "$arquivo_anexos" ]] && velhos="$(printf '%s\n' "$velhos" | grep -vFx "$arquivo_anexos" || true)"
    if [[ -n "$velhos" ]]; then
        echo "$velhos" | xargs -r rm -f
    fi
done

if [[ -n "$arquivo_anexos" ]]; then
    anexos_txt="  anexos $(du -h "$arquivo_anexos" | cut -f1)"
else
    anexos_txt="  anexos: NÃO copiados"
fi
echo "$(date -Is)  banco $(du -h "$arquivo_banco" | cut -f1)$anexos_txt  em $DESTINO"
