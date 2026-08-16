<script setup lang="ts">
definePageMeta({ middleware: 'auth' })

/** Importar campeonato é tela restrita; o link só faz sentido para quem pode. */
const { pode } = usePermissoes()
const administra = computed(() => pode('campeonatos.importar'))

/**
 * Montagem da rodada da semana.
 *
 * O organizador escolhe uma janela de datas e marca os jogos, de qualquer
 * campeonato importado. A lista da esquerda são as rodadas do bolão; a da
 * direita, a agenda de onde ele puxa.
 */

const route = useRoute()
const slug = computed(() => route.params.slug as string)
const { kickoff, diaChave, diaExtenso } = useFormat()

interface Rodada {
  id: number
  number: number
  name: string
  multiplier: number
  fixtures: number
  settled: number
  opens_at: string | null
  is_open: boolean
}
interface TimeAgenda {
  id: number
  name: string
  short_name: string | null
  crest_url: string | null
}
interface JogoAgenda {
  id: number
  kickoff_at: string
  competition: string
  competition_id: number
  round: string | null
  home_team: TimeAgenda
  away_team: TimeAgenda
}

const { data: rodadas, refresh: recarregarRodadas } = useApiData<Rodada[]>(
  `md-${slug.value}`,
  () => `/v1/pools/${slug.value}/matchdays`,
)

const selecionada = ref<number | null>(null)
const escolhidos = ref<Set<number>>(new Set())
const erro = ref('')
const mensagem = ref('')
const salvando = ref(false)

interface Janela {
  next_fixture_at: string | null
  suggested_from: string | null
  suggested_to: string | null
  total_upcoming: number
  competitions: string[]
}

const hoje = new Date().toISOString().slice(0, 10)

/**
 * A janela abre onde os jogos realmente estão.
 *
 * "Hoje + 7 dias" é uma escolha ruim: campeonato tem pausa, e a janela cai
 * exatamente entre duas rodadas. A tela abria vazia e parecia quebrada —
 * quando o próximo jogo era só três dias depois.
 *
 * Carregada no SSR para a primeira pintura já vir com a agenda certa.
 */
const { data: janela } = await useApiData<Janela>(
  'agenda-janela',
  '/v1/catalog/agenda/janela',
)

const de = ref(janela.value?.suggested_from ?? hoje)
const ate = ref(
  janela.value?.suggested_to ?? new Date(Date.now() + 7 * 86400_000).toISOString().slice(0, 10),
)
const filtroCompeticao = ref<number | null>(null)

/**
 * Campeonatos escolhidos na criação do bolão.
 *
 * Carregado com `await` de propósito: o endereço da agenda depende dele, e sem
 * esperar o filtro chegaria depois da busca — que foi exatamente o bug da lista
 * de rodadas. Lista vazia significa "todos", não "nenhum".
 */
const { data: bolao } = await useApiData<{ competition_ids: number[] }>(
  `filtro-${slug.value}`,
  () => `/v1/pools/${slug.value}`,
)

const doBolao = computed(() => bolao.value?.competition_ids ?? [])
const soDoBolao = ref(true)

const filtroAtivo = computed(() =>
  soDoBolao.value && doBolao.value.length ? doBolao.value.join(',') : '',
)

// O `watch` recarrega sozinho quando o organizador mexe nas datas ou no
// filtro — não há handler de change no template.
const { data: agendaBruta, pending: carregandoAgenda } = await useApiData<JogoAgenda[]>(
  `agenda-${slug.value}`,
  () => `/v1/catalog/agenda?de=${de.value}&ate=${ate.value}`
    + (filtroAtivo.value ? `&competicoes=${filtroAtivo.value}` : ''),
  { watch: [de, ate, filtroAtivo] },
)

const agenda = computed(() => agendaBruta.value ?? [])

useHead({ title: 'Montar rodada' })

watch(rodadas, (lista) => {
  if (selecionada.value === null && lista?.length) abrir(lista[0]!.id)
}, { immediate: true })

function irParaOProximoJogo() {
  if (!janela.value?.suggested_from || !janela.value.suggested_to) return
  // O watch em [de, ate] recarrega a agenda sozinho.
  de.value = janela.value.suggested_from
  ate.value = janela.value.suggested_to
}

/** Data do próximo jogo, já no fuso de quem lê. */
const proximoJogoEm = computed(() => {
  const iso = janela.value?.next_fixture_at
  return iso ? kickoff(iso) : null
})

async function abrir(matchdayId: number) {
  selecionada.value = matchdayId
  erro.value = ''
  try {
    const jogos = await apiFetch<{ id: number }[]>(
      `/v1/pools/${slug.value}/matchdays/${matchdayId}/fixtures`,
    )
    escolhidos.value = new Set(jogos.map(jogo => jogo.id))
  }
  catch (error_) {
    erro.value = (error_ as Error).message
  }
}

async function novaRodada() {
  erro.value = ''
  mensagem.value = ''
  try {
    const rodada = await apiFetch<Rodada>(`/v1/pools/${slug.value}/matchdays`, {
      method: 'POST',
      body: { name: null, multiplier: 1 },
    })
    await recarregarRodadas()
    await abrir(rodada.id)
    mensagem.value = `${rodada.name} criada. Agora escolha os jogos.`
  }
  catch (error_) {
    erro.value = (error_ as Error).message
  }
}

async function apagarRodada(rodada: Rodada) {
  if (!confirm(`Apagar ${rodada.name}? Os palpites dela vão junto.`)) return
  try {
    await apiFetch(`/v1/pools/${slug.value}/matchdays/${rodada.id}`, { method: 'DELETE' })
    selecionada.value = null
    escolhidos.value = new Set()
    await recarregarRodadas()
  }
  catch (error_) {
    erro.value = (error_ as Error).message
  }
}

function alternar(fixtureId: number) {
  const copia = new Set(escolhidos.value)
  if (copia.has(fixtureId)) copia.delete(fixtureId)
  else copia.add(fixtureId)
  escolhidos.value = copia
}

async function salvar() {
  if (selecionada.value === null) return
  erro.value = ''
  mensagem.value = ''
  salvando.value = true
  try {
    const resposta = await apiFetch<{
      added: number[]
      removed: number[]
      rejected: { reason: string }[]
      total: number
    }>(`/v1/pools/${slug.value}/matchdays/${selecionada.value}/fixtures`, {
      method: 'PUT',
      body: { fixture_ids: [...escolhidos.value] },
    })
    mensagem.value = `Rodada com ${resposta.total} jogo(s).`
    if (resposta.rejected.length) {
      erro.value = `${resposta.rejected.length} recusado(s): ${resposta.rejected[0]?.reason}`
    }
    await recarregarRodadas()
  }
  catch (error_) {
    erro.value = (error_ as Error).message
  }
  finally {
    salvando.value = false
  }
}

const competicoes = computed(() => {
  const mapa = new Map<number, string>()
  for (const jogo of agenda.value) mapa.set(jogo.competition_id, jogo.competition)
  return [...mapa.entries()].map(([id, nome]) => ({ id, nome }))
})

const agendaFiltrada = computed(() =>
  filtroCompeticao.value === null
    ? agenda.value
    : agenda.value.filter(jogo => jogo.competition_id === filtroCompeticao.value),
)

/** Agrupa por dia no fuso do usuário — nunca pelo UTC. */
const porDia = computed(() => {
  const grupos = new Map<string, JogoAgenda[]>()
  for (const jogo of agendaFiltrada.value) {
    const chave = diaChave(jogo.kickoff_at)
    if (!grupos.has(chave)) grupos.set(chave, [])
    grupos.get(chave)!.push(jogo)
  }
  return [...grupos.entries()].sort(([a], [b]) => a.localeCompare(b))
})
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
      Montar rodada
    </h1>
    <p class="fraco pequeno">
      Escolha os jogos da semana. Podem ser de campeonatos diferentes — o que
      define a rodada é a sua seleção, não a tabela de ninguém.
      <template v-if="janela?.competitions.length">
        Disponíveis agora: {{ janela.competitions.join(', ') }}.
      </template>
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
      <aside class="card lateral">
        <div class="linha">
          <strong>Rodadas</strong>
          <button
            type="button"
            class="btn pequeno empurra"
            @click="novaRodada"
          >
            + nova
          </button>
        </div>

        <p
          v-if="!rodadas?.length"
          class="fraco pequeno"
        >
          Nenhuma rodada ainda.
        </p>

        <button
          v-for="rodada in rodadas"
          :key="rodada.id"
          type="button"
          class="rodada"
          :class="{ 'rodada--ativa': rodada.id === selecionada }"
          @click="abrir(rodada.id)"
        >
          <span class="rodada__nome">{{ rodada.name }}</span>
          <span class="fraco pequeno num">{{ rodada.fixtures }}</span>
        </button>

        <button
          v-if="selecionada"
          type="button"
          class="btn btn--perigo pequeno"
          style="margin-top: 0.5rem"
          @click="apagarRodada(rodadas!.find(r => r.id === selecionada)!)"
        >
          apagar esta rodada
        </button>
      </aside>

      <section class="card">
        <div
          v-if="selecionada === null"
          class="vazio"
        >
          Crie uma rodada para começar a escolher os jogos.
        </div>

        <template v-else>
          <div class="filtros">
            <div class="campo">
              <label for="de">De</label>
              <input
                id="de"
                v-model="de"
                type="date"
              >
            </div>
            <div class="campo">
              <label for="ate">Até</label>
              <input
                id="ate"
                v-model="ate"
                type="date"
              >
            </div>
            <div
              class="campo"
              style="flex: 1; min-width: 12rem"
            >
              <label for="comp">Campeonato</label>
              <select
                id="comp"
                v-model="filtroCompeticao"
              >
                <option :value="null">
                  todos
                </option>
                <option
                  v-for="competicao in competicoes"
                  :key="competicao.id"
                  :value="competicao.id"
                >
                  {{ competicao.nome }}
                </option>
              </select>
            </div>
          </div>

          <label
            v-if="doBolao.length"
            class="linha pequeno"
            style="margin-bottom: 0.6rem; gap: 0.5rem"
          >
            <input
              v-model="soDoBolao"
              type="checkbox"
              style="width: auto"
            >
            <span>
              Só os {{ doBolao.length }} campeonatos deste bolão
              <span class="fraco">— desmarque para puxar de qualquer um</span>
            </span>
          </label>

          <p
            v-if="carregandoAgenda"
            class="vazio"
          >
            carregando agenda…
          </p>

          <div
            v-else-if="!agendaFiltrada.length"
            class="vazio"
          >
            <template v-if="proximoJogoEm">
              <p>
                <strong>Nenhum jogo entre essas datas.</strong>
              </p>
              <p>
                O próximo é <strong>{{ proximoJogoEm }}</strong> — campeonato
                costuma ter pausa entre rodadas.
              </p>
              <button
                type="button"
                class="btn btn--principal"
                @click="irParaOProximoJogo"
              >
                Ir para o próximo jogo
              </button>
            </template>

            <template v-else-if="janela && janela.total_upcoming === 0">
              <p>
                <strong>Não há nenhum jogo futuro importado.</strong>
              </p>
              <!-- Importar é tela de administração. Mandar para lá quem não
                   administra dá uma porta que não abre — melhor dizer a quem
                   pedir. -->
              <p
                v-if="administra"
                class="pequeno"
              >
                As temporadas que você importou já terminaram. Importe uma nova
                em
                <NuxtLink to="/campeonatos">
                  Campeonatos
                </NuxtLink>.
              </p>
              <p
                v-else
                class="pequeno"
              >
                As temporadas importadas já terminaram. Peça a quem administra
                a plataforma para importar um campeonato novo.
              </p>
            </template>

            <template v-else>
              <p>Nenhum jogo nesse período. Amplie as datas.</p>
            </template>
          </div>

          <template v-else>
            <div
              v-for="[dia, jogosDoDia] in porDia"
              :key="dia"
            >
              <h3 class="dia">
                {{ diaExtenso(dia) }}
              </h3>
              <ul class="lista">
                <li
                  v-for="jogo in jogosDoDia"
                  :key="jogo.id"
                >
                  <label
                    class="jogo"
                    :class="{ 'jogo--escolhido': escolhidos.has(jogo.id) }"
                  >
                    <input
                      type="checkbox"
                      :checked="escolhidos.has(jogo.id)"
                      :aria-label="`${jogo.home_team.name} contra ${jogo.away_team.name}`"
                      @change="alternar(jogo.id)"
                    >
                    <span class="hora num">{{ kickoff(jogo.kickoff_at).slice(-5) }}</span>

                    <span class="lado lado--casa">
                      <span class="jogo__nome">{{ jogo.home_team.name }}</span>
                      <EscudoTime
                        :nome="jogo.home_team.name"
                        :sigla="jogo.home_team.short_name"
                        :escudo="jogo.home_team.crest_url"
                        :tamanho="30"
                      />
                    </span>

                    <span class="versus">×</span>

                    <span class="lado lado--fora">
                      <EscudoTime
                        :nome="jogo.away_team.name"
                        :sigla="jogo.away_team.short_name"
                        :escudo="jogo.away_team.crest_url"
                        :tamanho="30"
                      />
                      <span class="jogo__nome">{{ jogo.away_team.name }}</span>
                    </span>

                    <span class="tag campeonato">{{ jogo.competition }}</span>
                  </label>
                </li>
              </ul>
            </div>

            <div class="barra">
              <strong>{{ escolhidos.size }}</strong> jogo(s) escolhido(s)
              <button
                type="button"
                class="btn btn--principal empurra"
                :disabled="salvando"
                @click="salvar"
              >
                {{ salvando ? 'salvando…' : 'Salvar rodada' }}
              </button>
            </div>
          </template>
        </template>
      </section>
    </div>
  </div>
</template>

<style scoped>
.colunas { display: grid; grid-template-columns: 15rem 1fr; gap: var(--e4); align-items: start; }
.lateral { display: grid; gap: var(--e1); align-content: start; max-height: 40rem; overflow-y: auto; }

.rodada {
  font: inherit;
  text-align: left;
  padding: 0.55rem 0.7rem;
  border: 1px solid transparent;
  border-radius: var(--raio-p);
  background: transparent;
  color: inherit;
  cursor: pointer;
  display: flex;
  align-items: center;
  gap: var(--e2);
}

.rodada:hover { background: var(--superficie-2); }

.rodada--ativa {
  background: var(--verde-fraco);
  border-color: var(--verde-borda);
  color: var(--verde);
  font-weight: 700;
}

.rodada__nome { flex: 1; overflow: hidden; text-overflow: ellipsis; white-space: nowrap; }

.filtros { display: flex; gap: var(--e3); flex-wrap: wrap; align-items: flex-end; }
.filtros .campo { margin-bottom: var(--e2); }

.dia {
  display: flex;
  align-items: center;
  gap: var(--e3);
  font-size: 0.72rem;
  text-transform: uppercase;
  letter-spacing: 0.08em;
  font-weight: 700;
  color: var(--texto-fraco);
  margin: var(--e5) 0 var(--e2);
}

.dia::after { content: ''; flex: 1; height: 1px; background: var(--borda); }

.lista { list-style: none; padding: 0; margin: 0; display: grid; gap: var(--e1); }

/* Linha de jogo em grade fixa: os escudos alinham em coluna de cima a baixo,
   em vez de dançarem conforme o tamanho do nome do clube. */
.jogo {
  display: grid;
  grid-template-columns: auto 3rem 1fr auto 1fr auto;
  align-items: center;
  gap: var(--e3);
  padding: 0.5rem 0.7rem;
  border: 1px solid transparent;
  border-radius: var(--raio-p);
  cursor: pointer;
  transition: background-color 0.12s ease, border-color 0.12s ease;
}

.jogo:hover { background: var(--superficie-2); }

.jogo--escolhido {
  background: var(--verde-fraco);
  border-color: var(--verde-borda);
}

.jogo--escolhido:hover { background: var(--verde-fraco); }
.jogo input { width: auto; }
.hora { font-size: 0.85rem; color: var(--texto-fraco); font-weight: 650; }

.lado { display: flex; align-items: center; gap: var(--e2); min-width: 0; }
.lado--casa { justify-content: flex-end; text-align: right; }
.lado--fora { justify-content: flex-start; text-align: left; }

.jogo__nome {
  white-space: nowrap;
  overflow: hidden;
  text-overflow: ellipsis;
  font-weight: 600;
  font-size: 0.92rem;
}

.versus { color: var(--texto-fraco); font-weight: 700; font-size: 0.85rem; }
.campeonato { max-width: 11rem; overflow: hidden; text-overflow: ellipsis; display: block; }

.barra {
  position: sticky;
  bottom: 0;
  display: flex;
  align-items: center;
  gap: var(--e3);
  /* Mesma razão da barra da tela de palpite: `viewport-fit=cover` deixa o
     conteúdo chegar até a borda, e a barra de gestos fica por cima. */
  padding: var(--e3) 0 calc(var(--e1) + env(safe-area-inset-bottom, 0px));
  margin-top: var(--e3);
  border-top: 1px solid var(--borda);
  background: linear-gradient(to top, var(--superficie) 75%, transparent);
}

@media (width <= 900px) {
  .campeonato { display: none; }
  .jogo { grid-template-columns: auto 3rem 1fr auto 1fr; }
}

@media (width <= 760px) {
  .colunas { grid-template-columns: 1fr; }

  .lateral {
    max-height: none;
    grid-auto-flow: column;
    grid-auto-columns: max-content;
    overflow-x: auto;
    padding-bottom: var(--e1);
  }
}

@media (width <= 560px) {
  /* Nome do clube continua visível — antes ele sumia no celular, e escolher
     jogo só pelo escudo é adivinhação. Some a hora, que se repete no grupo. */
  .jogo { grid-template-columns: auto 1fr auto 1fr; gap: var(--e2); padding: 0.5rem 0.4rem; }
  .hora { display: none; }
  .jogo__nome { font-size: 0.85rem; }
}
</style>
