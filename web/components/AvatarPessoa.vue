<script setup lang="ts">
/**
 * Foto de perfil, com recuo para as iniciais.
 *
 * Quase ninguém escolhe avatar no primeiro dia, então o caso comum é NÃO ter
 * foto — e uma silhueta cinza igual para todo mundo não ajuda a distinguir
 * ninguém numa lista de vinte pessoas. As iniciais sobre uma cor derivada do
 * nome dão identidade de graça: a mesma pessoa tem sempre a mesma cor, em
 * qualquer tela, sem guardar nada.
 */

const props = withDefaults(defineProps<{
  nome: string
  url?: string | null
  tamanho?: number
}>(), { url: null, tamanho: 40 })

/** Duas letras: a primeira do primeiro nome e a do último. */
const iniciais = computed(() => {
  const partes = props.nome.trim().split(/\s+/).filter(Boolean)
  if (!partes.length) return '?'
  if (partes.length === 1) return partes[0]!.slice(0, 2).toUpperCase()
  return (partes[0]![0]! + partes[partes.length - 1]![0]!).toUpperCase()
})

/**
 * Cor derivada do nome, determinística.
 *
 * Sem `Math.random()`: este componente é renderizado no servidor e no
 * navegador, e uma cor diferente nos dois lados quebra a hidratação — o Vue
 * perde os listeners e a tela fica bonita e morta.
 */
const matiz = computed(() => {
  let soma = 0
  for (const letra of props.nome) soma = (soma * 31 + letra.charCodeAt(0)) % 360
  return soma
})
</script>

<template>
  <img
    v-if="url"
    :src="url"
    :alt="`Foto de ${nome}`"
    :width="tamanho"
    :height="tamanho"
    class="avatar"
    :style="{ width: `${tamanho}px`, height: `${tamanho}px` }"
  >
  <span
    v-else
    class="avatar avatar--iniciais"
    :style="{
      width: `${tamanho}px`,
      height: `${tamanho}px`,
      fontSize: `${Math.round(tamanho * 0.38)}px`,
      background: `hsl(${matiz} 42% 32%)`,
    }"
    :aria-label="`Foto de ${nome}`"
    role="img"
  >{{ iniciais }}</span>
</template>

<style scoped>
.avatar {
  display: inline-block;
  border-radius: 50%;
  object-fit: cover;
  flex-shrink: 0;
  /* O avatar é uma ilha de cor própria dentro da página. Sem isto, no tema
     claro o navegador desenha a borda do <img> com a paleta clara e a foto
     ganha um halo que não existe no escuro. */
  background: var(--superficie-3);
}

.avatar--iniciais {
  display: inline-flex;
  align-items: center;
  justify-content: center;
  color: #fff;
  font-weight: 700;
  letter-spacing: 0.02em;
  user-select: none;
}
</style>
