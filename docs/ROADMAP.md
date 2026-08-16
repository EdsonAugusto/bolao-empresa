# Roadmap

Perfil de implantação: **bolão entre amigos, rede local, custo zero.** Isso
mudou o escopo de três fases do plano original — ver "Adaptações" no fim.

| Fase | Entrega | Estado |
|---|---|---|
| 0 | Docker Compose, migrations, CI, seed | ✅ |
| 1 | Contas, sessão, perfil, LGPD | ✅ |
| 2 | Catálogo, provedores, ingestão, CSV | ✅ |
| 3 | Motor de pontuação isolado + testes | ✅ 99% de cobertura |
| 4 | Bolão, convite, palpites, trava e blindagem | ✅ |
| 5 | Apuração, ranking, desempate, histórico | ✅ |
| 6 | Notificações | ✅ in-app + Telegram |
| 7 | Multiplicador, mata-mata, palpites de temporada | ✅ |
| 8 | Interface completa + PWA | ✅ (SEO não se aplica) |
| 9 | Planos e cobrança | ❌ removida por decisão |
| 10 | Administração do bolão | ✅ |

---

## O que está pronto

### Contas e sessão
- Cadastro com e-mail e senha (Argon2id). O e-mail é só identificador de login:
  a plataforma não envia e-mail nenhum, então endereços de rede local
  (`pai@casa.local`) são aceitos.
- JWT de 30 minutos + refresh rotativo. Reuso de refresh já rotacionado derruba
  a família inteira de sessões — assinatura de token roubado.
- A primeira conta criada vira administradora da instalação.
- LGPD: consentimento com data, e exclusão de conta que **anonimiza** em vez de
  apagar, para não quebrar o histórico dos bolões dos outros.

### Catálogo e ingestão
- `FootballDataProvider` com três implementações: `ManualProvider` (padrão,
  offline), `FootballDataOrgProvider` (tier gratuito), `ApiFootballProvider`.
- Importação por CSV, idempotente por `(provider_id, external_id)`.
- Jobs arq: `sync_fixtures`, `sync_live` (só acorda com jogo em andamento),
  `settle_fixtures`, `prediction_reminders`, `dispatch_notifications`,
  `close_predictions`, `reconcile`. Cada execução vai para `sync_runs`.
- Máquina de estados do jogo com tradução do status do provedor — o status cru
  nunca entra no banco.

### Motor de pontuação
Módulo puro em `app/scoring/`, sem I/O e sem relógio. Três modos, avaliação por
pontos decrescentes, desempate derivado da configuração. 99% de cobertura,
incluindo testes de propriedade com hypothesis.

### Bolões e palpites
- Convite por código de 8 letras sem caracteres ambíguos (nada de 0/O, 1/I/L —
  alguém vai ditar isso no grupo).
- **Trava de palpite garantida por trigger no Postgres**, não pela aplicação.
  Fecha por horário *e* por status do jogo.
- **Blindagem** aplicada em um único ponto (`visible_predictions`), com teste de
  vazamento para cada endpoint que toca palpite.
- `scoring_configs` versionado e congelado no primeiro palpite pontuado.

### Apuração
- Idempotente e recomputável: reprocessar produz exatamente o mesmo resultado.
- Correção de placar (VAR, W.O., tribunal) reapura e registra em `audit_log`
  quem mudou de posição.
- Jogo cancelado/abandonado sai do denominador.
- Snapshot do ranking por rodada, para o histórico de evolução.

### Interface
Nuxt 3 com SSR — a primeira pintura já traz ranking e jogos, sem "carregando".
Telas: bolões, criação, entrada por código, palpites da rodada, ranking,
detalhamento da pontuação, configuração, campeonatos, avisos, perfil e acesso
pela rede. PWA instalável.

---

## Adaptações ao perfil "amigos, LAN, custo zero"

**Fase 8 — SEO virou interface.** Não existe landing page indexada: a
plataforma não é pública. O SSR continua, porque melhora o primeiro
carregamento no celular.

**Fase 9 — cobrança removida.** Sem planos, sem limite de participantes por
plano, sem anúncio, sem gateway. O PWA foi mantido.

**Fase 10 — B2B virou administração.** Sem whitelabel comercial; ficou o que
serve a um grupo de amigos: gerir participantes, multiplicadores, pontuação e
reapuração.

---

### Palpites de bônus (Fase 7)
- **Multiplicador de rodada**: o organizador define, e ele entra na apuração.
- **Mata-mata**: acertar quem avança vale pontos independentes do placar.
  Trava no apito do jogo e é apurado junto com ele. É o único ponto do produto
  em que os pênaltis decidem alguma coisa — o classificado pode ter perdido nos
  90 minutos.
- **Temporada**: campeão, G-4 e rebaixados. Travam no primeiro jogo do
  campeonato e são apurados contra o desfecho declarado pelo organizador.
  G-4 e rebaixados pontuam **por acerto**, não tudo ou nada.
- Bônus soma ao total mas **não entra no desempate** — este continua sendo
  decidido pelos palpites de placar.

## O que falta

**Ranking ao vivo por SSE.** O nginx já está configurado para isso
(`/api/stream/`, buffering desligado); o endpoint não existe. Hoje o ranking
atualiza ao recarregar.

**Segundo provedor em produção.** `FootballDataOrgProvider` está implementado
mas não foi exercitado contra a API real — precisa de uma chave para isso.
