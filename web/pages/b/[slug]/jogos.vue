<script setup lang="ts">
definePageMeta({ middleware: 'auth' })

/**
 * Seleção de jogos do bolão.
 *
 * Num Brasileirão de 380 jogos ninguém quer marcar caixinha 380 vezes, então a
 * tela trabalha em dois níveis: liga/desliga a rodada inteira, e ajusta jogo a
 * jogo dentro dela. É exatamente o que a tabela `pool_fixtures` modela — ela
 * guarda só as exceções.
 */

const route = useRoute()
const slug = computed(() => route.params.slug as string)
const { kickoff } = useFormat()

interface Rodada {
  id: number
  number: number | null
  name: string
  multiplier: number
  fixtures: number
  settled: number
}
interface Time {
  id: number
  name: string
  short_name: string | null
  crest_url: string | null
}
interface Jogo {
  id: number
  kickoff_at: string
  home_team: Time
  away_team: Time
  is_included: boolean
  is_locked: boolean
  settled_at: string | null
}

const { data: rodadas } = useApiData<Rodada[]>(
  `sel-rodadas-${slug.value}`,
  () => `/v1/pools/${slug.value}/rounds`,
)

const rodadaAberta = ref<number | null>(null)
const jogos = ref<Jogo[]>([])
const carregandoJogos = ref(false)
const erro = ref('')
const mensagem = ref('')

useHead({ title: 'Jogos do bolão' })

watch(rodadas, (lista) => {
  if (rodadaAberta.value === null && lista?.length) abrir(lista[0]!.id)
}, { immediate: true })

async function abrir(roundId: number) {
  rodadaAberta.value = roundId
  carregandoJogos.value = true
  erro.value = ''
  try {
    // `todos=true` traz também os jogos desligados — é a tela em que eles
    // precisam aparecer para poderem ser religados.
    jogos.value = await apiFetch<Jogo[]>(
      `/v1/pools/${slug.value}/rounds/${roundId}/fixtures?todos=true`,
    )
  }
  catch (error_) {
    erro.value = (error_ as Error).message
    jogos.value = []
  }
  finally {
    carregandoJogos.value = false
  }
}

async function alternarJogo(jogo: Jogo) {
  erro.value = ''
  mensagem.value = ''
  const alvo = !jogo.is_included
  try {
    await apiFetch(`/v1/pools/${slug.value}/fixtures/selection`, {
      method: 'PUT',
      body: { fixture_ids: [jogo.id], included: alvo },
    })
    jogo.is_included = alvo
    mensagem.value = alvo ? 'Jogo incluído.' : 'Jogo excluído do bolão.'
  }
  catch (error_) {
    erro.value = (error_ as Error).message
  }
}

async function alternarRodada(included: boolean) {
  if (rodadaAberta.value === null) return
  erro.value = ''
  mensagem.value = ''
  try {
    const resposta = await apiFetch<{ detail: string }>(
      `/v1/pools/${slug.value}/fixtures/selection`,
      { method: 'PUT', body: { round_id: rodadaAberta.value, included } },
    )
    mensagem.value = resposta.detail
    await abrir(rodadaAberta.value)
  }
  catch (error_) {
    erro.value = (error_ as Error).message
  }
}

const incluidosNaRodada = computed(() => jogos.value.filter(jogo => jogo.is_included).length)
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
      Jogos do bolão
    </h1>
    <p class="fraco pequeno">
      Escolha o que vale pontos. Jogo desligado some da tela de palpite e não
      entra no ranking — e se já tinha sido apurado, reapure o bolão depois para
      tirar os pontos dele.
    </p>

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

    <div class="colunas">
      <nav
        class="card rodadas"
        aria-label="Rodadas"
      >
        <button
          v-for="rodada in rodadas"
          :key="rodada.id"
          type="button"
          class="rodada"
          :class="{ 'rodada--ativa': rodada.id === rodadaAberta }"
          @click="abrir(rodada.id)"
        >
          <span>{{ rodada.name }}</span>
          <span
            v-if="rodada.multiplier > 1"
            class="tag tag--verde"
          >{{ rodada.multiplier }}x</span>
        </button>
      </nav>

      <div class="card jogos">
        <div class="linha">
          <strong v-if="jogos.length">
            {{ incluidosNaRodada }} de {{ jogos.length }} valendo
          </strong>
          <div class="linha empurra">
            <button
              type="button"
              class="btn pequeno"
              @click="alternarRodada(true)"
            >
              incluir tudo
            </button>
            <button
              type="button"
              class="btn pequeno"
              @click="alternarRodada(false)"
            >
              excluir tudo
            </button>
          </div>
        </div>

        <p
          v-if="carregandoJogos"
          class="vazio"
        >
          carregando…
        </p>

        <ul
          v-else-if="jogos.length"
          class="lista"
        >
          <li
            v-for="jogo in jogos"
            :key="jogo.id"
            :class="{ lista__fora: !jogo.is_included }"
          >
            <label class="jogo">
              <input
                type="checkbox"
                :checked="jogo.is_included"
                :aria-label="`Incluir ${jogo.home_team.name} contra ${jogo.away_team.name}`"
                @change="alternarJogo(jogo)"
              >
              <EscudoTime
                :nome="jogo.home_team.name"
                :sigla="jogo.home_team.short_name"
                :escudo="jogo.home_team.crest_url"
                :tamanho="30"
              />
              <span class="jogo__nome">{{ jogo.home_team.name }}</span>
              <span class="fraco">×</span>
              <EscudoTime
                :nome="jogo.away_team.name"
                :sigla="jogo.away_team.short_name"
                :escudo="jogo.away_team.crest_url"
                :tamanho="30"
              />
              <span class="jogo__nome">{{ jogo.away_team.name }}</span>
              <span class="fraco pequeno num empurra hora">{{ kickoff(jogo.kickoff_at) }}</span>
              <span
                v-if="jogo.settled_at"
                class="tag"
              >apurado</span>
            </label>
          </li>
        </ul>

        <p
          v-else
          class="vazio"
        >
          Esta rodada não tem jogos.
        </p>
      </div>
    </div>
  </div>
</template>

<style scoped>
.colunas { display: grid; grid-template-columns: 12rem 1fr; gap: 0.75rem; align-items: start; }

.rodadas {
  max-height: 34rem;
  overflow-y: auto;
  padding: 0.4rem;
  display: grid;
  gap: 0.1rem;
}

.rodada {
  font: inherit;
  text-align: left;
  padding: 0.4rem 0.6rem;
  border: none;
  border-radius: var(--raio-p);
  background: transparent;
  color: inherit;
  cursor: pointer;
  display: flex;
  align-items: center;
  gap: 0.4rem;
}

.rodada:hover { background: var(--superficie-2); }
.rodada--ativa { background: var(--verde-fraco); color: var(--verde); font-weight: 700; }

.lista { list-style: none; padding: 0; margin: 0.5rem 0 0; }
.lista li { border-bottom: 1px solid var(--borda); }
.lista li:last-child { border-bottom: none; }
.lista__fora { opacity: 0.45; }

.jogo {
  display: flex;
  align-items: center;
  gap: 0.4rem;
  padding: 0.45rem 0.2rem;
  cursor: pointer;
}

.jogo { min-width: 0; }

/* Alvo de toque: a caixa de marcar padrão tem 13px e é o controle principal
   desta tela. O rótulo inteiro já é clicável; isto garante a altura mínima. */
.jogo input { width: auto; min-width: 20px; min-height: 20px; margin-right: 0.3rem; }
.jogo__nome { white-space: nowrap; overflow: hidden; text-overflow: ellipsis; min-width: 0; }

@media (width <= 700px) {
  .colunas { grid-template-columns: 1fr; }
  .rodadas { max-height: 9rem; grid-auto-flow: column; grid-auto-columns: max-content; overflow-x: auto; }

  /* O nome do clube FICA. Ele sumia aqui e sobrava escudo × escudo — e clube
     recém-importado ainda não tem escudo, então sobrava uma sigla de 7px como
     única identificação da partida. Some a hora, que se repete no grupo do dia.
     A tela de montar rodada já tinha essa correção; ela não tinha chegado aqui. */
  .jogo__nome { font-size: 0.85rem; }
  .hora { display: none; }
}
</style>
