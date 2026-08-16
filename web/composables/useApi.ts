/**
 * Cliente da API.
 *
 * Duas coisas não óbvias moram aqui:
 *
 * 1. A base muda conforme o lado. No SSR o Nitro fala direto com o container
 *    da API pela rede interna; no navegador, passa pelo nginx em `/api`.
 * 2. O access token dura 30 minutos. Quando ele expira, este cliente rotaciona
 *    o refresh **uma vez** e repete a requisição. Sem isso o usuário seria
 *    deslogado no meio de preencher uma rodada.
 */

export interface ApiError extends Error {
  status: number
  detail: string
}

interface ErroDeValidacao {
  loc?: (string | number)[]
  msg?: string
  type?: string
  ctx?: Record<string, unknown>
}

/** Nome do campo como ele aparece na tela, não como se chama no schema. */
const CAMPOS: Record<string, string> = {
  email: 'E-mail',
  password: 'Senha',
  display_name: 'Nome',
  invite_code: 'Código de convite',
  title: 'Título',
  body: 'Descrição',
  name: 'Nome',
  csv: 'CSV',
  year: 'Ano',
  url: 'Endereço',
  home_goals: 'Gols do mandante',
  away_goals: 'Gols do visitante',
}

/**
 * Traduz o erro de validação do Pydantic.
 *
 * Ele chega em inglês e no vocabulário do schema — `"String should have at
 * least 8 characters"`, com `loc: ["body", "password"]`. Quem está criando
 * conta vê isso e não sabe nem em qual campo mexer. A regra do produto é o
 * contrário: o erro diz o que fazer.
 *
 * Mensagem que já vem de um `ValueError` nosso passa direto: ela foi escrita
 * em português de propósito.
 */
function emPortugues(item: ErroDeValidacao): string {
  const original = item?.msg ?? ''
  // Nossos validadores levantam ValueError; o Pydantic prefixa com "Value error, ".
  const nosso = original.replace(/^Value error,\s*/, '')
  if (nosso !== original) return nosso

  const chave = String(item?.loc?.[item.loc.length - 1] ?? '')
  const campo = CAMPOS[chave] ?? chave
  const minimo = item?.ctx?.min_length ?? item?.ctx?.ge
  const maximo = item?.ctx?.max_length ?? item?.ctx?.le

  switch (item?.type) {
    case 'missing':
      return `${campo}: preencha este campo`
    case 'string_too_short':
      return `${campo}: use pelo menos ${minimo} caracteres`
    case 'string_too_long':
      return `${campo}: no máximo ${maximo} caracteres`
    case 'value_error.email':
    case 'value_error':
      return `${campo}: valor inválido`
    case 'int_parsing':
    case 'float_parsing':
      return `${campo}: informe um número`
    case 'greater_than_equal':
      return `${campo}: mínimo ${minimo}`
    case 'less_than_equal':
      return `${campo}: máximo ${maximo}`
    default:
      return campo ? `${campo}: ${original}` : original
  }
}

function toApiError(error: unknown): ApiError {
  const anyError = error as { status?: number, statusCode?: number, data?: { detail?: unknown } }
  const status = anyError?.status ?? anyError?.statusCode ?? 0
  const rawDetail = anyError?.data?.detail

  let detail = 'Não foi possível falar com o servidor.'
  if (typeof rawDetail === 'string') {
    detail = rawDetail
  }
  else if (Array.isArray(rawDetail)) {
    // Erro de validação do FastAPI: [{loc, msg}, ...]. As mensagens do Pydantic
    // vêm em inglês e falando de tipo — "String should have at least 8
    // characters" não diz a ninguém qual campo do formulário está errado.
    detail = rawDetail
      .map(item => emPortugues(item as ErroDeValidacao))
      .filter(Boolean)
      .join('. ') || detail
  }

  const apiError = new Error(detail) as ApiError
  apiError.status = status
  apiError.detail = detail
  return apiError
}

export function useApiBase(): string {
  const config = useRuntimeConfig()
  return import.meta.server ? config.apiInternal : config.public.apiBase
}

export function useTokens() {
  // Cookies e não localStorage: o SSR precisa enxergar a sessão para não
  // renderizar a tela de visitante e trocar depois (piscada feia).
  //
  // `httpOnly` não dá: quem lê estes cookies é o próprio JavaScript, para
  // montar o cabeçalho `Authorization`. Um cookie que o script não lê exigiria
  // a API passar a autenticar por cookie, e aí entraria CSRF na conta. A troca
  // é consciente.
  //
  // `secure` é condicional porque a plataforma roda nos dois mundos: em rede
  // local o endereço é `http://192.168.x.x` e um cookie `Secure` simplesmente
  // não é gravado — ninguém entraria. Com domínio e HTTPS ele passa a valer, e
  // aí impede que a sessão vaze num acesso http:// acidental.
  const comum = {
    sameSite: 'lax' as const,
    maxAge: 60 * 60 * 24 * 30,
    secure: import.meta.client ? window.location.protocol === 'https:' : false,
    path: '/',
  }
  const access = useCookie<string | null>('bolao_access', comum)
  const refresh = useCookie<string | null>('bolao_refresh', comum)
  return { access, refresh }
}

let refreshInFlight: Promise<string | null> | null = null

/**
 * Troca o refresh por um par novo. Devolve o access token novo, ou `null`.
 *
 * Devolver o token — em vez de só `true` — não é preferência de estilo. Quem
 * chama capturou o próprio `useTokens()` antes, e `useCookie` do Nuxt cria um
 * ref NOVO a cada chamada: os dois não se falam. No servidor então nem por
 * acaso, porque ali a leitura vem do cabeçalho da requisição, que ainda carrega
 * o token antigo. Sem devolver o valor, a repetição saía com o token expirado e
 * tomava 401 de novo — e a tela renderizava vazia até alguém apertar F5.
 */
async function rotateRefresh(): Promise<string | null> {
  const { access, refresh } = useTokens()
  if (!refresh.value) return null

  // Uma rotação por vez: várias requisições expirando juntas não podem
  // disparar vários refresh e invalidar a família por "reuso".
  if (!refreshInFlight) {
    refreshInFlight = $fetch<{ access_token: string, refresh_token: string }>(
      '/v1/auth/refresh',
      { baseURL: useApiBase(), method: 'POST', body: { refresh_token: refresh.value } },
    )
      .then((tokens) => {
        access.value = tokens.access_token
        refresh.value = tokens.refresh_token
        return tokens.access_token
      })
      .catch(() => {
        access.value = null
        refresh.value = null
        return null
      })
      .finally(() => {
        refreshInFlight = null
      })
  }
  return refreshInFlight
}

export async function apiFetch<T>(
  path: string,
  options: Record<string, unknown> = {},
): Promise<T> {
  const { access } = useTokens()
  const baseURL = useApiBase()

  const headers: Record<string, string> = {
    ...(options.headers as Record<string, string> | undefined),
  }
  if (access.value) headers.Authorization = `Bearer ${access.value}`

  try {
    return await $fetch<T>(path, { ...options, baseURL, headers })
  }
  catch (error) {
    const apiError = toApiError(error)
    if (apiError.status !== 401 || !access.value) throw apiError

    const novoToken = await rotateRefresh()
    if (!novoToken) throw apiError

    const retryHeaders = { ...headers, Authorization: `Bearer ${novoToken}` }
    try {
      return await $fetch<T>(path, { ...options, baseURL, headers: retryHeaders })
    }
    catch (retryError) {
      throw toApiError(retryError)
    }
  }
}

/**
 * Carrega dados para a renderização.
 *
 * Roda **nos dois lados**. No SSR funciona porque o token está em cookie, que
 * o servidor enxerga — então a primeira pintura já vem com o conteúdo real em
 * vez de um "carregando…". O payload é transferido para o cliente, que não
 * refaz a requisição.
 */
export function useApiData<T>(
  key: string,
  path: string | (() => string),
  options: Record<string, unknown> = {},
) {
  return useAsyncData<T>(key, () => apiFetch<T>(typeof path === 'function' ? path() : path), options)
}
