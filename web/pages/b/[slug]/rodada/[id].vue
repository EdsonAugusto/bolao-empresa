<script setup lang="ts">
definePageMeta({ middleware: 'auth' })

const route = useRoute()
const slug = computed(() => route.params.slug as string)
const rodadaId = computed(() => Number(route.params.id))
const { hora, diaChave, diaExtenso } = useFormat()

/**
 * A mesma tela serve aos dois tipos de bolão. O que muda é de onde os jogos
 * vêm: rodada do campeonato (`rounds`) ou rodada montada pelo organizador
 * (`matchdays`). O resto — blindagem, trava, gravação — é idêntico.
 */
const ehMontada = computed(() => route.query.tipo === 'montada')
const base = computed(() =>
  ehMontada.value
    ? `/v1/pools/${slug.value}/matchdays/${rodadaId.value}`
    : `/v1/pools/${slug.value}/rounds/${rodadaId.value}`,
)

interface Time {
  id: number
  name: string
  short_name: string | null
  slug: string
  crest_url: string | null
}
interface Jogo {
  id: number
  kickoff_at: string
  status: string
  home_team: Time
  away_team: Time
  home_ft: number | null
  away_ft: number | null
  home_et: number | null
  away_et: number | null
  is_locked: boolean
  settled_at: string | null
  minute: number | null
  home_form: string
  away_form: string
}
interface MeuPalpite { fixture_id: number, home_goals: number, away_goals: number }
interface PalpiteAlheio {
  membership_id: number
  display_name: string
  fixture_id: number
  home_goals: number | null
  away_goals: number | null
  is_hidden: boolean
}

const chave = computed(() => `${ehMontada.value ? 'md' : 'r'}-${rodadaId.value}`)

const { data: jogos, refresh: recarregarJogos } = useApiData<Jogo[]>(
  `jogos-${chave.value}`,
  () => `${base.value}/fixtures`,
)
const { data: meus, refresh: recarregarMeus } = useApiData<MeuPalpite[]>(
  `meus-${chave.value}`,
  () => `${base.value}/my-predictions`,
)
const { data: alheios, refresh: recarregarAlheios } = useApiData<PalpiteAlheio[]>(
  `alheios-${chave.value}`,
  () => `${base.value}/predictions`,
)

/**
 * O que aparece nos campos é DERIVADO, não sincronizado.
 *
 * A versão anterior copiava os palpites salvos para um rascunho dentro de um
 * `watch(..., { immediate: true })`. Isso quebra a hidratação, e de um jeito
 * que só aparece no console: no servidor o `watch` roda **uma vez**, durante o
 * setup, quando `useAsyncData` ainda não resolveu e `meus` é `null` — e no
 * servidor watchers não são reagendados depois. Então o servidor renderizava
 * "Faltam 5" com os campos vazios, e o cliente, que na hidratação já tem os
 * dados vindos do payload, renderizava "Tudo preenchido". Árvores diferentes.
 *
 * Aqui os dois lados calculam a mesma coisa na hora de renderizar: o valor do
 * campo é o que a pessoa digitou, e na falta disso o que está salvo.
 */
const edicoes = reactive<Record<number, [string, string]>>({})
const salvando = ref(false)
const mensagem = ref('')
const erro = ref('')

/** Palpite salvo por jogo, como texto — é o que o campo mostra. */
const salvos = computed(() => {
  const mapa = new Map<number, [string, string]>()
  for (const palpite of meus.value ?? []) {
    mapa.set(palpite.fixture_id, [String(palpite.home_goals), String(palpite.away_goals)])
  }
  return mapa
})

/** Valor de um lado do placar. A edição vence o salvo. */
function valor(fixtureId: number, lado: 0 | 1): string {
  const editado = edicoes[fixtureId]
  if (editado) return editado[lado]
  return salvos.value.get(fixtureId)?.[lado] ?? ''
}

function digitar(fixtureId: number, lado: 0 | 1, texto: string) {
  const atual: [string, string] = edicoes[fixtureId]
    ?? [...(salvos.value.get(fixtureId) ?? ['', ''])] as [string, string]
  atual[lado] = texto
  edicoes[fixtureId] = atual
}

useHead({ title: 'Palpites da rodada' })

/** Jogos agrupados por dia no fuso do usuário — não no UTC. */
const porDia = computed(() => {
  const grupos = new Map<string, Jogo[]>()
  for (const jogo of jogos.value ?? []) {
    const chave = diaChave(jogo.kickoff_at)
    if (!grupos.has(chave)) grupos.set(chave, [])
    grupos.get(chave)!.push(jogo)
  }
  return [...grupos.entries()].sort(([a], [b]) => a.localeCompare(b))
})

const alheiosPorJogo = computed(() => {
  const mapa = new Map<number, PalpiteAlheio[]>()
  for (const palpite of alheios.value ?? []) {
    if (!mapa.has(palpite.fixture_id)) mapa.set(palpite.fixture_id, [])
    mapa.get(palpite.fixture_id)!.push(palpite)
  }
  return mapa
})

const abertos = computed(() => (jogos.value ?? []).filter(jogo => !jogo.is_locked))
const pendentes = computed(() =>
  abertos.value.filter(jogo => !valor(jogo.id, 0) || !valor(jogo.id, 1)),
)

function placarFinal(jogo: Jogo): string | null {
  const casa = jogo.home_et ?? jogo.home_ft
  const fora = jogo.away_et ?? jogo.away_ft
  return casa === null || casa === undefined || fora === null || fora === undefined
    ? null
    : `${casa} × ${fora}`
}

const EM_CAMPO = new Set(['live', 'ht'])
const TRES_HORAS = 3 * 60 * 60 * 1000

/** Há quanto tempo a bola rolou. Negativo se ainda não rolou. */
function desdeOApito(jogo: Jogo): number {
  return Date.now() - new Date(jogo.kickoff_at).getTime()
}

/**
 * Em campo de verdade.
 *
 * O status sozinho não basta: uma fonte que não publica placar ao vivo deixa o
 * jogo em `LIVE` indefinidamente, e a tela ficaria anunciando "em campo" numa
 * partida que acabou há seis horas — foi o que apareceu na Eredivisie. Passadas
 * três horas do apito, a bola não está mais rolando, seja lá o que a fonte diga.
 */
function emCampo(jogo: Jogo): boolean {
  return EM_CAMPO.has(jogo.status.toLowerCase()) && desdeOApito(jogo) < TRES_HORAS
}

/**
 * Jogo que já devia ter acabado e continua sem placar.
 *
 * Acontece quando a fonte não publica ao vivo — o CSV das ligas europeias leva
 * horas. A plataforma não inventa resultado e não marca como adiado, porque
 * adiado reabriria o palpite. Fica travado até o placar chegar, e o organizador
 * pode lançar à mão se cansar de esperar.
 */
function aguardandoResultado(jogo: Jogo): boolean {
  return (
    jogo.is_locked
    && !jogo.settled_at
    && placarFinal(jogo) === null
    && desdeOApito(jogo) > TRES_HORAS
  )
}

const { data: bolao } = useApiData<{ my_role: string | null }>(
  `papel-${slug.value}`,
  () => `/v1/pools/${slug.value}`,
)
const podeLancar = computed(
  () => !!bolao.value?.my_role && bolao.value.my_role !== 'player',
)

const lancamento = reactive<Record<number, [string, string]>>({})
const lancando = ref(0)

function parLancamento(fixtureId: number): [string, string] {
  if (!lancamento[fixtureId]) lancamento[fixtureId] = ['', '']
  return lancamento[fixtureId]
}

async function lancarPlacar(jogo: Jogo) {
  const [casa, fora] = parLancamento(jogo.id)
  if (casa === '' || fora === '') return

  erro.value = ''
  lancando.value = jogo.id
  try {
    await apiFetch(`/v1/catalog/fixtures/${jogo.id}/score`, {
      method: 'PUT',
      body: { home_goals: Number(casa), away_goals: Number(fora), finished: true },
    })
    mensagem.value = 'Placar lançado. A apuração corre atrás.'
    await Promise.all([recarregarJogos(), recarregarAlheios()])
  }
  catch (error_) {
    erro.value = (error_ as Error).message
  }
  finally {
    lancando.value = 0
  }
}

/**
 * Recarrega sozinho enquanto a rodada está acontecendo.
 *
 * A condição **não** é "existe jogo em campo": seria um impasse. O jogo só
 * aparece em campo depois de uma recarga, e a recarga só existiria se ele já
 * estivesse em campo — quem abrisse a tela dez minutos antes do apito ficaria
 * olhando "vai começar" até apertar F5.
 *
 * Então o relógio liga quando existe jogo cujo horário já chegou e que ainda
 * não terminou. Desliga sozinho quando o último acaba: um `setInterval`
 * permanente ficaria batendo na API a noite inteira para nada.
 */
const FINAIS = new Set(['finished', 'cancelled', 'abandoned', 'postponed'])

const rodadaAcontecendo = computed(() =>
  (jogos.value ?? []).some(
    jogo =>
      !FINAIS.has(jogo.status.toLowerCase())
      && new Date(jogo.kickoff_at).getTime() <= Date.now() + 60_000,
  ),
)

let timer: ReturnType<typeof setInterval> | null = null

function pararRelogio() {
  if (timer) {
    clearInterval(timer)
    timer = null
  }
}

watch(rodadaAcontecendo, (acontecendo) => {
  pararRelogio()
  // `setInterval` no SSR rodaria no servidor e nunca seria limpo.
  if (!acontecendo || !import.meta.client) return
  timer = setInterval(() => {
    // Só o placar. O palpite alheio segue blindado até o apito de cada jogo, e
    // o que a pessoa está digitando não é tocado: `edicoes` vence `salvos` na
    // hora de montar o campo.
    recarregarJogos()
  }, 45_000)
}, { immediate: true })

onBeforeUnmount(pararRelogio)

/**
 * Publica a altura da barra grudada para o balão de relatar ficar acima dela.
 *
 * A altura muda: ela cresce quando entra a mensagem de "N palpite(s)
 * salvo(s)". Um valor fixo no CSS acertaria um dos dois estados e cobriria o
 * outro — e o que ele cobriria é justamente a confirmação de que o palpite foi
 * salvo.
 */
const barra = ref<HTMLElement | null>(null)
let observador: ResizeObserver | null = null

onMounted(() => {
  if (!import.meta.client || typeof ResizeObserver === 'undefined') return
  observador = new ResizeObserver(([entrada]) => {
    const altura = entrada?.contentRect.height ?? 0
    document.documentElement.style.setProperty('--altura-barra', `${Math.ceil(altura) + 16}px`)
  })
  watchEffect(() => {
    observador?.disconnect()
    if (barra.value) observador?.observe(barra.value)
  })
})

onBeforeUnmount(() => {
  observador?.disconnect()
  document.documentElement.style.removeProperty('--altura-barra')
})

/**
 * Placar muda sozinho de 45 em 45 segundos. Para quem enxerga isso é óbvio;
 * para quem usa leitor de tela, nada acontece — a região viva abaixo é o que
 * transforma a atualização silenciosa em informação.
 *
 * Só o que MUDOU é anunciado, e num texto que se sustenta sozinho: repetir a
 * rodada inteira a cada 45 s seria pior do que não anunciar nada.
 */
const anuncio = ref('')
const placarAnterior = new Map<number, string>()

watch(jogos, (lista) => {
  if (!lista) return
  const novidades: string[] = []

  for (const jogo of lista) {
    if (jogo.home_ft == null || jogo.away_ft == null) continue
    const agora = `${jogo.home_ft}-${jogo.away_ft}`
    const antes = placarAnterior.get(jogo.id)
    placarAnterior.set(jogo.id, agora)
    if (antes !== undefined && antes !== agora) {
      novidades.push(
        `${jogo.home_team.name} ${jogo.home_ft} a ${jogo.away_ft} ${jogo.away_team.name}`,
      )
    }
  }

  if (novidades.length) anuncio.value = novidades.join('. ')
})

async function salvar() {
  erro.value = ''
  mensagem.value = ''
  salvando.value = true

  const predictions = abertos.value
    .filter(jogo => valor(jogo.id, 0) !== '' && valor(jogo.id, 1) !== '')
    .map(jogo => ({
      fixture_id: jogo.id,
      home_goals: Number(valor(jogo.id, 0)),
      away_goals: Number(valor(jogo.id, 1)),
    }))

  if (!predictions.length) {
    erro.value = 'Preencha pelo menos um palpite.'
    salvando.value = false
    return
  }

  try {
    const resultado = await apiFetch<{ saved: MeuPalpite[], rejected: { reason: string }[] }>(
      `/v1/pools/${slug.value}/predictions`,
      { method: 'PUT', body: { predictions } },
    )
    mensagem.value = `${resultado.saved.length} palpite(s) salvo(s).`
    // O que foi aceito agora vem do servidor; limpar a edição local devolve a
    // fonte da verdade para `meus` e evita divergir num refresh futuro.
    for (const salvo of resultado.saved) {
      // Reflete o que o servidor aceitou, em vez de apagar a chave: `delete`
      // em objeto reativo é operação que o eslint barra, e reescrever tem o
      // mesmo efeito prático aqui.
      edicoes[salvo.fixture_id] = [String(salvo.home_goals), String(salvo.away_goals)]
    }
    if (resultado.rejected.length) {
      erro.value = `${resultado.rejected.length} recusado(s): ${resultado.rejected[0]?.reason}`
    }
    await Promise.all([recarregarMeus(), recarregarJogos(), recarregarAlheios()])
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
    <!-- Fora da tela, mas lido: é o canal por onde a mudança de placar chega a
         quem não está olhando para ela. -->
    <p
      class="so-leitor"
      role="status"
      aria-live="polite"
      aria-atomic="true"
    >
      {{ anuncio }}
    </p>

    <NuxtLink
      :to="`/b/${slug}`"
      class="pequeno"
    >← voltar ao bolão</NuxtLink>
    <h1 style="margin-top: 0.5rem">
      Palpites da rodada
    </h1>

    <p
      v-if="abertos.length"
      class="fraco pequeno"
    >
      {{ abertos.length }} jogo(s) aberto(s).
      <template v-if="pendentes.length">
        Faltam <strong>{{ pendentes.length }}</strong>.
      </template>
      <template v-else>
        Tudo preenchido.
      </template>
    </p>
    <p
      v-else
      class="fraco pequeno"
    >
      Todos os jogos desta rodada já começaram. Os palpites estão fechados.
    </p>

    <div
      v-for="[dia, jogosDoDia] in porDia"
      :key="dia"
      class="grupo"
    >
      <h2 class="dia">
        {{ diaExtenso(dia) }}
      </h2>

      <article
        v-for="jogo in jogosDoDia"
        :key="jogo.id"
        class="card jogo"
      >
        <div class="jogo__topo">
          <span class="pequeno fraco num">{{ hora(jogo.kickoff_at) }}</span>
          <span
            v-if="emCampo(jogo)"
            class="tag tag--vivo"
          >
            <span class="pulso" />
            {{ jogo.status.toLowerCase() === 'ht' ? 'intervalo' : 'em campo' }}
            <template v-if="jogo.minute">{{ jogo.minute }}'</template>
          </span>
          <span
            v-else-if="aguardandoResultado(jogo)"
            class="tag tag--espera"
          >aguardando resultado</span>
          <span
            v-else-if="jogo.is_locked"
            class="tag tag--fechado"
          >fechado</span>
          <span
            v-if="placarFinal(jogo)"
            class="empurra placar-chip"
            :class="{ 'placar-chip--vivo': emCampo(jogo) }"
          >
            {{ placarFinal(jogo) }}
          </span>
        </div>

        <div class="confronto">
          <span class="time time--casa">
            <span class="time__texto">
              <span class="time__nome">{{ jogo.home_team.name }}</span>
              <RetrospectoTime
                :resumo="jogo.home_form"
                :nome="jogo.home_team.name"
              />
            </span>
            <EscudoTime
              :nome="jogo.home_team.name"
              :sigla="jogo.home_team.short_name"
              :escudo="jogo.home_team.crest_url"
              :tamanho="44"
            />
          </span>

          <div class="entrada">
            <input
              :value="valor(jogo.id, 0)"
              type="number"
              min="0"
              max="99"
              inputmode="numeric"
              :disabled="jogo.is_locked"
              :aria-label="`Gols de ${jogo.home_team.name}`"
              @input="digitar(jogo.id, 0, ($event.target as HTMLInputElement).value)"
            >
            <span class="x">×</span>
            <input
              :value="valor(jogo.id, 1)"
              type="number"
              min="0"
              max="99"
              inputmode="numeric"
              :disabled="jogo.is_locked"
              :aria-label="`Gols de ${jogo.away_team.name}`"
              @input="digitar(jogo.id, 1, ($event.target as HTMLInputElement).value)"
            >
          </div>

          <span class="time time--fora">
            <EscudoTime
              :nome="jogo.away_team.name"
              :sigla="jogo.away_team.short_name"
              :escudo="jogo.away_team.crest_url"
              :tamanho="44"
            />
            <span class="time__texto">
              <span class="time__nome">{{ jogo.away_team.name }}</span>
              <RetrospectoTime
                :resumo="jogo.away_form"
                :nome="jogo.away_team.name"
              />
            </span>
          </span>
        </div>

        <div
          v-if="podeLancar && aguardandoResultado(jogo)"
          class="lancar"
        >
          <span class="fraco pequeno">
            A fonte não publicou o resultado. Lance o placar para a rodada fechar:
          </span>
          <span class="lancar__campos">
            <input
              v-model="parLancamento(jogo.id)[0]"
              type="number"
              min="0"
              max="99"
              inputmode="numeric"
              :aria-label="`Gols de ${jogo.home_team.name}`"
            >
            <span class="fraco">×</span>
            <input
              v-model="parLancamento(jogo.id)[1]"
              type="number"
              min="0"
              max="99"
              inputmode="numeric"
              :aria-label="`Gols de ${jogo.away_team.name}`"
            >
            <button
              type="button"
              class="btn pequeno"
              :disabled="lancando === jogo.id"
              @click="lancarPlacar(jogo)"
            >
              {{ lancando === jogo.id ? 'lançando…' : 'lançar' }}
            </button>
          </span>
        </div>

        <details
          v-if="alheiosPorJogo.get(jogo.id)?.length"
          class="outros"
        >
          <summary class="pequeno fraco">
            Palpites do grupo ({{ alheiosPorJogo.get(jogo.id)!.length }})
          </summary>
          <ul class="lista-palpites">
            <li
              v-for="palpite in alheiosPorJogo.get(jogo.id)"
              :key="palpite.membership_id"
            >
              <span>{{ palpite.display_name }}</span>
              <span
                v-if="palpite.is_hidden"
                class="fraco pequeno"
              >
                aguardando o apito
              </span>
              <span
                v-else
                class="num"
              ><strong>{{ palpite.home_goals }} × {{ palpite.away_goals }}</strong></span>
            </li>
          </ul>
        </details>

        <NuxtLink
          v-if="jogo.settled_at"
          :to="`/b/${slug}/jogo/${jogo.id}`"
          class="pequeno"
        >
          ver como cada um pontuou →
        </NuxtLink>
      </article>
    </div>

    <div
      v-if="abertos.length"
      ref="barra"
      class="barra"
    >
      <p
        v-if="mensagem"
        class="aviso aviso--ok"
        role="status"
      >
        {{ mensagem }}
      </p>
      <p
        v-if="erro"
        class="aviso aviso--erro"
        role="alert"
      >
        {{ erro }}
      </p>
      <button
        type="button"
        class="btn btn--principal btn--bloco"
        :disabled="salvando"
        @click="salvar"
      >
        {{ salvando ? 'salvando…' : 'Salvar palpites' }}
      </button>
    </div>
  </div>
</template>

<style scoped>
.grupo { margin-top: var(--e5); }

/* Divisória de dia: a linha atravessa a tela e o rótulo repousa nela. */
.dia {
  display: flex;
  align-items: center;
  gap: var(--e3);
  font-size: 0.75rem;
  text-transform: uppercase;
  letter-spacing: 0.08em;
  font-weight: 700;
  color: var(--texto-fraco);
  margin-bottom: var(--e3);
}

.dia::after {
  content: '';
  flex: 1;
  height: 1px;
  background: var(--borda);
}

.jogo { padding: var(--e3) var(--e4); }
.jogo + .jogo { margin-top: var(--e2); }

.jogo__topo {
  display: flex;
  align-items: center;
  gap: var(--e2);
  margin-bottom: var(--e2);
  min-height: 1.6rem;
}

.confronto {
  display: grid;
  grid-template-columns: 1fr auto 1fr;
  align-items: center;
  gap: var(--e3);
}

.time {
  font-weight: 650;
  font-size: 1rem;
  display: flex;
  align-items: center;
  gap: var(--e3);
  min-width: 0;
}

.time__texto {
  display: flex;
  flex-direction: column;
  gap: 0.2rem;
  min-width: 0;
}

.time__nome {
  overflow: hidden;
  text-overflow: ellipsis;
  white-space: nowrap;
  line-height: 1.25;
}

.time--casa { justify-content: flex-end; text-align: right; }
.time--casa .time__texto { align-items: flex-end; }
.time--fora { justify-content: flex-start; text-align: left; }
.time--fora .time__texto { align-items: flex-start; }

/* O marcador: dois campos escuros, como painel de estádio. */
.entrada {
  display: flex;
  align-items: center;
  gap: var(--e2);
  background: var(--placar-fundo);
  padding: 0.3rem 0.4rem;
  border-radius: var(--raio-p);

  /* Esta pílula é escura NOS DOIS TEMAS, e o navegador precisa saber disso.
     A raiz declara `color-scheme: light dark`, então em sistema claro o
     navegador pinta as partes do campo que não são nossas com a paleta clara:
     o realce de seleção vem azul-claro com texto PRETO, o preenchimento
     automático vem com fundo amarelo e texto preto, e em algumas plataformas o
     cursor também. Dá para digitar e não conseguir ler o que se digitou —
     exatamente o sintoma. Declarar o escuro aqui alinha o que é nosso com o
     que é do navegador. */
  color-scheme: dark;
}

/* O realce de seleção também, explicitamente: `color-scheme` cobre o padrão,
   e isto cobre o caso de a página inteira ter definido outro. */
.entrada ::selection {
  background: var(--verde);
  color: var(--sobre-verde);
}

/* 44px de alvo: são dois campos vizinhos, e errar o de casa pelo de fora é o
   tipo de erro que só se percebe depois do apito. */
.entrada input {
  width: 2.8rem;
  min-height: 44px;
  text-align: center;
  font-family: var(--fonte-num);
  font-weight: 700;
  font-size: 1.25rem;
  padding: 0.3rem 0.1rem;

  /* Campo transparente e sem borda desaparecia dentro da pílula.
     No tema escuro isso passava, porque a pílula se parece com o resto da
     página e o conjunto lê como um controle. No claro ela é uma placa preta no
     meio de uma tela branca: sem nenhuma marca, ninguém vê que ali existem
     DOIS campos para digitar. A moldura tênue custa quase nada visualmente e
     devolve o "digite aqui". */
  background: rgb(255 255 255 / 8%);
  border: 1px solid rgb(255 255 255 / 18%);
  border-radius: calc(var(--raio-p) - 3px);

  color: var(--placar-texto);
  /* No WebKit é este que decide a cor do texto do campo, não `color`. */
  -webkit-text-fill-color: var(--placar-texto);
  caret-color: var(--placar-texto);
  -moz-appearance: textfield;
  appearance: textfield;
}

.entrada input:hover:not(:disabled) { border-color: rgb(255 255 255 / 38%); }

.entrada input:focus-visible {
  background: rgb(255 255 255 / 14%);
  border-color: var(--verde-claro);
}

.entrada input:focus-visible { outline-offset: -2px; }

.entrada input::-webkit-outer-spin-button,
.entrada input::-webkit-inner-spin-button { appearance: none; margin: 0; }

/* Jogo travado mostra o palpite QUE A PESSOA DEU — é o que ela mais quer ler
   nessa tela, e era o texto mais apagado dela. Duas atenuações se somavam
   (40% na cor mais 60% no conjunto) e o resultado media 2:1, metade do mínimo
   legível. Agora recua o conjunto e o número fica cheio dentro dele. */
.entrada:has(input:disabled) { opacity: 0.75; }
.entrada input:disabled { color: var(--placar-texto); -webkit-text-fill-color: var(--placar-texto); }

.x { color: rgb(245 247 248 / 45%); font-weight: 700; }

/* Só o organizador vê. Aparece no jogo travado, que é onde o problema está. */
.lancar {
  display: flex;
  flex-wrap: wrap;
  align-items: center;
  gap: var(--e2);
  margin-top: var(--e3);
  padding: var(--e2) var(--e3);
  border: 1px dashed var(--aviso);
  border-radius: var(--raio-p);
}

.lancar__campos { display: flex; align-items: center; gap: var(--e1); margin-left: auto; }
.lancar__campos input { width: 3rem; text-align: center; font-weight: 700; }

.outros { margin-top: var(--e3); border-top: 1px solid var(--borda); padding-top: var(--e2); }
.outros summary { cursor: pointer; list-style-position: outside; }
.outros summary:hover { color: var(--texto); }

.lista-palpites {
  list-style: none;
  padding: var(--e2) 0 0;
  margin: 0;
  display: grid;
  gap: var(--e1);
}

.lista-palpites li {
  display: flex;
  justify-content: space-between;
  gap: var(--e3);
  font-size: 0.9rem;
  padding: 0.15rem 0;
}

.barra {
  position: sticky;
  bottom: 0;
  /* A faixa de gestos do iPhone come os 16px de baixo desde que a
     aplicação passou a usar `viewport-fit=cover`. Sem somar o inset, o botão
     principal do produto fica embaixo dela. */
  padding: var(--e3) 0 calc(var(--e4) + env(safe-area-inset-bottom, 0px));
  background: linear-gradient(to top, var(--fundo) 70%, transparent);
}

@media (width <= 560px) {
  .confronto {
    grid-template-columns: 1fr auto 1fr;
    gap: var(--e2);
  }

  /* No celular o nome vai para baixo do escudo: cabe inteiro e o marcador
     continua no centro, que é onde o polegar está. O mandante inverte a
     coluna porque no HTML o nome dele vem antes — assim os dois escudos
     ficam na mesma altura. */
  .time {
    gap: var(--e1);
    text-align: center;
    font-size: 0.82rem;
    justify-content: center;
  }

  .time--casa { flex-direction: column-reverse; }
  .time--fora { flex-direction: column; }
  .time--casa .time__texto, .time--fora .time__texto { align-items: center; }
  .time__nome { white-space: normal; max-height: 2.5em; }
  .entrada input { width: 2.4rem; font-size: 1.15rem; }
}
</style>
