<script setup lang="ts">
/**
 * Escudo do clube.
 *
 * Cai para a sigla quando não há imagem — e a sigla também é desenhada em
 * forma de brasão, para a lista não ficar com dois formatos brigando quando
 * só metade dos clubes tem escudo.
 */
const props = withDefaults(
  defineProps<{
    nome: string
    sigla?: string | null
    escudo?: string | null
    tamanho?: number
  }>(),
  { sigla: null, escudo: null, tamanho: 34 },
)

const falhou = ref(false)

/**
 * Sigla de três letras. Nome composto vira as iniciais das palavras
 * ("Real Sociedad" → RS) e nome simples vira o começo ("Arsenal" → ARS):
 * cortar "Real Sociedad" em "REA" não ajuda ninguém a reconhecer o clube.
 */
const iniciais = computed(() => {
  if (props.sigla) return props.sigla.toUpperCase().slice(0, 3)

  const palavras = props.nome
    .split(/[\s-]+/)
    .filter(palavra => palavra.length > 2 || /^\d/.test(palavra))

  return (palavras.length > 1
    ? palavras.map(palavra => palavra[0]).join('')
    : props.nome).toUpperCase().slice(0, 3)
})
</script>

<template>
  <span
    class="escudo"
    :style="{ '--tam': `${tamanho}px` }"
    :title="nome"
  >
    <img
      v-if="escudo && !falhou"
      :src="escudo"
      :alt="`Escudo do ${nome}`"
      loading="lazy"
      @error="falhou = true"
    >
    <span
      v-else
      class="escudo__sigla"
      aria-hidden="true"
    >{{ iniciais }}</span>
  </span>
</template>

<style scoped>
.escudo {
  width: var(--tam);
  height: var(--tam);
  flex: 0 0 var(--tam);
}

.escudo__sigla { font-size: calc(var(--tam) * 0.32); }
</style>
