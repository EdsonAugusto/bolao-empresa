<script setup lang="ts">
definePageMeta({ middleware: 'auth' })

const route = useRoute()
const slug = computed(() => route.params.slug as string)
const { movimento } = useFormat()

interface Bolao {
  id: number
  slug: string
  name: string
  description: string | null
  status: string
  kind: string
  season: string
  competition: string
  members: number
  invite_code: string | null
  my_role: string | null
  owner_name: string
  rounds_included: number
  scoring: {
    mode: string
    max_points: number
    version: number
    knockout_advance_points: number
    champion_points: number
    top4_points: number
    relegated_points: number
  }
}

interface Posicao {
  position: number
  previous_position: number | null
  movement: number
  membership_id: number
  display_name: string
  points: number
  predictions_made: number
  fixtures_settled: number
  criterion_hits: Record<string, number>
  is_me: boolean
}

interface Rodada {
  id: number
  kind: 'season' | 'custom'
  number: number | null
  name: string
  starts_at?: string | null
  ends_at?: string | null
  multiplier: number
  fixtures: number
  settled: number
  is_open: boolean
}

const { data: bolao } = useApiData<Bolao>(`bolao-${slug.value}`, () => `/v1/pools/${slug.value}`)
const { data: ranking } = useApiData<Posicao[]>(
  `ranking-${slug.value}`,
  () => `/v1/pools/${slug.value}/standings`,
)
const ehMontado = computed(() => bolao.value?.kind === 'custom')

/**
 * Endereço fixo, sem olhar o tipo do bolão.
 *
 * Já foi `bolao.value?.kind === 'custom' ? matchdays : rounds` — e essa
 * pergunta era feita **antes** de `bolao` carregar: os dois carregamentos
 * disparam juntos. Caía sempre no endpoint de campeonato, que devolve lista
 * vazia para bolão montado, e a tela dizia "ainda não tem rodadas" com as
 * rodadas todas montadas no banco. O endpoint agora responde pelos dois tipos
 * e cada item diz de que tipo é.
 */
const { data: rodadas } = useApiData<Rodada[]>(
  `rodadas-${slug.value}`,
  () => `/v1/pools/${slug.value}/rounds`,
)

function linkRodada(rodada: Rodada): string {
  const sufixo = rodada.kind === 'custom' ? '?tipo=montada' : ''
  return `/b/${slug.value}/rodada/${rodada.id}${sufixo}`
}

const podeGerenciar = computed(() => bolao.value?.my_role && bolao.value.my_role !== 'player')

/** Só mostra a aba de bônus se o bolão realmente pontua algum. */
const temBonus = computed(() => {
  const s = bolao.value?.scoring
  if (!s) return false
  return Boolean(
    s.knockout_advance_points || s.champion_points || s.top4_points || s.relegated_points,
  )
})
const copiado = ref(false)

const proximaRodada = computed(() => rodadas.value?.find(rodada => rodada.settled < rodada.fixtures))

useHead({ title: () => bolao.value?.name ?? 'Bolão' })

async function copiarConvite() {
  if (!bolao.value?.invite_code) return
  try {
    await navigator.clipboard.writeText(bolao.value.invite_code)
    copiado.value = true
    setTimeout(() => (copiado.value = false), 2000)
  }
  catch {
    copiado.value = false
  }
}
</script>

<template>
  <div v-if="bolao">
    <div
      class="linha"
      style="margin-bottom: 0.25rem"
    >
      <h1>{{ bolao.name }}</h1>
      <NuxtLink
        :to="`/b/${slug}/regras`"
        class="btn btn--fantasma pequeno empurra"
      >
        Como funciona
      </NuxtLink>
      <NuxtLink
        v-if="temBonus"
        :to="`/b/${slug}/bonus`"
        class="btn btn--fantasma pequeno"
      >
        Bônus
      </NuxtLink>
      <NuxtLink
        v-if="podeGerenciar"
        :to="ehMontado ? `/b/${slug}/rodadas` : `/b/${slug}/jogos`"
        class="btn btn--fantasma pequeno"
      >
        Jogos
      </NuxtLink>
      <NuxtLink
        v-if="podeGerenciar"
        :to="`/b/${slug}/config`"
        class="btn btn--fantasma pequeno"
      >
        Configurar
      </NuxtLink>
    </div>
    <p class="fraco pequeno">
      {{ bolao.competition }} {{ bolao.season }} · {{ bolao.members }} participantes ·
      organizado por {{ bolao.owner_name }}
    </p>

    <div
      v-if="bolao.invite_code"
      class="card convite"
    >
      <div>
        <div class="pequeno fraco">
          Código de convite
        </div>
        <strong class="codigo">{{ bolao.invite_code }}</strong>
      </div>
      <button
        type="button"
        class="btn empurra"
        @click="copiarConvite"
      >
        {{ copiado ? 'copiado!' : 'copiar' }}
      </button>
    </div>

    <div
      v-if="proximaRodada"
      class="card destaque"
    >
      <div>
        <div class="pequeno fraco">
          Próxima rodada
        </div>
        <strong>{{ proximaRodada.name }}</strong>
        <span
          v-if="proximaRodada.multiplier > 1"
          class="tag tag--verde"
        >
          vale {{ proximaRodada.multiplier }}x
        </span>
      </div>
      <NuxtLink
        :to="linkRodada(proximaRodada)"
        class="btn btn--principal empurra"
      >
        Palpitar
      </NuxtLink>
    </div>

    <h2 style="margin-top: 1.5rem">
      Ranking
    </h2>
    <div
      v-if="!ranking?.length"
      class="card vazio"
    >
      Ninguém pontuou ainda. O ranking aparece quando o primeiro jogo for apurado.
    </div>
    <div
      v-else
      class="card tabela-rolavel"
    >
      <table>
        <thead>
          <tr>
            <th style="width: 3rem">
              #
            </th>
            <th>Participante</th>
            <th class="num">
              Pontos
            </th>
            <th class="num">
              Exatos
            </th>
            <th class="num">
              Mov.
            </th>
          </tr>
        </thead>
        <tbody>
          <tr
            v-for="linha in ranking"
            :key="linha.membership_id"
            :class="{ eu: linha.is_me }"
          >
            <td
              class="pos"
              :class="linha.position <= 3 ? `pos--${linha.position}` : ''"
            >
              {{ linha.position }}
            </td>
            <td>
              {{ linha.display_name }}
              <span
                v-if="linha.is_me"
                class="tag tag--verde"
              >você</span>
            </td>
            <td class="num pontos">
              {{ linha.points }}
            </td>
            <td class="num fraco">
              {{ linha.criterion_hits.exact ?? 0 }}
            </td>
            <td
              class="num pequeno"
              :style="{
                color: linha.movement > 0 ? 'var(--ok)' : linha.movement < 0 ? 'var(--alerta)' : 'var(--texto-fraco)',
              }"
            >
              {{ movimento(linha.movement) }}
            </td>
          </tr>
        </tbody>
      </table>
    </div>

    <h2 style="margin-top: 1.5rem">
      Rodadas
    </h2>
    <div
      v-if="!rodadas?.length"
      class="card vazio"
    >
      Este bolão ainda não tem rodadas. Importe o campeonato primeiro.
    </div>
    <div
      v-else
      class="pilha"
    >
      <NuxtLink
        v-for="rodada in rodadas"
        :key="rodada.id"
        :to="linkRodada(rodada)"
        class="card rodada"
      >
        <div class="linha">
          <strong>{{ rodada.name }}</strong>
          <span
            v-if="rodada.multiplier > 1"
            class="tag tag--verde"
          >{{ rodada.multiplier }}x</span>
          <span class="empurra pequeno fraco num">
            {{ rodada.settled }}/{{ rodada.fixtures }} apurados
          </span>
        </div>
      </NuxtLink>
    </div>
  </div>

  <div
    v-else
    class="vazio"
  >
    carregando…
  </div>
</template>

<style scoped>
.convite, .destaque { display: flex; align-items: center; gap: 1rem; flex-wrap: wrap; }
.codigo { font-size: 1.4rem; letter-spacing: 0.15em; font-family: ui-monospace, monospace; }
.rodada { text-decoration: none; color: inherit; display: block; padding: 0.7rem 1rem; }
.rodada:hover { border-color: var(--verde); }
</style>
