<script setup lang="ts">
definePageMeta({ middleware: ['auth', 'admin'] })

/**
 * Painel de pessoas: quem tem conta, em que nível, com o que pode fazer.
 *
 * Duas decisões que valem explicar:
 *
 * 1. **Quem pode o quê vem do servidor**, em `pode_gerenciar` e
 *    `niveis_possiveis`. A tela não compara níveis por conta própria: a regra
 *    de hierarquia moraria em dois lugares, e no dia em que divergissem a tela
 *    ofereceria um botão que a API recusa.
 * 2. **A origem de cada permissão aparece.** Uma caixa marcada pode vir do
 *    nível, de um grupo ou de um ajuste — e o que acontece ao desmarcar é
 *    diferente em cada caso. Sem isso o painel vira adivinhação.
 */
interface Conta {
  id: number
  email: string
  display_name: string
  nivel: string
  nivel_rotulo: string
  is_active: boolean
  permissoes: string[]
  grupos: string[]
  concedidas: string[]
  revogadas: string[]
  pode_gerenciar: boolean
  niveis_possiveis: string[]
  created_at: string
  last_login_at: string | null
}

interface Vocabulario {
  niveis: { chave: string, rotulo: string, ajuda: string, peso: number, permissoes: string[] }[]
  permissoes: { chave: string, rotulo: string, ajuda: string, area: string }[]
  meu_nivel: string
  minhas_permissoes: string[]
}

interface Grupo {
  id: number
  slug: string
  name: string
  description: string
  permissions: string[]
  is_system: boolean
  membros: number
}

const busca = ref('')
const erro = ref('')
const salvando = ref(0)
const aberta = ref<number | null>(null)

const { data: vocabulario } = await useApiData<Vocabulario>(
  'pessoas-vocabulario',
  () => '/v1/usuarios/vocabulario',
)
const { data: contas, refresh } = await useApiData<Conta[]>(
  'pessoas-contas',
  () => '/v1/usuarios',
)
const { data: grupos, refresh: recarregarGrupos } = await useApiData<Grupo[]>(
  'pessoas-grupos',
  () => '/v1/usuarios/grupos/todos',
)

const { kickoff } = useFormat()

/** Quem está olhando, para a tela saber qual linha é a própria. */
const { user: eu } = useAuth()

const visiveis = computed(() => {
  const alvo = busca.value.trim().toLowerCase()
  if (!alvo) return contas.value ?? []
  return (contas.value ?? []).filter(
    conta =>
      conta.display_name.toLowerCase().includes(alvo)
      || conta.email.toLowerCase().includes(alvo),
  )
})

/** Permissões agrupadas por área, na ordem em que o catálogo as declara. */
const porArea = computed(() => {
  const mapa = new Map<string, Vocabulario['permissoes']>()
  for (const item of vocabulario.value?.permissoes ?? []) {
    if (!mapa.has(item.area)) mapa.set(item.area, [])
    mapa.get(item.area)!.push(item)
  }
  return [...mapa.entries()]
})

const rotuloDoNivel = (chave: string) =>
  vocabulario.value?.niveis.find(nivel => nivel.chave === chave)?.rotulo ?? chave

/** O que o nível da pessoa já dá, sem grupo nem ajuste. */
function doNivel(conta: Conta): Set<string> {
  const nivel = vocabulario.value?.niveis.find(item => item.chave === conta.nivel)
  return new Set(nivel?.permissoes ?? [])
}

/** De onde vem esta permissão para esta pessoa. Muda o texto e o que fazer. */
function origem(conta: Conta, chave: string): 'ajuste' | 'revogada' | 'nivel' | 'grupo' | 'nao' {
  if (conta.revogadas.includes(chave)) return 'revogada'
  if (conta.concedidas.includes(chave)) return 'ajuste'
  if (!conta.permissoes.includes(chave)) return 'nao'
  return doNivel(conta).has(chave) ? 'nivel' : 'grupo'
}

const ORIGENS: Record<string, string> = {
  nivel: 'vem do nível',
  grupo: 'vem de um grupo',
  ajuste: 'concedida só para esta pessoa',
  revogada: 'tirada só desta pessoa',
  nao: '',
}

/** Quem está olhando não pode oferecer o que ele mesmo não tem. */
const posso = (chave: string) => vocabulario.value?.minhas_permissoes.includes(chave) ?? false

async function chamar(conta: Conta, caminho: string, metodo: string, corpo: object) {
  erro.value = ''
  salvando.value = conta.id
  try {
    await apiFetch(`/v1/usuarios/${conta.id}${caminho}`, { method: metodo, body: corpo })
    await refresh()
    await recarregarGrupos()
  }
  catch (falha) {
    erro.value = (falha as Error).message
  }
  finally {
    salvando.value = 0
  }
}

const mudarNivel = (conta: Conta, nivel: string) =>
  chamar(conta, '/nivel', 'PATCH', { nivel })

/**
 * Três estados, não dois: conceder, revogar e **voltar ao padrão**.
 *
 * A terceira opção falta em quase todo painel de permissão, e sem ela não há
 * como desfazer um ajuste sem adivinhar o que o nível daria.
 */
const mudarPermissao = (conta: Conta, permissao: string, estado: boolean | null) =>
  chamar(conta, '/permissao', 'PATCH', { permissao, estado })

function alternarGrupo(conta: Conta, slug: string) {
  const atuais = new Set(conta.grupos)
  if (atuais.has(slug)) atuais.delete(slug)
  else atuais.add(slug)
  return chamar(conta, '/grupos', 'PUT', { grupos: [...atuais] })
}

const mudarAcesso = (conta: Conta, ativa: boolean) =>
  chamar(conta, '/acesso', 'PATCH', { ativa })

useHead({ title: 'Pessoas' })
</script>

<template>
  <div>
    <div
      class="linha"
      style="margin-bottom: var(--e4)"
    >
      <h1>Pessoas</h1>
      <NuxtLink
        to="/pessoas/grupos"
        class="btn empurra"
      >
        Grupos de permissão
      </NuxtLink>
    </div>

    <p class="fraco pequeno">
      O <strong>nível</strong> é a hierarquia: só se mexe em quem está abaixo.
      As <strong>permissões</strong> ajustam o que cada nível já dá — por grupo,
      ou por pessoa.
      Você é <strong>{{ rotuloDoNivel(vocabulario?.meu_nivel ?? '') }}</strong>.
    </p>

    <div
      class="campo"
      style="max-width: 22rem"
    >
      <label for="busca">Procurar</label>
      <input
        id="busca"
        v-model="busca"
        type="search"
        placeholder="nome ou e-mail"
      >
    </div>

    <p
      v-if="erro"
      class="aviso aviso--erro"
      role="alert"
    >
      {{ erro }}
    </p>

    <ul class="contas">
      <li
        v-for="conta in visiveis"
        :key="conta.id"
        class="card conta"
        :class="{ 'conta--inativa': !conta.is_active }"
      >
        <div class="conta__topo">
          <div class="conta__quem">
            <strong>{{ conta.display_name }}</strong>
            <span class="fraco pequeno">{{ conta.email }}</span>
          </div>

          <span
            class="tag"
            :class="`tag--n${conta.nivel}`"
          >{{ conta.nivel_rotulo }}</span>

          <span
            v-if="!conta.is_active"
            class="tag tag--inativa"
          >sem acesso</span>

          <button
            type="button"
            class="btn btn--fantasma pequeno empurra"
            :aria-expanded="aberta === conta.id"
            @click="aberta = aberta === conta.id ? null : conta.id"
          >
            {{ aberta === conta.id ? 'fechar' : 'detalhes' }}
          </button>
        </div>

        <p class="fraco pequeno conta__resumo">
          {{ conta.permissoes.length }} permissão(ões)
          <template v-if="conta.grupos.length">
            · grupos: {{ conta.grupos.join(', ') }}
          </template>
          <template v-if="conta.last_login_at">
            · último acesso {{ kickoff(conta.last_login_at) }}
          </template>
          <template v-else>
            · nunca entrou
          </template>
        </p>

        <!-- Detalhes só quando pedidos: aberto por padrão, uma instalação com
             trinta contas viraria uma parede de caixas de marcar. -->
        <div
          v-if="aberta === conta.id"
          class="conta__detalhe"
        >
          <template v-if="!conta.pode_gerenciar">
            <!-- A própria conta e a de alguém acima são recusadas pelo mesmo
                 campo, mas por motivos diferentes — e dizer "está acima de
                 você" sobre a sua própria conta confunde mais do que explica. -->
            <p
              v-if="conta.id === eu?.id"
              class="aviso pequeno"
            >
              Esta é a sua conta. Ninguém altera o próprio nível ou as próprias
              permissões — é o caminho mais curto para se trancar do lado de fora.
            </p>
            <p
              v-else
              class="aviso pequeno"
            >
              Você não pode alterar esta conta — ela está no seu nível ou acima.
            </p>
          </template>

          <template v-else>
            <div class="campo">
              <label :for="`nivel-${conta.id}`">Nível</label>
              <select
                :id="`nivel-${conta.id}`"
                :value="conta.nivel"
                :disabled="salvando === conta.id"
                @change="mudarNivel(conta, ($event.target as HTMLSelectElement).value)"
              >
                <option
                  v-for="chave in conta.niveis_possiveis"
                  :key="chave"
                  :value="chave"
                >
                  {{ rotuloDoNivel(chave) }}
                </option>
              </select>
              <span class="dica">
                {{ vocabulario?.niveis.find(n => n.chave === conta.nivel)?.ajuda }}
              </span>
            </div>

            <div
              v-if="grupos?.length"
              class="bloco"
            >
              <h3>Grupos</h3>
              <div class="chips">
                <button
                  v-for="grupo in grupos"
                  :key="grupo.slug"
                  type="button"
                  class="chip"
                  :class="{ 'chip--on': conta.grupos.includes(grupo.slug) }"
                  :disabled="salvando === conta.id"
                  :title="grupo.description"
                  @click="alternarGrupo(conta, grupo.slug)"
                >
                  {{ grupo.name }}
                </button>
              </div>
            </div>

            <div class="bloco">
              <h3>Permissões</h3>
              <div
                v-for="[area, itens] in porArea"
                :key="area"
                class="area"
              >
                <h4 class="pequeno fraco">
                  {{ area }}
                </h4>
                <ul class="permissoes">
                  <li
                    v-for="item in itens"
                    :key="item.chave"
                    :class="`origem--${origem(conta, item.chave)}`"
                  >
                    <div class="permissao__texto">
                      <strong class="pequeno">{{ item.rotulo }}</strong>
                      <span class="fraco pequeno">{{ item.ajuda }}</span>
                      <span
                        v-if="ORIGENS[origem(conta, item.chave)]"
                        class="fraco pequeno origem"
                      >{{ ORIGENS[origem(conta, item.chave)] }}</span>
                    </div>

                    <div class="permissao__acoes">
                      <button
                        type="button"
                        class="btn btn--fantasma pequeno"
                        :disabled="salvando === conta.id || !posso(item.chave)"
                        :title="posso(item.chave) ? '' : 'Você não tem esta permissão, então não pode concedê-la'"
                        @click="mudarPermissao(conta, item.chave, !conta.permissoes.includes(item.chave))"
                      >
                        {{ conta.permissoes.includes(item.chave) ? 'tirar' : 'dar' }}
                      </button>
                      <button
                        v-if="['ajuste', 'revogada'].includes(origem(conta, item.chave))"
                        type="button"
                        class="btn btn--fantasma pequeno"
                        :disabled="salvando === conta.id"
                        title="Desfaz o ajuste: volta ao que o nível e os grupos dão"
                        @click="mudarPermissao(conta, item.chave, null)"
                      >
                        padrão
                      </button>
                    </div>
                  </li>
                </ul>
              </div>
            </div>

            <div class="bloco">
              <h3>Acesso</h3>
              <p class="fraco pequeno">
                Desativar tira a entrada sem apagar nada. Apagar a conta levaria
                junto os palpites e mudaria o ranking de temporadas passadas.
              </p>
              <button
                type="button"
                class="btn"
                :class="{ 'btn--perigo': conta.is_active }"
                :disabled="salvando === conta.id"
                @click="mudarAcesso(conta, !conta.is_active)"
              >
                {{ conta.is_active ? 'Desativar acesso' : 'Devolver acesso' }}
              </button>
            </div>
          </template>
        </div>
      </li>
    </ul>

    <p
      v-if="!visiveis.length"
      class="vazio"
    >
      Ninguém encontrado.
    </p>
  </div>
</template>

<style scoped>
.contas { list-style: none; padding: 0; margin: var(--e4) 0 0; display: grid; gap: var(--e3); }
.conta--inativa { opacity: 0.72; }

.conta__topo { display: flex; align-items: center; gap: var(--e2); flex-wrap: wrap; }
.conta__quem { display: grid; }
.conta__resumo { margin: var(--e2) 0 0; }
.conta__detalhe { margin-top: var(--e3); border-top: 1px solid var(--borda); padding-top: var(--e3); }

.bloco { margin-top: var(--e4); }
.bloco h3 { margin-bottom: var(--e2); }
.area { margin-bottom: var(--e3); }
.area h4 { margin: 0 0 0.2rem; text-transform: uppercase; letter-spacing: 0.04em; }

.chips { display: flex; gap: var(--e2); flex-wrap: wrap; }

.chip {
  font: inherit;
  font-size: 0.85rem;
  font-weight: 600;
  min-height: 44px;
  padding: 0.4rem 0.85rem;
  border-radius: var(--raio-pill);
  border: 1px solid var(--borda-forte);
  background: var(--superficie);
  color: var(--texto);
  cursor: pointer;
}

.chip--on { background: var(--verde); border-color: var(--verde); color: var(--sobre-verde); }
.chip:focus-visible { outline: 2px solid var(--verde); outline-offset: 2px; }

.permissoes { list-style: none; padding: 0; margin: 0; display: grid; gap: 0.2rem; }

.permissoes li {
  display: flex;
  align-items: center;
  gap: var(--e3);
  padding: 0.45rem var(--e2);
  border-radius: var(--raio-p);
  border-left: 3px solid transparent;
}

.permissao__texto { display: grid; min-width: 0; flex: 1; }
.permissao__acoes { display: flex; gap: var(--e1); flex-shrink: 0; }

/* A cor diz de onde a permissão vem — e portanto o que acontece ao mexer. */
.origem--nivel { border-left-color: var(--verde-borda); background: var(--superficie-2); }
.origem--grupo { border-left-color: var(--verde); background: var(--verde-fraco); }
.origem--ajuste { border-left-color: var(--ok); background: var(--verde-fraco); }
.origem--revogada { border-left-color: var(--alerta); }
.origem--revogada .permissao__texto { text-decoration: line-through; }
.origem--nao { opacity: 0.62; }

.origem { font-style: italic; }

.tag--ndono { background: var(--ouro); color: var(--superficie); border-color: transparent; }
.tag--nadmin { background: var(--verde); color: var(--sobre-verde); border-color: transparent; }
.tag--inativa { color: var(--alerta); border-color: var(--alerta); }

@media (width <= 560px) {
  .permissoes li { flex-direction: column; align-items: stretch; gap: var(--e2); }
  .permissao__acoes { justify-content: flex-end; }
}
</style>
