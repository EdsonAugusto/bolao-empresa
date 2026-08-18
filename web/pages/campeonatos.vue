<script setup lang="ts">
definePageMeta({ middleware: ['auth', 'admin'] })

/**
 * Cadastro de campeonato.
 *
 * O caminho principal é o CSV: o organizador copia a tabela do campeonato,
 * cola aqui e pronto. Sem chave de API, sem cota, sem depender de serviço
 * nenhum — que é o que faz a plataforma custar zero e funcionar offline.
 */

interface Temporada {
  id: number
  year: number
  label: string
  competition: string
  is_current: boolean
  rounds: number
  fixtures: number
  teams: number
  upcoming: number
  next_fixture_at: string | null
}

interface Liga {
  slug: string
  name: string
  country: string
  rounds: number
  fixtures: number
  first_round: string
  season_year: number
  verified_at: string
  imported: boolean
}

interface Provedor { slug: string, name: string, ready: boolean, detail: string }
interface Modelo { columns: string[], template: string, notes: string[] }

const { kickoff } = useFormat()

const { data: temporadas, refresh } = useApiData<Temporada[]>('cat-temporadas', '/v1/catalog/seasons')
const { data: provedores } = useApiData<Provedor[]>('cat-provedores', '/v1/catalog/providers')
const { data: modelo } = useApiData<Modelo>('cat-modelo', '/v1/catalog/manual/template')

const nomeCampeonato = ref('')
const ano = ref(new Date().getFullYear())
const csv = ref('')
const erro = ref('')
const resultado = ref('')
const importando = ref(false)

const anoBrasileirao = ref(new Date().getFullYear())
const criandoBrasileirao = ref(false)
const importandoReal = ref(false)

interface Preset {
  slug: string
  name: string
  country: string
  rounds: number
  season_year: number
  verified_at: string
}
interface GeInfo { presets: Preset[], how_to_find: string[] }

const { data: geInfo } = useApiData<GeInfo>('ge-competicoes', '/v1/catalog/ge-competitions')
const importandoPreset = ref('')

const { data: ligas, refresh: recarregarLigas } = useApiData<Liga[]>('ligas', '/v1/catalog/ligas')
const importandoLiga = ref('')

interface Copa {
  slug: string
  name: string
  country: string
  article: string
  year: number
  verified_at: string
  imported: boolean
}

interface Escudos {
  with_crest: number
  missing: number
  by_competition: Record<string, number>
  fast_path: boolean
  hint: string
}

const { data: escudos, refresh: recarregarEscudos }
  = useApiData<Escudos>('escudos', '/v1/catalog/escudos')
const buscandoEscudos = ref(false)

async function buscarEscudos() {
  erro.value = ''
  resultado.value = ''
  buscandoEscudos.value = true
  try {
    const dados = await apiFetch<{
      filled_local: number
      filled_remote: number
      queued?: boolean
      note?: string
    }>('/v1/catalog/escudos', { method: 'POST', body: { remote: true } })

    const achados = dados.filled_local + dados.filled_remote
    resultado.value = dados.queued
      ? `${achados} escudos preenchidos na hora. ${dados.note}`
      : `${achados} escudos preenchidos.`
    await Promise.all([recarregarEscudos(), refresh()])
  }
  catch (error_) {
    erro.value = (error_ as Error).message
  }
  finally {
    buscandoEscudos.value = false
  }
}

interface CopaEspn {
  slug: string
  name: string
  country: string
  year: number
  label: string
  verified_at: string
  imported: boolean
}

const { data: copasEspn, refresh: recarregarCopasEspn }
  = useApiData<CopaEspn[]>('copas', '/v1/catalog/copas')
const importandoCopaEspn = ref('')

async function importarCopaEspn(copa: CopaEspn) {
  erro.value = ''
  resultado.value = ''
  importandoCopaEspn.value = copa.slug
  try {
    const dados = await apiFetch<{
      teams: number
      fixtures: number
      phases: string[]
      note: string
    }>(
      '/v1/catalog/copas/import',
      { method: 'POST', body: { slug: copa.slug } },
    )
    resultado.value
      = `${copa.name}: ${dados.fixtures} jogos e ${dados.teams} clubes `
        + `(${dados.phases.join(', ')}). ${dados.note}`
    await Promise.all([refresh(), recarregarCopasEspn()])
  }
  catch (error_) {
    erro.value = (error_ as Error).message
  }
  finally {
    importandoCopaEspn.value = ''
  }
}

const { data: copas, refresh: recarregarCopas }
  = useApiData<Copa[]>('mata-matas', '/v1/catalog/mata-matas')
const importandoCopa = ref('')

async function importarCopa(copa: Copa) {
  erro.value = ''
  resultado.value = ''
  importandoCopa.value = copa.slug
  try {
    const dados = await apiFetch<{ teams: number, fixtures: number, phases: string[] }>(
      '/v1/catalog/mata-matas/import',
      { method: 'POST', body: { slug: copa.slug } },
    )
    resultado.value
      = `${copa.name}: ${dados.fixtures} jogos (${dados.phases.join(', ')}).`
    await Promise.all([refresh(), recarregarCopas()])
  }
  catch (error_) {
    erro.value = (error_ as Error).message
  }
  finally {
    importandoCopa.value = ''
  }
}

async function importarLiga(liga: Liga) {
  erro.value = ''
  resultado.value = ''
  importandoLiga.value = liga.slug
  try {
    const dados = await apiFetch<{ teams: number, fixtures: number, played: number }>(
      '/v1/catalog/ligas/import',
      { method: 'POST', body: { slug: liga.slug } },
    )
    resultado.value
      = `${liga.name}: ${dados.teams} times e ${dados.fixtures} jogos importados.`
    await Promise.all([refresh(), recarregarLigas()])
  }
  catch (error_) {
    erro.value = (error_ as Error).message
  }
  finally {
    importandoLiga.value = ''
  }
}

// Cadastro de campeonato que não está nos presets.
const manualNome = ref('')
const manualPais = ref('')
const manualTabela = ref('')
const manualFase = ref('')
const manualRodadas = ref(38)
const manualAno = ref(new Date().getFullYear())
const importandoManual = ref(false)

/**
 * Descoberta automática.
 *
 * O GE não tem catálogo público: o identificador da tabela é um UUID embutido
 * no HTML, misturado com dezenas de ids de matéria. Achar à mão é inviável —
 * aqui você cola o endereço e a plataforma testa os candidatos.
 */
interface Achado {
  table_id: string
  phase: string
  fixtures: number
  upcoming: number
  rounds_found: number
  sample: string[]
}

const urlDescoberta = ref('https://ge.globo.com/futebol/')
const descobrindo = ref(false)
const achados = ref<Achado[]>([])

async function descobrir() {
  erro.value = ''
  resultado.value = ''
  achados.value = []
  descobrindo.value = true
  try {
    achados.value = await apiFetch<Achado[]>('/v1/catalog/ge-discover', {
      method: 'POST',
      body: { url: urlDescoberta.value.trim() },
    })
  }
  catch (error_) {
    erro.value = (error_ as Error).message
  }
  finally {
    descobrindo.value = false
  }
}

function usarAchado(achado: Achado) {
  manualTabela.value = achado.table_id
  manualFase.value = achado.phase
  const ano = achado.phase.match(/(\d{4})$/)
  if (ano) manualAno.value = Number(ano[1])
}

async function importarPreset(preset: Preset) {
  erro.value = ''
  resultado.value = ''
  importandoPreset.value = preset.slug
  try {
    const dados = await apiFetch<{ teams: number, fixtures: number, played: number }>(
      '/v1/catalog/ge-import',
      { method: 'POST', body: { slug: preset.slug } },
    )
    resultado.value
      = `${preset.name}: ${dados.teams} times, ${dados.fixtures} jogos, `
        + `${dados.played} já com placar.`
    await refresh()
  }
  catch (error_) {
    erro.value = (error_ as Error).message
  }
  finally {
    importandoPreset.value = ''
  }
}

async function importarManual() {
  erro.value = ''
  resultado.value = ''
  importandoManual.value = true
  try {
    const dados = await apiFetch<{ teams: number, fixtures: number, played: number }>(
      '/v1/catalog/ge-import',
      {
        method: 'POST',
        body: {
          name: manualNome.value,
          country: manualPais.value.trim() || null,
          table_id: manualTabela.value.trim(),
          phase: manualFase.value.trim(),
          rounds: manualRodadas.value,
          year: manualAno.value,
        },
      },
    )
    resultado.value
      = `${manualNome.value}: ${dados.teams} times, ${dados.fixtures} jogos, `
        + `${dados.played} já com placar.`
    await refresh()
  }
  catch (error_) {
    erro.value = (error_ as Error).message
  }
  finally {
    importandoManual.value = false
  }
}

async function importarReal() {
  erro.value = ''
  resultado.value = ''
  importandoReal.value = true
  try {
    const dados = await apiFetch<{
      teams: number
      fixtures: number
      played: number
      undated: string[]
    }>('/v1/catalog/brasileirao/importar-real', {
      method: 'POST',
      body: { year: anoBrasileirao.value },
    })
    resultado.value
      = `Brasileirão ${anoBrasileirao.value} importado do GE: ${dados.teams} clubes, `
        + `${dados.fixtures} jogos, ${dados.played} já com placar.`
        + (dados.undated.length
          ? ` ${dados.undated.length} jogo(s) ainda sem data marcada ficaram de fora `
          + 'e entram na próxima importação.'
          : '')
    await refresh()
  }
  catch (error_) {
    erro.value = (error_ as Error).message
  }
  finally {
    importandoReal.value = false
  }
}

async function criarBrasileirao() {
  erro.value = ''
  resultado.value = ''
  criandoBrasileirao.value = true
  try {
    const dados = await apiFetch<{
      teams: number
      rounds: number
      fixtures: number
      note: string
    }>('/v1/catalog/brasileirao', {
      method: 'POST',
      body: { year: anoBrasileirao.value },
    })
    resultado.value
      = `Brasileirão ${anoBrasileirao.value}: ${dados.teams} clubes, `
        + `${dados.rounds} rodadas, ${dados.fixtures} jogos. ${dados.note}`
    await refresh()
  }
  catch (error_) {
    erro.value = (error_ as Error).message
  }
  finally {
    criandoBrasileirao.value = false
  }
}

useHead({ title: 'Campeonatos' })

watch(modelo, (valor) => {
  if (valor && !csv.value) csv.value = valor.template
}, { immediate: true })

async function importar() {
  erro.value = ''
  resultado.value = ''
  importando.value = true
  try {
    const dados = await apiFetch<{ teams: number, fixtures: number, settled_now: number }>(
      '/v1/catalog/manual/import',
      {
        method: 'POST',
        body: {
          competition_name: nomeCampeonato.value,
          year: ano.value,
          csv: csv.value,
          timezone_offset_hours: -3,
        },
      },
    )
    resultado.value
      = `${dados.fixtures} jogos e ${dados.teams} times importados.`
        + (dados.settled_now ? ` ${dados.settled_now} já apurados.` : '')
    await refresh()
  }
  catch (error_) {
    erro.value = (error_ as Error).message
  }
  finally {
    importando.value = false
  }
}
</script>

<template>
  <div>
    <h1>Campeonatos</h1>

    <div
      v-if="temporadas?.length"
      class="pilha"
      style="margin-bottom: 1.5rem"
    >
      <div
        v-for="temporada in temporadas"
        :key="temporada.id"
        class="card linha"
      >
        <div>
          <strong>{{ temporada.competition }} {{ temporada.label }}</strong>
          <div class="fraco pequeno">
            {{ temporada.rounds }} rodadas · {{ temporada.fixtures }} jogos ·
            {{ temporada.teams }} times
            <template v-if="temporada.upcoming">
              · próximo jogo {{ kickoff(temporada.next_fixture_at!) }}
            </template>
          </div>
        </div>
        <span
          v-if="temporada.upcoming"
          class="tag tag--verde empurra"
        >{{ temporada.upcoming }} jogos por vir</span>
        <span
          v-else
          class="tag empurra"
        >temporada encerrada</span>
      </div>
    </div>

    <div
      v-else
      class="card vazio"
    >
      Nenhum campeonato cadastrado. Importe um abaixo para poder criar bolões.
    </div>

    <div class="card">
      <h2>Brasileirão Série A</h2>

      <div
        class="campo"
        style="width: 8rem"
      >
        <label for="ano-br">Ano</label>
        <input
          id="ano-br"
          v-model.number="anoBrasileirao"
          type="number"
          min="2000"
          max="2100"
        >
      </div>

      <div class="opcoes">
        <div class="opcao">
          <h3>Tabela real, do GE Globo</h3>
          <p class="fraco pequeno">
            Calendário com as datas de verdade, placares dos jogos já
            realizados e os escudos oficiais dos clubes. Leva cerca de 40
            segundos.
          </p>
          <p class="aviso aviso--atencao pequeno">
            Coleta do site do GE. <strong>Não é uma API pública</strong>: os
            termos de uso do Globo não autorizam coleta automatizada, e o
            endereço interno deles pode mudar sem aviso. Para um bolão entre
            amigos numa máquina de casa o risco prático é baixo, mas a escolha
            é sua.
          </p>
          <button
            type="button"
            class="btn btn--principal"
            :disabled="importandoReal || criandoBrasileirao"
            @click="importarReal"
          >
            {{ importandoReal ? 'coletando… (~40s)' : 'Importar tabela real' }}
          </button>
        </div>

        <div class="opcao">
          <h3>Tabela gerada</h3>
          <p class="fraco pequeno">
            Monta uma temporada estruturalmente correta — 20 clubes, turno e
            returno completos, 380 jogos — com datas plausíveis. Instantâneo e
            sem depender de ninguém.
          </p>
          <p class="fraco pequeno">
            Serve para testar a plataforma ou para um campeonato inventado
            entre amigos. Para valer, prefira a tabela real.
          </p>
          <button
            type="button"
            class="btn"
            :disabled="criandoBrasileirao || importandoReal"
            @click="criarBrasileirao"
          >
            {{ criandoBrasileirao ? 'criando…' : 'Gerar tabela' }}
          </button>
        </div>
      </div>
    </div>

    <div
      v-if="escudos"
      class="card"
    >
      <h2>Escudos</h2>
      <div class="linha">
        <div>
          <p
            class="fraco pequeno"
            style="margin: 0"
          >
            <strong>{{ escudos.with_crest }}</strong> clubes com escudo oficial ·
            <strong>{{ escudos.missing }}</strong> ainda usando o escudo desenhado
            pela plataforma.
          </p>
          <p
            class="fraco pequeno"
            style="margin: 0.35rem 0 0"
          >
            {{ escudos.hint }}
          </p>
        </div>
        <button
          v-if="escudos.missing"
          type="button"
          class="btn empurra"
          :disabled="buscandoEscudos"
          @click="buscarEscudos"
        >
          {{ buscandoEscudos ? 'buscando…' : 'Buscar escudos' }}
        </button>
      </div>

      <div
        v-if="escudos.missing"
        class="fraco pequeno"
        style="margin-top: 0.6rem"
      >
        Faltam em:
        {{ Object.entries(escudos.by_competition)
          .map(([campeonato, quantos]) => `${campeonato} (${quantos})`)
          .join(', ') }}
      </div>
    </div>

    <div
      v-if="ligas?.length"
      class="card"
    >
      <h2>Ligas do mundo</h2>
      <p class="fraco pequeno">
        Temporada {{ ligas[0]!.season_year }}-{{ (ligas[0]!.season_year + 1) % 100 }},
        de calendário público em CSV — sem chave e sem cota. Uma requisição por
        liga, com as datas já em UTC. O escudo não vem junto: depois de importar,
        use <strong>Buscar escudos</strong> acima.
      </p>

      <div class="pilha">
        <div
          v-for="liga in ligas"
          :key="liga.slug"
          class="linha preset"
        >
          <div>
            <strong>{{ liga.name }}</strong>
            <div class="fraco pequeno">
              {{ liga.country }} · {{ liga.rounds }} rodadas ·
              {{ liga.fixtures }} jogos · começa em
              {{ liga.first_round.split('-').reverse().join('/') }}
            </div>
          </div>
          <span
            v-if="liga.imported"
            class="tag tag--verde empurra"
          >no banco</span>
          <button
            type="button"
            class="btn pequeno"
            :class="{ empurra: !liga.imported }"
            :disabled="importandoLiga !== ''"
            @click="importarLiga(liga)"
          >
            {{ importandoLiga === liga.slug
              ? 'importando…'
              : (liga.imported ? 'atualizar' : 'importar') }}
          </button>
        </div>
      </div>
    </div>

    <div
      v-if="copasEspn?.length"
      class="card"
    >
      <h2>Copas</h2>
      <p class="fraco pequeno">
        Copa eliminatória não sai em CSV e o chaveamento da Wikipédia não traz
        horário de jogo — sem horário não dá para travar palpite. Estas vêm da
        ESPN, que publica data, mandante, placar e pênaltis, inclusive de jogo
        que ainda não aconteceu.
      </p>

      <div class="pilha">
        <div
          v-for="copa in copasEspn"
          :key="copa.slug"
          class="linha preset"
        >
          <div>
            <strong>{{ copa.name }} {{ copa.label }}</strong>
            <div class="fraco pequeno">
              {{ copa.country }} · fases sorteadas até agora
            </div>
          </div>
          <span
            v-if="copa.imported"
            class="tag tag--verde empurra"
          >no banco</span>
          <button
            type="button"
            class="btn pequeno"
            :class="{ empurra: !copa.imported }"
            :disabled="importandoCopaEspn !== ''"
            @click="importarCopaEspn(copa)"
          >
            {{ importandoCopaEspn === copa.slug
              ? 'importando…'
              : (copa.imported ? 'atualizar' : 'importar') }}
          </button>
        </div>
      </div>
    </div>

    <div
      v-if="copas?.length"
      class="card"
    >
      <h2>Mata-mata</h2>
      <p class="fraco pequeno">
        Torneio eliminatório não tem calendário em CSV: o chaveamento só existe
        depois do sorteio. Estes vêm da Wikipédia em português, que publica cada
        jogo com o fuso explícito — o que evita errar a hora por causa de time
        jogando em outro fuso.
      </p>

      <div class="pilha">
        <div
          v-for="copa in copas"
          :key="copa.slug"
          class="linha preset"
        >
          <div>
            <strong>{{ copa.name }} {{ copa.year }}</strong>
            <div class="fraco pequeno">
              {{ copa.country }} · fonte: {{ copa.article }}
            </div>
          </div>
          <span
            v-if="copa.imported"
            class="tag tag--verde empurra"
          >no banco</span>
          <button
            type="button"
            class="btn pequeno"
            :class="{ empurra: !copa.imported }"
            :disabled="importandoCopa !== ''"
            @click="importarCopa(copa)"
          >
            {{ importandoCopa === copa.slug
              ? 'importando…'
              : (copa.imported ? 'atualizar' : 'importar') }}
          </button>
        </div>
      </div>
    </div>

    <div
      v-if="geInfo?.presets.length"
      class="card"
    >
      <h2>Campeonatos do GE</h2>
      <p class="fraco pequeno">
        Coletados do GE do mesmo jeito. Cada um foi verificado contra a API —
        a data ao lado é de quando isso foi conferido pela última vez.
      </p>

      <div class="pilha">
        <div
          v-for="preset in geInfo.presets"
          :key="preset.slug"
          class="linha preset"
        >
          <div>
            <strong>{{ preset.name }}</strong>
            <div class="fraco pequeno">
              {{ preset.country }} · {{ preset.rounds }} rodadas ·
              temporada {{ preset.season_year }} · conferido em
              {{ preset.verified_at.split('-').reverse().join('/') }}
            </div>
          </div>
          <button
            type="button"
            class="btn pequeno empurra"
            :disabled="importandoPreset !== ''"
            @click="importarPreset(preset)"
          >
            {{ importandoPreset === preset.slug ? 'coletando…' : 'importar' }}
          </button>
        </div>
      </div>

      <details style="margin-top: 0.9rem">
        <summary class="pequeno">
          Cadastrar um campeonato que não está na lista
        </summary>
        <p
          class="fraco pequeno"
          style="margin-top: 0.5rem"
        >
          O GE não tem catálogo público: o identificador da tabela é um UUID
          embutido na página, no meio de dezenas de ids de matéria. Cole o
          endereço do campeonato e a plataforma testa os candidatos para você.
        </p>

        <div
          class="linha"
          style="gap: 0.6rem; align-items: flex-end"
        >
          <div
            class="campo"
            style="flex: 1; min-width: 16rem"
          >
            <label for="url-desc">Endereço no ge.globo.com</label>
            <input
              id="url-desc"
              v-model="urlDescoberta"
              placeholder="https://ge.globo.com/futebol/futebol-internacional/futebol-espanhol/"
            >
          </div>
          <button
            type="button"
            class="btn"
            style="margin-bottom: 0.85rem"
            :disabled="descobrindo"
            @click="descobrir"
          >
            {{ descobrindo ? 'testando…' : 'Descobrir' }}
          </button>
        </div>

        <div
          v-if="achados.length"
          class="pilha"
          style="margin-bottom: 0.9rem"
        >
          <div
            v-for="achado in achados"
            :key="achado.phase + achado.table_id"
            class="card linha"
          >
            <div>
              <strong class="pequeno">{{ achado.phase }}</strong>
              <div class="fraco pequeno">
                {{ achado.fixtures }} jogos nas {{ achado.rounds_found }} primeiras
                rodadas testadas
                <template v-if="achado.sample.length">
                  · ex: {{ achado.sample.join(', ') }}
                </template>
              </div>
            </div>
            <button
              type="button"
              class="btn pequeno empurra"
              @click="usarAchado(achado)"
            >
              usar este
            </button>
          </div>
          <p class="fraco pequeno">
            Confira o nome e o número de rodadas abaixo, e importe.
          </p>
        </div>

        <details style="margin-bottom: 0.6rem">
          <summary class="fraco pequeno">
            Ou ache os códigos você mesmo
          </summary>
          <ol class="fraco pequeno">
            <li
              v-for="(passo, indice) in geInfo.how_to_find"
              :key="indice"
            >
              {{ passo }}
            </li>
          </ol>
        </details>

        <div
          class="linha"
          style="gap: 0.6rem; align-items: flex-end"
        >
          <div
            class="campo"
            style="flex: 1; min-width: 11rem"
          >
            <label for="m-nome">Nome</label>
            <input
              id="m-nome"
              v-model="manualNome"
              placeholder="LaLiga"
            >
          </div>
          <div
            class="campo"
            style="width: 9rem"
          >
            <label for="m-pais">País</label>
            <input
              id="m-pais"
              v-model="manualPais"
              placeholder="Espanha"
            >
          </div>
          <div
            class="campo"
            style="width: 6rem"
          >
            <label for="m-rodadas">Rodadas</label>
            <input
              id="m-rodadas"
              v-model.number="manualRodadas"
              type="number"
              min="1"
              max="60"
            >
          </div>
          <div
            class="campo"
            style="width: 6rem"
          >
            <label for="m-ano">Ano</label>
            <input
              id="m-ano"
              v-model.number="manualAno"
              type="number"
              min="2000"
              max="2100"
            >
          </div>
        </div>
        <div class="campo">
          <label for="m-tabela">Tabela (UUID)</label>
          <input
            id="m-tabela"
            v-model="manualTabela"
            placeholder="c33769be-62ec-43c6-a633-8c826e14a696"
          >
        </div>
        <div class="campo">
          <label for="m-fase">Fase</label>
          <input
            id="m-fase"
            v-model="manualFase"
            placeholder="fase-unica-campeonato-espanhol-2025-2026"
          >
        </div>
        <button
          type="button"
          class="btn"
          :disabled="importandoManual || !manualTabela || !manualFase"
          @click="importarManual"
        >
          {{ importandoManual ? 'coletando…' : 'Importar' }}
        </button>
      </details>
    </div>

    <div class="card">
      <h2>Importar por CSV</h2>
      <p class="fraco pequeno">
        Cole a tabela do campeonato. Importar de novo o mesmo arquivo
        <strong>atualiza</strong> os jogos em vez de duplicar — então você pode
        colar de novo depois de preencher os placares.
      </p>

      <form @submit.prevent="importar">
        <div
          class="linha"
          style="gap: 0.75rem; align-items: flex-end"
        >
          <div
            class="campo"
            style="flex: 1; min-width: 14rem"
          >
            <label for="campeonato">Nome do campeonato</label>
            <input
              id="campeonato"
              v-model="nomeCampeonato"
              required
              placeholder="Brasileirão Série A"
            >
          </div>
          <div
            class="campo"
            style="width: 7rem"
          >
            <label for="ano">Ano</label>
            <input
              id="ano"
              v-model.number="ano"
              type="number"
              min="1900"
              max="2200"
              required
            >
          </div>
        </div>

        <div class="campo">
          <label for="csv">Dados</label>
          <textarea
            id="csv"
            v-model="csv"
            spellcheck="false"
            required
          />
          <span
            v-if="modelo"
            class="dica"
          >
            Colunas: {{ modelo.columns.join(', ') }}
          </span>
        </div>

        <ul
          v-if="modelo"
          class="fraco pequeno notas"
        >
          <li
            v-for="(nota, indice) in modelo.notes"
            :key="indice"
          >
            {{ nota }}
          </li>
        </ul>

        <p
          v-if="erro"
          class="aviso aviso--erro"
        >
          {{ erro }}
        </p>
        <p
          v-if="resultado"
          class="aviso aviso--ok"
        >
          {{ resultado }}
        </p>

        <button
          type="submit"
          class="btn btn--principal"
          :disabled="importando"
        >
          {{ importando ? 'importando…' : 'Importar' }}
        </button>
      </form>
    </div>

    <div class="card">
      <h2>Provedores de dados</h2>
      <p class="fraco pequeno">
        Nenhum é obrigatório. O cadastro manual sempre funciona e não custa nada.
      </p>
      <ul class="provedores">
        <li
          v-for="provedor in provedores"
          :key="provedor.slug"
        >
          <div class="linha">
            <strong>{{ provedor.name }}</strong>
            <span
              class="tag"
              :class="provedor.ready ? 'tag--verde' : ''"
            >
              {{ provedor.ready ? 'configurado' : 'sem chave' }}
            </span>
          </div>
          <div class="fraco pequeno">
            {{ provedor.detail }}
          </div>
        </li>
      </ul>
    </div>
  </div>
</template>

<style scoped>
.notas { padding-left: 1.1rem; margin: 0 0 0.75rem; }

.opcoes {
  display: grid;
  grid-template-columns: repeat(auto-fit, minmax(17rem, 1fr));
  gap: 0.75rem;
}

.opcao {
  border: 1px solid var(--borda);
  border-radius: var(--raio-p);
  padding: 0.85rem;
  display: flex;
  flex-direction: column;
}

.opcao h3 { margin-bottom: 0.35rem; }
.opcao .btn { margin-top: auto; align-self: flex-start; }
.provedores { list-style: none; padding: 0; margin: 0; display: grid; gap: 0.75rem; }
</style>
