<script setup lang="ts">
// O manifest, os ícones e as cores de barra estão em `nuxt.config.ts`, junto
// do resto do `head`. Ficavam duplicados aqui, e a `theme-color` sem `media`
// deste arquivo vinha por último no HTML — ou seja, vencia as duas versões por
// tema e a barra do navegador ficava verde nos dois.
useHead({
  titleTemplate: title => (title ? `${title} · Bolão` : 'Bolão'),
})
</script>

<template>
  <NuxtLayout>
    <NuxtPage />
  </NuxtLayout>
</template>
