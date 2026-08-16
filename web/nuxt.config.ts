export default defineNuxtConfig({
  // SSR ligado e inegociável: metade do produto são as landings de campeonato
  // indexadas pelo Google.
  ssr: true,

  // Desligado de propósito. Aqui o `docker compose up` é o modo que a pessoa
  // realmente usa no dia a dia — não existe um "modo produção" separado numa
  // instalação caseira. O DevTools injeta um overlay que captura cliques, um
  // iframe e um canal RPC que polui o console, sem contrapartida.
  devtools: { enabled: false },

  app: {
    head: {
      htmlAttrs: { lang: 'pt-BR' },
      meta: [
        { charset: 'utf-8' },
        // `viewport-fit=cover` é o que permite o conteúdo chegar até a borda
        // no iPhone com entalhe; sem ele o sistema deixa duas faixas pretas.
        // O respiro seguro volta pelo `env(safe-area-inset-*)` no CSS.
        { name: 'viewport', content: 'width=device-width, initial-scale=1, viewport-fit=cover' },

        // A cor da barra do navegador acompanha o tema do aparelho. Sem as
        // duas linhas, o modo escuro ganha uma tarja clara no topo.
        //
        // O `key` é obrigatório: o Nuxt deduplica `meta` pelo `name`, e sem ele
        // a segunda declaração simplesmente apaga a primeira — sobra uma cor
        // só, sem `media`, que é o que estava acontecendo.
        {
          key: 'theme-claro',
          name: 'theme-color',
          content: '#f4f6f7',
          media: '(prefers-color-scheme: light)',
        },
        {
          key: 'theme-escuro',
          name: 'theme-color',
          content: '#0b0d11',
          media: '(prefers-color-scheme: dark)',
        },

        // O Safari ignora boa parte do manifest e lê estas.
        { name: 'apple-mobile-web-app-capable', content: 'yes' },
        { name: 'apple-mobile-web-app-title', content: 'Bolão' },
        { name: 'apple-mobile-web-app-status-bar-style', content: 'black-translucent' },
        { name: 'mobile-web-app-capable', content: 'yes' },

        // Números de telefone virando link é útil num site de restaurante e
        // atrapalha aqui: "3 x 1" e datas viram links no iOS.
        { name: 'format-detection', content: 'telephone=no' },
      ],
      link: [
        { rel: 'manifest', href: '/manifest.webmanifest' },
        { rel: 'icon', href: '/favicon-32.png', sizes: '32x32', type: 'image/png' },
        { rel: 'icon', href: '/icone.svg', type: 'image/svg+xml' },
        { rel: 'apple-touch-icon', href: '/apple-touch-icon.png', sizes: '180x180' },
      ],
    },
  },

  css: ['~/assets/css/main.css'],

  runtimeConfig: {
    // Só no servidor. Durante o SSR o Nuxt fala com a API pela rede interna do
    // Docker, sem passar pelo nginx — uma URL relativa aqui bateria no próprio
    // Nitro e devolveria 404.
    apiInternal: process.env.NUXT_API_INTERNAL || 'http://api:8000',

    public: {
      // No navegador o nginx expõe a API sob /api, no mesmo host.
      apiBase: process.env.NUXT_PUBLIC_API_BASE || '/api',
      displayTimezone: 'America/Sao_Paulo',
    },
  },
  compatibilityDate: '2025-01-01',

  // HMR atrás do nginx dentro do Docker.
  vite: {
    server: {
      hmr: { clientPort: Number(process.env.NGINX_HOST_PORT) || 8080 },
      watch: { usePolling: true },
    },
  },

  typescript: {
    strict: true,
    typeCheck: false, // roda separado via `npm run typecheck`, fora do dev server
  },
})
