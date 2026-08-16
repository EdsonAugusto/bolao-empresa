<script setup lang="ts">
definePageMeta({ middleware: 'auth' })

/** Importar campeonato é tela restrita; o link só faz sentido para quem pode. */
const { pode } = usePermissoes()
const administra = computed(() => pode('campeonatos.importar'))

interface Temporada {
  id: number
  year: number
  label: string
  competition: string
  competition_slug: string
  is_current: boolean
  rounds: number
  fixtures: number
  upcoming: number
  next_fixture_at: string | null
}

interface Criterio {
  key: string
  enabled: boolean
  points: number
  label: string
  description: string
}

interface Campeonato {
  id: number
  name: string
  country: string | null
  seasons: { id: number, year: number, is_current: boolean, upcoming: number }[]
}

const { data: temporadas } = useApiData<Temporada[]>('temporadas', '/v1/catalog/seasons')
const { data: campeonatos } = useApiData<Campeonato[]>(
  'campeonatos-novo',
  '/v1/catalog/competitions',
)

/** Só faz sentido filtrar por quem ainda tem jogo pela frente. */
const campeonatosAtivos = computed(() =>
  (campeonatos.value ?? [])
    .map(item => ({
      ...item,
      porVir: item.seasons.reduce((total, temporada) => total + (temporada.upcoming ?? 0), 0),
    }))
    .filter(item => item.porVir > 0)
    .sort((a, b) => b.porVir - a.porVir),
)

const escolhidos = ref<number[]>([])

function alternarCampeonato(id: number) {
  const posicao = escolhidos.value.indexOf(id)
  if (posicao === -1) escolhidos.value.push(id)
  else escolhidos.value.splice(posicao, 1)
}

const nome = ref('')
const tipo = ref<'season' | 'custom'>('season')
const temporadaId = ref<number | null>(null)
const modo = ref<'classic' | 'simple' | 'custom'>('classic')
const visibilidade = ref('private')
const criterios = ref<Criterio[]>([])
const erro = ref('')
const salvando = ref(false)

useHead({ title: 'Criar bolão' })

const PRESETS: Record<string, Record<string, number | null>> = {
  classic: {
    exact: 10,
    winner_and_one_score: 7,
    winner_and_goal_diff: null,
    draw: 5,
    winner_only: 5,
    one_score_only: 2,
  },
  simple: {
    exact: 10,
    winner_and_one_score: null,
    winner_and_goal_diff: null,
    draw: 5,
    winner_only: 7,
    one_score_only: null,
  },
}

const ROTULOS: Record<string, [string, string]> = {
  exact: ['Placar exato', 'Cravou o resultado.'],
  winner_and_one_score: ['Vencedor + 1 placar', 'Acertou quem ganhou e os gols de um dos times.'],
  winner_and_goal_diff: ['Vencedor + saldo', 'Acertou quem ganhou e a diferença de gols.'],
  draw: ['Empate', 'Acertou que ia empatar, mas não o placar.'],
  winner_only: ['Vencedor', 'Acertou só quem ganhou.'],
  one_score_only: ['1 placar certo', 'Acertou os gols de um time, mas errou o vencedor.'],
}

function aplicarPreset(nome: 'classic' | 'simple') {
  criterios.value = Object.entries(PRESETS[nome]!).map(([key, points]) => ({
    key,
    enabled: points !== null,
    points: points ?? 0,
    label: ROTULOS[key]![0],
    description: ROTULOS[key]![1],
  }))
}

aplicarPreset('classic')

watch(modo, (novo) => {
  if (novo === 'classic' || novo === 'simple') aplicarPreset(novo)
})

/** Quem ainda tem jogo primeiro. Um bolão numa temporada encerrada nasce sem
 *  nada para palpitar, e a lista não deve empurrar você para lá. */
const ordenadas = computed(() =>
  (temporadas.value ?? [])
    .slice()
    .sort((a, b) => (b.upcoming > 0 ? 1 : 0) - (a.upcoming > 0 ? 1 : 0) || b.year - a.year),
)

const escolhida = computed(() => ordenadas.value.find(t => t.id === temporadaId.value))

watch(ordenadas, (lista) => {
  if (temporadaId.value || !lista.length) return
  temporadaId.value = (lista.find(t => t.upcoming > 0) ?? lista[0]!).id
}, { immediate: true })

/** Ordem em que os critérios vão ser avaliados: pontos decrescentes. */
const ordemAvaliacao = computed(() =>
  criterios.value
    .filter(criterio => criterio.enabled)
    .slice()
    .sort((a, b) => b.points - a.points)
    .map(criterio => criterio.label),
)

async function criar() {
  erro.value = ''
  salvando.value = true
  try {
    const bolao = await apiFetch<{ slug: string }>('/v1/pools', {
      method: 'POST',
      body: {
        name: nome.value,
        kind: tipo.value,
        // Rodada personalizada não pertence a temporada nenhuma.
        season_id: tipo.value === 'season' ? temporadaId.value : null,
        competition_ids: tipo.value === 'custom' ? escolhidos.value : null,
        visibility: visibilidade.value,
        scoring: {
          mode: modo.value,
          criteria: criterios.value.map(({ key, enabled, points }) => ({ key, enabled, points })),
        },
      },
    })
    await navigateTo(`/b/${bolao.slug}`)
  }
  catch (error_) {
    erro.value = (error_ as Error).message
  }
  finally {
    salvando.value = false
  }
}
</script>

<template>
  <div class="estreito">
    <h1>Criar bolão</h1>

    <div
      v-if="!temporadas?.length"
      class="card aviso aviso--atencao"
    >
      Não há campeonato cadastrado ainda.
      <template v-if="administra">
        <NuxtLink to="/campeonatos">
          Importe um primeiro.
        </NuxtLink>
      </template>
      <template v-else>
        Peça a quem administra a plataforma para importar um.
      </template>
    </div>

    <form
      v-else
      @submit.prevent="criar"
    >
      <div class="card">
        <div class="campo">
          <label for="nome">Nome do bolão</label>
          <input
            id="nome"
            v-model="nome"
            required
            minlength="3"
            placeholder="Bolão da firma"
          >
        </div>

        <div class="campo">
          <label for="tipo">Como as rodadas funcionam</label>
          <select
            id="tipo"
            v-model="tipo"
          >
            <option value="season">
              Seguir um campeonato — as rodadas vêm da tabela
            </option>
            <option value="custom">
              Eu monto a rodada — escolho os jogos, de qualquer campeonato
            </option>
          </select>
          <span class="dica">
            {{ tipo === 'season'
              ? 'Todos os jogos do campeonato entram; você pode desligar o que quiser depois.'
              : 'A cada semana você cria uma rodada e marca os jogos. Dá para misturar Brasileirão, Premier League e o que mais estiver importado.' }}
          </span>
        </div>

        <div
          v-if="tipo === 'season'"
          class="campo"
        >
          <label for="temporada">Campeonato</label>
          <select
            id="temporada"
            v-model="temporadaId"
            required
          >
            <option
              v-for="temporada in ordenadas"
              :key="temporada.id"
              :value="temporada.id"
            >
              {{ temporada.competition }} {{ temporada.label }}
              — {{ temporada.upcoming > 0
                ? `${temporada.upcoming} jogos por vir`
                : 'temporada encerrada' }}
            </option>
          </select>
          <span
            v-if="escolhida && escolhida.upcoming === 0"
            class="dica"
          >
            Essa temporada já acabou: os {{ escolhida.fixtures }} jogos dela estão
            todos no passado e ninguém vai conseguir palpitar. Serve para conferir
            o histórico — para jogar, escolha uma com jogos por vir ou monte a
            rodada você mesmo.
          </span>
          <span
            v-else-if="escolhida"
            class="dica"
          >
            {{ escolhida.rounds }} rodadas, {{ escolhida.fixtures }} jogos no total.
          </span>
        </div>

        <div
          v-if="tipo === 'custom' && campeonatosAtivos.length"
          class="campo"
        >
          <span class="rotulo">De quais campeonatos</span>
          <div class="escolha">
            <label
              v-for="campeonato in campeonatosAtivos"
              :key="campeonato.id"
              class="chip"
              :class="{ 'chip--on': escolhidos.includes(campeonato.id) }"
            >
              <input
                type="checkbox"
                :checked="escolhidos.includes(campeonato.id)"
                @change="alternarCampeonato(campeonato.id)"
              >
              <span>{{ campeonato.name }}</span>
              <span class="fraco pequeno num">{{ campeonato.porVir }}</span>
            </label>
          </div>
          <span class="dica">
            {{ escolhidos.length
              ? `A lista de jogos da rodada vai mostrar só esses ${escolhidos.length} campeonatos.`
              : 'Nenhum marcado: a rodada pode puxar jogo de qualquer campeonato importado.' }}
            Dá para mudar depois, e mudar aqui não mexe em rodada já montada.
          </span>
        </div>

        <div class="campo">
          <label for="visibilidade">Quem pode entrar</label>
          <select
            id="visibilidade"
            v-model="visibilidade"
          >
            <option value="private">
              Só quem tem o código
            </option>
            <option value="unlisted">
              Qualquer um com o link
            </option>
            <option value="public">
              Aparece na lista de bolões
            </option>
          </select>
        </div>
      </div>

      <div class="card">
        <h2>Pontuação</h2>

        <div class="campo">
          <label for="modo">Modo</label>
          <select
            id="modo"
            v-model="modo"
          >
            <option value="classic">
              Clássico — 6 critérios, até 10 pontos
            </option>
            <option value="simple">
              Simples — exato, vencedor e empate
            </option>
            <option value="custom">
              Personalizado — eu defino tudo
            </option>
          </select>
        </div>

        <table class="criterios">
          <thead>
            <tr>
              <th>Critério</th>
              <th style="width: 5rem">
                Vale
              </th>
              <th style="width: 4rem">
                Usa?
              </th>
            </tr>
          </thead>
          <tbody>
            <tr
              v-for="criterio in criterios"
              :key="criterio.key"
            >
              <td>
                <strong>{{ criterio.label }}</strong><br>
                <span class="fraco pequeno">{{ criterio.description }}</span>
              </td>
              <td>
                <input
                  v-model.number="criterio.points"
                  type="number"
                  min="0"
                  max="100"
                  :disabled="modo !== 'custom' || !criterio.enabled"
                  :aria-label="`Pontos de ${criterio.label}`"
                >
              </td>
              <td class="centro">
                <input
                  v-model="criterio.enabled"
                  type="checkbox"
                  :disabled="modo !== 'custom'"
                  :aria-label="`Usar ${criterio.label}`"
                  style="width: auto"
                >
              </td>
            </tr>
          </tbody>
        </table>

        <p
          class="fraco pequeno"
          style="margin-top: 0.75rem"
        >
          <strong>Ordem de avaliação:</strong> {{ ordemAvaliacao.join(' → ') }}.<br>
          Cada palpite leva o critério de maior valor que ele satisfaz. O desempate
          segue essa mesma ordem — e, em último caso, quem entrou antes no bolão.
        </p>
      </div>

      <p
        v-if="erro"
        class="aviso aviso--erro"
      >
        {{ erro }}
      </p>

      <button
        type="submit"
        class="btn btn--principal btn--bloco"
        :disabled="salvando"
      >
        {{ salvando ? 'criando…' : 'Criar bolão' }}
      </button>
    </form>
  </div>
</template>

<style scoped>
.estreito { max-width: 42rem; margin-inline: auto; }
.criterios input[type="number"] { text-align: center; }

.rotulo { font-weight: 650; font-size: 0.9rem; }
.escolha { display: flex; flex-wrap: wrap; gap: var(--e2); }

.chip {
  display: inline-flex;
  align-items: center;
  gap: var(--e2);
  padding: 0.4rem 0.7rem;
  border: 1px solid var(--borda-forte);
  border-radius: var(--raio-pill);
  cursor: pointer;
  font-size: 0.88rem;
  font-weight: 600;
  transition: background-color 0.12s ease, border-color 0.12s ease;
}

.chip:hover { background: var(--superficie-2); }

.chip--on {
  background: var(--verde-fraco);
  border-color: var(--verde);
  color: var(--verde);
}

.chip input { width: 1rem; height: 1rem; }
</style>
