<script setup lang="ts">
definePageMeta({ middleware: 'auth' })

/**
 * Detalhamento da pontuação de um jogo.
 *
 * Existe para responder "por que eu levei 7 e ele 10" sem ninguém precisar
 * abrir o regulamento — a explicação vem do mesmo código que pontuou.
 */

const route = useRoute()
const slug = computed(() => route.params.slug as string)
const fixtureId = computed(() => Number(route.params.fid))

interface Detalhe {
  fixture_id: number
  membership_id: number
  display_name: string
  is_me: boolean
  prediction: string | null
  actual: string | null
  criterion: string
  reason: string
  base_points: number
  multiplier: number
  final_points: number
}

const { data: detalhes, error } = useApiData<Detalhe[]>(
  `breakdown-${fixtureId.value}`,
  () => `/v1/pools/${slug.value}/fixtures/${fixtureId.value}/breakdown`,
)

useHead({ title: 'Como cada um pontuou' })
</script>

<template>
  <div>
    <NuxtLink
      :to="`/b/${slug}`"
      class="pequeno"
    >← voltar ao bolão</NuxtLink>
    <h1 style="margin-top: 0.5rem">
      Como cada um pontuou
    </h1>

    <p
      v-if="error"
      class="aviso aviso--atencao"
    >
      {{ (error as Error).message }}
    </p>

    <div
      v-else-if="!detalhes?.length"
      class="card vazio"
    >
      Este jogo ainda não foi apurado.
    </div>

    <div
      v-else
      class="card tabela-rolavel"
    >
      <p class="fraco pequeno">
        Resultado: <strong>{{ detalhes[0]?.actual }}</strong>
      </p>
      <table>
        <thead>
          <tr>
            <th scope="col">
              Quem
            </th>
            <th scope="col">
              Palpite
            </th>
            <th scope="col">
              Critério
            </th>
            <th
              scope="col"
              class="num"
            >
              Base
            </th>
            <th
              scope="col"
              class="num"
            >
              Mult.
            </th>
            <th
              scope="col"
              class="num"
            >
              Total
            </th>
          </tr>
        </thead>
        <tbody>
          <tr
            v-for="(item, indice) in detalhes"
            :key="item.membership_id"
            :class="{ 'linha--eu': item.is_me }"
          >
            <!-- A posição vem da ordem que a API já mandou (pontos, depois
                 nome), então não há o que recalcular aqui. -->
            <th
              scope="row"
              class="quem"
            >
              <span class="pos">{{ indice + 1 }}º</span>
              <span class="nome">{{ item.display_name }}</span>
              <span
                v-if="item.is_me"
                class="tag"
              >você</span>
            </th>
            <td class="num">
              {{ item.prediction ?? '—' }}
            </td>
            <td
              class="criterio"
              :title="item.reason"
            >
              {{ item.reason }}
            </td>
            <td class="num">
              {{ item.base_points }}
            </td>
            <td class="num fraco">
              {{ item.multiplier }}×
            </td>
            <td class="num">
              <strong>{{ item.final_points }}</strong>
            </td>
          </tr>
        </tbody>
      </table>

      <p class="fraco pequeno rodape-tabela">
        {{ detalhes.length }} participante(s) palpitaram neste jogo.
        Quem não palpitou não aparece — e não pontuou.
      </p>
    </div>
  </div>
</template>

<style scoped>
.quem {
  /* `th` de linha vem em negrito e centralizado por padrão do navegador. */
  font-weight: 600;
  text-align: left;
  display: flex;
  align-items: baseline;
  gap: var(--e2);
  white-space: nowrap;
}

.pos {
  color: var(--texto-fraco);
  font-variant-numeric: tabular-nums;
  font-size: 0.82rem;
  min-width: 1.8rem;
}

.nome { overflow: hidden; text-overflow: ellipsis; }

/* A própria linha em destaque: é a primeira coisa que a pessoa procura ao
   abrir esta tela. */
.linha--eu { background: var(--verde-fraco); }
.linha--eu .nome { font-weight: 700; }
.linha--eu .tag { background: var(--verde); color: var(--sobre-verde); border-color: transparent; }

.rodape-tabela { margin: var(--e3) 0 0; }

@media (width <= 560px) {
  /* Em tela estreita o critério é o que mais ocupa e o que menos se lê de
     relance — ele some, e continua disponível no toque longo pelo `title`. */
  .criterio { display: none; }
  .pos { display: none; }
}
</style>
