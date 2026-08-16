import type { Permissao } from '~/composables/usePermissoes'

/**
 * Exige a permissão que a tela pede.
 *
 * Esconder o link do menu não basta: o endereço continua digitável, e outras
 * telas linkam para lá. Sem este guarda a pessoa chegava numa tela que
 * respondia 403 em cada botão sem explicar por quê.
 *
 * O mapa fica aqui e não em cada página porque a lista curta num lugar só é
 * mais fácil de conferir contra o que a API exige.
 */
const EXIGIDA: Record<string, Permissao> = {
  '/campeonatos': 'campeonatos.importar',
  '/rede': 'plataforma.configurar',
  '/pessoas': 'usuarios.ver',
  '/pessoas/grupos': 'usuarios.ver',
}

export default defineNuxtRouteMiddleware((to) => {
  const { acesso, pode } = usePermissoes()
  // Sem acesso carregado ainda não há o que decidir: quem não tiver a
  // permissão leva 403 do servidor no primeiro botão, que é a guarda de
  // verdade. Bloquear aqui por falta de dado tiraria a tela de quem pode.
  if (!acesso.value) return

  const exigida = EXIGIDA[to.path]
  if (exigida && !pode(exigida)) {
    return navigateTo('/')
  }
})
