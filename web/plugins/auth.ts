/**
 * Carrega a sessão **nos dois lados**, servidor e navegador.
 *
 * Tem que ser universal, não `.client`. Se o servidor renderizar como visitante
 * e o cliente como logado, as duas árvores não casam, a hidratação falha e o
 * Vue perde os listeners de evento — o sintoma é uma tela que parece certa mas
 * cujos botões não fazem nada.
 *
 * Funciona no servidor porque o token está em cookie (não em localStorage) e
 * `useCookie` enxerga o cookie da requisição.
 */
export default defineNuxtPlugin(async () => {
  const { user, loadMe } = useAuth()

  // O servidor já buscou e o resultado veio junto no payload — `useState`
  // atravessa a hidratação. Buscar de novo no navegador é uma requisição
  // bloqueante repetida a cada carregamento, e ela atrasa a hidratação inteira
  // porque o plugin é `await`. A árvore continua idêntica dos dois lados, que
  // é o que a hidratação exige.
  if (import.meta.client && user.value) {
    return
  }

  await loadMe()

  // As permissões vêm junto da sessão: quase toda tela pergunta algo a elas
  // (o menu, os botões de importar, o painel de pessoas), e buscá-las depois
  // faria a navegação piscar botões que aparecem tarde.
  const { carregar } = usePermissoes()
  await carregar()
})
