import { createConfigForNuxt } from '@nuxt/eslint-config/flat'

export default createConfigForNuxt({
  features: { stylistic: true },
}).append({
  ignores: ['.nuxt/**', '.output/**', 'node_modules/**', 'dist/**', 'coverage/**'],
})
