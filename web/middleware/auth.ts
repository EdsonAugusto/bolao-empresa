/** Exige sessão. Guarda o destino para voltar depois de entrar. */
export default defineNuxtRouteMiddleware((to) => {
  const { access } = useTokens()
  if (!access.value) {
    return navigateTo({ path: '/entrar', query: { destino: to.fullPath } })
  }
})
