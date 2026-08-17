# Plataforma de Bolões

## Contexto

Plataforma de bolões esportivos. Usuários criam bolões de campeonatos de futebol,
convidam amigos, palpitam placares e competem em ranking automático.
Entretenimento — **NÃO há aposta com dinheiro real na plataforma.**

## Perfil de implantação

**Bolão entre amigos, em rede local, custo zero.** Isso não é um detalhe de
deploy — define o que existe e o que não existe no produto:

- **Não existe cobrança.** Sem planos, sem limite de participantes, sem
  anúncio, sem gateway de pagamento. Nada de Asaas/Pix/Stripe.
- **Não existe dinheiro real.** Sem quota, prêmio, carteira, saldo ou saque.
- **Acesso pela LAN**, por IP do host. Sem domínio público, sem TLS, sem SEO,
  sem landing page indexada, sem publicação em loja de aplicativo.
- **Nenhum serviço pago obrigatório.** Todo provedor externo é opcional e tem
  um caminho gratuito equivalente.

## Decisões travadas (não reabrir sem me perguntar)

1. **Sem dinheiro real e sem cobrança.** Se aparecer pedido de split de prêmio,
   pare e escale — é Lei 14.790/2023 e precisa de outorga federal.
2. **Dados dos jogos sem custo.** `ManualProvider` (o organizador cadastra
   jogos e placares, ou importa CSV) é o padrão e funciona offline, sem
   depender de ninguém. `FootballDataOrgProvider` usa o tier gratuito.
   `ApiFootballProvider` existe para quem tiver chave, mas nunca é exigido.
   Tudo atrás de `FootballDataProvider`.
3. **Notificação sem custo**, atrás de `NotificationChannel`: `InAppChannel`
   (central de avisos no próprio produto) é o padrão; `TelegramChannel` é
   opcional e usa long polling, então não precisa de webhook público nem de
   IP fixo. WhatsApp fica de fora: a API oficial cobra por conversa e a
   não-oficial viola os termos de uso.

## Stack

Nuxt 3 (frontend SSR) · FastAPI + SQLAlchemy 2 + Alembic (backend) ·
PostgreSQL 16 · Redis + arq (jobs) · nginx · Docker Compose · pytest · vitest

## Regras invioláveis

1. Palpite é **BLINDADO**. Nenhum endpoint pode retornar palpite de terceiro
   antes de `fixture.kickoff_at`. Isso é validado no serializer, não na UI.
   Todo endpoint que toca predictions tem teste de vazamento — por endpoint,
   incluindo listagens, exports e webhooks.
2. Palpite não pode ser criado nem editado após o kickoff. Garantia no **BANCO**
   (trigger/constraint), não só na aplicação. Worker atrasado não abre brecha.
3. O motor de pontuação (`app/scoring/`) é **PURO**: sem I/O, sem
   `datetime.now()`, sem acesso a banco. Recebe dataclasses, devolve dataclasses.
4. Toda escrita vinda de provedor externo é **upsert idempotente** por
   `(provider_id, external_id)`. Rodar o sync duas vezes não duplica nada.
5. Apuração é **recomputável**: reprocessar um fixture produz exatamente o mesmo
   resultado e corrige o que estava errado.
6. Provedor de dados fica atrás da interface `FootballDataProvider`.
   Nenhum código de domínio importa o cliente HTTP do provedor diretamente.
7. Nada de copiar textos, imagens, logo ou layout de nenhuma plataforma
   existente. Design e copy são originais.

## Convenções

### Python
- Type hints obrigatórios. `ruff` no projeto todo; `mypy --strict` em
  `app/scoring` e `app/services`.
- Migrations sempre via Alembic, nunca DDL manual.
- Pontos e dinheiro: **inteiros**, nunca float.
- Timestamps: `TIMESTAMPTZ` em UTC no banco. `America/Sao_Paulo` só na borda de
  apresentação. Nada de `datetime.utcnow()` (naive) — use
  `datetime.now(timezone.utc)`.
- Toda regra de negócio tem teste **antes** do endpoint.

### Banco
- PK interna: `BIGINT GENERATED ALWAYS AS IDENTITY`. Barata e rápida.
- Identificador público: `slug` ou código curto opaco (ex.: `pools.slug`,
  `pools.invite_code`). **Rotas públicas usam o identificador público**, não o
  bigint — id sequencial em URL vaza volume de negócio.
- Todo registro de provedor externo: índice único em `(provider_id, external_id)`.
- Enums de domínio vivem como enum nativo do Postgres, criados por migration.

### Camadas
```
app/api/       → HTTP. Fino. Sem regra de negócio.
app/schemas/   → Pydantic. Entrada/saída. É aqui que a blindagem é aplicada.
app/services/  → Regra de negócio. Orquestra models + scoring. Testado.
app/scoring/   → Motor puro. Zero I/O. Cobertura ≥ 95%.
app/models/    → SQLAlchemy. Sem lógica além de constraints e relacionamentos.
app/providers/ → Integrações externas atrás de interface abstrata.
app/jobs/      → Tarefas arq. Idempotentes. Registram em sync_runs.
app/core/      → Config, db, redis, logging, segurança.
```
Regra de dependência: `api → schemas → services → {scoring, models, providers}`.
Nunca o contrário. `scoring` não importa nada do projeto além de si mesmo.

## Como rodar

Linux/macOS/WSL:
```
make up && make migrate && make seed
make test
```

Windows (PowerShell) — `make` não existe, use o script equivalente:
```
.\task.ps1 up ; .\task.ps1 migrate ; .\task.ps1 seed
.\task.ps1 test
```

Serviços após `up`: web em http://localhost:8080, API em
http://localhost:8080/api, docs em http://localhost:8080/api/docs.

## Armadilhas já pagas (não reintroduza)

Cada uma custou um diagnóstico durante a construção. Detalhe em
`docs/DECISOES.md`.

1. **A trava de palpite olha o status do jogo, não só o relógio.** Um jogo pode
   estar `FINISHED` com o `kickoff_at` no futuro (placar lançado antes da hora,
   ou data errada no CSV). Trigger e `predictions.is_locked` têm que concordar.
2. **`proxy_pass` do nginx usa variável + `resolver 127.0.0.11`.** Com destino
   literal ele resolve o IP uma vez e devolve 502 depois de todo
   `--force-recreate`.
3. **O plugin de sessão é universal, não `.client`.** Se o servidor renderizar
   como visitante e o cliente como logado, a hidratação falha e o Vue perde os
   listeners — a tela parece certa e os botões não fazem nada.
4. **`cors_origins` precisa de `NoDecode`.** O pydantic-settings faz JSON-decode
   de campo de lista antes de qualquer validator, e derruba a API na subida.
5. **Nada de `EmailStr`.** Recusa `.local`, que é o domínio de uma rede
   doméstica. A plataforma não envia e-mail.
6. **Regra `TC` do ruff fica desabilitada.** Mover import para `TYPE_CHECKING`
   quebra `Mapped[datetime]` do SQLAlchemy e a injeção do FastAPI em runtime.
7. **Não use `Get-Content`/`Set-Content` do PowerShell para editar arquivo com
   acento.** O Windows PowerShell lê UTF-8 como ANSI e corrompe o arquivo.
8. **`fixtures.external_id` é `varchar(64)`.** Todo provedor monta o
   identificador com `build_external_id()`, que corta e fecha com resumo. Sem
   isso, clube de nome comprido derruba a importação no meio do INSERT — com
   metade da temporada já gravada.
9. **"Temporada em andamento" não sai do ano.** Sai de ter jogo pela frente
   (`_jogos_por_vir`). A Premier 2025-26 tem ano 2026 e acabou em maio.
10. **Liga de virada é gravada pelo ano em que termina.** A 2026-27 entra como
    2027. Gravar como 2026 faz ela cair em cima da 2025-26, que já está no banco
    com o mesmo nome de competição. O rótulo da tela (`2026-27`) é derivado das
    datas dos jogos, não do ano.
11. **Casamento de escudo recusa na dúvida.** Buscar "Inter" devolve um clube da
    quarta divisão espanhola; "Man Utd" devolve um time da copa da Finlândia. Só
    entra resultado da liga esperada. Escudo trocado é pior do que ausente: a
    pessoa vê o erro na hora e ele sobrevive a toda recoleta, porque o campo já
    está preenchido.
12. **Job longo commita aos poucos e trabalha em lote.** `fill_crests` leva 6s
    por clube; cem clubes passam do `job_timeout` de 600s e o lote inteiro se
    perde. Ele grava a cada dez e se reagenda — mas **só enquanto a passada
    render**, senão uma lista de clubes que nenhuma fonte conhece reagenda para
    sempre.
13. **Endereço de `useApiData` não pode depender de outro `useApiData`.** Os
    carregamentos disparam juntos: quando a URL pergunta `bolao.value?.kind`, a
    resposta é `undefined` e a chamada vai para o endpoint errado — que devolve
    lista vazia, não erro, então ninguém vê. Ou o endpoint responde pelos dois
    casos (preferido), ou o `await` vem antes e o `watch` recarrega.
14. **`alembic revision --autogenerate` nesta base propõe lixo.** Ele "detecta"
    remoção dos `server_default` de `pools.kind`, `matchdays.multiplier` e
    `seasons.outcome` — diferença de representação, não mudança real. Aplicar
    tira default de coluna NOT NULL. Leia e apague o que não é seu.
15. **Não existe provedor global.** Cada competição é coletada pela **sua**
    fonte, reconstruída de `competitions.provider_config`. O antigo
    `settings.football_provider` vinha `manual`, e por isso nenhum placar
    entrava sozinho: os jobs saíam na primeira linha todo dia.
16. **Coleta repetida não pode desfazer coleta boa.** Durante o jogo o ingest
    roda de dois em dois minutos. Status final não regride (`_status_sem_regressao`),
    placar `None` não apaga placar, e coleta parcial manda `start_date=None`
    para não encolher a temporada ao intervalo da rodada que trouxe.
17. **`POSTPONED` reabre a trava de palpite.** Nunca use adiado para "resolver"
    jogo sem placar: um jogo de duas semanas atrás voltaria a aceitar palpite.
    Sem placar ele fica `LIVE` (travado) e vira pendência para o organizador.
18. **`asyncpg` não infere tipo dentro de `jsonb_build_object` com parâmetro.**
    Em migration, monte o JSON em Python e passe com `CAST(:x AS jsonb)`.
19. **`seasons.is_current` não decide o que o worker coleta.** A coluna é
    escrita pelo provedor e o do GE a deriva do ano civil: em 1º de janeiro a
    recoleta gravaria `False` numa temporada que ainda tem rodada, e como o
    único escritor da coluna é a própria coleta, seria porta de mão única.
    `temporadas_ativas` usa **ter jogo por perto**, igual a `_jogos_por_vir`.
20. **Ter placar não é ter resultado final.** A coleta ao vivo grava o placar
    durante a partida. Só promova `LIVE → FINISHED` quando a fonte confirmou o
    placar **depois** do fim provável do jogo (`synced_at`), senão um 1x0 de
    minuto 20 vira resultado e apura a rodada errada.
21. **`chave()` funde clubes diferentes.** `Vitória` (BA) e `Vitória SC`
    (Guimarães) colidem porque `sc` é ruído. Fusão de identidade exige nome
    idêntico, ou nome equivalente **e** país compatível.
22. **Recoleta não apaga o que ela não sabe.** Escudo, sigla e país vêm `None`
    das fontes que não os têm; gravar isso apagaria de madrugada tudo que a
    busca de escudos preencheu.
23. **Pendência de coleta é só o que a recoleta resolve.** `!= FINISHED` inclui
    CANCELLED, POSTPONED e o LIVE-sem-placar que fica travado de propósito —
    e cada um faria a temporada inteira ser rebaixada de meia em meia hora,
    para sempre.
24. **Busca de upsert usa a chave da restrição única, e só ela.** O `existing`
    de `upsert_fixtures` filtrava também por temporada; bastou a competição ser
    resolvida para outra linha para o jogo não ser encontrado, virar `INSERT` e
    estourar `uq_fixtures_provider_external`. Toda coleta ao vivo do Brasileirão
    falhou assim por oito dias, **em silêncio**.
25. **O coletor tem que se apresentar com o `external_id` gravado.** O do GE
    devolvia o UUID da tabela, mas o Brasileirão está gravado com o slug: a cada
    coleta ele criava uma competição paralela. Quem monta o provedor passa
    `competition_external_id`.
26. **Erro de banco num campeonato não pode derrubar os outros.** Os laços de
    coleta separam falha de fonte (sessão limpa, segue) de falha de banco
    (`rollback`, segue) e gravam competição a competição. Sem isso, um erro
    tardio descarta o placar que já tinha entrado.
27. **`LIVE` não volta para `SCHEDULED` com o horário no passado.** O CSV das
    ligas europeias só conhece "tem placar" e "não tem", e afirma `SCHEDULED`
    sobre jogo encerrado. O jogo ficava pulando entre os dois estados a cada
    passada. Só volta a agendado se o `kickoff_at` novo estiver no futuro — aí é
    remarcação de verdade.
28. **`provider_config` é mesclado, nunca substituído.** Cada subsistema grava
    as suas chaves ali. A coleta do GE trocava o dicionário inteiro e apagava o
    `espn_league` que a migration tinha acabado de gravar — o placar ao vivo
    simplesmente não acontecia, sem erro nenhum.
29. **A ESPN recusa `User-Agent` de navegador** (403 do Akamai) e responde 200
    sem cabeçalho nenhum. É o oposto de todos os outros coletores.
30. **A ESPN agrupa por data local da liga, não UTC.** Botafogo x Fluminense às
    00:00 UTC de 09/08 está na gaveta de 08/08. Peça sempre a janela de três
    dias — o endpoint aceita intervalo e custa a mesma requisição.
31. **Dependência nova exige `docker compose build`.** `pip install` dentro do
    container morre na primeira recriação — e a API sobe quebrada, não
    degradada. Foi assim que o `python-multipart` derrubou tudo por dois
    minutos.
32. **Upload: o tipo vem da assinatura, o nome vem de nós.** Confiar no
    `Content-Type` ou no nome do arquivo é como upload vira XSS armazenado e
    travessia de diretório. SVG é recusado por ser XML executável.
33. **`isalnum()` é Unicode; cabeçalho HTTP é latin-1.** Sanear nome de
    arquivo só por `isalnum()` deixa passar `Скриншот.png`, que estoura ao
    serializar o `Content-Disposition` — erro 500 no download, não recusa no
    upload. O nome ASCII é o `filename`; o original volta em `filename*`.
34. **Anexo não tem URL pública.** Herda a permissão do relato, responde com
    `nosniff` e CSP `sandbox`, e devolve 404 (não 403) para quem não pode ver —
    senão o identificador vira oráculo do que existe.
35. **Placar de fonte externa casa pelo confronto, não pelo time.** ``PSV`` e
    ``PSV Eindhoven`` precisam casar, mas afrouxar time a time faria ``Vitória``
    casar com dois clubes. Mandante + visitante + liga + horário próximo: as
    quatro juntas. Medido em 13 jogos reais: 13 acertos, zero ambiguidade.

## Fases

Ver `docs/ROADMAP.md`. **Não avance de fase sem os testes da fase anterior
passando.**

## Implantação em servidor público

O perfil de rede local continua sendo o padrão; o de servidor é um segundo modo,
não uma substituição. Ver `docs/DEPLOY.md`. Armadilhas específicas dele:

36. **`docker compose -f a.yml -f b.yml` MESCLA `ports` e `volumes`.** Não
    substitui. Por isso `docker-compose.prod.yml` é um arquivo inteiro: um
    complemento não conseguiria *tirar* a porta do Postgres publicada no host
    nem o bind-mount do código.
37. **Caddy: `dns.providers.cloudflare` e `http.ip_sources.cloudflare` são
    plugins DIFERENTES.** O primeiro é o desafio DNS-01, o segundo é o
    `trusted_proxies`. Faltando o segundo, o Caddy nem sobe — falha ao adaptar
    o Caddyfile e entra em laço de reinício, servindo nada nas portas 80 e 443.
    O Dockerfile confere os dois por nome.
38. **`X-Forwarded-For` é uma cadeia à qual cada proxy ANEXA.** O primeiro
    elemento é o que o cliente alega. Ler o primeiro fazia
    `curl -H 'X-Forwarded-For: 1.2.3.4'` criar um balde novo por requisição e o
    limite de taxa virava enfeite. Vale `X-Real-IP`, que a borda escreve.
39. **Volume nomeado herda o dono do ponto de montagem só se ele já existir na
    imagem.** Senão nasce root, e a imagem de produção roda como `appuser` —
    gravar anexo falhava com "permission denied" só em produção.
40. **A primeira conta vira administradora.** Publicar o domínio antes de ela
    existir abre uma janela em que qualquer pessoa da internet se cadastra e
    sai administrando. O instalador sobe a aplicação, cria o administrador, e
    só então liga a borda.
41. **`set -e` mais `ls` sem casar nada derruba o script.** O laço de retenção
    do backup fazia isso na instalação nova, e como `atualizar.sh` recusa
    atualizar sem backup, travava o deploy da primeira correção.
42. **Service worker em SSR autenticado só pode cachear `/_nuxt/`.** HTML e
    `/api/` de fora, sempre: a mesma URL devolve o palpite de quem está logado.
    Ícone e manifest ficam em revalidação — cache-first prenderia o ícone velho
    na tela de início para sempre, porque eles não têm hash no nome.
43. **`clients.claim()` dispara `controllerchange` na PRIMEIRA visita.**
    Recarregar ali apaga o formulário que a pessoa está preenchendo. Só recarrega
    quando já havia controlador, ou seja, quando é troca de versão de verdade.
44. **O Nuxt deduplica `meta` pelo `name`.** Duas `theme-color` com `media`
    diferente viram uma só; é preciso `key` distinto em cada.
45. **`useCookie` devolve um ref NOVO a cada chamada.** Dois `useTokens()` não
    se falam, e no SSR a leitura vem do cabeçalho da requisição. Quem rotaciona
    o token tem que DEVOLVER o valor novo — reler o cookie não funciona.
46. **`rollback()` expira todo objeto da sessão**, mesmo com
    `expire_on_commit=False`. Ler `objeto.id` depois dispara um SELECT síncrono
    que numa sessão assíncrona vira `MissingGreenlet` — dentro do `except`, onde
    ninguém o pega. Materialize os ids antes do laço.

## Hierarquia e permissões

Ver `docs/PERMISSOES.md`. O que não pode ser reintroduzido:

47. **`users.nivel` e `users.is_superuser` andam SEMPRE juntos.** A coluna
    booleana é derivada do nível e usada em consulta; quem grava uma sem a
    outra cria uma conta que passa em `is_superuser` e não tem permissão
    nenhuma — ou o contrário. Só `services/permissoes` e o cadastro da primeira
    conta escrevem as duas.
48. **A tela não decide hierarquia.** O painel recebe `pode_gerenciar` e
    `niveis_possiveis` prontos da API. Recalcular no cliente colocaria a regra
    em dois lugares, e o dia em que divergirem a tela oferece um botão que a
    API recusa.
49. **Revogação é gravada, não é ausência.** `user_permissions.granted=False`
    existe para que mudar o padrão de um nível não ressuscite acesso que alguém
    tirou de propósito.
50. **`manda_em` é estritamente maior, com os dois topos como exceção.** `dev`
    e `dono` mandam no próprio nível — sem isso a posição nunca seria
    transferida e a instalação ficaria presa na primeira conta. Mas o **dono
    não alcança o dev**: alcançá-lo seria caminho para subir ao nível acima do
    próprio. `dev` só se atribui por `app.cli definir-nivel`, no servidor.
51. **Service worker sem versão na URL se apaga sozinho.** Uma instalação antiga
    servindo `/_nuxt/` do cache quebra o modo de desenvolvimento — o navegador
    recebe CSS onde esperava módulo e a página não hidrata. O código que
    desregistraria está justamente na página que não carrega, então a saída tem
    que estar dentro do próprio `sw.js`.
52. **`watch(..., {immediate: true})` não sincroniza estado de renderização em
    SSR.** No servidor ele roda uma vez, no setup, quando `useAsyncData` ainda
    não resolveu — e watchers não são reagendados lá. O servidor renderiza com
    o valor vazio e o cliente com o valor do payload: hidratação divergente.
    Derive com `computed` em vez de copiar num `watch`.
53. **Cor no contêiner do layout desce por herança até dentro do card.** A tela
    de entrada põe texto claro sobre a cena escura; pôr `color` no elemento de
    fora deixou o título e os rótulos do formulário cinza-claro sobre branco,
    quase invisíveis. Cor de texto solto vai no elemento do texto solto.
54. **Posição aleatória em elemento renderizado no servidor quebra a
    hidratação.** `Math.random()` dá números diferentes nos dois lados. Os
    flashes da cena usam um gerador semeado pelo índice, que produz o mesmo
    valor no servidor e no navegador.
55. **Sucesso de `docker compose up -d` não significa que o serviço serve.** Ele
    devolve 0 quando o container INICIA. O worker subia, morria e reiniciava em
    laço enquanto o instalador dizia "Pronto." — e o sintoma só aparecia dias
    depois, como jogos terminados sem ninguém pontuar. Quem sobe confere depois,
    e duas vezes: container em laço aparece como `running` no instante certo.
56. **`*healthy*` casa com `unhealthy`.** O glob transformava o único portão de
    saúde do instalador em carimbo: ele declarava "API respondendo" no exato
    momento em que o Docker acabara de marcar a API como doente. Comparação de
    estado é exata (`== "api healthy"`), nunca por substring.
57. **`>/dev/null 2>&1` num comando sem `|| erro` apaga a única explicação que
    existia.** Três bugs distintos vieram daí. A regra é: ou a saída vai para um
    arquivo que a mensagem de erro cita, ou fica na tela. Nunca some.
58. **`curl -f` não falha em 3xx.** Sem `-L` e sem exigir 200, um 301 sai com
    código 0 — e a Cloudflare em modo `Flexible` devolve exatamente isso, num
    laço infinito. O instalador declarava "certificado válido" para um site que
    não abria para ninguém. Verificação de disponibilidade compara o código.
59. **Backup só existe depois de conferido.** Dump escrito direto no nome final
    deixa, ao morrer no meio, um `.gz` íntegro e plausível — e o `--clean` da
    restauração derruba o banco vivo antes de descobrir. Grava-se em
    `.parcial`, confere-se `gzip -t` e a marca `PostgreSQL database dump
    complete`, e só então `mv`.
60. **Restaurar sem `--single-transaction` destrói o que ainda estava bom.**
    `ON_ERROR_STOP=1` para no primeiro erro, mas parar não é desfazer, e o dump
    começa por dezenas de `DROP TABLE`.
61. **`name:` igual nos dois compose faz o de desenvolvimento reescrever a
    produção.** Um `docker compose up -d` sem `-f` no servidor recriava os
    containers com bind-mount e publicava Postgres e Redis no host — e como o
    caddy não existe no arquivo de dev, ele sobrevivia servindo o site: nada
    parecia quebrado. Projetos separados, `bolao` e `bolao-dev`.
62. **`pg_isready` sem `-h` fica saudável durante o `initdb`.** O servidor
    temporário do initdb escuta só no socket unix; sem `-h` o teste fala com
    ele e aprova. O `migrate` então disca TCP e leva "connection refused". O
    healthcheck exercita o mesmo caminho que a aplicação usa.
63. **Limpeza de colagem não se aplica a senha.** Aspas e espaço são caracteres
    legítimos. Aparar as pontas gravava o hash de uma senha que a pessoa nunca
    digitou — e a confirmação passava pelo mesmo filtro, então as duas batiam e
    nada acusava. Token tem alfabeto conhecido; senha não.
64. **Validar que o token existe não é validar que ele serve.**
    `/user/tokens/verify` responde `success:true` para qualquer token ativo,
    seja qual for o escopo. O ✓ verde daí contradizia a dica certa do passo do
    certificado. Quem valida credencial repete a chamada que o consumidor real
    vai fazer — aqui, `GET /zones?name=<zona>`, a mesma do módulo DNS do Caddy.
65. **Reescrever o `.env` do zero apaga o que o operador ajustou.** O próprio
    projeto ensina a editar `REGISTRATION_MODE` ali; a reinstalação o devolvia
    para `convite` sem aviso, e a descoberta vinha dias depois como "o pessoal
    não consegue mais se cadastrar". O gerador preserva as chaves de operação e
    remonta no fim as que não conhece.
