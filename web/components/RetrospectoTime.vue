<script setup lang="ts">
/**
 * Retrospecto: os últimos jogos do clube, do mais recente para o mais antigo.
 *
 * Cor sozinha não basta — daltonismo vermelho-verde é o tipo mais comum, e é
 * exatamente o par que separa vitória de derrota. Por isso cada quadradinho
 * carrega a letra, e o título de cada um diz por extenso.
 */
const props = withDefaults(
  defineProps<{
    /** Sequência ``V``/``E``/``D``, a mais recente primeiro. */
    resumo: string
    nome?: string
    tamanho?: number
  }>(),
  { nome: '', tamanho: 18 },
)

const LEGENDA: Record<string, string> = { V: 'vitória', E: 'empate', D: 'derrota' }

/** Invertido na tela: a leitura natural é da esquerda (antigo) para a direita (agora). */
const jogos = computed(() =>
  [...props.resumo.toUpperCase()]
    .filter(letra => letra in LEGENDA)
    .reverse()
    .map((letra, indice) => ({ letra, indice })),
)
</script>

<template>
  <span
    v-if="jogos.length"
    class="retrospecto"
    :style="{ '--quad': `${tamanho}px` }"
    :aria-label="`Últimos jogos${nome ? ` do ${nome}` : ''}: ${jogos.map(j => LEGENDA[j.letra]).join(', ')}`"
    role="img"
  >
    <span
      v-for="jogo in jogos"
      :key="jogo.indice"
      class="quad"
      :class="`quad--${jogo.letra}`"
      :title="LEGENDA[jogo.letra]"
      aria-hidden="true"
    >{{ jogo.letra }}</span>
  </span>
</template>

<style scoped>
.retrospecto {
  display: inline-flex;
  gap: 2px;
  vertical-align: middle;
}

.quad {
  width: var(--quad);
  height: var(--quad);
  border-radius: 4px;
  display: inline-flex;
  align-items: center;
  justify-content: center;
  font-size: calc(var(--quad) * 0.6);
  font-weight: 800;
  line-height: 1;
  color: #fff;
  /* O último jogo é o que mais informa. A 72% a letra caía para ~3,4:1 e
     deixava de ser legível — o destaque agora vem do contorno no mais recente,
     não de apagar os outros. */
  opacity: 0.88;
}

.quad:last-child {
  opacity: 1;
  outline: 2px solid currentcolor;
  outline-offset: 1px;
}

.quad--V { background: var(--verde); }
.quad--E { background: var(--texto-fraco); }
.quad--D { background: var(--alerta); }

@media (prefers-color-scheme: dark) {
  .quad { color: #08100c; }
}
</style>
