/** Sessão do usuário. */

export interface User {
  id: number
  email: string
  display_name: string
  avatar_url: string | null
  favorite_team_id: number | null
  favorite_team: {
    id: number
    name: string
    short_name: string | null
    crest_url: string | null
  } | null
  titulos: number
  must_change_password: boolean
  timezone: string
  is_superuser: boolean
  notify_in_app: boolean
  notify_telegram: boolean
  telegram_chat_id: string | null
  quiet_hours_start: number
  quiet_hours_end: number
}

interface TokenPair {
  access_token: string
  refresh_token: string
  expires_in: number
}

export function useAuth() {
  const user = useState<User | null>('auth:user', () => null)
  const loading = useState<boolean>('auth:loading', () => false)
  const { access, refresh } = useTokens()

  const isLoggedIn = computed(() => Boolean(user.value))

  function store(tokens: TokenPair) {
    access.value = tokens.access_token
    refresh.value = tokens.refresh_token
  }

  async function loadMe(): Promise<User | null> {
    if (!access.value) {
      user.value = null
      return null
    }
    try {
      user.value = await apiFetch<User>('/v1/auth/me')
    }
    catch {
      user.value = null
      access.value = null
      refresh.value = null
    }
    return user.value
  }

  async function login(email: string, password: string): Promise<void> {
    loading.value = true
    try {
      store(await apiFetch<TokenPair>('/v1/auth/login', {
        method: 'POST',
        body: { email, password },
      }))
      await loadMe()
    }
    finally {
      loading.value = false
    }
  }

  async function register(payload: {
    email: string
    password: string
    display_name: string
    /** Só é exigido quando a instalação está em modo `convite`. */
    invite_code?: string
  }): Promise<void> {
    loading.value = true
    try {
      store(await apiFetch<TokenPair>('/v1/auth/register', {
        method: 'POST',
        body: { ...payload, accepted_terms: true },
      }))
      await loadMe()
    }
    finally {
      loading.value = false
    }
  }

  async function logout(): Promise<void> {
    const token = refresh.value
    access.value = null
    refresh.value = null
    user.value = null
    if (token) {
      // Melhor esforço: se o servidor não responder, a sessão local já morreu.
      await apiFetch('/v1/auth/logout', {
        method: 'POST',
        body: { refresh_token: token },
      }).catch(() => undefined)
    }
    await navigateTo('/entrar')
  }

  async function updateProfile(patch: Partial<User>): Promise<void> {
    user.value = await apiFetch<User>('/v1/auth/me', { method: 'PATCH', body: patch })
  }

  return { user, loading, isLoggedIn, login, register, logout, loadMe, updateProfile }
}
