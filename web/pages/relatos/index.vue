<script setup lang="ts">
definePageMeta({ middleware: 'auth' })

/**
 * Lista de relatos.
 *
 * Quem administra vê tudo e tria; os demais veem os próprios e acompanham a
 * resposta. Essa segunda parte não é detalhe: relato que some num buraco sem
 * retorno é relato que ninguém manda de novo.
 */
interface Resumo {
  code: string
  kind: string
  severity: string
  status: string
  title: string
  reporter_name: string
  created_at: string
  attachments: number
}

const { kickoff } = useFormat()

const filtroEstado = ref('')
const filtroTipo = ref('')

const { data: relatos, refresh } = await useApiData<Resumo[]>(
  'relatos',
  () => {
    const busca = new URLSearchParams()
    if (filtroEstado.value) busca.set('estado', filtroEstado.value)
    if (filtroTipo.value) busca.set('tipo', filtroTipo.value)
    const query = busca.toString()
    return `/v1/reports${query ? `?${query}` : ''}`
  },
  { watch: [filtroEstado, filtroTipo] },
)

const { data: contagem } = await useApiData<Record<string, number>>(
  'relatos-resumo',
  '/v1/reports/resumo',
)

const ehAdmin = computed(() => Object.keys(contagem.value ?? {}).length > 0)

const ROTULO_ESTADO: Record<string, string> = {
  aberto: 'aberto',
  triado: 'triado',
  fazendo: 'fazendo',
  resolvido: 'resolvido',
  descartado: 'descartado',
}

const ROTULO_TIPO: Record<string, string> = {
  bug: '🐞 problema',
  feedback: '💬 retorno',
  ideia: '💡 ideia',
}

const CLASSE_GRAVIDADE: Record<string, string> = {
  critica: 'tag--critica',
  alta: 'tag--alta',
}

async function exportar() {
  const texto = await apiFetch<string>('/v1/reports/export/markdown', {
    responseType: 'text',
  } as Record<string, unknown>)
  const url = URL.createObjectURL(new Blob([texto], { type: 'text/markdown' }))
  const link = document.createElement('a')
  link.href = url
  link.download = 'relatos.md'
  link.click()
  URL.revokeObjectURL(url)
}

useHead({ title: 'Relatos' })
</script>

<template>
  <div>
    <div class="linha">
      <h1>Relatos</h1>
      <button
        v-if="ehAdmin"
        type="button"
        class="btn pequeno empurra"
        @click="exportar"
      >
        exportar tudo
      </button>
    </div>

    <p
      v-if="!ehAdmin"
      class="fraco pequeno"
    >
      Aqui ficam os problemas e retornos que você mandou, com a resposta de quem
      cuida da plataforma.
    </p>

    <div
      v-if="ehAdmin && contagem"
      class="placar"
    >
      <div
        v-for="estado in ['aberto', 'triado', 'fazendo', 'resolvido']"
        :key="estado"
        class="card numero"
      >
        <strong class="pontos">{{ contagem[estado] ?? 0 }}</strong>
        <span class="fraco pequeno">{{ ROTULO_ESTADO[estado] }}</span>
      </div>
    </div>

    <div
      class="linha filtros"
      style="margin: 1rem 0"
    >
      <div
        class="campo"
        style="margin: 0; min-width: 10rem"
      >
        <label for="f-estado">Estado</label>
        <select
          id="f-estado"
          v-model="filtroEstado"
        >
          <option value="">
            todos
          </option>
          <option
            v-for="(rotulo, valor) in ROTULO_ESTADO"
            :key="valor"
            :value="valor"
          >
            {{ rotulo }}
          </option>
        </select>
      </div>
      <div
        class="campo"
        style="margin: 0; min-width: 10rem"
      >
        <label for="f-tipo">Tipo</label>
        <select
          id="f-tipo"
          v-model="filtroTipo"
        >
          <option value="">
            todos
          </option>
          <option
            v-for="(rotulo, valor) in ROTULO_TIPO"
            :key="valor"
            :value="valor"
          >
            {{ rotulo }}
          </option>
        </select>
      </div>
      <button
        type="button"
        class="btn pequeno empurra"
        @click="refresh()"
      >
        atualizar
      </button>
    </div>

    <div
      v-if="!relatos?.length"
      class="card vazio"
    >
      Nenhum relato por aqui. Use o botão 🐞 no canto da tela para mandar o
      primeiro — ele funciona em qualquer página.
    </div>

    <div
      v-else
      class="pilha"
    >
      <NuxtLink
        v-for="relato in relatos"
        :key="relato.code"
        :to="`/relatos/${relato.code}`"
        class="card linha item"
      >
        <div class="corpo">
          <div class="linha pequeno">
            <span class="codigo num">{{ relato.code }}</span>
            <span class="fraco">{{ ROTULO_TIPO[relato.kind] }}</span>
            <span
              v-if="relato.kind === 'bug'"
              class="tag"
              :class="CLASSE_GRAVIDADE[relato.severity]"
            >{{ relato.severity }}</span>
            <span
              v-if="relato.attachments"
              class="fraco"
            >📎 {{ relato.attachments }}</span>
          </div>
          <strong>{{ relato.title }}</strong>
          <div class="fraco pequeno">
            {{ relato.reporter_name || '—' }} · {{ kickoff(relato.created_at) }}
          </div>
        </div>
        <span
          class="tag empurra"
          :class="{ 'tag--verde': relato.status === 'resolvido' }"
        >{{ ROTULO_ESTADO[relato.status] }}</span>
      </NuxtLink>
    </div>
  </div>
</template>

<style scoped>
.placar { display: grid; grid-template-columns: repeat(auto-fit, minmax(7rem, 1fr)); gap: var(--e2); }
.numero { display: grid; gap: 0.1rem; text-align: center; padding: var(--e3); }
.numero .pontos { font-size: 1.6rem; }

.item { text-decoration: none; color: inherit; align-items: flex-start; }
.item:hover { border-color: var(--verde); }
.corpo { display: grid; gap: 0.15rem; min-width: 0; }
.codigo { font-weight: 700; color: var(--verde); }

.tag--critica { color: var(--alerta); border-color: var(--alerta); }
.tag--alta { color: var(--aviso); border-color: var(--aviso); }
.filtros { align-items: flex-end; }
</style>
