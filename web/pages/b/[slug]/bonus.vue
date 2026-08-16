<script setup lang="ts">
definePageMeta({ middleware: 'auth' })

/**
 * Palpites que não são de placar.
 *
 * Mata-mata trava no apito de cada jogo; os de temporada travam no primeiro
 * jogo do campeonato. O servidor é quem decide — aqui só evitamos oferecer um
 * campo que já seria recusado.
 */

const route = useRoute()
const slug = computed(() => route.params.slug as string)
const { kickoff } = useFormat()

interface Bonus {
  kind: string
  reference_id: number
  team_ids: number[]
  locks_at: string
  is_locked: boolean
  points_awarded: number | null
}
interface Confronto {
  fixture_id: number
  label: string
  kickoff_at: string
  is_locked: boolean
  home_team_id: number
  away_team_id: number
  advancing_team_id: number | null
}
interface Time { id: number, name: string }
interface Desfecho {
  outcome: {
    champion_team_id?: number | null
    top4_team_ids?: number[]
    relegated_team_ids?: number[]
  }
  teams: Time[]
}
interface Bolao {
  my_role: string | null
  scoring: {
    knockout_advance_points: number
    champion_points: number
    top4_points: number
    relegated_points: number
  }
}

const { data: bolao } = useApiData<Bolao>(`bonus-pool-${slug.value}`, () => `/v1/pools/${slug.value}`)
const { data: meus, refresh: recarregarMeus } = useApiData<Bonus[]>(
  `bonus-meus-${slug.value}`,
  () => `/v1/pools/${slug.value}/bonus`,
)
const { data: confrontos } = useApiData<Confronto[]>(
  `bonus-mata-mata-${slug.value}`,
  () => `/v1/pools/${slug.value}/bonus/knockout`,
)
const { data: desfecho, refresh: recarregarDesfecho } = useApiData<Desfecho>(
  `bonus-desfecho-${slug.value}`,
  () => `/v1/pools/${slug.value}/bonus/outcome`,
)

const erro = ref('')
const mensagem = ref('')
const podeGerenciar = computed(() => bolao.value?.my_role && bolao.value.my_role !== 'player')
const pontos = computed(() => bolao.value?.scoring)

/** Escolha atual por confronto, e por tipo de palpite de temporada. */
const escolhaMataMata = reactive<Record<number, number | null>>({})
const campeao = ref<number | null>(null)
const g4 = ref<number[]>([])
const rebaixados = ref<number[]>([])

// Desfecho oficial (só organizador edita).
const oficialCampeao = ref<number | null>(null)
const oficialG4 = ref<number[]>([])
const oficialRebaixados = ref<number[]>([])

useHead({ title: 'Palpites de bônus' })

watch(meus, (lista) => {
  for (const item of lista ?? []) {
    if (item.kind === 'knockout_advance') escolhaMataMata[item.reference_id] = item.team_ids[0] ?? null
    if (item.kind === 'champion') campeao.value = item.team_ids[0] ?? null
    if (item.kind === 'top4') g4.value = [...item.team_ids]
    if (item.kind === 'relegated') rebaixados.value = [...item.team_ids]
  }
}, { immediate: true })

watch(desfecho, (valor) => {
  if (!valor) return
  oficialCampeao.value = valor.outcome.champion_team_id ?? null
  oficialG4.value = [...(valor.outcome.top4_team_ids ?? [])]
  oficialRebaixados.value = [...(valor.outcome.relegated_team_ids ?? [])]
}, { immediate: true })

const temporadaTravada = computed(() =>
  (meus.value ?? []).some(item => item.kind !== 'knockout_advance' && item.is_locked),
)

function pontuacaoDe(kind: string, referencia = 0): number | null {
  const item = (meus.value ?? []).find(x => x.kind === kind && x.reference_id === referencia)
  return item?.points_awarded ?? null
}

async function salvar(kind: string, teamIds: (number | null)[], referenceId = 0) {
  erro.value = ''
  mensagem.value = ''
  const limpos = teamIds.filter((id): id is number => typeof id === 'number')
  if (!limpos.length) {
    erro.value = 'Escolha pelo menos um time.'
    return
  }
  try {
    const resposta = await apiFetch<{ detail: string }>(`/v1/pools/${slug.value}/bonus`, {
      method: 'PUT',
      body: { kind, team_ids: limpos, reference_id: referenceId },
    })
    mensagem.value = resposta.detail
    await recarregarMeus()
  }
  catch (error_) {
    erro.value = (error_ as Error).message
  }
}

async function salvarDesfecho() {
  erro.value = ''
  mensagem.value = ''
  try {
    const resposta = await apiFetch<{ detail: string }>(
      `/v1/pools/${slug.value}/bonus/outcome`,
      {
        method: 'PUT',
        body: {
          champion_team_id: oficialCampeao.value,
          top4_team_ids: oficialG4.value.filter(Boolean),
          relegated_team_ids: oficialRebaixados.value.filter(Boolean),
        },
      },
    )
    mensagem.value = resposta.detail
    await Promise.all([recarregarDesfecho(), recarregarMeus()])
  }
  catch (error_) {
    erro.value = (error_ as Error).message
  }
}
</script>

<template>
  <div>
    <NuxtLink
      :to="`/b/${slug}`"
      class="pequeno"
    >
      ← voltar ao bolão
    </NuxtLink>
    <h1 style="margin-top: 0.5rem">
      Palpites de bônus
    </h1>

    <p
      v-if="erro"
      class="aviso aviso--erro"
    >
      {{ erro }}
    </p>
    <p
      v-if="mensagem"
      class="aviso aviso--ok"
    >
      {{ mensagem }}
    </p>

    <!-- Mata-mata -->
    <div
      v-if="confrontos?.length && pontos?.knockout_advance_points"
      class="card"
    >
      <h2>Quem avança</h2>
      <p class="fraco pequeno">
        Vale {{ pontos.knockout_advance_points }} pontos por confronto, independente
        do placar. Nos pênaltis conta quem classificou.
      </p>

      <div class="pilha">
        <div
          v-for="confronto in confrontos"
          :key="confronto.fixture_id"
          class="confronto"
        >
          <div class="linha">
            <strong>{{ confronto.label }}</strong>
            <span class="fraco pequeno empurra">{{ kickoff(confronto.kickoff_at) }}</span>
          </div>
          <div class="linha">
            <select
              v-model="escolhaMataMata[confronto.fixture_id]"
              :disabled="confronto.is_locked"
              :aria-label="`Quem avança em ${confronto.label}`"
            >
              <option :value="null">
                — escolha —
              </option>
              <option :value="confronto.home_team_id">
                {{ confronto.label.split(' x ')[0] }}
              </option>
              <option :value="confronto.away_team_id">
                {{ confronto.label.split(' x ')[1] }}
              </option>
            </select>
            <button
              type="button"
              class="btn pequeno"
              :disabled="confronto.is_locked"
              @click="salvar('knockout_advance', [escolhaMataMata[confronto.fixture_id] ?? null], confronto.fixture_id)"
            >
              salvar
            </button>
            <span
              v-if="pontuacaoDe('knockout_advance', confronto.fixture_id) !== null"
              class="tag tag--verde"
            >
              {{ pontuacaoDe('knockout_advance', confronto.fixture_id) }} pts
            </span>
            <span
              v-else-if="confronto.is_locked"
              class="tag tag--fechado"
            >fechado</span>
          </div>
        </div>
      </div>
    </div>

    <!-- Temporada -->
    <div
      v-if="desfecho?.teams.length && (pontos?.champion_points || pontos?.top4_points || pontos?.relegated_points)"
      class="card"
    >
      <h2>Palpites de temporada</h2>
      <p
        v-if="temporadaTravada"
        class="aviso aviso--atencao pequeno"
      >
        O campeonato já começou — estes palpites estão fechados.
      </p>
      <p
        v-else
        class="fraco pequeno"
      >
        Travam quando o campeonato começa. No G-4 e nos rebaixados você pontua
        <strong>por acerto</strong>, não é tudo ou nada.
      </p>

      <div
        v-if="pontos?.champion_points"
        class="campo"
      >
        <label for="campeao">Campeão ({{ pontos.champion_points }} pts)</label>
        <div class="linha">
          <select
            id="campeao"
            v-model="campeao"
            :disabled="temporadaTravada"
          >
            <option :value="null">
              — escolha —
            </option>
            <option
              v-for="time in desfecho.teams"
              :key="time.id"
              :value="time.id"
            >
              {{ time.name }}
            </option>
          </select>
          <button
            type="button"
            class="btn pequeno"
            :disabled="temporadaTravada"
            @click="salvar('champion', [campeao])"
          >
            salvar
          </button>
          <span
            v-if="pontuacaoDe('champion') !== null"
            class="tag tag--verde"
          >{{ pontuacaoDe('champion') }} pts</span>
        </div>
      </div>

      <div
        v-if="pontos?.top4_points"
        class="campo"
      >
        <label for="g4">G-4 ({{ pontos.top4_points }} pts por acerto) — escolha 4</label>
        <select
          id="g4"
          v-model="g4"
          multiple
          size="6"
          :disabled="temporadaTravada"
        >
          <option
            v-for="time in desfecho.teams"
            :key="time.id"
            :value="time.id"
          >
            {{ time.name }}
          </option>
        </select>
        <div class="linha">
          <button
            type="button"
            class="btn pequeno"
            :disabled="temporadaTravada"
            @click="salvar('top4', g4)"
          >
            salvar
          </button>
          <span
            v-if="pontuacaoDe('top4') !== null"
            class="tag tag--verde"
          >{{ pontuacaoDe('top4') }} pts</span>
        </div>
      </div>

      <div
        v-if="pontos?.relegated_points"
        class="campo"
      >
        <label for="rebaixados">Rebaixados ({{ pontos.relegated_points }} pts por acerto)</label>
        <select
          id="rebaixados"
          v-model="rebaixados"
          multiple
          size="6"
          :disabled="temporadaTravada"
        >
          <option
            v-for="time in desfecho.teams"
            :key="time.id"
            :value="time.id"
          >
            {{ time.name }}
          </option>
        </select>
        <div class="linha">
          <button
            type="button"
            class="btn pequeno"
            :disabled="temporadaTravada"
            @click="salvar('relegated', rebaixados)"
          >
            salvar
          </button>
          <span
            v-if="pontuacaoDe('relegated') !== null"
            class="tag tag--verde"
          >{{ pontuacaoDe('relegated') }} pts</span>
        </div>
      </div>
    </div>

    <!-- Desfecho oficial -->
    <div
      v-if="podeGerenciar && desfecho?.teams.length"
      class="card"
    >
      <h2>Desfecho do campeonato</h2>
      <p class="fraco pequeno">
        Preencha no fim da temporada. Salvar aqui apura os palpites de temporada
        de todo mundo e recalcula o ranking.
      </p>

      <div class="campo">
        <label for="oficial-campeao">Campeão</label>
        <select
          id="oficial-campeao"
          v-model="oficialCampeao"
        >
          <option :value="null">
            — não definido —
          </option>
          <option
            v-for="time in desfecho.teams"
            :key="time.id"
            :value="time.id"
          >
            {{ time.name }}
          </option>
        </select>
      </div>

      <div class="campo">
        <label for="oficial-g4">G-4</label>
        <select
          id="oficial-g4"
          v-model="oficialG4"
          multiple
          size="5"
        >
          <option
            v-for="time in desfecho.teams"
            :key="time.id"
            :value="time.id"
          >
            {{ time.name }}
          </option>
        </select>
      </div>

      <div class="campo">
        <label for="oficial-rebaixados">Rebaixados</label>
        <select
          id="oficial-rebaixados"
          v-model="oficialRebaixados"
          multiple
          size="5"
        >
          <option
            v-for="time in desfecho.teams"
            :key="time.id"
            :value="time.id"
          >
            {{ time.name }}
          </option>
        </select>
      </div>

      <button
        type="button"
        class="btn btn--principal"
        @click="salvarDesfecho"
      >
        Salvar desfecho e apurar
      </button>
    </div>

    <div
      v-if="!confrontos?.length && !desfecho?.teams.length"
      class="card vazio"
    >
      Este bolão não tem palpites de bônus configurados.
    </div>
  </div>
</template>

<style scoped>
.confronto { padding: 0.5rem 0; border-bottom: 1px solid var(--borda); }
.confronto:last-child { border-bottom: none; }
select[multiple] { padding: 0.3rem; }
</style>
