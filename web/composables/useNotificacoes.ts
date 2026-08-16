/** Central de avisos (canal in-app). */

export interface Aviso {
  id: number
  template: string
  title: string
  body: string
  payload: Record<string, unknown>
  created_at: string
  read_at: string | null
}

export function useNotificacoes() {
  const avisos = useState<Aviso[]>('avisos', () => [])
  const carregando = useState<boolean>('avisos:carregando', () => false)

  async function carregar() {
    carregando.value = true
    try {
      avisos.value = await apiFetch<Aviso[]>('/v1/notifications')
    }
    catch {
      avisos.value = []
    }
    finally {
      carregando.value = false
    }
  }

  async function marcarLidos(ids: number[]) {
    if (!ids.length) return
    await apiFetch('/v1/notifications/read', { method: 'POST', body: { ids } })
    avisos.value = avisos.value.filter(aviso => !ids.includes(aviso.id))
  }

  return { avisos, carregando, carregar, marcarLidos }
}
