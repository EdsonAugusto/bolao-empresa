/**
 * Mantém a sessão de pé quando o app volta do segundo plano.
 *
 * No aplicativo instalado ninguém "abre o site": a pessoa volta a ele. O
 * sistema congela a página por horas ou dias e depois a devolve exatamente
 * como estava — com um token de acesso que venceu enquanto ela estava
 * guardada.
 *
 * Sem isto, a primeira coisa que a pessoa faz ao voltar toma 401, e só então o
 * app renova. Na melhor das hipóteses é uma tela vazia que se enche depois; na
 * pior, ela toca num botão que não responde. Renovar ANTES, assim que a página
 * volta a ficar visível, faz a sessão parecer nunca ter parado.
 *
 * `.client` porque `document` não existe no servidor.
 */
export default defineNuxtPlugin(() => {
  const { isLoggedIn, loadMe } = useAuth()

  /** Quanto tempo escondido já justifica renovar antes de perguntar qualquer coisa. */
  const LIMITE_MS = 60_000

  let escondidoDesde: number | null = null

  document.addEventListener('visibilitychange', () => {
    if (document.visibilityState === 'hidden') {
      escondidoDesde = Date.now()
      return
    }

    const parado = escondidoDesde === null ? 0 : Date.now() - escondidoDesde
    escondidoDesde = null

    // Trocar de aba por dez segundos não precisa de nada. O caso que importa é
    // o app que passou a noite fechado.
    if (!isLoggedIn.value || parado < LIMITE_MS) return

    // `loadMe` usa `apiFetch`, que já renova sozinho ao tomar 401 — e agora só
    // desloga se o servidor recusar, não se a rede falhar. Aqui basta provocar
    // essa passagem enquanto ninguém está esperando por uma tela.
    void loadMe()
  })
})
