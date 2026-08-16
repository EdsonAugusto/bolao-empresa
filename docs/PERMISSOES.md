# Quem pode o quê

Duas ideias que se completam, e vale não confundi-las.

**Nível** é hierarquia — uma escada curta e ordenada. A ordem tem consequência
prática: **só se mexe em quem está abaixo**. Sem isso, dois administradores
podem se rebaixar um ao outro e a instalação vira disputa.

**Permissão** é capacidade. Cada nível já vem com um conjunto, mas ele é
ajustável por grupo e por pessoa — porque a realidade não cabe em cinco caixas.
O caso que motivou tudo isto: o amigo que organiza o bolão da firma precisa
importar campeonato, mas não precisa (nem deve) mexer nas contas dos outros.

---

## A escada

| Nível | O que significa |
|---|---|
| **Desenvolvedor** | Quem constrói e mantém a plataforma. Acima de tudo, inclusive do dono. |
| **Dono** | Quem instalou esta plataforma. Manda em tudo que roda nela — mas não em quem a constrói. |
| **Administrador** | Administra a plataforma inteira. |
| **Moderador** | Cuida dos relatos e dos participantes que estão abaixo dele. |
| **Organizador** | Cria bolões, monta rodadas e traz campeonato. |
| **Jogador** | Participa e palpita. |

Três regras seguram a escada, e as três estão no serviço, não na tela:

1. **Ninguém mexe em quem não está estritamente abaixo.** Os dois topos são
   exceção quanto ao próprio nível — sem isso a posição nunca poderia ser
   transferida, e a instalação ficaria presa na primeira conta para sempre.
   O dono **não** alcança o desenvolvedor: alcançá-lo seria um caminho para
   subir ao nível acima do próprio, que é justamente o que a escada impede.
2. **Ninguém concede o que não tem.** Senão bastaria um moderador se dar
   `usuarios.gerenciar` para virar administrador.
3. **Ninguém mexe na própria conta.** É o caminho mais curto para se promover,
   e também para se trancar do lado de fora.

E uma que o banco não garante sozinho: **nenhum dos dois topos fica vazio.**
Sem ninguém naquele nível, ele vira inalcançável pela tela — a saída seria
`app.cli definir-nivel` no servidor.

> Não confundir com o papel **dentro de um bolão** (dono / administrador /
> jogador daquele bolão). São eixos independentes de propósito: alguém pode
> organizar o próprio bolão sem administrar nada da plataforma, e o contrário
> também.

---

## As permissões

| Chave | O que abre |
|---|---|
| `usuarios.ver` | Abrir o painel de pessoas |
| `usuarios.gerenciar` | Mudar nível e permissões de quem está abaixo; desativar contas |
| `grupos.gerenciar` | Criar e editar grupos de permissão |
| `campeonatos.importar` | Trazer tabela, jogos e escudos |
| `campeonatos.placar` | Lançar ou corrigir resultado, o que reapura os bolões |
| `boloes.criar` | Abrir bolão e convidar gente |
| `rodadas.montar` | Escolher os jogos de uma rodada personalizada |
| `relatos.triar` | Ver todos os relatos de bug, responder e mudar o estado |
| `plataforma.configurar` | Ver o endereço da instalação e o diagnóstico |

O que cada nível já traz, sem ninguém marcar nada:

| | Dev | Dono | Admin | Moderador | Organizador | Jogador |
|---|:-:|:-:|:-:|:-:|:-:|:-:|
| `usuarios.ver` | ✓ | ✓ | ✓ | ✓ | | |
| `usuarios.gerenciar` | ✓ | ✓ | ✓ | ✓ | | |
| `grupos.gerenciar` | ✓ | ✓ | ✓ | | | |
| `campeonatos.importar` | ✓ | ✓ | ✓ | | ✓ | |
| `campeonatos.placar` | ✓ | ✓ | ✓ | ✓ | ✓ | |
| `boloes.criar` | ✓ | ✓ | ✓ | ✓ | ✓ | ✓ |
| `rodadas.montar` | ✓ | ✓ | ✓ | ✓ | ✓ | ✓ |
| `relatos.triar` | ✓ | ✓ | ✓ | ✓ | | |
| `plataforma.configurar` | ✓ | ✓ | ✓ | | | |

**Jogador cria bolão de propósito.** Numa plataforma entre amigos, quem chega e
quer organizar o bolão da firma não deveria ter que pedir permissão. Se a sua
instalação preferir o contrário, é uma caixa a desmarcar.

---

## Como uma permissão é decidida

```
    o que o NÍVEL dá
  + o que os GRUPOS dão
  + o que foi concedido À PESSOA
  − o que foi revogado DA PESSOA
  ─────────────────────────────────
  = o que vale
```

A revogação vence tudo, e é guardada explicitamente — não como ausência. É o
que permite mudar o padrão de um nível sem ressuscitar acesso que alguém tirou
de propósito.

Os dois topos são exceção: têm tudo, mesmo com revogação registrada. Revogar
`usuarios.gerenciar` de quem administra trancaria a instalação inteira para fora
do próprio painel, sem caminho de volta pela tela.

### Três estados, não dois

No painel, cada permissão tem **dar**, **tirar** e **padrão**. A terceira falta
em quase todo painel de permissão, e sem ela não há como desfazer um ajuste sem
adivinhar o que o nível daria. O painel também mostra **de onde** cada
permissão vem — nível, grupo ou ajuste —, porque o que acontece ao desmarcar é
diferente em cada caso.

---

## Grupos

Um grupo é um pacote de permissões com nome, para não marcar caixa uma a uma.
A instalação nasce com três:

| Grupo | Para quem |
|---|---|
| **Curadoria de campeonatos** | Traz tabela e escudo, corrige placar. Não mexe em conta. |
| **Suporte** | Lê e responde os relatos de bug. |
| **Organização de bolões** | Cria bolão e monta rodada. |

Os três são "da plataforma": dá para editar as permissões, mas não apagar —
apagar o grupo que dá acesso ao painel não teria volta pela tela.

Entregar um grupo é entregar tudo que ele carrega, então **você só pode pôr num
grupo, e só pode atribuir um grupo com, o que você mesmo pode fazer**. Sem essa
regra bastava criar um grupo com tudo e entrar nele.

---

## Onde isso vive no código

```
app/core/permissoes.py      vocabulário e regras de comparação. PURO.
app/services/permissoes.py  lê e grava; é onde as invariantes moram
app/api/deps.py             `Requer(Permissao.X)` — a guarda dos endpoints
app/api/usuarios.py         o painel
web/composables/usePermissoes.ts   o que a sessão pode; ESCONDE botão, não autoriza
web/middleware/admin.ts     mapa de tela → permissão exigida
web/pages/pessoas/          o painel e os grupos
```

Duas coisas que valem repetir:

**A tela nunca decide.** O painel recebe `pode_gerenciar` e `niveis_possiveis`
prontos do servidor. Se a tela comparasse níveis por conta própria, a regra de
hierarquia moraria em dois lugares — e no dia em que divergissem, ela ofereceria
um botão que a API recusa.

**`is_superuser` continua existindo**, derivado do nível e escrito só por
`services/permissoes`. A coluna é usada em consulta e por código anterior à
hierarquia; duas fontes de verdade divergem no primeiro descuido.

---

## Operação

Promover pela linha de comando, quando ninguém consegue entrar no painel:

```bash
docker compose exec api python -m app.cli criar-admin --email pessoa@exemplo.com
```

Ele cria a conta como **dona** se ela não existir, ou promove a existente. É
idempotente — é o mesmo comando que o instalador usa.

Mudar o nível de uma conta que já existe, inclusive para `dev`:

```bash
docker compose exec api python -m app.cli definir-nivel --email pessoa@exemplo.com --nivel dev
```

**`dev` só se atribui por aqui.** Ninguém dentro da plataforma alcança esse
nível para concedê-lo — nem o dono, de propósito. Quem roda o comando já tem
acesso ao servidor e portanto ao banco, então checar hierarquia ali seria
teatro.

Ver quem é o quê:

```bash
docker compose exec api python -m app.cli list-users
```
