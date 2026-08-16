# Decisões de arquitetura

Registro curto do que foi decidido e por quê. Mudar qualquer uma destas exige
mexer no schema ou na infraestrutura — não decida de novo sem ler o motivo.

---

## D0 — Perfil: amigos, rede local, custo zero

**Decisão.** A plataforma roda num computador de casa, é acessada pelo IP na
rede local e não depende de nenhum serviço pago. Não existe cobrança, plano,
anúncio, domínio público, TLS nem publicação em loja de aplicativo.

**Consequência no código.** As fases 8, 9 e 10 do plano original mudaram de
escopo (ver `ROADMAP.md`). Todo provedor externo é opcional e tem caminho
gratuito equivalente. O `docker compose up` é o modo que a pessoa realmente
usa — não existe um "modo produção" separado, e é por isso que o Nuxt DevTools
está desligado.

---

## D1 — Sem dinheiro real na plataforma

**Decisão.** A plataforma não movimenta dinheiro de bolão. Não existe quota,
prêmio, carteira, saldo ou split. Monetização é assinatura, remoção de anúncio
e plano corporativo. Se os participantes acertarem dinheiro entre si, isso
acontece fora do sistema.

**Por quê.** Operar quota de bolão com premiação em dinheiro cai na Lei
14.790/2023 e na regulamentação da SPA/MF, e exige autorização federal com
outorga na casa de dezenas de milhões. Não é um detalhe de compliance que se
resolve depois — é um produto diferente.

**Consequência no código.** Nenhuma tabela de prêmio ou saldo. O rodapé declara
a natureza de entretenimento. Se aparecer pedido de split de prêmio, pare e
escale antes de escrever qualquer linha.

---

## D2 — Notificação: in-app por padrão, Telegram opcional, WhatsApp fora

**Decisão.** Toda notificação passa por `NotificationChannel`. O canal padrão é
`InAppChannel` (central de avisos na própria plataforma). `TelegramChannel` é
opcional. **WhatsApp está fora.**

**Por quê.** A Cloud API da Meta cobra por conversa, exige template aprovado e
**não envia mensagem para grupo** — o "ranking aparece no grupo" não existe na
API oficial. As bibliotecas não-oficiais mandam em grupo e custam zero, mas
violam os termos de uso e derrubam o número. Nenhuma das duas cabe em "custo
zero entre amigos".

O Telegram entra porque o envio é uma chamada HTTP **de saída**: funciona atrás
de qualquer roteador doméstico, sem IP fixo e sem webhook público.

**Consequência no código.** Nenhum service importa cliente de mensageria. As
regras de opt-out, silêncio noturno e deduplicação valem igual para todo canal.

---

## D3 — Cadastro manual como provedor padrão

> **Correção.** Uma versão anterior deste documento afirmava que o plano
> gratuito do football-data.org cobre a Série A brasileira. **Não cobre** — ela
> está só nos planos pagos. Ver D11b, que é o caminho gratuito que sobrou.

**Decisão.** `ManualProvider` é o padrão: o organizador cadastra os jogos (ou
cola um CSV) e lança os placares. `FootballDataOrgProvider` (tier gratuito) e
`ApiFootballProvider` existem para quem quiser, mas **nenhum é obrigatório**.

**Por quê.** Um bolão entre amigos não pode parar no domingo à noite porque uma
API gratuita atingiu a cota diária, ou porque o cadastro no site do provedor
expirou. O manual não tem cota, não tem chave, não tem rede — e digitar uma
rodada leva um minuto, ou trinta segundos com o CSV.

O API-Football continua sendo o único com estaduais e Copinha, e por isso está
implementado; mas 100 requisições por dia não sustentam placar ao vivo, então
ele não pode ser o padrão de uma instalação gratuita.

**Consequência no código.** Nenhum módulo de domínio importa o cliente HTTP do
provedor. `build_provider()` cai para o manual quando o provedor configurado não
está utilizável — falta de chave não derruba a plataforma.

**Não fazer scraping** de SofaScore/Flashscore. Não é questão moral, é
operacional: anti-bot agressivo, DOM que muda sem aviso, e o bolão inteiro para
no domingo à noite.

---

## D4 — PK bigint interna, identificador público separado

**Decisão.** Chave primária é `BIGINT GENERATED ALWAYS AS IDENTITY`. Rotas
públicas usam `slug` ou código curto opaco (`pools.slug`, `pools.invite_code`).

**Por quê.** UUID em toda PK custa índice e cache sem devolver nada em troca
num modelo onde os identificadores públicos já existem por outro motivo. E id
sequencial em URL pública vaza volume de negócio.

---

## D5 — Front e back separados, não monolito

**Decisão.** Nuxt 3 e FastAPI são serviços distintos, conversando por HTTP.

**Por quê.** O app mobile e o worker de notificação consomem a mesma API. Um
monolito com server actions empurraria a regra de negócio para o cliente — e a
regra que mais importa aqui (blindagem de palpite) **não pode** morar no
cliente.

---

## D6 — UTC no banco, conversão só na borda

**Decisão.** Toda coluna de tempo é `TIMESTAMPTZ` em UTC. O container do
Postgres roda com `TZ=UTC`. A conversão para `America/Sao_Paulo` acontece na
apresentação: `web/utils/datetime.ts` no frontend, camada de serialização no
backend.

**Por quê.** É a armadilha número 1 desse tipo de produto. Um jogo às 21:30 de
sábado em São Paulo é 00:30 de **domingo** em UTC; agrupar a rodada pela data
UTC coloca o jogo no dia errado e o lembrete de palpite dispara na hora errada.
Há teste cobrindo exatamente esse caso desde a Fase 0
(`web/tests/datetime.spec.ts`).

---

## D7 — Nuxt 3 em vez de Next

**Decisão.** Frontend em Nuxt 3.

**Por quê.** Ecossistema Vue 3, que é o que já se domina aqui. SSR e SEO — que
é o que a Fase 8 exige — são equivalentes nos dois. Trocar de ecossistema para
imitar a stack de outro produto não compra nada.

---

## D8 — A trava de palpite olha o status, não só o relógio

**Decisão.** Um palpite só pode ser criado ou editado se o jogo estiver
`SCHEDULED` (e antes do horário) ou `POSTPONED`. Qualquer outro status fecha,
mesmo que o `kickoff_at` ainda esteja no futuro. A regra vive na trigger do
Postgres e é espelhada em `predictions.is_locked`.

**Por quê.** Encontrado rodando o fluxo real, não em revisão de código. No modo
manual o organizador lança o placar pela tela, e isso acontece **antes** do
horário marcado com frequência — ou a data veio errada no CSV. Com a regra
antiga, baseada só no relógio, o jogo ficava `FINISHED` e já pontuado enquanto
ainda aceitava palpite: dava para palpitar sabendo o resultado. E a blindagem
continuava ativa, então o ranking mexia sem ninguém conseguir ver por quê.

**Consequência no código.** Migration `0004`. Três testes de regressão em
`tests/test_predictions.py`, sendo um por SQL cru contra a trigger.

---

## D9 — nginx resolve os upstreams em tempo de requisição

**Decisão.** O `proxy_pass` aponta para uma **variável**, com `resolver
127.0.0.11` (DNS embutido do Docker).

**Por quê.** Com destino literal, o nginx resolve o IP dos containers uma única
vez, na subida, e guarda. Recriar a API muda o IP e o nginx passa a devolver 502
até ser reiniciado — o que acontece em todo `docker compose up --force-recreate`.
Custou um diagnóstico durante a construção; não vai custar de novo.

---

## D10 — Nuxt DevTools desligado

**Decisão.** `devtools: { enabled: false }`.

**Por quê.** Numa instalação caseira o `docker compose up` é o modo que a pessoa
usa de verdade — não existe um "modo produção" separado. O DevTools injeta um
overlay que captura cliques, um iframe e um canal RPC que enche o console de
aviso. Custo real, benefício nenhum para quem só quer palpitar.

---

## D11 — E-mail é identificador de login, não endereço de entrega

**Decisão.** O campo de e-mail é validado só quanto ao formato
(`nome@dominio`), sem `EmailStr`.

**Por quê.** A plataforma **não envia e-mail** — não há recuperação de senha por
link, confirmação nem notificação por e-mail. O validador estrito do Pydantic
recusa domínios reservados como `.local`, que é exatamente o que alguém usaria
numa rede doméstica (`pai@casa.local`). Exigir endereço público seria pedir um
dado que o sistema não usa.

**Consequência.** Sem recuperação de senha automática: quem esquecer a senha
pede ao administrador da instalação. É o compromisso certo para um grupo de
amigos, e evita depender de um servidor de e-mail.

---

## D11b — Coleta do GE Globo para os dados reais

**Decisão.** `GloboProvider` coleta calendário, placares e escudos oficiais do
endpoint interno que alimenta o ge.globo.com. É o caminho recomendado para um
bolão de verdade.

**Por quê.** As alternativas gratuitas não servem: o plano free do
football-data.org **não cobre** a Série A brasileira (eu afirmei o contrário em
uma versão anterior deste documento — estava errado), e o free do API-Football
são 100 requisições/dia. A tabela oficial da CBF não existe em formato aberto.
Sem coleta, não há dados reais de graça.

**O que isso custa.** Os termos de uso do Globo não autorizam coleta
automatizada. Para um bolão entre amigos numa máquina de casa o risco prático é
baixo, mas a decisão é do dono da instalação e a interface diz isso na cara.

**Consequência no código.** Fica atrás de `FootballDataProvider` como qualquer
outro provedor — nenhum código de domínio sabe que existe scraping. Falha
**alto e específica** quando o formato muda: meia rodada errada é pior do que
rodada nenhuma. Uma requisição por segundo, com User-Agent honesto.

**O que o payload real ensinou** (conferido em 31/07/2026, e o motivo de o
parser ter sido reescrito depois da primeira tentativa):

- Não existe campo de status. Quem informa é `jogo_ja_comecou` mais a presença
  de placar — e separar "em andamento" de "encerrado" importa, senão a rodada
  seria apurada no intervalo e os palpites vazariam antes da hora.
- `data_realizacao` é ISO **com hora**, em horário de Brasília e sem fuso.
- Pode vir `null`: 5 dos 380 jogos de 2026 não tinham data marcada. Esses são
  **pulados**, não inventados — sem horário não há como travar o palpite na
  hora certa.

---

## D11c — Rodada montada pelo organizador

**Decisão.** Um bolão é **de campeonato** (`kind=season`, rodadas vêm da tabela)
ou **de rodada montada** (`kind=custom`, o organizador escolhe os jogos).
`pools.season_id` virou opcional; `matchdays` + `matchday_fixtures` guardam as
rodadas montadas.

**Por quê.** O modelo anterior amarrava o bolão a uma temporada — inclusive na
apuração, que achava os bolões de um jogo por `pool.season_id ==
fixture.season_id`. Isso torna impossível o caso mais natural de um bolão entre
amigos: a rodada da semana com o melhor de cada campeonato.

**Duas tabelas com semânticas opostas, de propósito.** `pool_fixtures` guarda
**exceções** (um bolão de 380 jogos não gera linha nenhuma no comportamento
padrão); `matchday_fixtures` guarda a **lista completa** (a rodada é exatamente
o que foi escolhido). Confundir as duas produziria ou 380 linhas inúteis ou uma
rodada que "herda" jogos que ninguém marcou.

**Consequência no código.** `_pools_with_fixture` passou a somar os dois
caminhos — e é a linha que, se esquecida, faria o bolão personalizado nunca
apurar, sem erro nenhum no log. `prediction_scores` ganhou `matchday_id`, e só
um dos dois campos de rodada é preenchido.

**Duas regras que a tela impõe**, ambas por causa da trava no banco: jogo que já
começou não entra na rodada (seria palpite de resultado conhecido), e jogo em
andamento não sai (apagaria palpite de quem já respondeu).

**Dois bugs que só apareceram rodando o fluxo real**, ambos do mesmo tipo —
código que assumia `season_id`:

- O endpoint de palpite filtrava os jogos por `season_id` do bolão. Nulo no
  personalizado, então **nenhum** jogo casava: a rodada aparecia na tela e
  recusava todos os palpites com "jogo não é deste bolão".
- A agenda oferecia jogos já encerrados só porque a data ainda era futura —
  mesma classe do D8, relógio versus status. Agora usa `is_locked`.

---

## D12 — Calendário gerado, escudos desenhados

**Decisão.** A plataforma monta o Brasileirão sozinha: 20 clubes, turno e
returno completos, 38 rodadas, 380 jogos. Os escudos são SVG gerados a partir
das cores de cada clube.

**Por quê.** A tabela oficial da CBF não existe em formato aberto e nenhum
provedor gratuito entrega o calendário completo sem cadastro. Esperar isso
significaria não ter bolão. O que dá para garantir sem provedor é a
**estrutura**: cada clube enfrenta todos os outros duas vezes, uma em casa —
propriedade coberta por teste, porque um turno-returno errado só aparece na
rodada 20, quando alguém nota que dois times nunca se enfrentaram.

Escudos oficiais são marca registrada, e apontar para imagem hospedada por
terceiro quebraria o uso offline — que é a razão de o provedor manual ser o
padrão.

**Consequência.** A tela avisa que o calendário é gerado. Quando o oficial
sair, o organizador cola o CSV: a ingestão é idempotente por
`(provider_id, external_id)` e atualiza no lugar.

**A rodada 1 não cai sempre em abril.** Se abril já passou, começa no próximo
sábado — senão, criar o campeonato do ano corrente em agosto nasce com metade
das rodadas fechadas para palpite e o bolão não serve para nada. Encontrado
rodando o fluxo real.

---

## D13 — Seleção de jogos é tabela de exceção

**Decisão.** `pool_fixtures` guarda só as exceções: sem linha para o jogo, vale
o que a rodada disser; com linha, a linha manda.

**Por quê.** Um bolão do Brasileirão inteiro teria 380 linhas para se comportar
do jeito padrão. Como exceção, o caso comum custa zero linha e tirar um jogo
custa uma. E `set_round_inclusion` limpa as exceções da rodada: quem acabou de
dizer "a rodada 12 inteira entra" não espera uma exclusão esquecida sobreviver.

**Consequência.** `pools.fixture_is_included` é o **ponto único** da regra. A
apuração e o CRUD de palpite chamam a mesma função; se cada um refizesse a
lógica, um jogo excluído poderia aceitar palpite e não pontuar.

---

## D14 — Regulamento gerado, não escrito

**Decisão.** A tela "Como funciona" de cada bolão sai do endpoint `/rules`, que
lê a configuração vigente e o motor de pontuação.

**Por quê.** Regulamento escrito à mão diverge da configuração no dia em que
alguém mexe nos pontos — e aí a discussão vira "a página diz uma coisa e o
sistema faz outra", que é exatamente o que destrói a confiança num bolão.

---

## D15 — Migrations reais nos testes, não `create_all`

**Decisão.** O banco de teste é construído com `alembic upgrade head`, e o CI
roda `upgrade → downgrade base → upgrade` a cada push.

**Por quê.** `metadata.create_all` testa o modelo, não a migration. Numa base
que vai receber correção de placar e recomputação de ranking em produção, a
migration quebrada é o problema caro — e ela só aparece no dia do deploy se não
for exercitada no CI.
