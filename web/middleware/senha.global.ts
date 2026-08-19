/**
 * Quem teve a senha redefinida por outra pessoa passa pelo perfil primeiro.
 *
 * Quando quem administra redefine a senha de alguém, essa senha é conhecida
 * por um terceiro. Deixá-la valendo indefinidamente transformaria um socorro
 * ("perdi minha senha") numa conta compartilhada sem ninguém perceber. O
 * servidor marca a conta, e aqui a navegação é desviada até ela escolher a
 * própria.
 *
 * `.global` porque vale para toda tela: sem isso bastaria digitar qualquer
 * outro endereço para contornar.
 *
 * Não é uma prisão: sair continua funcionando, e as telas de entrada seguem
 * livres. O que não dá é usar a plataforma com uma senha que não é sua.
 */

/** Onde a pessoa PODE estar mesmo devendo a troca. */
const LIVRES = ['/perfil', '/entrar', '/sair']

export default defineNuxtRouteMiddleware((to) => {
  const { user } = useAuth()
  if (!user.value?.must_change_password) return
  if (LIVRES.some(caminho => to.path === caminho || to.path.startsWith(`${caminho}/`))) return

  return navigateTo('/perfil')
})
