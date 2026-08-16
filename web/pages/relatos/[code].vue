<script setup lang="ts">
definePageMeta({ middleware: 'auth' })

/**
 * Um relato: o que foi dito, o que foi anexado, e a conversa.
 *
 * Os anexos são carregados com autenticação, não por URL pública — captura de
 * tela de bug carrega palpite alheio e nome de participante. Por isso a imagem
 * é buscada e convertida em URL local em vez de ir direto no `src`.
 */
interface Anexo {
  id: number
  kind: string
  content_type: string
  original_name: string
  size_bytes: number
  duration_ms: number | null
}
interface Comentario {
  id: number
  author_name: string
  body: string
  created_at: string
}
interface Relato {
  code: string
  kind: string
  severity: string
  status: string
  title: string
  body: string
  reporter_name: string
  page_url: string
  user_agent: string
  viewport: string
  created_at: string
  resolved_at: string | null
  resolution: string
  attachments: Anexo[]
  comments: Comentario[]
  can_triage: boolean
}

const route = useRoute()
const code = computed(() => String(route.params.code).toUpperCase())
const { kickoff } = useFormat()

const { data: relato, refresh } = await useApiData<Relato>(
  `relato-${code.value}`,
  () => `/v1/reports/${code.value}`,
)

const erro = ref('')
const salvando = ref(false)
const comentario = ref('')

/**
 * Anexo precisa do cabeçalho de autenticação, então não dá para pôr a URL da
 * API direto no `src`. Busca uma vez e guarda o endereço local.
 */
const midias = ref<Record<number, string>>({})

async function carregarMidias() {
  for (const anexo of relato.value?.attachments ?? []) {
    if (midias.value[anexo.id]) continue
    try {
      const blob = await apiFetch<Blob>(`/v1/reports/attachments/${anexo.id}`, {
        responseType: 'blob',
      } as Record<string, unknown>)
      midias.value[anexo.id] = URL.createObjectURL(blob as Blob)
    }
    catch {
      // Anexo que sumiu do disco não pode derrubar a tela do relato.
    }
  }
}

onMounted(carregarMidias)
watch(relato, carregarMidias)
onBeforeUnmount(() => {
  Object.values(midias.value).forEach(URL.revokeObjectURL)
})

async function mudar(campos: Record<string, string>) {
  erro.value = ''
  salvando.value = true
  try {
    await apiFetch(`/v1/reports/${code.value}`, { method: 'PATCH', body: campos })
    await refresh()
  }
  catch (falha) {
    erro.value = (falha as Error).message
  }
  finally {
    salvando.value = false
  }
}

async function comentar() {
  if (!comentario.value.trim()) return
  erro.value = ''
  salvando.value = true
  try {
    await apiFetch(`/v1/reports/${code.value}/comments`, {
      method: 'POST',
      body: { body: comentario.value },
    })
    comentario.value = ''
    await refresh()
  }
  catch (falha) {
    erro.value = (falha as Error).message
  }
  finally {
    salvando.value = false
  }
}

const confirmacao = ref('')
async function apagar() {
  if (confirmacao.value.trim().toUpperCase() !== code.value) return
  try {
    await apiFetch(`/v1/reports/${code.value}`, { method: 'DELETE' })
    await navigateTo('/relatos')
  }
  catch (falha) {
    erro.value = (falha as Error).message
  }
}

const ESTADOS = ['aberto', 'triado', 'fazendo', 'resolvido', 'descartado']
const GRAVIDADES = ['baixa', 'media', 'alta', 'critica']

useHead({ title: () => `${code.value} · Relatos` })
</script>

<template>
  <div v-if="relato">
    <NuxtLink
      to="/relatos"
      class="pequeno"
    >
      ← todos os relatos
    </NuxtLink>

    <div
      class="linha"
      style="margin-top: 0.5rem"
    >
      <h1>{{ relato.title }}</h1>
      <span class="tag empurra">{{ relato.status }}</span>
    </div>

    <p class="fraco pequeno">
      <span class="num">{{ relato.code }}</span> ·
      {{ relato.kind }}
      <template v-if="relato.kind === 'bug'">
        · gravidade {{ relato.severity }}
      </template>
      · {{ relato.reporter_name || '—' }} · {{ kickoff(relato.created_at) }}
    </p>

    <div
      v-if="relato.body"
      class="card"
    >
      <p style="white-space: pre-wrap; margin: 0">
        {{ relato.body }}
      </p>
    </div>

    <div class="card">
      <h2>Onde aconteceu</h2>
      <dl class="contexto pequeno">
        <dt>Tela</dt>
        <dd class="num">
          {{ relato.page_url || '—' }}
        </dd>
        <dt>Tela do aparelho</dt>
        <dd class="num">
          {{ relato.viewport || '—' }}
        </dd>
        <dt>Navegador</dt>
        <dd>{{ relato.user_agent || '—' }}</dd>
      </dl>
    </div>

    <div
      v-if="relato.attachments.length"
      class="card"
    >
      <h2>Anexos</h2>
      <div class="anexos">
        <figure
          v-for="anexo in relato.attachments"
          :key="anexo.id"
        >
          <img
            v-if="anexo.kind === 'imagem' && midias[anexo.id]"
            :src="midias[anexo.id]"
            :alt="anexo.original_name || 'Anexo do relato'"
          >
          <audio
            v-else-if="anexo.kind === 'audio' && midias[anexo.id]"
            :src="midias[anexo.id]"
            controls
          />
          <span
            v-else
            class="fraco pequeno"
          >carregando…</span>
          <figcaption class="fraco pequeno">
            {{ anexo.original_name || anexo.kind }} ·
            {{ Math.round(anexo.size_bytes / 1024) }} KB
          </figcaption>
        </figure>
      </div>
    </div>

    <div
      v-if="relato.can_triage"
      class="card"
    >
      <h2>Triagem</h2>
      <div class="linha">
        <div
          class="campo"
          style="margin: 0; min-width: 9rem"
        >
          <label for="t-estado">Estado</label>
          <select
            id="t-estado"
            :value="relato.status"
            :disabled="salvando"
            @change="mudar({ status: ($event.target as HTMLSelectElement).value })"
          >
            <option
              v-for="estado in ESTADOS"
              :key="estado"
              :value="estado"
            >
              {{ estado }}
            </option>
          </select>
        </div>
        <div
          class="campo"
          style="margin: 0; min-width: 9rem"
        >
          <label for="t-grav">Gravidade</label>
          <select
            id="t-grav"
            :value="relato.severity"
            :disabled="salvando"
            @change="mudar({ severity: ($event.target as HTMLSelectElement).value })"
          >
            <option
              v-for="nivel in GRAVIDADES"
              :key="nivel"
              :value="nivel"
            >
              {{ nivel }}
            </option>
          </select>
        </div>
      </div>

      <div
        class="campo"
        style="margin-top: 0.75rem"
      >
        <label for="t-res">O que foi feito</label>
        <textarea
          id="t-res"
          :value="relato.resolution"
          rows="3"
          @change="mudar({ resolution: ($event.target as HTMLTextAreaElement).value })"
        />
        <span class="dica">Fica no relato e aparece para quem reportou.</span>
      </div>
    </div>

    <div
      v-else-if="relato.resolution"
      class="card"
    >
      <h2>Resposta</h2>
      <p style="white-space: pre-wrap; margin: 0">
        {{ relato.resolution }}
      </p>
    </div>

    <div class="card">
      <h2>Conversa</h2>
      <ul
        v-if="relato.comments.length"
        class="conversa"
      >
        <li
          v-for="item in relato.comments"
          :key="item.id"
        >
          <strong class="pequeno">{{ item.author_name }}</strong>
          <span class="fraco pequeno">{{ kickoff(item.created_at) }}</span>
          <p style="white-space: pre-wrap; margin: 0.15rem 0 0">
            {{ item.body }}
          </p>
        </li>
      </ul>
      <p
        v-else
        class="fraco pequeno"
      >
        Ninguém comentou ainda.
      </p>

      <div
        class="campo"
        style="margin-top: 0.75rem"
      >
        <label for="c-novo">Comentar</label>
        <textarea
          id="c-novo"
          v-model="comentario"
          rows="2"
          placeholder="Uma pergunta, um detalhe que faltou, o que foi descoberto…"
        />
      </div>
      <button
        type="button"
        class="btn"
        :disabled="salvando || !comentario.trim()"
        @click="comentar"
      >
        Enviar comentário
      </button>
    </div>

    <p
      v-if="erro"
      class="aviso aviso--erro"
    >
      {{ erro }}
    </p>

    <details
      v-if="relato.can_triage"
      class="card perigo"
    >
      <summary class="pequeno">
        Apagar este relato
      </summary>
      <p class="fraco pequeno">
        Some o relato, os anexos do disco e a conversa. Não tem volta. Para
        confirmar, escreva <strong>{{ relato.code }}</strong>.
      </p>
      <div class="linha">
        <input
          v-model="confirmacao"
          :placeholder="relato.code"
          style="max-width: 12rem"
        >
        <button
          type="button"
          class="btn btn--perigo"
          :disabled="confirmacao.trim().toUpperCase() !== relato.code"
          @click="apagar"
        >
          Apagar
        </button>
      </div>
    </details>
  </div>
</template>

<style scoped>
.contexto { display: grid; grid-template-columns: auto 1fr; gap: 0.3rem 1rem; margin: 0; }
.contexto dt { font-weight: 650; color: var(--texto-fraco); }
.contexto dd { margin: 0; overflow-wrap: anywhere; }

.anexos { display: grid; grid-template-columns: repeat(auto-fit, minmax(14rem, 1fr)); gap: var(--e3); }
.anexos figure { margin: 0; display: grid; gap: 0.3rem; }
.anexos img { width: 100%; border-radius: var(--raio-p); border: 1px solid var(--borda); }
.anexos audio { width: 100%; }

.conversa { list-style: none; padding: 0; margin: 0; display: grid; gap: var(--e3); }
.conversa li { border-left: 2px solid var(--borda); padding-left: var(--e3); }
.conversa strong { margin-right: 0.4rem; }

.perigo { border-color: var(--alerta); }
.perigo summary { color: var(--alerta); cursor: pointer; font-weight: 650; }
</style>
