<script setup lang="ts">
definePageMeta({ middleware: ['auth', 'admin'] })

/**
 * Grupos de permissão: pacotes nomeados, para não marcar caixa uma a uma.
 *
 * O painel só oferece permissões que **quem está olhando tem** — pôr num grupo
 * algo que você não pode fazer seria dar a si mesmo o acesso pela porta dos
 * fundos, bastando entrar no próprio grupo depois.
 */
interface Grupo {
  id: number
  slug: string
  name: string
  description: string
  permissions: string[]
  is_system: boolean
  membros: number
}

interface Vocabulario {
  permissoes: { chave: string, rotulo: string, ajuda: string, area: string }[]
  meu_nivel: string
  minhas_permissoes: string[]
}

const erro = ref('')
const salvando = ref(false)
const editando = ref<number | null>(null)
const criando = ref(false)

const novo = reactive({ nome: '', descricao: '', permissoes: [] as string[] })

const { data: vocabulario } = await useApiData<Vocabulario>(
  'grupos-vocabulario',
  () => '/v1/usuarios/vocabulario',
)
const { data: grupos, refresh } = await useApiData<Grupo[]>(
  'grupos-todos',
  () => '/v1/usuarios/grupos/todos',
)

const porArea = computed(() => {
  const mapa = new Map<string, Vocabulario['permissoes']>()
  for (const item of vocabulario.value?.permissoes ?? []) {
    if (!mapa.has(item.area)) mapa.set(item.area, [])
    mapa.get(item.area)!.push(item)
  }
  return [...mapa.entries()]
})

const posso = (chave: string) => vocabulario.value?.minhas_permissoes.includes(chave) ?? false
const rotulo = (chave: string) =>
  vocabulario.value?.permissoes.find(item => item.chave === chave)?.rotulo ?? chave

async function agir(acao: () => Promise<unknown>) {
  erro.value = ''
  salvando.value = true
  try {
    await acao()
    await refresh()
  }
  catch (falha) {
    erro.value = (falha as Error).message
  }
  finally {
    salvando.value = false
  }
}

function alternar(grupo: Grupo, chave: string) {
  const atuais = new Set(grupo.permissions)
  if (atuais.has(chave)) atuais.delete(chave)
  else atuais.add(chave)
  return agir(() =>
    apiFetch(`/v1/usuarios/grupos/${grupo.id}`, {
      method: 'PATCH',
      body: { permissoes: [...atuais] },
    }),
  )
}

function alternarNovo(chave: string) {
  const i = novo.permissoes.indexOf(chave)
  if (i >= 0) novo.permissoes.splice(i, 1)
  else novo.permissoes.push(chave)
}

async function criar() {
  await agir(async () => {
    await apiFetch('/v1/usuarios/grupos', { method: 'POST', body: { ...novo } })
    novo.nome = ''
    novo.descricao = ''
    novo.permissoes = []
    criando.value = false
  })
}

const confirmacao = ref('')

const apagar = (grupo: Grupo) =>
  agir(async () => {
    await apiFetch(`/v1/usuarios/grupos/${grupo.id}`, { method: 'DELETE' })
    confirmacao.value = ''
  })

useHead({ title: 'Grupos de permissão' })
</script>

<template>
  <div>
    <NuxtLink
      to="/pessoas"
      class="pequeno"
    >
      ← pessoas
    </NuxtLink>

    <div
      class="linha"
      style="margin-top: 0.5rem"
    >
      <h1>Grupos de permissão</h1>
      <button
        type="button"
        class="btn btn--principal empurra"
        @click="criando = !criando"
      >
        {{ criando ? 'cancelar' : 'Criar grupo' }}
      </button>
    </div>

    <p class="fraco pequeno">
      Um grupo é um pacote de permissões com nome. Entregar o grupo a alguém é
      entregar tudo que ele carrega — por isso você só pode pôr num grupo o que
      você mesmo pode fazer.
    </p>

    <p
      v-if="erro"
      class="aviso aviso--erro"
      role="alert"
    >
      {{ erro }}
    </p>

    <form
      v-if="criando"
      class="card"
      @submit.prevent="criar"
    >
      <h2>Grupo novo</h2>
      <div class="campo">
        <label for="g-nome">Nome</label>
        <input
          id="g-nome"
          v-model="novo.nome"
          required
          minlength="3"
          maxlength="80"
          placeholder="Curadoria de campeonatos"
        >
      </div>
      <div class="campo">
        <label for="g-desc">Para que serve</label>
        <input
          id="g-desc"
          v-model="novo.descricao"
          maxlength="500"
          placeholder="Quem traz tabela e corrige placar"
        >
      </div>

      <div
        v-for="[area, itens] in porArea"
        :key="area"
        class="area"
      >
        <h4 class="pequeno fraco">
          {{ area }}
        </h4>
        <div class="chips">
          <button
            v-for="item in itens"
            :key="item.chave"
            type="button"
            class="chip"
            :class="{ 'chip--on': novo.permissoes.includes(item.chave) }"
            :disabled="!posso(item.chave)"
            :title="posso(item.chave) ? item.ajuda : 'Você não tem esta permissão'"
            @click="alternarNovo(item.chave)"
          >
            {{ item.rotulo }}
          </button>
        </div>
      </div>

      <button
        type="submit"
        class="btn btn--principal"
        :disabled="salvando || novo.nome.trim().length < 3"
      >
        Criar
      </button>
    </form>

    <ul class="grupos">
      <li
        v-for="grupo in grupos"
        :key="grupo.id"
        class="card"
      >
        <div class="linha">
          <div>
            <strong>{{ grupo.name }}</strong>
            <span
              v-if="grupo.is_system"
              class="tag"
            >da plataforma</span>
          </div>
          <span class="fraco pequeno empurra">
            {{ grupo.membros }} pessoa(s)
          </span>
          <button
            type="button"
            class="btn btn--fantasma pequeno"
            :aria-expanded="editando === grupo.id"
            @click="editando = editando === grupo.id ? null : grupo.id"
          >
            {{ editando === grupo.id ? 'fechar' : 'editar' }}
          </button>
        </div>

        <p class="fraco pequeno">
          {{ grupo.description || 'Sem descrição.' }}
        </p>

        <!-- `flex` com `gap` e não margem entre irmãos: as etiquetas quebram
             em várias linhas quando o grupo tem muitas permissões, e margem
             lateral não separa quem cai na linha de baixo. -->
        <div
          v-if="grupo.permissions.length"
          class="etiquetas"
        >
          <span
            v-for="chave in grupo.permissions"
            :key="chave"
            class="tag"
          >{{ rotulo(chave) }}</span>
        </div>
        <p
          v-else
          class="fraco pequeno"
        >
          Este grupo não dá nenhuma permissão.
        </p>

        <div
          v-if="editando === grupo.id"
          class="editor"
        >
          <div
            v-for="[area, itens] in porArea"
            :key="area"
            class="area"
          >
            <h4 class="pequeno fraco">
              {{ area }}
            </h4>
            <div class="chips">
              <button
                v-for="item in itens"
                :key="item.chave"
                type="button"
                class="chip"
                :class="{ 'chip--on': grupo.permissions.includes(item.chave) }"
                :disabled="salvando || !posso(item.chave)"
                :title="posso(item.chave) ? item.ajuda : 'Você não tem esta permissão'"
                @click="alternar(grupo, item.chave)"
              >
                {{ item.rotulo }}
              </button>
            </div>
          </div>

          <!-- Grupo da plataforma não é apagável: apagar o que dá acesso ao
               painel deixaria a instalação sem caminho de volta pela tela. -->
          <details
            v-if="!grupo.is_system"
            class="perigo"
          >
            <summary class="pequeno">
              Apagar este grupo
            </summary>
            <p class="fraco pequeno">
              {{ grupo.membros }} pessoa(s) perdem o que ele dá. Para confirmar,
              escreva <strong>{{ grupo.name }}</strong>.
            </p>
            <div class="linha">
              <input
                v-model="confirmacao"
                :placeholder="grupo.name"
                style="max-width: 14rem"
              >
              <button
                type="button"
                class="btn btn--perigo"
                :disabled="confirmacao.trim() !== grupo.name"
                @click="apagar(grupo)"
              >
                Apagar
              </button>
            </div>
          </details>
        </div>
      </li>
    </ul>
  </div>
</template>

<style scoped>
.grupos { list-style: none; padding: 0; margin: var(--e4) 0 0; display: grid; gap: var(--e3); }
.editor { margin-top: var(--e3); border-top: 1px solid var(--borda); padding-top: var(--e3); }
.area { margin-bottom: var(--e3); }
.area h4 { margin: 0 0 0.3rem; text-transform: uppercase; letter-spacing: 0.04em; }

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
.chip:disabled { opacity: 0.45; cursor: not-allowed; }
.chip:focus-visible { outline: 2px solid var(--verde); outline-offset: 2px; }

.etiquetas { display: flex; flex-wrap: wrap; gap: var(--e2); margin-bottom: var(--e2); }

.perigo { margin-top: var(--e3); border-top: 1px solid var(--alerta); padding-top: var(--e3); }
.perigo summary { color: var(--alerta); cursor: pointer; font-weight: 650; }
</style>
