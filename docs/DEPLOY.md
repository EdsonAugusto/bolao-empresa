# Colocar no ar

Servidor Linux, domínio próprio, HTTPS válido. Um comando e algumas respostas.

---

## O que você precisa antes

**Um servidor.** Qualquer VPS de 2 GB de RAM dá conta de um bolão entre amigos
— a plataforma inteira em repouso ocupa menos de 800 MB. Ubuntu 22.04 ou 24.04,
Debian 12, ou qualquer distribuição onde o Docker rode.

**Um domínio na Cloudflare.** Não precisa comprar nada dela: basta o domínio
ter os servidores de nome apontando para a Cloudflare, que é gratuito. É de lá
que sai o certificado.

**Um registro A** apontando o domínio (ou o subdomínio) para o IP do servidor.
Pode estar com o proxy ligado (nuvem laranja) ou desligado — funciona nos dois.

**Com o proxy ligado, o modo SSL/TLS da zona precisa ser `Full (strict)`**
(painel: SSL/TLS → Overview). No modo `Flexible` — padrão histórico, ainda
comum em zonas antigas — a Cloudflare fala com o servidor em HTTP, o Caddy
responde redirecionando para HTTPS, e o visitante entra em laço
(`ERR_TOO_MANY_REDIRECTS`). O instalador detecta isso no fim e avisa.

**Portas 80 e 443 abertas** no firewall do provedor.

---

## O comando

```bash
git clone https://github.com/EdsonAugusto/bolao-empresa.git && cd bolao-empresa && sudo ./instalar.sh
```

Ele pergunta o seguinte — as duas últimas já vêm com sugestão, é só apertar
Enter:

| Pergunta | Exemplo | Para quê |
|---|---|---|
| Domínio | `bolao.meusite.com.br` | endereço da plataforma e nome no certificado |
| Token da Cloudflare | *(oculto)* | emitir o certificado HTTPS |
| Senha do administrador | *(oculta, com confirmação)* | a conta que importa campeonato e cria bolão |
| E-mail do ACME | `admin@seu-dominio` | a Let's Encrypt avisar sobre vencimento |
| E-mail do administrador | `admin@seu-dominio` | login da conta de administração |

E cuida do resto: instala o Docker se faltar, gera chave de assinatura e senha
de banco aleatórias, constrói as imagens, aplica as migrations, cria a conta de
administração e emite o certificado. Cinco a dez minutos na primeira vez.

Rodar de novo é seguro. O `.env` anterior é copiado para `.env.bak.<data>` e
o novo preserva:

- a **chave de assinatura** e a **senha do banco** — sem elas as sessões
  abertas cairiam e a aplicação perderia acesso aos próprios dados;
- o **modo de cadastro**, o fuso, o número de workers, o provedor de futebol e
  a validade do token — ou seja, o que você ajustou depois de instalar;
- **qualquer chave que você tenha acrescentado à mão** (aviso por Telegram,
  chave de provedor), remontada no fim do arquivo sob um cabeçalho próprio, e
  listada na tela para você conferir.

### O token da Cloudflare

Painel da Cloudflare → **Meu Perfil** → **Tokens de API** → **Criar Token** →
modelo **Editar zona DNS**:

- Permissões: `Zona` · `DNS` · `Editar`
- Recursos da zona: `Zona específica` · *seu domínio*

O token fica só no `.env` do servidor, com permissão `600`. Ele é usado para
criar um registro TXT temporário que comprova que o domínio é seu — o desafio
DNS-01. É esse mecanismo que faz o certificado funcionar **com o proxy da
Cloudflare ligado** e **sem depender da porta 80**, ao contrário do desafio
HTTP-01, que a nuvem laranja intercepta.

O instalador valida o token na API da Cloudflare antes de tentar emitir
qualquer coisa. Token errado falha em dois segundos, não depois de gastar uma
tentativa no limite semanal da Let's Encrypt.

---

## O que sobe

```
        internet
            │  443 (TLS, HTTP/3)
        ┌───▼────┐
        │ caddy  │  certificado, compressão, cabeçalhos de segurança
        └───┬────┘
     ┌──────┴──────┐
┌────▼───┐    ┌────▼───┐
│  web   │    │  api   │   Nuxt SSR · FastAPI
└────────┘    └───┬────┘
                  │
       ┌──────────┼──────────┐
  ┌────▼───┐ ┌────▼───┐ ┌────▼────┐
  │postgres│ │ redis  │ │ worker  │
  └────────┘ └────────┘ └─────────┘
```

A conta de administração é criada **antes** de a borda subir. A primeira conta
a se cadastrar vira administradora — o comportamento certo em rede local — e
publicar o domínio antes de ela existir abriria uma janela em que qualquer
pessoa da internet se cadastraria e sairia administrando a instalação. Por isso
o instalador sobe a aplicação, cria o administrador, troca o cadastro para
`convite` e só então abre as portas 80 e 443.

Postgres e Redis **não publicam porta no host**. Eles falam com a aplicação
pela rede interna do Compose e não são alcançáveis de fora — publicar a 5432
num servidor com IP público é convite para varredura automatizada.

O `docker-compose.prod.yml` é um arquivo inteiro, não um complemento do de
desenvolvimento. A razão é chata mas decisiva: o Compose **mescla** listas de
`ports` e `volumes` em vez de substituí-las, então um complemento não
conseguiria *tirar* a porta do banco nem o bind-mount do código-fonte.

---

## O que muda entre a casa e a internet

A plataforma nasceu para rodar na rede de casa, onde estar na rede já era
autorização suficiente. Sair para a internet muda o que é aceitável, e a
configuração muda junto:

| | Rede local | Servidor público |
|---|---|---|
| Cadastro | aberto | exige código de convite |
| `Origin` de rede privada | aceito | recusado |
| Chave de assinatura | valor de exemplo serve | **recusa subir** com ele |
| Senha do banco | valor de exemplo serve | **recusa subir** com ela |
| `DEBUG` | pode ficar ligado | **recusa subir** ligado |
| Portas de Postgres/Redis | publicadas no host | só na rede interna |
| Documentação da API (`/api/docs`) | aberta | fechada |
| Código-fonte | bind-mount, recarrega ao salvar | dentro da imagem |
| Limite de tentativa de login | ativo | ativo |

As três linhas em negrito são validadas na subida da API: com
`ENVIRONMENT=production` e um valor de exemplo, o container morre com a
mensagem dizendo qual é o problema. Falhar na subida é melhor do que subir
funcionando com a chave que está publicada no repositório.

### Abrir o cadastro para qualquer pessoa

O padrão do servidor é `REGISTRATION_MODE=convite`: para criar conta é preciso
o código de 8 letras de um bolão, o mesmo que o organizador manda no grupo.
Para deixar aberto, troque no `.env` e rode `./atualizar.sh`.

`fechado` também existe: ninguém se cadastra pelo site, e as contas nascem por
linha de comando.

---

## Operação

```bash
sudo ./atualizar.sh    # depois de um git pull: backup, reconstrói, migra, sobe
./backup.sh            # banco + anexos em backups/
docker compose -f docker-compose.prod.yml logs -f
docker compose -f docker-compose.prod.yml ps
```

**`atualizar.sh` faz backup antes de qualquer coisa** e recusa continuar se o
backup falhar. Se a API não voltar saudável, ele mostra o log e para — o
backup está lá.

### Backup automático

```bash
sudo crontab -e
```

```
0 3 * * * cd /caminho/do/bolao && ./backup.sh >> /var/log/bolao-backup.log 2>&1
```

Guarda os 14 mais recentes e apaga os antigos — backup que enche o disco
derruba a aplicação que ele deveria proteger. Para mudar, ponha
`BOLAO_BACKUPS_MANTER=60` no `.env` ou na própria linha do cron. O valor
mínimo é 1: `0` significaria apagar tudo, inclusive o backup recém-criado, e
por isso é recusado.

### Restaurar

Pare a aplicação antes, para nada escrever no meio da restauração. Duas
opções fazem a diferença entre restaurar e destruir:

`ON_ERROR_STOP=1` faz o `psql` parar no primeiro erro — sem ele, ele engole e
segue. Mas parar não é desfazer: o dump começa por dezenas de `DROP TABLE`, e
um arquivo com problema deixaria o banco pela metade sem caminho de volta.
`--single-transaction` põe tudo numa transação só, e qualquer erro devolve o
banco exatamente como estava.

Confira o arquivo antes, é um segundo:

```bash
gzip -t backups/bolao-AAAAMMDD-HHMMSS.sql.gz && echo "arquivo íntegro"
```

```bash
docker compose -f docker-compose.prod.yml stop api worker web
```

```bash
gunzip -c backups/bolao-AAAAMMDD-HHMMSS.sql.gz | docker compose -f docker-compose.prod.yml exec -T postgres psql -v ON_ERROR_STOP=1 --single-transaction -U bolao -d bolao
```

```bash
docker compose -f docker-compose.prod.yml start api worker web
```

Os anexos voltam separado:

```bash
gunzip -c backups/anexos-AAAAMMDD-HHMMSS.tar.gz | docker compose -f docker-compose.prod.yml exec -T api tar -xf - -C /data
```

### Trocar uma senha

Não há e-mail de recuperação — a plataforma não envia e-mail, e é assim que ela
não depende de nenhum serviço pago. Quem tem acesso ao servidor redefine:

```bash
docker compose -f docker-compose.prod.yml exec api python -m app.cli reset-password --email pessoa@exemplo.com
```

Sem `--password`, uma senha forte é gerada e mostrada uma vez. Todas as sessões
abertas daquela conta são encerradas.

---

## Quando algo dá errado

**O certificado não sai.** Quase sempre é uma destas três: o domínio ainda não
aponta para este servidor, o registro A aponta para outro IP, ou o token não
tem permissão de DNS nesta zona.

```bash
dig +short SEU-DOMINIO          # o IP que sai daqui é este servidor?
curl -fsS https://api.cloudflare.com/client/v4/user/tokens/verify \
  -H "Authorization: Bearer SEU-TOKEN"
docker compose -f docker-compose.prod.yml logs caddy
```

**A API não sobe.** Se o log traz `configuração insegura para
ENVIRONMENT=production`, ele já diz qual campo está com valor de exemplo.

**502 depois de recriar containers.** O Caddy resolve o nome dos serviços a
cada requisição, então isso não deveria acontecer. Se acontecer, é a API que
não subiu: `docker compose -f docker-compose.prod.yml ps` mostra quem está de
pé.

**Disco cheio.** As camadas de imagem antigas se acumulam a cada atualização.
`atualizar.sh` já roda `docker image prune -f` no fim; para uma limpeza mais
funda, `docker system prune -a` — sem `--volumes`, que apagaria o banco.

---

## Instalar no celular

Com HTTPS válido, o navegador oferece a instalação. Um botão "Instalar" aparece
na tela inicial e no perfil; no iPhone, onde o Safari não tem API para isso, a
mesma seção ensina o caminho (Compartilhar → Adicionar à Tela de Início).

Instalado, o atalho abre sem barra de endereço e com ícone próprio. Não é uma
cópia offline: placar e palpite continuam vindo do servidor — um bolão sem
placar atualizado não teria graça. Sem conexão, aparece uma tela dizendo isso
em vez do erro do navegador.

**O HTTPS também libera o microfone.** O gravador de áudio do relato de bug
depende de contexto seguro, então ele não funcionava pelo IP da rede local.
Com domínio, funciona.
