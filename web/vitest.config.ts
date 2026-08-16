import { fileURLToPath } from 'node:url'

import vue from '@vitejs/plugin-vue'
import { defineConfig } from 'vitest/config'

export default defineConfig({
  plugins: [vue()],
  resolve: {
    alias: {
      '~': fileURLToPath(new URL('./', import.meta.url)),
      '@': fileURLToPath(new URL('./', import.meta.url)),
    },
  },
  test: {
    environment: 'happy-dom',
    include: ['tests/**/*.spec.ts'],
    // O fuso do runner é irrelevante: as funções de data recebem o fuso
    // explicitamente. Fixamos UTC para que um runner mal configurado não
    // esconda um bug de conversão.
    env: { TZ: 'UTC' },
    coverage: {
      provider: 'v8',
      include: ['utils/**/*.ts', 'composables/**/*.ts'],
    },
  },
})
