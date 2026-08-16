<script setup lang="ts">
definePageMeta({ middleware: 'auth' })

const route = useRoute()
const slug = computed(() => route.params.slug as string)

interface Criterio {
  key: string
  enabled: boolean
  points: number
  label: string
  description: string
}
interface Pontuacao {
  version: number
  mode: string
  criteria: Criterio[]
  evaluation_order: string[]
  tiebreak_order: string[]
  max_points: number
  is_frozen: boolean
  knockout_advance_points: number
  champion_points: number
  top4_points: number
  relegated_points: number
}
interface Membro {
  membership_id: number
  display_name: string
  role: string
  status: string
  joined_at: string
  is_me: boolean
}
interface Rodada { id: number, name: string, multiplier: number }

const { data: pontuacao, refresh: recarregarPontuacao } = useApiData<Pontuacao>(
  `scoring-${slug.value}`,
  () => `/v1/pools/${slug.value}/scoring`,
)
const { data: membros, refresh: recarregarMembros } = useApiData<Membro[]>(
  `membros-${slug.value}`,
  () => `/v1/pools/${slug.value}/members`,
)
const { data: rodadas, refresh: recarregarRodadas } = useApiData<Rodada[]>(
  `cfg-rodadas-${slug.value}`,
  () => `/v1/pools/${slug.value}/rounds`,
)

interface Bolao {
  name: string
  kind: string
  members: number
  is_owner: boolean
  competition_ids: number[]
}
interface Campeonato {
  id: number
  name: string
  seasons: { upcoming: number }[]
}

const { data: bolao, refresh: recarregarBolao } = useApiData<Bolao>(
  `cfg-bolao-${slug.value}`,
  () => `/v1/pools/${slug.value}`,
)
const { data: campeonatos } = useApiData<Campeonato[]>(
  'cfg-campeonatos',
  '/v1/catalog/competitions',
)

const erro = ref('')
const mensagem = ref('')
const salvando = ref(false)

useHead({ title: 'Configurar bolão' })

/* --- Campeonatos que alimentam a rodada --------------------------------- */

const campeonatosAtivos = computed(() =>
  (campeonatos.value ?? [])
    .map(item => ({
      ...item,
      porVir: item.seasons.reduce((total, temporada) => total + (temporada.upcoming ?? 0), 0),
    }))
    .filter(item => item.porVir > 0 || (bolao.value?.competition_ids ?? []).includes(item.id))
    .sort((a, b) => b.porVir - a.porVir),
)

const filtro = ref<number[]>([])

watch(bolao, (valor) => {
  if (valor) filtro.value = [...valor.competition_ids]
}, { immediate: true })

function alternarCampeonato(id: number) {
  const posicao = filtro.value.indexOf(id)
  if (posicao === -1) filtro.value.push(id)
  else filtro.value.splice(posicao, 1)
}

async function salvarCampeonatos() {
  erro.value = ''
  mensagem.value = ''
  salvando.value = true
  try {
    const resposta = await apiFetch<{ detail: string }>(
      `/v1/pools/${slug.value}/competitions`,
      { method: 'PUT', body: { competition_ids: filtro.value } },
    )
    mensagem.value = resposta.detail
    await recarregarBolao()
  }
  catch (error_) {
    erro.value = (error_ as Error).message
  }
  finally {
    salvando.value = false
  }
}

/* --- Exclusão ------------------------------------------------------------ */

/**
 * Confirmação por digitação, não por `confirm()`.
 *
 * O bolão leva junto os palpites e o ranking de todo mundo. Um clique em "ok"
 * num diálogo do navegador é fácil demais para uma coisa que não tem volta —
 * escrever o nome obriga a olhar o que está sendo apagado.
 */
const confirmacao = ref('')
const excluindo = ref(false)
const podeExcluir = computed(
  () => !!bolao.value && confirmacao.value.trim() === bolao.value.name,
)

async function excluir() {
  if (!podeExcluir.value) return
  erro.value = ''
  excluindo.value = true
  try {
    await apiFetch(`/v1/pools/${slug.value}`, { method: 'DELETE' })
    await navigateTo('/')
  }
  catch (error_) {
    erro.value = (error_ as Error).message
    excluindo.value = false
  }
}

async function salvarPontuacao() {
  if (!pontuacao.value) return
  erro.value = ''
  mensagem.value = ''
  salvando.value = true
  try {
    await apiFetch(`/v1/pools/${slug.value}/scoring`, {
      method: 'PUT',
      body: {
        mode: 'custom',
        criteria: pontuacao.value.criteria.map(({ key, enabled, points }) => ({
          key,
          enabled,
          points,
        })),
        knockout_advance_points: pontuacao.value.knockout_advance_points,
        champion_points: pontuacao.value.champion_points,
        top4_points: pontuacao.value.top4_points,
        relegated_points: pontuacao.value.relegated_points,
      },
    })
    await recarregarPontuacao()
    mensagem.value = pontuacao.value.is_frozen
      ? 'Nova versão da pontuação criada. O histórico anterior foi preservado.'
      : 'Pontuação atualizada.'
  }
  catch (error_) {
    erro.value = (error_ as Error).message
  }
  finally {
    salvando.value = false
  }
}

async function salvarMultiplicador(rodada: Rodada) {
  erro.value = ''
  try {
    await apiFetch(`/v1/pools/${slug.value}/rounds/multiplier`, {
      method: 'PUT',
      body: { round_id: rodada.id, multiplier: rodada.multiplier },
    })
    await recarregarRodadas()
    mensagem.value = `${rodada.name} agora vale ${rodada.multiplier}×.`
  }
  catch (error_) {
    erro.value = (error_ as Error).message
  }
}

async function remover(membro: Membro) {
  if (!confirm(`Remover ${membro.display_name} do bolão?`)) return
  try {
    await apiFetch(`/v1/pools/${slug.value}/members/${membro.membership_id}`, {
      method: 'DELETE',
    })
    await recarregarMembros()
  }
  catch (error_) {
    erro.value = (error_ as Error).message
  }
}

async function reapurar() {
  if (!confirm('Recalcular a pontuação de todos os jogos deste bolão?')) return
  salvando.value = true
  try {
    const dados = await apiFetch<{ fixtures: number, settled: number }>(
      `/v1/pools/${slug.value}/recompute`,
      { method: 'POST' },
    )
    mensagem.value = `${dados.settled} de ${dados.fixtures} jogos reapurados.`
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
  <div>
    <NuxtLink
      :to="`/b/${slug}`"
      class="pequeno"
    >← voltar ao bolão</NuxtLink>
    <h1 style="margin-top: 0.5rem">
      Configurar
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

    <div
      v-if="pontuacao"
      class="card"
    >
      <div class="linha">
        <h2 style="margin: 0">
          Pontuação
        </h2>
        <span class="tag empurra">versão {{ pontuacao.version }}</span>
      </div>

      <p
        v-if="pontuacao.is_frozen"
        class="aviso aviso--atencao pequeno"
      >
        Esta configuração já pontuou palpite. Alterar agora cria uma
        <strong>versão nova</strong> — o que já foi apurado não é reescrito.
      </p>

      <div class="tabela-rolavel">
        <table>
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
              v-for="criterio in pontuacao.criteria"
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
                  :disabled="!criterio.enabled"
                  :aria-label="`Pontos de ${criterio.label}`"
                  style="text-align: center"
                >
              </td>
              <td class="centro">
                <input
                  v-model="criterio.enabled"
                  type="checkbox"
                  :aria-label="`Usar ${criterio.label}`"
                  style="width: auto"
                >
              </td>
            </tr>
          </tbody>
        </table>
      </div>

      <p class="fraco pequeno">
        <strong>Desempate:</strong> {{ pontuacao.tiebreak_order.join(' → ') }} → quem entrou antes.
      </p>

      <h3 style="margin-top: 1rem">
        Bônus
      </h3>
      <p class="fraco pequeno">
        Zero desliga. Bônus soma ao total mas não entra no desempate — este
        continua sendo decidido pelos palpites de placar.
      </p>
      <div class="bonus">
        <label>
          <span>Quem avança (mata-mata)</span>
          <input
            v-model.number="pontuacao.knockout_advance_points"
            type="number"
            min="0"
            max="100"
          >
        </label>
        <label>
          <span>Campeão</span>
          <input
            v-model.number="pontuacao.champion_points"
            type="number"
            min="0"
            max="100"
          >
        </label>
        <label>
          <span>G-4 (por acerto)</span>
          <input
            v-model.number="pontuacao.top4_points"
            type="number"
            min="0"
            max="100"
          >
        </label>
        <label>
          <span>Rebaixado (por acerto)</span>
          <input
            v-model.number="pontuacao.relegated_points"
            type="number"
            min="0"
            max="100"
          >
        </label>
      </div>

      <button
        type="button"
        class="btn btn--principal"
        :disabled="salvando"
        @click="salvarPontuacao"
      >
        Salvar pontuação
      </button>
    </div>

    <div
      v-if="rodadas?.length"
      class="card"
    >
      <h2>Multiplicadores</h2>
      <p class="fraco pequeno">
        Rodada decisiva pode valer o dobro ou o triplo.
      </p>
      <div class="pilha">
        <div
          v-for="rodada in rodadas"
          :key="rodada.id"
          class="linha multiplicador"
        >
          <span>{{ rodada.name }}</span>
          <div
            class="linha empurra"
            style="gap: 0.4rem"
          >
            <input
              v-model.number="rodada.multiplier"
              type="number"
              min="1"
              max="10"
              style="width: 4rem; text-align: center"
              :aria-label="`Multiplicador de ${rodada.name}`"
            >
            <button
              type="button"
              class="btn pequeno"
              @click="salvarMultiplicador(rodada)"
            >
              aplicar
            </button>
          </div>
        </div>
      </div>
    </div>

    <div
      v-if="membros?.length"
      class="card"
    >
      <h2>Participantes</h2>
      <div class="pilha">
        <div
          v-for="membro in membros"
          :key="membro.membership_id"
          class="linha"
        >
          <span>
            {{ membro.display_name }}
            <span
              v-if="membro.role !== 'player'"
              class="tag tag--verde"
            >{{ membro.role }}</span>
          </span>
          <button
            v-if="membro.role === 'player'"
            type="button"
            class="btn btn--perigo pequeno empurra"
            @click="remover(membro)"
          >
            remover
          </button>
        </div>
      </div>
    </div>

    <div class="card">
      <h2>Reapurar tudo</h2>
      <p class="fraco pequeno">
        Recalcula a pontuação de todos os jogos com a configuração vigente.
        A operação é idempotente: rodar duas vezes dá o mesmo resultado.
        Use depois de corrigir um placar antigo.
      </p>
      <button
        type="button"
        class="btn"
        :disabled="salvando"
        @click="reapurar"
      >
        Recalcular pontuação
      </button>
    </div>

    <div
      v-if="bolao?.kind === 'custom' && campeonatosAtivos.length"
      class="card"
    >
      <h2>Campeonatos da rodada</h2>
      <p class="fraco pequeno">
        Encurta a lista de onde você garimpa os jogos da semana. Sem nenhum
        marcado, a agenda mostra tudo que está importado. Mexer aqui
        <strong>não</strong> altera rodada já montada nem palpite de ninguém.
      </p>

      <div class="escolha">
        <label
          v-for="campeonato in campeonatosAtivos"
          :key="campeonato.id"
          class="chip"
          :class="{ 'chip--on': filtro.includes(campeonato.id) }"
        >
          <input
            type="checkbox"
            :checked="filtro.includes(campeonato.id)"
            @change="alternarCampeonato(campeonato.id)"
          >
          <span>{{ campeonato.name }}</span>
          <span class="fraco pequeno num">{{ campeonato.porVir }}</span>
        </label>
      </div>

      <button
        type="button"
        class="btn"
        style="margin-top: 0.9rem"
        :disabled="salvando"
        @click="salvarCampeonatos"
      >
        Salvar campeonatos
      </button>
    </div>

    <div
      v-if="bolao?.is_owner"
      class="card perigo"
    >
      <h2>Excluir bolão</h2>
      <p class="fraco pequeno">
        Apaga o bolão e tudo que pende dele: os palpites e a pontuação de todos
        os <strong>{{ bolao.members }}</strong> participantes, o ranking, as
        rodadas montadas e o código de convite.
        <strong>Não há lixeira e não dá para desfazer.</strong>
      </p>

      <div class="campo">
        <label for="confirmar">
          Para confirmar, escreva <strong>{{ bolao.name }}</strong>
        </label>
        <input
          id="confirmar"
          v-model="confirmacao"
          autocomplete="off"
          :placeholder="bolao.name"
        >
      </div>

      <button
        type="button"
        class="btn btn--perigo"
        :disabled="!podeExcluir || excluindo"
        @click="excluir"
      >
        {{ excluindo ? 'excluindo…' : 'Excluir este bolão para sempre' }}
      </button>
    </div>
  </div>
</template>

<style scoped>
.multiplicador { padding: 0.35rem 0; border-bottom: 1px solid var(--borda); }
.multiplicador:last-child { border-bottom: none; }

.bonus {
  display: grid;
  grid-template-columns: repeat(auto-fit, minmax(13rem, 1fr));
  gap: 0.6rem;
  margin-bottom: 1rem;
}

.bonus label { display: grid; gap: 0.25rem; font-size: 0.9rem; }
.bonus input { text-align: center; }

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

/* Zona de perigo: separada e marcada, para não se confundir com o resto. */
.perigo { border-color: var(--alerta); }
.perigo h2 { color: var(--alerta); }
</style>
