<script setup lang="ts">
definePageMeta({ middleware: 'auth' })

/**
 * Regulamento do bolão.
 *
 * Nada aqui é texto fixo sobre pontuação: os números e a ordem vêm do mesmo
 * motor que apura. Regulamento escrito à mão diverge da configuração no dia em
 * que alguém mexe nos pontos, e aí a discussão vira "o site diz uma coisa e a
 * página diz outra".
 */

const route = useRoute()
const slug = computed(() => route.params.slug as string)

interface Criterio {
  key: string
  label: string
  description: string
  points: number
}
interface Regras {
  pool_name: string
  scoring_version: number
  max_points_per_fixture: number
  fixtures_included: number
  max_points_total: number
  criteria: Criterio[]
  tiebreak: string[]
  bonus: Record<string, number>
  notes: string[]
}

const { data: regras } = useApiData<Regras>(
  `regras-${slug.value}`,
  () => `/v1/pools/${slug.value}/rules`,
)

const ROTULOS_BONUS: Record<string, string> = {
  knockout_advance: 'Acertar quem avança no mata-mata',
  champion: 'Acertar o campeão',
  top4: 'Cada acerto no G-4',
  relegated: 'Cada acerto nos rebaixados',
}

const bonusAtivos = computed(() =>
  Object.entries(regras.value?.bonus ?? {})
    .filter(([, pontos]) => pontos > 0)
    .map(([chave, pontos]) => ({ rotulo: ROTULOS_BONUS[chave] ?? chave, pontos })),
)

useHead({ title: 'Como funciona' })
</script>

<template>
  <div
    v-if="regras"
    class="estreito"
  >
    <NuxtLink
      :to="`/b/${slug}`"
      class="pequeno"
    >
      ← voltar ao bolão
    </NuxtLink>
    <h1 style="margin-top: 0.5rem">
      Como funciona
    </h1>
    <p class="fraco pequeno">
      Regras do <strong>{{ regras.pool_name }}</strong>, versão
      {{ regras.scoring_version }} da pontuação.
    </p>

    <div class="card">
      <h2>Pontuação por jogo</h2>
      <p class="fraco pequeno">
        Seu palpite leva <strong>o critério de maior valor que ele satisfaz</strong>.
        Quem crava o placar nunca ganha menos que quem só acertou o vencedor.
      </p>

      <table>
        <thead>
          <tr>
            <th>Você acertou</th>
            <th class="num">
              Vale
            </th>
          </tr>
        </thead>
        <tbody>
          <tr
            v-for="criterio in regras.criteria"
            :key="criterio.key"
          >
            <td>
              <strong>{{ criterio.label }}</strong><br>
              <span class="fraco pequeno">{{ criterio.description }}</span>
            </td>
            <td class="num pontos">
              {{ criterio.points }}
            </td>
          </tr>
          <tr>
            <td>
              <strong>Nada</strong><br>
              <span class="fraco pequeno">Errou, ou não palpitou antes do apito.</span>
            </td>
            <td class="num pontos">
              0
            </td>
          </tr>
        </tbody>
      </table>

      <p
        class="fraco pequeno"
        style="margin-top: 0.75rem"
      >
        A ordem acima é a ordem de avaliação, e ela vem dos pontos — não é fixa.
        Se o organizador fizer "só o vencedor" valer mais que "placar exato",
        a lista se reorganiza sozinha.
      </p>
    </div>

    <div
      v-if="bonusAtivos.length"
      class="card"
    >
      <h2>Bônus</h2>
      <table>
        <tbody>
          <tr
            v-for="bonus in bonusAtivos"
            :key="bonus.rotulo"
          >
            <td>{{ bonus.rotulo }}</td>
            <td class="num pontos">
              {{ bonus.pontos }}
            </td>
          </tr>
        </tbody>
      </table>
      <p class="fraco pequeno">
        Bônus soma ao total, mas <strong>não entra no desempate</strong> — ele
        continua sendo decidido pelos palpites de placar.
      </p>
    </div>

    <div class="card">
      <h2>Ranking e desempate</h2>
      <p>
        O ranking é a soma dos pontos de todos os jogos que valem neste bolão.
        Quando dois participantes empatam, a ordem é decidida assim:
      </p>
      <ol class="desempate">
        <li
          v-for="(criterio, indice) in regras.tiebreak"
          :key="criterio"
        >
          quem tem mais acertos em <strong>{{ criterio }}</strong>
          <span
            v-if="indice === 0"
            class="fraco pequeno"
          > — o critério que este bolão mais valoriza</span>
        </li>
        <li class="fraco">
          quem entrou antes no bolão
        </li>
      </ol>
      <p class="fraco pequeno">
        Empate de verdade — mesma pontuação e mesmos acertos em tudo —
        <strong>divide a posição</strong>. Dois segundos lugares e nenhum
        terceiro, como em qualquer classificação esportiva.
      </p>
    </div>

    <div class="card">
      <h2>Neste bolão</h2>
      <dl class="numeros">
        <div>
          <dt>Jogos valendo</dt>
          <dd class="num">
            {{ regras.fixtures_included }}
          </dd>
        </div>
        <div>
          <dt>Máximo por jogo</dt>
          <dd class="num">
            {{ regras.max_points_per_fixture }}
          </dd>
        </div>
        <div>
          <dt>Teto do campeonato</dt>
          <dd class="num">
            {{ regras.max_points_total }}
          </dd>
        </div>
      </dl>
      <p class="fraco pequeno">
        O teto não considera multiplicadores de rodada nem bônus.
      </p>
    </div>

    <div class="card">
      <h2>O resto das regras</h2>
      <ul class="notas">
        <li
          v-for="nota in regras.notes"
          :key="nota"
        >
          {{ nota }}
        </li>
      </ul>
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
.estreito { max-width: 42rem; margin-inline: auto; }
.pontos { font-weight: 800; font-size: 1.05rem; width: 4rem; }
.desempate { padding-left: 1.2rem; margin: 0.5rem 0; }
.desempate li { margin-bottom: 0.25rem; }
.notas { padding-left: 1.2rem; margin: 0; }
.notas li { margin-bottom: 0.35rem; }

.numeros {
  display: grid;
  grid-template-columns: repeat(auto-fit, minmax(8rem, 1fr));
  gap: 0.75rem;
  margin: 0 0 0.5rem;
}

.numeros dt { font-size: 0.8rem; color: var(--texto-fraco); }
.numeros dd { margin: 0; font-size: 1.5rem; font-weight: 800; }
</style>
