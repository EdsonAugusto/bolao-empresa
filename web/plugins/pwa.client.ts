/**
 * Registra o service worker e observa o que o navegador diz sobre instalação.
 *
 * `.client` no nome porque nada disto existe no servidor: `navigator` e
 * `window` não estão lá, e tentar tocá-los durante o SSR derruba a página
 * inteira em vez de degradar.
 */
export default defineNuxtPlugin(() => {
  const evento = useState<Event | null>('pwa:evento', () => null)
  const instalado = useState<boolean>('pwa:instalado', () => false)
  const sistema = useState<'ios' | 'outro'>('pwa:sistema', () => 'outro')
  const pronto = useState<boolean>('pwa:pronto', () => false)

  // --- estado do aparelho ---------------------------------------------------
  const agente = navigator.userAgent
  // O iPadOS recente se anuncia como Mac; o toque é o que o denuncia.
  const eIOS = /iPad|iPhone|iPod/.test(agente)
    || (agente.includes('Macintosh') && navigator.maxTouchPoints > 1)
  sistema.value = eIOS ? 'ios' : 'outro'

  const modoAplicativo = () =>
    window.matchMedia('(display-mode: standalone)').matches
    || window.matchMedia('(display-mode: window-controls-overlay)').matches
    // Propriedade só do Safari, e a única forma de saber no iPhone.
    || (window.navigator as { standalone?: boolean }).standalone === true

  instalado.value = modoAplicativo()
  pronto.value = true

  window.addEventListener('beforeinstallprompt', (e) => {
    // Sem isto o Chrome mostra o próprio aviso e o botão da tela vira um
    // segundo pedido para a mesma coisa.
    e.preventDefault()
    evento.value = e
  })

  window.addEventListener('appinstalled', () => {
    instalado.value = true
    evento.value = null
  })

  // --- service worker -------------------------------------------------------
  if (!('serviceWorker' in navigator)) return

  // Em desenvolvimento o Vite serve os módulos sem hash no nome
  // (`/_nuxt/pages/entrar.vue`), então guardá-los em cache congelaria o código
  // de quem está editando e quebraria o recarregamento a quente. O
  // `unregister` cobre quem rodou a versão de produção na mesma porta e ficou
  // com um service worker antigo instalado — sem ele, a máquina serviria o
  // cache velho para sempre.
  if (import.meta.dev) {
    navigator.serviceWorker.getRegistrations().then((registros) => {
      registros.forEach(registro => registro.unregister())
    })
    return
  }

  // Contexto inseguro não registra service worker. Acontece de verdade aqui:
  // por IP da rede local a plataforma roda em http:// e a instalação não é
  // oferecida — é justamente o que o domínio com HTTPS resolve.
  if (!window.isSecureContext) return

  // `jaControlado` decide se uma troca de controlador é atualização ou
  // primeira instalação. Sem ele, `clients.claim()` no `activate` dispara
  // `controllerchange` na PRIMEIRA visita e a página recarrega sozinha — no
  // meio de alguém digitando e-mail e senha, ou um palpite.
  const jaControlado = Boolean(navigator.serviceWorker.controller)

  function registrar() {
    // A versão do build entra na URL: é o que faz o navegador enxergar um
    // arquivo diferente e buscar a versão nova, e é de onde o próprio service
    // worker tira o nome do cache para descartar o da versão anterior.
    const versao = useRuntimeConfig().app.buildId || 'sem-versao'

    navigator.serviceWorker.register(`/sw.js?v=${versao}`, { scope: '/' }).then((registro) => {
      registro.addEventListener('updatefound', () => {
        const novo = registro.installing
        if (!novo) return
        novo.addEventListener('statechange', () => {
          // `controller` presente significa que já havia uma versão servindo:
          // é atualização, não primeira instalação.
          if (novo.state === 'installed' && navigator.serviceWorker.controller) {
            novo.postMessage('assumir-agora')
          }
        })
      })
    }).catch(() => {
      // Falhar em registrar não pode custar a aplicação: sem service worker
      // tudo continua funcionando, só não abre tão rápido nem instala.
    })
  }

  // Esperar `load` cegamente não funciona: num app com renderização no
  // servidor a hidratação costuma acontecer DEPOIS do `load`, e o ouvinte
  // registrado aqui nunca dispararia — o service worker simplesmente não era
  // instalado. Por isso o estado é conferido antes.
  if (document.readyState === 'complete') registrar()
  else window.addEventListener('load', registrar, { once: true })

  let recarregando = false
  navigator.serviceWorker.addEventListener('controllerchange', () => {
    // Só recarrega quando havia um controlador antes — ou seja, quando isto é
    // de fato uma troca de versão. Na primeira instalação não há o que
    // recarregar, e recarregar ali apagaria o formulário que a pessoa está
    // preenchendo. `recarregando` continua guardando contra laço.
    if (!jaControlado || recarregando) return
    recarregando = true
    window.location.reload()
  })
})
