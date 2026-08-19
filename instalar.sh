#!/usr/bin/env bash
#
# Instalação da plataforma de bolões num servidor Linux com domínio e HTTPS.
#
#   git clone <repositório> bolao && cd bolao && sudo ./instalar.sh
#
# Pergunta três coisas — domínio, token da Cloudflare e senha do administrador
# — e cuida do resto: Docker, segredos, certificado, banco, migrations e a
# conta de administração.
#
# Rodar de novo é seguro: o que já está configurado é reaproveitado, e nada é
# apagado sem confirmação explícita.

set -Eeuo pipefail

RAIZ="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
readonly RAIZ
readonly ENV_ARQUIVO="$RAIZ/.env"
readonly COMPOSE_ARQUIVO="$RAIZ/docker-compose.prod.yml"

# --- aparência --------------------------------------------------------------
if [[ -t 1 ]]; then
    readonly VERDE=$'\033[0;32m' AMARELO=$'\033[0;33m' VERMELHO=$'\033[0;31m'
    readonly AZUL=$'\033[0;36m' FORTE=$'\033[1m' FIM=$'\033[0m'
else
    readonly VERDE='' AMARELO='' VERMELHO='' AZUL='' FORTE='' FIM=''
fi

titulo() { printf '\n%s%s%s\n' "$FORTE" "$1" "$FIM"; }
ok()     { printf '  %s✓%s %s\n' "$VERDE" "$FIM" "$1"; }
aviso()  { printf '  %s!%s %s\n' "$AMARELO" "$FIM" "$1"; }
erro()   { printf '\n  %s✗ %s%s\n\n' "$VERMELHO" "$1" "$FIM" >&2; exit 1; }
passo()  { printf '  %s·%s %s\n' "$AZUL" "$FIM" "$1"; }

trap 'erro "falhou na linha $LINENO. Nada foi deixado pela metade que um novo ./instalar.sh não resolva."' ERR

# --- verificações -----------------------------------------------------------
[[ "$(uname -s)" == "Linux" ]] || erro "este instalador é para Linux. No Windows ou macOS use \`docker compose up\` para uso caseiro."
[[ $EUID -eq 0 ]] || erro "rode com sudo: sudo ./instalar.sh"
[[ -f "$COMPOSE_ARQUIVO" ]] || erro "rode a partir da pasta do projeto (não achei docker-compose.prod.yml)"

cat <<BANNER

  ${FORTE}Plataforma de Bolões${FIM}
  Instalação em servidor com domínio próprio e HTTPS

BANNER

# --- Docker -----------------------------------------------------------------
titulo "1. Docker"

if ! command -v docker >/dev/null 2>&1; then
    passo "Docker não encontrado, instalando pelo script oficial…"
    curl -fsSL https://get.docker.com | sh >/dev/null 2>&1 \
        || erro "não consegui instalar o Docker. Instale manualmente e rode de novo."
    systemctl enable --now docker >/dev/null 2>&1 || true
    ok "Docker instalado"
else
    ok "Docker $(docker --version | grep -oE '[0-9]+\.[0-9]+\.[0-9]+' | head -1)"
fi

docker compose version >/dev/null 2>&1 \
    || erro "o plugin 'docker compose' não está disponível. Atualize o Docker (versão 20.10+)."
ok "Compose $(docker compose version --short 2>/dev/null || echo disponível)"

systemctl is-active --quiet docker || systemctl start docker
ok "serviço ativo"

# --- perguntas --------------------------------------------------------------
titulo "2. Configuração"

# Reaproveita o que já existe, para reinstalar não custar de novo.
valor_atual() {
    [[ -f "$ENV_ARQUIVO" ]] || return 0
    sed -n "s/^$1=//p" "$ENV_ARQUIVO" | head -1
}

DOMINIO="${BOLAO_DOMINIO:-$(valor_atual BOLAO_DOMINIO)}"
CF_TOKEN="${CLOUDFLARE_API_TOKEN:-$(valor_atual CLOUDFLARE_API_TOKEN)}"
ACME_EMAIL="${BOLAO_ACME_EMAIL:-$(valor_atual BOLAO_ACME_EMAIL)}"
ADMIN_EMAIL="${BOLAO_ADMIN_EMAIL:-$(valor_atual BOLAO_ADMIN_EMAIL)}"
ADMIN_SENHA="${BOLAO_ADMIN_SENHA:-}"

# Tira o que a colagem traz junto e o valor nunca deveria ter.
#
# O terminal em "bracketed paste" embrulha o texto colado em ESC[200~ e
# ESC[201~. O `read` guarda isso literalmente, e o token vai para a
# Cloudflare com lixo na frente — que responde "inválido" sobre um token
# perfeito. Espaço, tabulação e aspas entram pela mesma porta, de quem
# copiou com um caractere a mais.
limpar_colagem() {
    printf '%s' "$1" \
        | tr -d '[:cntrl:]' \
        | sed -e 's/\[20[01]~//g' -e "s/^[[:space:]\"']*//" -e "s/[[:space:]\"']*$//"
}

# Um e-mail que o Caddy não consegue registrar na Let's Encrypt custa o
# certificado inteiro: a conta ACME nem chega a existir, e o erro que aparece
# é "unable to parse email address", longe de onde o valor foi digitado.
email_valido() {
    [[ "$1" =~ ^[A-Za-z0-9._%+-]+@[A-Za-z0-9]([A-Za-z0-9-]*[A-Za-z0-9])?(\.[A-Za-z0-9]([A-Za-z0-9-]*[A-Za-z0-9])?)*\.[A-Za-z]{2,}$ ]]
}

perguntar() {
    local rotulo="$1" padrao="${2:-}" resposta
    if [[ -n "$padrao" ]]; then
        read -r -p "  $rotulo [$padrao]: " resposta </dev/tty
        limpar_colagem "${resposta:-$padrao}"
    else
        read -r -p "  $rotulo: " resposta </dev/tty
        limpar_colagem "$resposta"
    fi
}

# Para segredo de forma livre — senha — só o embrulho da colagem sai.
#
# Aspas e espaço são caracteres LEGÍTIMOS numa senha. Aparar as pontas aqui
# gravava o hash de uma senha que a pessoa nunca digitou: a confirmação passa
# pelo mesmo filtro, então as duas batem, o instalador diz ✓, e o erro só
# aparece no primeiro login — como "senha incorreta", que não distingue
# "errei de senha" de "o instalador mudou a minha".
limpar_marcadores() {
    printf '%s' "$1" | tr -d '[:cntrl:]' | sed 's/\[20[01]~//g'
}

# $2 = "livre" para não aparar pontas (senha). Vazio = limpeza completa (token).
perguntar_secreto() {
    local rotulo="$1" forma="${2:-}" resposta
    read -r -s -p "  $rotulo: " resposta </dev/tty
    printf '
' >&2
    if [[ "$forma" == livre ]]; then
        limpar_marcadores "$resposta"
    else
        limpar_colagem "$resposta"
    fi
}

# Domínio
while [[ ! "$DOMINIO" =~ ^[a-zA-Z0-9]([a-zA-Z0-9.-]*[a-zA-Z0-9])?\.[a-zA-Z]{2,}$ ]]; do
    [[ -n "$DOMINIO" ]] && aviso "domínio inválido: $DOMINIO"
    echo
    echo "  O domínio que vai apontar para este servidor, sem https:// e sem barra."
    echo "  Exemplo: bolao.meusite.com.br"
    DOMINIO="$(perguntar 'Domínio')"
done

# Token da Cloudflare
if [[ -z "$CF_TOKEN" ]]; then
    cat <<'AJUDA'

  Token da Cloudflare, usado para emitir o certificado HTTPS.

  Onde criar: painel da Cloudflare → Meu Perfil → Tokens de API → Criar Token
  Use o modelo "Editar zona DNS" (Edit zone DNS), com:
      Permissões  Zona · DNS · Editar
      Recursos    Zona · <seu domínio>

  O token não sai deste servidor. É usado só para criar o registro TXT que
  comprova que o domínio é seu — é assim que o certificado é emitido sem
  precisar abrir a porta 80 e funcionando com o proxy da Cloudflare ligado.

AJUDA
    CF_TOKEN="$(perguntar_secreto 'Token da Cloudflare')"
fi
[[ -n "$CF_TOKEN" ]] || erro "o token da Cloudflare é obrigatório para emitir o certificado"

# E-mail do ACME
#
# Laço, e não `if [[ -z ]]`: numa segunda execução o valor vem do .env
# anterior, e um endereço inválido gravado lá seria reaproveitado sem
# ninguém olhar — foi assim que um colchete sobreviveu a uma reinstalação
# e derrubou a emissão do certificado.
while ! email_valido "$ACME_EMAIL"; do
    [[ -n "$ACME_EMAIL" ]] && aviso "e-mail inválido: $ACME_EMAIL"
    echo
    echo "  E-mail para a Let's Encrypt avisar se um certificado estiver para vencer."
    ACME_EMAIL="$(perguntar 'E-mail' "admin@$DOMINIO")"
done

# Conta de administração
while ! email_valido "$ADMIN_EMAIL"; do
    [[ -n "$ADMIN_EMAIL" ]] && aviso "e-mail inválido: $ADMIN_EMAIL"
    echo
    echo "  Conta de administração da plataforma — quem importa campeonato,"
    echo "  cria bolão e responde os relatos de bug."
    ADMIN_EMAIL="$(perguntar 'E-mail do administrador' "admin@$DOMINIO")"
done

if [[ -z "$ADMIN_SENHA" ]]; then
    echo
    while :; do
        ADMIN_SENHA="$(perguntar_secreto 'Senha do administrador (mínimo 10 caracteres)' livre)"
        if [[ ${#ADMIN_SENHA} -lt 10 ]]; then
            aviso "muito curta — use pelo menos 10 caracteres"
            continue
        fi
        confirma="$(perguntar_secreto 'Repita a senha' livre)"
        [[ "$ADMIN_SENHA" == "$confirma" ]] && break
        aviso "as senhas não conferem"
    done
fi

# --- valida o token antes de gastar uma emissão -----------------------------
titulo "3. Conferindo o token da Cloudflare"

# Sem `-f`: com ele o curl descarta o corpo das respostas 4xx, que é
# justamente onde a Cloudflare explica o que houve. E o código HTTP vem
# separado, para distinguir "token recusado" de "não consegui falar com a
# Cloudflare" — os dois davam a mesma mensagem, e ela culpava o token.
# O texto do curl fica onde está.
#
# `|| resposta_token=...` sobrescrevia a variável inteira no erro — justamente
# o caso em que ela guardava a explicação ("Could not resolve host",
# "Connection timed out"). O `Detalhe:` abaixo saía sempre vazio, e "DNS não
# resolve" ficava indistinguível de "sem rota" e de "TLS recusado".
if ! resposta_token="$(curl -sS -m 20 -w $'\n%{http_code}' -X GET \
    "https://api.cloudflare.com/client/v4/user/tokens/verify" \
    -H "Authorization: Bearer $CF_TOKEN" \
    -H "Content-Type: application/json" 2>&1)"; then
    erro "não consegui falar com api.cloudflare.com a partir deste servidor.
      Isto NÃO é o token — é rede. Confira a saída para a internet e o DNS.
      Detalhe: $(printf '%s' "$resposta_token" | tr '\n' ' ' | head -c 200)"
fi

codigo_http="$(printf '%s' "$resposta_token" | tail -n1)"
corpo_token="$(printf '%s' "$resposta_token" | sed '$d')"

if [[ "$corpo_token" != *'"success":true'* ]]; then
    # A própria Cloudflare diz o motivo; repassar a frase dela vale mais do
    # que a minha suposição sobre o que deu errado.
    motivo="$(printf '%s' "$corpo_token" | grep -o '"message":"[^"]*"' | head -1 | cut -d'"' -f4)"
    erro "a Cloudflare recusou este token (HTTP $codigo_http).
      ${motivo:-sem detalhe na resposta}

      O recurso do token tem que ser a ZONA (${DOMINIO#*.}), não o
      subdomínio, com permissão Zona - DNS - Editar.
      Caracteres invisíveis da colagem já são removidos automaticamente."
fi
ok "token aceito pela Cloudflare"

# Aceito não é o mesmo que suficiente.
#
# `/user/tokens/verify` responde success:true para QUALQUER token ativo da
# conta — ele confere existência e validade, não alcance. Um token com
# `Zona · DNS · Ler`, ou apontado para a zona errada, passa por ali e só falha
# quarenta minutos depois, quando o Caddy tenta criar o registro TXT e toma
# 403. Pior: o ✓ verde daqui contradiz a dica do passo do certificado, e quem
# lê os dois descarta o palpite certo — "mas o token já foi aceito".
#
# Esta é exatamente a chamada que o módulo DNS do Caddy faz para achar a zona.
# Se ela não responde aqui, não vai responder para ele.
if [[ "${BOLAO_PULAR_CHECAGEM_ZONA:-}" != "1" ]]; then
    zona=""
    candidato="$DOMINIO"
    while [[ "$candidato" == *.* ]]; do
        resposta_zona="$(curl -sS -m 15 \
            -H "Authorization: Bearer $CF_TOKEN" \
            "https://api.cloudflare.com/client/v4/zones?name=$candidato" 2>&1 || true)"
        if [[ "$resposta_zona" == *'"success":true'* && "$resposta_zona" != *'"result":[]'* ]]; then
            zona="$candidato"
            break
        fi
        [[ "$candidato" == *.*.* ]] || break
        candidato="${candidato#*.}"
    done

    if [[ -z "$zona" ]]; then
        erro "o token é válido, mas não alcança a zona de $DOMINIO.
      É por isso que o certificado falharia — e falharia só daqui a minutos,
      com uma mensagem que não aponta para cá.

      No painel da Cloudflare, o token precisa de:
          Permissões  Zona · DNS · Editar
          Recursos    Zona · a zona do domínio (não o subdomínio)

      Se você tem certeza de que está certo, repita com:
          BOLAO_PULAR_CHECAGEM_ZONA=1 sudo -E ./instalar.sh"
    fi
    ok "token com acesso de DNS à zona $zona"
fi

# --- segredos ---------------------------------------------------------------
titulo "4. Segredos"

gerar_segredo() { openssl rand -base64 48 2>/dev/null | tr -d '\n=+/' | cut -c1-64; }

# Ajustes que o operador faz e que a reexecução não pode desfazer.
#
# O heredoc abaixo reescreve o .env do zero. Antes, só SECRET_KEY e
# POSTGRES_PASSWORD sobreviviam — e o próprio script (e o DEPLOY.md) ensinam a
# editar REGISTRATION_MODE aqui. Quem abrira o cadastro para o grupo via ele
# voltar para `convite` numa reinstalação, sem aviso e sem log, e descobria
# dias depois como "o pessoal não consegue mais se cadastrar".
MODO_CADASTRO="$(valor_atual REGISTRATION_MODE)"
TTL_ATUAL="$(valor_atual ACCESS_TOKEN_TTL_MINUTES)"
WORKERS_ATUAL="$(valor_atual API_WORKERS)"
FUSO_ATUAL="$(valor_atual DISPLAY_TIMEZONE)"
PROVEDOR_ATUAL="$(valor_atual FOOTBALL_PROVIDER)"

SECRET_KEY="$(valor_atual SECRET_KEY)"
if [[ -z "$SECRET_KEY" || "$SECRET_KEY" == *troque-esta-chave* ]]; then
    SECRET_KEY="$(gerar_segredo)"
    ok "chave de assinatura gerada"
else
    ok "chave de assinatura preservada (sessões abertas continuam valendo)"
fi

POSTGRES_PASSWORD="$(valor_atual POSTGRES_PASSWORD)"
if [[ -z "$POSTGRES_PASSWORD" || "$POSTGRES_PASSWORD" == "bolao_dev_password" ]]; then
    if docker volume inspect bolao_postgres_data >/dev/null 2>&1; then
        erro "já existe um banco criado com a senha de desenvolvimento.
      Trocá-la agora deixaria a aplicação sem acesso aos próprios dados.
      Para começar do zero:  docker compose -f docker-compose.prod.yml down -v"
    fi
    POSTGRES_PASSWORD="$(gerar_segredo)"
    ok "senha do banco gerada"
else
    ok "senha do banco preservada"
fi

# --- .env -------------------------------------------------------------------
titulo "5. Arquivo de configuração"

ENV_ANTERIOR=""
if [[ -f "$ENV_ARQUIVO" ]]; then
    ENV_ANTERIOR="$ENV_ARQUIVO.bak.$(date +%Y%m%d%H%M%S)"
    cp "$ENV_ARQUIVO" "$ENV_ANTERIOR"
    ok "cópia do .env anterior guardada"
fi

umask 077
cat > "$ENV_ARQUIVO" <<ENV
# Gerado por instalar.sh em $(date -Is)
# Contém segredos. Não commite, não copie para lugar nenhum.

# --- Ambiente --------------------------------------------------------------
ENVIRONMENT=production
DEBUG=false
LOG_LEVEL=INFO

# --- Domínio e certificado -------------------------------------------------
BOLAO_DOMINIO=$DOMINIO
BOLAO_ACME_EMAIL=$ACME_EMAIL
CLOUDFLARE_API_TOKEN=$CF_TOKEN

# --- Postgres --------------------------------------------------------------
POSTGRES_USER=bolao
POSTGRES_PASSWORD=$POSTGRES_PASSWORD
POSTGRES_DB=bolao
POSTGRES_HOST=postgres
POSTGRES_PORT=5432

# --- Redis -----------------------------------------------------------------
REDIS_HOST=redis
REDIS_PORT=6379
REDIS_DB=0

# --- Segurança -------------------------------------------------------------
SECRET_KEY=$SECRET_KEY
ACCESS_TOKEN_TTL_MINUTES=${TTL_ATUAL:-30}
REFRESH_TOKEN_TTL_DAYS=30
CORS_ORIGINS=https://$DOMINIO
# Fora da rede local esta opção precisa estar desligada: com ela ligada a API
# aceitaria qualquer origem de faixa privada, e num servidor público isso não
# prova nada sobre quem está chamando.
ALLOW_PRIVATE_NETWORK_ORIGINS=false

# Quem pode criar conta. Nasce \`fechado\` e o instalador troca para
# \`convite\` depois de criar a conta de administração — ver o passo 8.
REGISTRATION_MODE=${MODO_CADASTRO:-fechado}

# --- Aplicação -------------------------------------------------------------
API_WORKERS=${WORKERS_ATUAL:-2}
NUXT_PUBLIC_API_BASE=/api
UPLOADS_DIR=/data/uploads
DISPLAY_TIMEZONE=${FUSO_ATUAL:-America/Sao_Paulo}
FOOTBALL_PROVIDER=${PROVEDOR_ATUAL:-manual}

# --- Administração ---------------------------------------------------------
BOLAO_ADMIN_EMAIL=$ADMIN_EMAIL
ENV

# O que o operador acrescentou continua aqui.
#
# O heredoc acima só conhece as chaves que o instalador gerencia. Quem
# configurou aviso por Telegram, chave de provedor de futebol ou qualquer
# ajuste próprio via tudo isso sumir na reexecução — sem erro, sem log, e com
# um ✓ verde dizendo ".env escrito". Resgatamos as chaves que ele não conhece.
if [[ -n "$ENV_ANTERIOR" ]]; then
    resgatadas=()
    while IFS= read -r linha; do
        chave="${linha%%=*}"
        [[ "$linha" == *=* && "$chave" =~ ^[A-Z][A-Z0-9_]*$ ]] || continue
        grep -q "^$chave=" "$ENV_ARQUIVO" && continue
        resgatadas+=("$linha")
    done < "$ENV_ANTERIOR"

    if [[ ${#resgatadas[@]} -gt 0 ]]; then
        {
            echo
            echo "# --- Preservado da configuração anterior ---------------------------------"
            printf '%s\n' "${resgatadas[@]}"
        } >> "$ENV_ARQUIVO"
        ok "preservadas ${#resgatadas[@]} chave(s) que você tinha acrescentado:"
        printf '      %s\n' "${resgatadas[@]%%=*}"
    fi
fi
chmod 600 "$ENV_ARQUIVO"
ok ".env escrito (só o dono lê)"

# --- imagens ----------------------------------------------------------------
titulo "6. Construindo as imagens"
passo "primeira vez leva alguns minutos"

cd "$RAIZ"
docker compose -f "$COMPOSE_ARQUIVO" build --pull >/tmp/bolao-build.log 2>&1 \
    || { tail -30 /tmp/bolao-build.log; erro "a construção falhou (log completo em /tmp/bolao-build.log)"; }
ok "imagens prontas"

# --- notificação do celular -------------------------------------------------
# As chaves VAPID só podem ser geradas AGORA, não junto do resto do .env: quem
# sabe o formato exato que o navegador aceita é a biblioteca, e ela só existe
# depois da imagem construída.
#
# São preservadas entre execuções porque trocá-las invalida toda inscrição já
# feita: cada aparelho assinou contra a chave pública antiga, e o serviço de
# push passa a recusar. Ninguém perde dado, mas todo mundo teria que autorizar
# de novo, sem entender por quê.
if ! grep -q '^VAPID_PRIVATE_KEY=.' "$ENV_ARQUIVO"; then
    if chaves_vapid="$(docker compose -f "$COMPOSE_ARQUIVO" run --rm --no-deps \
        api python -m app.cli gerar-vapid 2>/dev/null)" \
        && [[ "$chaves_vapid" == *VAPID_PUBLIC_KEY=* ]]; then
        {
            echo
            echo "# --- Notificação do navegador (Web Push) ---------------------------------"
            printf '%s\n' "$chaves_vapid"
            echo "VAPID_SUBJECT=mailto:$ACME_EMAIL"
        } >> "$ENV_ARQUIVO"
        ok "chaves de notificação geradas"
    else
        # Não é motivo para parar a instalação: sem chave, o canal de push
        # simplesmente não se constrói e os avisos continuam dentro do app.
        aviso "não consegui gerar as chaves de notificação; os avisos ficarão só na plataforma"
    fi
else
    ok "chaves de notificação preservadas"
fi

# --- subida -----------------------------------------------------------------
titulo "7. Subindo os serviços"

# A borda (caddy) fica de fora nesta etapa, de propósito.
#
# A primeira conta a se cadastrar vira administradora — é o comportamento certo
# em rede local, onde estar na rede já é o convite. Num servidor com o domínio
# já apontado, publicar antes de existir conta abre uma janela de segundos a
# minutos em que qualquer pessoa da internet se cadastra e sai administrando a
# instalação. Então: sobe tudo menos a porta 443, cria o admin, e só depois
# publica.
# O log vai para arquivo, não para /dev/null.
#
# `api` depende de `migrate: service_completed_successfully` e `web` de
# `api: service_healthy`, e o compose HONRA essas condições — ou seja, no modo
# de falha mais comum (migration quebrada, banco que não fica saudável) quem
# falha é ESTA linha, não o laço abaixo. A única frase que diz qual dependência
# morreu sai em stderr. Mandá-la para /dev/null deixava a pessoa com
# "falhou na linha 332" e mais nada, e tornava o diagnóstico do laço — escrito
# justamente para esse caso — código inalcançável.
docker compose -f "$COMPOSE_ARQUIVO" up -d --remove-orphans     postgres redis migrate api worker web >/tmp/bolao-up.log 2>&1 || {
        tail -30 /tmp/bolao-up.log
        docker compose -f "$COMPOSE_ARQUIVO" logs --tail 40 postgres migrate api
        erro "os serviços não subiram. Log acima e em /tmp/bolao-up.log"
    }
ok "containers no ar (ainda sem publicar na internet)"

passo "aguardando o banco e as migrations…"
estado=""
for _ in $(seq 1 60); do
    estado="$(docker compose -f "$COMPOSE_ARQUIVO" ps --format '{{.Service}} {{.Health}}' 2>/dev/null | grep '^api ' || true)"
    # Comparação exata: "unhealthy" CONTÉM "healthy", e o glob `*healthy*`
    # casava com os dois. O portão de saúde do instalador virava carimbo — ele
    # declarava "API respondendo" no exato momento em que o Docker acabara de
    # marcar a API como doente.
    [[ "$estado" == "api healthy" ]] && break
    # Doente não melhora sozinho: o Docker só marca assim depois de esgotar as
    # tentativas. Esperar os 180 s restantes só atrasa a mensagem.
    [[ "$estado" == "api unhealthy" ]] && break
    sleep 3
done
[[ "$estado" == "api healthy" ]] || {
    docker compose -f "$COMPOSE_ARQUIVO" logs --tail 40 api migrate
    erro "a API não ficou saudável (estado: ${estado:-desconhecido}). Log acima."
}
ok "banco migrado e API respondendo"

# O worker não tem healthcheck e não aparece em mais nenhum passo. Se ele morre
# na subida, o Docker o reinicia em laço para sempre: o site abre, dá para
# entrar e criar bolão, e o instalador diz "Pronto." — mas quem apura os jogos
# é ele, e o sintoma só chega dias depois, como "os jogos acabaram e ninguém
# pontuou". Duas medições espaçadas, porque um container em laço aparece como
# `running` no instante certo.
for tentativa in 1 2; do
    [[ $tentativa == 2 ]] && sleep 15
    for servico in worker web; do
        vivo="$(docker compose -f "$COMPOSE_ARQUIVO" ps "$servico" --format '{{.State}}' 2>/dev/null || true)"
        [[ "$vivo" == running ]] || {
            docker compose -f "$COMPOSE_ARQUIVO" logs --tail 30 "$servico"
            erro "o serviço $servico não ficou de pé (estado: ${vivo:-ausente}). Log acima."
        }
    done
done
ok "worker e site estáveis"

# --- administrador ----------------------------------------------------------
titulo "8. Conta de administração"

BOLAO_ADMIN_SENHA="$ADMIN_SENHA" docker compose -f "$COMPOSE_ARQUIVO" exec -T \
    -e BOLAO_ADMIN_SENHA \
    api python -m app.cli criar-admin --email "$ADMIN_EMAIL" --da-variavel-de-ambiente \
    || erro "não consegui criar a conta de administração"

# Existe administrador: o cadastro pode abrir para quem tem convite.
#
# O .env nasce `fechado` de propósito — entre o `up` e a criação da conta
# existe uma janela em que a primeira pessoa a se cadastrar viraria dona da
# instalação. Agora que a conta existe, a janela fechou.
sed -i 's/^REGISTRATION_MODE=fechado$/REGISTRATION_MODE=convite/' "$ENV_ARQUIVO"
docker compose -f "$COMPOSE_ARQUIVO" up -d api worker >/dev/null 2>&1
ok "cadastro liberado para quem tem código de convite"

# --- borda ------------------------------------------------------------------
titulo "9. Publicando na internet"

# É AQUI que o site passa a existir para o mundo. Sem esta linha, tudo sobe,
# tudo fica saudável, e as portas 80 e 443 continuam vazias — a Cloudflare
# responde 521 e nada no log explica, porque não há log: o container nunca
# foi criado.
# Quem já está nas portas? Imagem de provedor costuma vir com nginx ou apache
# ativo, e esse é o motivo nº1 de a borda não subir. Perguntar antes é mais
# barato do que explicar depois.
ocupadas="$(ss -lntp '( sport = :80 or sport = :443 )' 2>/dev/null | tail -n +2 || true)"
if [[ -n "$ocupadas" ]]; then
    aviso "as portas 80/443 já têm alguém escutando:"
    printf '%s\n' "$ocupadas" | sed 's/^/      /'
    erro "libere as portas antes de continuar (ex.: systemctl disable --now nginx apache2)"
fi

# A saída é capturada, não descartada.
#
# Quando a porta está ocupada o Docker diz exatamente isto:
#   Bind for 0.0.0.0:80 failed: port is already allocated
# — e o container NÃO chega a ser criado. Mandar isso para /dev/null e sugerir
# `logs caddy` era o pior dos mundos: a única explicação era apagada, e a
# pessoa ia consultar um log que, por definição, estava vazio.
saida_caddy="$(docker compose -f "$COMPOSE_ARQUIVO" up -d caddy 2>&1)" \
    || erro "não consegui subir a borda (caddy):
$(printf '%s' "$saida_caddy" | sed 's/^/      /')"

# Subir o container não é servir: se o Caddy não conseguir o certificado ele
# sai, e `up -d` já terá devolvido sucesso. Conferimos que ele continua de pé.
sleep 4
if ! docker compose -f "$COMPOSE_ARQUIVO" ps caddy --format '{{.State}}' 2>/dev/null | grep -q running; then
    docker compose -f "$COMPOSE_ARQUIVO" logs caddy --tail 25
    erro "a borda subiu e caiu. O log está acima."
fi
ok "borda no ar nas portas 80 e 443"

# --- certificado ------------------------------------------------------------
titulo "10. Certificado HTTPS"
passo "a emissão pela Cloudflare leva de 10 a 60 segundos…"

# Exigir 200, e não "não deu erro".
#
# `curl -f` só falha em HTTP >= 400, e sem `-L` um 301 sai com código 0. Com a
# zona da Cloudflare em SSL/TLS "Flexible" — padrão histórico, comum em zonas
# antigas — a Cloudflare fala com a origem em HTTP na porta 80, o Caddy
# responde 301 para https, e o visitante entra em laço infinito. O instalador
# declarava "certificado válido" para um site que não abria para ninguém. E o
# TLS testado era o do edge da Cloudflare, que tem certificado próprio: o Caddy
# podia não ter emitido nada.
certificado_ok=false
laco_de_redirecionamento=false
for _ in $(seq 1 40); do
    codigo="$(curl -sS -L --max-redirs 4 -o /dev/null -m 8 \
        -w '%{http_code}' "https://$DOMINIO/api/health/live" 2>/dev/null || echo 000)"
    if [[ "$codigo" == 200 ]]; then
        certificado_ok=true
        break
    fi
    # Estourar o teto de redirecionamentos é a assinatura do modo Flexible.
    if [[ "$codigo" == 000 ]] \
        && curl -sSL --max-redirs 4 -o /dev/null -m 8 "https://$DOMINIO/" 2>&1 \
           | grep -qi 'redirect'; then
        laco_de_redirecionamento=true
        break
    fi
    sleep 5
done

# E o certificado do PRÓPRIO Caddy: `--resolve` fura o proxy da Cloudflare e
# fala com o container aqui mesmo. Se o Caddy não emitiu nada, isto falha —
# mesmo com o site respondendo bonito pela borda da Cloudflare.
if [[ "$certificado_ok" == true ]] \
    && ! curl -fsS --resolve "$DOMINIO:443:127.0.0.1" -o /dev/null -m 8 \
         "https://$DOMINIO/api/health/live" 2>/dev/null; then
    aviso "o site responde pela Cloudflare, mas a origem não apresentou certificado próprio."
    echo "      Confira em SSL/TLS → Overview se o modo da zona é Full (strict)."
fi

if [[ "$laco_de_redirecionamento" == true ]]; then
    aviso "o domínio entrou em laço de redirecionamento."
    echo "      Quase sempre é o modo SSL/TLS da zona: em \"Flexible\" a Cloudflare"
    echo "      fala com o servidor em HTTP e o Caddy devolve para HTTPS, sem fim."
    echo "      Troque para \"Full (strict)\" no painel: SSL/TLS → Overview."
fi

if [[ "$certificado_ok" == true ]]; then
    ok "https://$DOMINIO respondendo com certificado válido"
else
    aviso "o certificado ainda não ficou pronto"
    echo
    echo "  Isso quase sempre é uma destas três coisas:"
    echo "    · o domínio $DOMINIO ainda não aponta para este servidor (DNS propagando)"
    echo "    · o registro A/AAAA aponta para outro IP"
    echo "    · o token não tem permissão de DNS nesta zona"
    echo
    echo "  Acompanhe:  docker compose -f docker-compose.prod.yml logs -f caddy"
fi

# --- fim --------------------------------------------------------------------
ip_publico="$(curl -fsS --max-time 5 https://api.ipify.org 2>/dev/null || echo '?')"

cat <<FIM_TEXTO

${FORTE}Pronto.${FIM}

  Endereço      ${AZUL}https://$DOMINIO${FIM}
  Administrador $ADMIN_EMAIL
  IP público    $ip_publico

  ${FORTE}Primeiros passos${FIM}
  1. Entre com a conta de administração
  2. Campeonatos → importe o Brasileirão ou uma liga do mundo
  3. Crie o bolão e passe o código de convite para o grupo

  ${FORTE}Comandos${FIM}
  Ver o que está acontecendo    docker compose -f docker-compose.prod.yml logs -f
  Estado dos serviços           docker compose -f docker-compose.prod.yml ps
  Atualizar depois de um git pull  ./atualizar.sh
  Backup do banco               ./backup.sh
  Trocar uma senha              docker compose -f docker-compose.prod.yml exec api python -m app.cli reset-password --email <conta>

  O cadastro está em modo ${FORTE}convite${FIM}: só entra quem tiver o código de um
  bolão. Para abrir o cadastro a qualquer pessoa, troque REGISTRATION_MODE
  para \`aberto\` no .env e rode ./atualizar.sh.

FIM_TEXTO
