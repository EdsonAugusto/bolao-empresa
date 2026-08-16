# Bolão

Bolão de futebol entre amigos, rodando na sua própria rede. Você cria o bolão,
manda o código para o grupo, todo mundo palpita os placares e o ranking se
atualiza sozinho conforme os jogos são apurados.

**Custo zero. Sem cobrança, sem assinatura, sem serviço externo obrigatório.
Sem aposta com dinheiro real.**

---

## Dois jeitos de rodar

**Na sua rede, para o grupo da firma.** Docker Desktop, dois comandos, todo
mundo entra pelo IP da máquina. É o que a próxima seção explica.

**Num servidor, com domínio e HTTPS.** Um comando, três respostas — domínio,
token da Cloudflare e senha do administrador — e a plataforma sobe com
certificado válido, cadastro por convite e backup. Está em
[docs/DEPLOY.md](docs/DEPLOY.md).

```bash
git clone https://github.com/EdsonAugusto/bolao-empresa.git && cd bolao-empresa && sudo ./instalar.sh
```

## Requisitos

Docker Desktop (ou Docker Engine + Compose v2). Só isso — Python e Node rodam
dentro dos containers.

## Subir

**Windows (PowerShell)** — `make` não existe no Windows, use o script equivalente:

```powershell
Copy-Item .env.example .env
.\task.ps1 up
.\task.ps1 migrate
```

**Linux / macOS / WSL**

```bash
cp .env.example .env
make up
make migrate
```

Abra <http://localhost:8080>, crie sua conta (a primeira vira administradora da
instalação) e siga o roteiro abaixo.

## Dois tipos de bolão

**De campeonato.** Segue a tabela: as rodadas são as do campeonato, e todos os
jogos valem. Em *Jogos* você desliga o que não quiser.

**De rodada montada.** Você cria uma rodada por semana e escolhe os jogos, de
**qualquer campeonato importado** — o clássico do Brasileirão, o jogo grande da
Premier League e a decisão da Libertadores na mesma rodada. Em *Rodadas* você
filtra por data e campeonato e marca o que entra.

Dois detalhes que a tela impõe, e valem saber por quê:

- Jogo que já começou **não entra** numa rodada. Seria pedir palpite de
  resultado conhecido.
- Jogo em andamento **não sai** da rodada. Tirar apagaria os palpites de quem
  já respondeu.

A pontuação, a blindagem e o desempate são idênticos nos dois — é o mesmo motor.

## Roteiro do primeiro bolão

1. **Campeonatos → Importar tabela real.** Traz o Brasileirão de verdade do GE
   Globo: datas, placares dos jogos já realizados e escudos oficiais. Leva ~40s.
   *Ou* use **Importar por CSV** para qualquer outro campeonato — colunas
   `rodada, data, mandante, visitante, gols_mandante, gols_visitante`, data em
   horário de Brasília, gols em branco para jogo que não aconteceu.
   Reimportar o mesmo arquivo **atualiza** em vez de duplicar.
2. **Criar bolão.** Escolha o campeonato e o modo de pontuação.
3. **Jogos** (só o organizador). Por padrão todos os 380 valem. Aqui você
   liga/desliga rodadas inteiras ou jogos avulsos — dá para montar um bolão só
   do turno, ou só dos clássicos.
4. **Passe o código de convite** (8 letras) para o grupo. Quem receber entra em
   *Entrar com código*.
5. **Todo mundo palpita** antes do apito de cada jogo. A tela **Como funciona**
   mostra as regras deste bolão, geradas da configuração real.
6. **Lance os placares** conforme os jogos terminam — pela tela do jogo ou
   reimportando o CSV com os gols preenchidos. A apuração e o ranking são
   automáticos.

> **Tabela real ou gerada?** Prefira a real. A gerada existe para quando você
> quiser testar a plataforma sem internet, ou montar um campeonato inventado:
> ela é estruturalmente correta — cada clube enfrenta todos os outros duas
> vezes, uma em casa — mas as datas são plausíveis, não oficiais.

## Jogar pelo celular

Vá em **Rede**: a plataforma descobre o endereço da máquina e mostra o link
para abrir no celular (mesmo Wi-Fi). Se não abrir, libere a porta 8080 no
firewall do computador.

### Instalar como aplicativo

Um botão **Instalar** aparece na tela inicial e no perfil. Ele põe o Bolão
junto dos outros ícones do celular: abre sem barra de endereço, com ícone
próprio, e não ocupa espaço — é um atalho para este mesmo site.

No iPhone o Safari não tem API para isso, então a mesma seção ensina o caminho:
Compartilhar → Adicionar à Tela de Início.

Duas coisas que só funcionam com HTTPS, ou seja, num servidor com domínio:

- **A oferta de instalar.** Por IP na rede local o navegador não considera a
  página confiável o bastante, e nem oferece.
- **Gravar áudio no relato de bug.** O microfone exige contexto seguro. Pela
  LAN, a tela diz isso em vez de fingir que gravou — e anexar um arquivo de
  áudio do gravador do celular sempre funciona.

Não é uma cópia offline: placar e palpite continuam vindo do servidor. Um bolão
sem placar atualizado não teria graça. Sem conexão, aparece uma tela dizendo
isso em vez do erro do navegador.

## Como funciona a pontuação

Três modos. O personalizado é um superconjunto dos outros dois.

| Critério | Clássico | Simples |
|---|---|---|
| Placar exato | 10 | 10 |
| Vencedor + placar de um time | 7 | — |
| Vencedor + saldo de gols | — | — |
| Empate (sem cravar o placar) | 5 | 5 |
| Só o vencedor | 5 | 7 |
| Placar de um time, errando o vencedor | 2 | — |
| Errou tudo, ou não palpitou | 0 | 0 |

Regras que valem sempre:

- **Cada palpite leva o critério de maior valor que satisfaz.** Cravar o placar
  nunca paga menos que acertar só o vencedor, mesmo no modo personalizado.
- **A ordem de avaliação é por pontos, decrescente** — não é fixa. Se você puser
  "só o vencedor" valendo mais que "placar exato", é isso que vale.
- **Tempo normal + prorrogação.** Pênaltis decidem quem avança, não o resultado.
- **Desempate** pela quantidade de acertos no critério mais valioso do bolão,
  descendo a lista. Em último caso, quem entrou antes. Empate de verdade
  (mesma pontuação e mesmos acertos) **divide a posição**.
- **Palpite blindado**: ninguém vê o palpite de ninguém antes do jogo começar.
- Rodada pode valer 2×, 3× — o organizador define.
- **Só valem os jogos selecionados** pelo organizador na tela *Jogos*.
- Jogo cancelado ou abandonado não pontua para ninguém e sai da conta.
- Placar corrigido depois de apurado reapura a rodada e ajusta o ranking.

A tela **Como funciona** de cada bolão mostra tudo isso com os números daquele
bolão — ela é gerada do mesmo motor que apura, então nunca diverge da
configuração.

## A tela de entrada

Estádio à noite: gramado em perspectiva com o corte da grama convergindo,
quatro refletores com facho de luz, arquibancada com flashes de câmera piscando.

Tudo é CSS — sem WebGL, sem biblioteca, sem imagem. As únicas propriedades
animadas são `opacity` e `transform`, que o navegador resolve na GPU sem tocar
no layout. O custo é zero JavaScript rodando e ~50 elementos no DOM, o que
importa porque a primeira tela costuma abrir num celular em rede móvel.

Quem tem "reduzir movimento" ligado no sistema recebe a mesma cena, parada.

## Quem pode o quê

A primeira conta da instalação é a **dona** e manda em tudo. A partir daí, o
painel **Pessoas** distribui: nível na hierarquia (dono, administrador,
moderador, organizador, jogador) e permissões por grupo ou por pessoa.

A regra que sustenta o resto: **só se mexe em quem está abaixo**, e **ninguém
concede o que não tem**. É o que permite dar ao amigo que organiza o bolão da
firma o direito de importar campeonato sem lhe dar as contas de todo mundo.

Cada permissão tem três estados — dar, tirar e **voltar ao padrão** — e o painel
mostra de onde ela vem, porque desmarcar uma que veio do nível é diferente de
desmarcar uma que veio de um grupo.

Detalhes em [docs/PERMISSOES.md](docs/PERMISSOES.md).

## Relatos: bug, retorno e ideia

Um botão 🐞 no canto de **qualquer tela** abre o painel de relato. Ele aceita
texto, imagem e áudio, e captura sozinho o contexto que faz a diferença entre
um bug reproduzível e um "não consegui reproduzir": a tela em que a pessoa
estava, o navegador e o tamanho do monitor.

- **Colar captura de tela funciona** (`Ctrl+V`), assim como arrastar o arquivo
  para dentro da janela. É como as pessoas realmente reportam bug.
- **Áudio**: dá para gravar direto na tela — mas só em HTTPS ou `localhost`.
  Por IP na rede local o navegador bloqueia o microfone, e a tela diz isso em
  vez de fingir que funcionou. O caminho que sempre funciona é gravar no
  gravador do telefone e anexar o arquivo.
- Cada relato ganha um código curto (`R-7K3M`) para citar em conversa.

Quem administra a plataforma vê todos os relatos, tria (aberto → triado →
fazendo → resolvido) e exporta tudo em Markdown para levar direto ao trabalho.
Quem relatou acompanha o próprio relato e lê a resposta — sem isso, ninguém
manda o segundo.

### O que o upload aceita, e por quê

PNG, JPEG, GIF, WebP, e áudio em WebM, OGG, MP4, WAV e MP3. **O tipo é detectado
pela assinatura do arquivo**, não pelo que o navegador declara: um HTML
renomeado para `.png` é recusado.

**SVG não entra.** É XML que executa script, e servi-lo de volta para quem abre
o relato seria XSS na sessão de quem administra — justamente quem abre todos.

Anexo herda a permissão do relato e nunca tem URL pública: captura de tela de
bug carrega palpite alheio e nome de participante.

## Onde os dados dos jogos vêm

| Fonte | Custo | O que traz |
|---|---|---|
| **fixturedownload.com** (CSV) | zero | calendário das ligas do mundo, em UTC |
| **GE Globo** (coleta) | zero | Brasileirão real, com placares e escudos oficiais |
| **Wikipédia** | zero | mata-mata: chaveamento, data e fuso de cada jogo |
| **Cadastro manual / CSV** (padrão) | zero | o que você digitar |
| Tabela gerada | zero | temporada plausível, sem depender de nada |
| API-Football | grátis até 100 req/dia | Brasileirão, estaduais e Copinha |
| football-data.org | pago para o Brasil | Série A brasileira **não** está no plano gratuito |

Nenhuma é obrigatória. O manual sempre funciona, inclusive sem internet.

### Campeonatos disponíveis

Cada fonte serve a um formato diferente, e a razão é essa:

**Ligas de pontos corridos** — `Campeonatos → Ligas do mundo`. Uma requisição
por liga, datas já em UTC, sem chave e sem cota. Verificadas em 31/07/2026:

| Liga | País | Rodadas | Jogos |
|---|---|---|---|
| Premier League | Inglaterra | 38 | 380 |
| LaLiga | Espanha | 38 | 380 |
| Serie A | Itália | 38 | 380 |
| Bundesliga | Alemanha | 34 | 306 |
| Ligue 1 | França | 34 | 306 |
| Primeira Liga | Portugal | 34 | 306 |
| Eredivisie | Holanda | 34 | 306 |
| MLS | Estados Unidos | 34 | 510 |
| Championship · League One · Ligue 2 · Süper Lig · Scottish Premiership | — | — | — |

Essa fonte não traz escudo — veja **Escudos** abaixo.

**Brasileirão** — `Campeonatos → Importar tabela real`, do GE. É a única fonte
gratuita com a tabela da CBF, e vem com os escudos oficiais.

**Mata-mata** — `Campeonatos → Mata-mata`, da Wikipédia em português. Torneio
eliminatório não tem calendário em CSV porque o chaveamento só existe depois do
sorteio, e o GE devolve lista vazia para as fases eliminatórias. A Wikipédia
publica cada jogo com o fuso explícito, o que evita errar a hora de jogo em
outro fuso. Funciona para qualquer artigo com caixas `Footballbox` — Copa do
Brasil, Champions, Copa do Mundo.

**Qualquer outro campeonato do GE** pode ser acrescentado por você, em
*Campeonatos → Cadastrar um campeonato que não está na lista*: cole o endereço
da página e a plataforma testa os identificadores candidatos. O GE não tem
catálogo público — o identificador da tabela é um UUID embutido no HTML, no meio
de dezenas de ids de matéria, e testar é a única forma de saber qual é.

### Resultado e atualização automática

A plataforma coleta o placar sozinha. Cada competição é atualizada pela **sua**
fonte — o Brasileirão pelo GE, as ligas europeias pelo CSV, o mata-mata pela
Wikipédia — e a configuração de cada uma fica gravada na importação.

| Job | Quando | O que faz |
|---|---|---|
| `sync_live` | a cada 2 min | placar durante o jogo; só roda se houver partida em campo |
| `close_predictions` | a cada 1 min | fecha o palpite no minuto do apito |
| `sync_results` | 2× por hora | busca resultado de quem já jogou e ainda não tem placar |
| `settle_fixtures` | 4× por hora | apura e atualiza o ranking |
| `close_stale_fixtures` | 1× por hora | encerra jogo com placar que ficou preso "em andamento" |
| `sync_fixtures` | 1× por dia | recoleta o calendário: jogo remarcado, horário confirmado |

**Duas camadas, porque calendário e placar não vêm da mesma lugar.**

O **calendário** vem da fonte que importou o campeonato: o GE para o
Brasileirão, o CSV para as ligas europeias, a Wikipédia para o mata-mata. Cada
uma é recoletada pela sua própria fonte, e é isso que traz jogo remarcado e
horário confirmado.

O **placar ao vivo** vem da API pública da ESPN, sobreposta ao calendário que já
existe. Ela publica durante o jogo — placar, estado e minuto — e cobre as dez
competições do catálogo numa requisição por liga e por dia. Isso resolve o
problema que o CSV tinha: ele só publica o resultado horas depois do apito, e
PSV × Fortuna Sittard ficava marcado como "em campo" muito depois de acabar.

A sobreposição **não importa jogo**. Ela casa o confronto — mandante, visitante,
liga e horário próximo — e só atualiza placar e estado. O calendário continua
sendo de quem o importou, e nada é duplicado. Em caso de dúvida ela não escreve:
placar errado é pior do que placar ausente.

Fora da janela de jogo, `sync_live` não faz requisição nenhuma — a pergunta
"tem jogo agora?" é uma consulta local.

A tela de palpite se atualiza sozinha a cada 45 segundos **enquanto há jogo em
campo**, e para quando a rodada acaba. O que você digitou e ainda não salvou não
se perde na atualização.

#### Quando a rodada não conclui

Um jogo pode ficar sem placar: a fonte não publicou, ou a partida não aconteceu.
Nesse caso ele **continua travado** para palpite e aparece como *aguardando
resultado*. A plataforma não inventa placar e não marca como adiado — adiado
reabriria o palpite num jogo que todo mundo já viu. O caminho é o organizador
lançar o placar na tela do jogo, e a apuração corre atrás sozinha.

### Retrospecto

Cada clube mostra os últimos cinco jogos (V/E/D) na tela de palpite. O
retrospecto é montado pelo nome comparável do clube, não pelo `team_id`: o mesmo
clube tem uma linha por fonte — o Flamengo do GE e o da Wikipédia são registros
diferentes — e por id o histórico sairia partido, com o clube "estreando na
vida" na tela da Libertadores.

### Escudos

Nenhuma fonte de calendário completo traz escudo junto, então isso é um passo
separado: `Campeonatos → Escudos → Buscar escudos`. Sem escudo o clube aparece
com o escudo desenhado pela plataforma — funciona, mas não é a mesma coisa.

Três fontes, nessa ordem:

1. **O que já está no banco.** O coletor do GE traz o escudo oficial em SVG. Se
   o mesmo clube já entrou por outro campeonato, o escudo já está aqui — de
   graça, sem rede, e no mesmo padrão visual dos outros.
2. **football-data.org**, se você tiver a chave. Uma requisição por liga traz os
   18 ou 20 clubes de uma vez. É instantâneo. A chave é gratuita, mas exige
   cadastro por e-mail — ponha em `FOOTBALL_DATA_ORG_KEY`.
3. **TheSportsDB**, sem cadastro nenhum. Vai clube a clube, com seis segundos
   entre um e outro para não ser bloqueada; cem clubes levam uns dez minutos.
   Roda no worker, em lotes, e retoma de onde parou.

**O casamento é por nome, e nome é o problema.** Cada fonte chama o mesmo clube
de um jeito: `Man Utd` no CSV, `Manchester United` no GE, `FC Bayern München`
numa, `Bayern de Munique` na outra. A busca externa só aceita resultado da liga
esperada — buscar "Inter" devolve um clube da quarta divisão espanhola antes da
Internazionale, e "Man Utd" devolve um time da copa da Finlândia. **Escudo
trocado é pior do que escudo ausente**, porque a pessoa vê o erro na hora e ele
sobrevive a qualquer recoleta. Na dúvida, a plataforma não preenche.

Se um escudo vier errado mesmo assim, apague o campo e rode de novo — o
preenchimento nunca sobrescreve o que já está lá.

#### Por que a temporada europeia nova não vem do GE

O GE só publica a temporada em curso: em 31/07/2026 a página do Campeonato
Inglês ainda apontava para `fase-unica-campeonato-ingles-2025-2026`, encerrada
em maio. A plataforma dizia "temporada encerrada" — e estava certa sobre o que
tinha no banco, mas a 2026-27 já existia. É por isso que as ligas vêm de CSV, e
o Brasileirão continua vindo do GE.

### Sobre a coleta do GE Globo

É o caminho que traz os dados **reais** de graça, e por isso é o recomendado
para valer. Duas coisas para você decidir com consciência:

- **Não é uma API pública.** É o endpoint interno que alimenta o site do ge.
  Os termos de uso do Globo não autorizam coleta automatizada. Para um bolão
  entre amigos numa máquina de casa o risco prático é baixo, mas a escolha é
  sua e ela não é tecnicamente neutra.
- **Vai quebrar um dia.** Endpoint interno muda sem aviso e sem versão. Quando
  mudar, o coletor **falha com mensagem específica** em vez de importar meia
  rodada errada — e aí a saída é o CSV, que nunca depende de ninguém.

```bash
docker compose exec api python -m app.cli import-brasileirao --year 2026
```

Ou pela tela **Campeonatos → Importar tabela real**. Leva cerca de 40 segundos
(38 requisições espaçadas em um segundo, para não abusar). Rodar de novo
atualiza os placares em vez de duplicar.

Jogo que a CBF ainda não marcou data fica **de fora**, e entra na importação
seguinte. Chutar uma data faria o palpite fechar na hora errada.

### Escudos

Vindos do GE, são os oficiais, servidos pelo CDN do Globo. Na tabela gerada, a
plataforma desenha um escudo com as cores de cada clube — os oficiais são marca
registrada e um link de terceiro quebraria o uso offline. Se um link cair, a
tela volta sozinha para a sigla.

## Avisos

Por padrão, dentro da própria plataforma (o item **Avisos** no menu). Opcional:
Telegram — crie um bot com o [@BotFather](https://t.me/botfather), ponha o token
em `TELEGRAM_BOT_TOKEN` e cada participante cola o `chat_id` no perfil. Funciona
atrás de qualquer roteador doméstico, sem IP fixo.

WhatsApp ficou de fora de propósito: a API oficial cobra por conversa e não
manda em grupo; as bibliotecas não-oficiais violam os termos de uso e derrubam
o número.

## Comandos

| PowerShell | make | O que faz |
|---|---|---|
| `.\task.ps1 up` | `make up` | sobe tudo |
| `.\task.ps1 down` | `make down` | derruba (mantém os dados) |
| `.\task.ps1 nuke` | `make nuke` | derruba e **apaga** os dados |
| `.\task.ps1 logs` | `make logs` | acompanha os logs |
| `.\task.ps1 migrate` | `make migrate` | aplica as migrations |
| `.\task.ps1 test` | `make test` | pytest + vitest |
| `.\task.ps1 lint` | `make lint` | ruff + mypy + eslint |
| `.\task.ps1 psql` | `make psql` | abre o banco |

### Administração da instalação

Não existe recuperação de senha por e-mail — a plataforma não envia e-mail.
Quem tem acesso à máquina redefine:

```bash
docker compose exec api python -m app.cli list-users
```

```bash
docker compose exec api python -m app.cli reset-password --email voce@casa.local
```

Sem `--password`, uma senha forte é gerada e mostrada uma única vez. Todas as
sessões daquela conta são encerradas.

## Estrutura

```
api/                  FastAPI + SQLAlchemy 2 + Alembic
  app/
    api/              rotas HTTP (fino)
    core/             config, banco, redis, logging, segurança
    models/           SQLAlchemy
    schemas/          Pydantic — a blindagem de palpite mora aqui
    services/         regra de negócio
    scoring/          motor de pontuação puro (sem I/O, 99% de cobertura)
    providers/        integrações externas atrás de interface
    jobs/             tarefas arq (sincronização, apuração, lembretes)
  alembic/            migrations
  tests/
web/                  Nuxt 3 (SSR) + PWA
infra/nginx/          proxy reverso
docs/                 roadmap e decisões de arquitetura
```

## Testes

```
.\task.ps1 test
```

157 testes no backend e 16 no frontend. Os que mais importam:

- `tests/test_scoring.py` — tabela-verdade dos 3 modos, desempate e testes de
  propriedade. 99% de cobertura no motor.
- `tests/test_predictions.py` — trava de palpite (inclusive por SQL cru, contra
  a trigger do banco) e blindagem.
- `tests/test_api.py` — **teste de vazamento por endpoint**.
- `tests/test_settlement.py` — rodada real com 10 participantes e 5 jogos,
  conferida contra uma tabela escrita à mão; correção de placar; idempotência.

## Aviso

Plataforma de entretenimento entre amigos. Não movimenta dinheiro e não opera
apostas. Ver `docs/DECISOES.md`.
