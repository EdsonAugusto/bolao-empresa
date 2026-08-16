/*
 * Service worker da plataforma.
 *
 * O que ele faz é menos interessante do que o que ele se recusa a fazer.
 *
 * Esta é uma aplicação com renderização no servidor e conteúdo por pessoa: a
 * mesma URL devolve o palpite de quem está logado. Um service worker
 * "esperto", desses que guardam a última página vista para mostrar offline,
 * aqui significaria servir a tela de uma pessoa para outra que usasse o mesmo
 * aparelho — e mostrar placar velho como se fosse o de agora.
 *
 * Então a regra é:
 *
 *   /_nuxt/*                     → cache primeiro. Têm hash de conteúdo no
 *                                  nome, então nunca mudam sob a mesma URL — é
 *                                  daí que vem a abertura instantânea.
 *   ícone, manifest, offline     → devolve do cache e revalida em segundo
 *                                  plano. Não têm hash: cache-first prenderia
 *                                  o ícone velho na tela de início para sempre.
 *   /api/*                       → só rede. Palpite, placar e ranking nunca
 *                                  saem do cache, nem em último caso.
 *   navegação (HTML)             → só rede, com a tela offline como consolo
 *                                  quando não há conexão nenhuma.
 *
 * Ou seja: a instalação serve para abrir rápido e ter ícone na tela de início,
 * não para fingir que funciona sem internet. Um bolão sem placar atualizado
 * não teria graça.
 */

/*
 * A versão vem do build, pela URL de registro (`/sw.js?v=<buildId>`).
 *
 * Com um número fixo escrito à mão, o nome do cache não mudaria entre deploys:
 * a limpeza do `activate` nunca descartaria nada e o cache só cresceria, com os
 * arquivos de todas as versões já publicadas. Como cada build do Nuxt gera um
 * `buildId` novo, o service worker também passa a ser um arquivo diferente aos
 * olhos do navegador — que é justamente o que dispara a atualização dele.
 */
const BUILD = new URL(self.location.href).searchParams.get('v')
const VERSAO = `bolao-${BUILD}`
const CACHE_ESTATICO = `${VERSAO}-estatico`

/*
 * Sem versão na URL, este service worker se apaga.
 *
 * Isso acontece de verdade e é feio: uma instalação antiga — de antes de a
 * versão existir, ou deixada por um teste da build de produção na mesma porta
 * — continua servindo `/_nuxt/` do cache. Em desenvolvimento aqueles arquivos
 * já não existem, o navegador recebe CSS onde esperava módulo, e a página não
 * hidrata. O código que desregistraria o service worker está justamente nessa
 * página que não carrega.
 *
 * Então a saída tem que estar aqui dentro: sem versão, não serve nada do
 * cache, limpa o que guardou e desregistra.
 */
if (!BUILD) {
  self.addEventListener('install', () => self.skipWaiting())
  self.addEventListener('activate', (evento) => {
    evento.waitUntil(
      caches.keys()
        .then(nomes => Promise.all(nomes.map(nome => caches.delete(nome))))
        .then(() => self.registration.unregister())
        .then(() => self.clients.matchAll({ type: 'window' }))
        .then(abas => abas.forEach(aba => aba.navigate(aba.url))),
    )
  })
}

/** O mínimo para a tela offline aparecer sem depender da rede. */
const ESSENCIAIS = [
  '/offline.html',
  '/icone.svg',
  '/icone-192.png',
  '/favicon-32.png',
  '/manifest.webmanifest',
]

self.addEventListener('install', (evento) => {
  if (!BUILD) return
  evento.waitUntil(
    caches
      .open(CACHE_ESTATICO)
      // `addAll` é tudo-ou-nada: um 404 num item abortaria a instalação
      // inteira e o service worker nunca assumiria. Item a item, o que falhar
      // apenas não entra no cache.
      //
      // Sem `skipWaiting()` aqui, de propósito: a versão nova espera. Quem
      // manda ela assumir é a página, pela mensagem `assumir-agora`. Assumir
      // sozinha, no meio de alguém preenchendo palpite, custaria o palpite.
      .then(cache => Promise.allSettled(ESSENCIAIS.map(url => cache.add(url)))),
  )
})

self.addEventListener('activate', (evento) => {
  if (!BUILD) return
  evento.waitUntil(
    caches
      .keys()
      .then(nomes => Promise.all(
        nomes.filter(nome => !nome.startsWith(VERSAO)).map(nome => caches.delete(nome)),
      ))
      .then(() => self.clients.claim()),
  )
})

/**
 * Recurso de build: o nome carrega o hash do conteúdo, então nunca muda.
 *
 * Vale para `/_nuxt/` inteiro porque este service worker só é registrado na
 * versão de produção — é lá que os arquivos saem como `3-MMeAUZ.js` e
 * `entrar.CvNLAJRL.css`. Em desenvolvimento o Vite serve `/_nuxt/pages/x.vue`,
 * que muda a cada gravação; por isso o plugin nem chega a registrar o service
 * worker naquele modo (e desfaz um registro antigo se encontrar).
 */
function eImutavel(url) {
  // SÓ `/_nuxt/`. Ícone, manifest e a tela offline não têm hash no nome: se
  // entrassem aqui, trocar a cor do ícone ou corrigir o texto da tela offline
  // nunca chegaria a quem já instalou — nem reinstalando o atalho, porque o
  // manifest também viria do cache.
  return url.pathname.startsWith('/_nuxt/')
}

/** Recurso sem hash que vale a pena ter offline, mas precisa poder mudar. */
function eRevalidavel(url) {
  const caminho = url.pathname
  return (
    caminho === '/manifest.webmanifest'
    || caminho === '/offline.html'
    || /^\/(icone|favicon|apple-touch-icon)[\w-]*\.(png|svg)$/.test(caminho)
  )
}

self.addEventListener('fetch', (evento) => {
  // Sem versão, nada é interceptado: tudo vai direto para a rede enquanto o
  // `activate` acima termina de se desfazer.
  if (!BUILD) return

  const { request } = evento

  // POST, PATCH e DELETE não passam por aqui de jeito nenhum.
  if (request.method !== 'GET') return

  const url = new URL(request.url)

  // Outro domínio é problema de quem o serve.
  if (url.origin !== self.location.origin) return

  // A API nunca é cacheada, e o fluxo de eventos ao vivo muito menos — um
  // service worker no meio de um SSE segura o evento até a conexão fechar.
  if (url.pathname.startsWith('/api/')) return

  if (eImutavel(url)) {
    evento.respondWith(
      caches.match(request).then(guardado => guardado || fetch(request).then((resposta) => {
        if (resposta.ok) {
          const copia = resposta.clone()
          // `waitUntil` e não solto: sem ele a gravação pode ser cancelada se
          // o service worker for encerrado logo depois de responder.
          evento.waitUntil(caches.open(CACHE_ESTATICO).then(cache => cache.put(request, copia)))
        }
        return resposta
      })),
    )
    return
  }

  // Devolve o guardado na hora e busca a versão nova em segundo plano: abre
  // rápido e ainda assim atualiza. É o comportamento certo para ícone e
  // manifest, que mudam raramente mas precisam poder mudar.
  if (eRevalidavel(url)) {
    evento.respondWith(
      caches.match(request).then((guardado) => {
        const daRede = fetch(request).then((resposta) => {
          if (resposta.ok) {
            const copia = resposta.clone()
            evento.waitUntil(caches.open(CACHE_ESTATICO).then(cache => cache.put(request, copia)))
          }
          return resposta
        }).catch(() => guardado)
        return guardado || daRede
      }),
    )
    return
  }

  // Navegação: rede sempre. Sem conexão, a tela offline — que diz o que
  // aconteceu em vez de deixar o navegador mostrar o dinossauro.
  if (request.mode === 'navigate') {
    evento.respondWith(
      fetch(request).catch(() => caches.match('/offline.html').then(
        pagina => pagina || new Response(
          'Sem conexão.',
          { status: 503, headers: { 'Content-Type': 'text/plain; charset=utf-8' } },
        ),
      )),
    )
  }
})

// Permite que a página peça a troca imediata quando uma versão nova é
// detectada, em vez de esperar todas as abas fecharem.
self.addEventListener('message', (evento) => {
  if (evento.data === 'assumir-agora') self.skipWaiting()
})
