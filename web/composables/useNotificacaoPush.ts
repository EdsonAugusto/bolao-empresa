/**
 * Notificação do navegador — a que chega com o app fechado.
 *
 * O aviso dentro da plataforma só é visto por quem abre a plataforma, e quem
 * esqueceu de palpitar é justamente quem não abriu. Este é o caminho que
 * alcança a pessoa na tela de bloqueio.
 *
 * Três coisas precisam ser verdade ao mesmo tempo, e cada uma pode faltar por
 * um motivo diferente:
 *
 * 1. o navegador ter as APIs (`Notification`, `PushManager`, service worker);
 * 2. a instalação ter chave VAPID — o que exige HTTPS, e portanto não existe
 *    na instalação de rede local;
 * 3. a pessoa autorizar.
 *
 * O estado exposto distingue as três, porque "não dá" e "você não quis" pedem
 * frases diferentes na tela.
 */

export type EstadoPush =
  /** Ainda perguntando ao navegador e ao servidor. */
  | 'verificando'
  /** Este navegador não faz, ou a instalação não tem HTTPS/chave. */
  | 'indisponivel'
  /** Dá para ligar, e ainda não está. */
  | 'desligado'
  /** Ligado neste aparelho. */
  | 'ligado'
  /** A pessoa bloqueou no navegador — só ela pode desfazer, nas permissões do site. */
  | 'bloqueado'

/**
 * Converte a chave VAPID (base64url) no formato que o `PushManager` exige.
 *
 * Devolve o `ArrayBuffer` e não a `Uint8Array` que o envolve: `applicationServerKey`
 * pede `BufferSource`, e uma view genérica pode estar sobre `SharedArrayBuffer`,
 * que não serve. Criar o buffer primeiro deixa o tipo exato sem conversão forçada.
 */
function chaveParaBytes(base64url: string): ArrayBuffer {
  const preenchimento = '='.repeat((4 - (base64url.length % 4)) % 4)
  const base64 = (base64url + preenchimento).replace(/-/g, '+').replace(/_/g, '/')
  const cru = atob(base64)
  const buffer = new ArrayBuffer(cru.length)
  const bytes = new Uint8Array(buffer)
  for (let i = 0; i < cru.length; i++) bytes[i] = cru.charCodeAt(i)
  return buffer
}

/** Extrai as duas chaves de cifra da inscrição, em base64url. */
function chavesDa(inscricao: PushSubscription): { p256dh: string, auth: string } | null {
  const bruto = inscricao.toJSON().keys
  if (!bruto?.p256dh || !bruto?.auth) return null
  return { p256dh: bruto.p256dh, auth: bruto.auth }
}

export function useNotificacaoPush() {
  const estado = useState<EstadoPush>('push:estado', () => 'verificando')
  const ocupado = ref(false)
  const erro = ref('')

  function suportado(): boolean {
    return Boolean(
      import.meta.client
        && 'serviceWorker' in navigator
        && 'PushManager' in window
        && 'Notification' in window,
    )
  }

  async function verificar(): Promise<void> {
    if (!suportado()) {
      estado.value = 'indisponivel'
      return
    }

    try {
      const { disponivel } = await apiFetch<{ disponivel: boolean }>(
        '/v1/notifications/push/chave',
      )
      if (!disponivel) {
        estado.value = 'indisponivel'
        return
      }
    }
    catch {
      estado.value = 'indisponivel'
      return
    }

    if (Notification.permission === 'denied') {
      estado.value = 'bloqueado'
      return
    }

    const registro = await navigator.serviceWorker.ready
    const atual = await registro.pushManager.getSubscription()
    estado.value = atual ? 'ligado' : 'desligado'
  }

  async function ligar(): Promise<void> {
    if (ocupado.value) return
    ocupado.value = true
    erro.value = ''

    try {
      const permissao = await Notification.requestPermission()
      if (permissao !== 'granted') {
        estado.value = permissao === 'denied' ? 'bloqueado' : 'desligado'
        return
      }

      const { disponivel, chave_publica: chave } = await apiFetch<{
        disponivel: boolean
        chave_publica: string
      }>('/v1/notifications/push/chave')
      if (!disponivel || !chave) {
        estado.value = 'indisponivel'
        return
      }

      const registro = await navigator.serviceWorker.ready
      // Reaproveita a inscrição existente: assinar de novo por cima gera outro
      // endpoint e deixa o anterior órfão no servidor, recebendo para sempre.
      const inscricao = await registro.pushManager.getSubscription()
        ?? await registro.pushManager.subscribe({
          // Sem isto o Chrome recusa: ele exige que todo push mostre alguma
          // coisa, e não aceita inscrição que se reserve o direito de ficar
          // calada.
          userVisibleOnly: true,
          applicationServerKey: chaveParaBytes(chave),
        })

      const chaves = chavesDa(inscricao)
      if (!chaves) {
        erro.value = 'o navegador devolveu uma inscrição incompleta'
        return
      }

      await apiFetch('/v1/notifications/push/inscrever', {
        method: 'POST',
        body: { endpoint: inscricao.endpoint, ...chaves },
      })
      estado.value = 'ligado'
    }
    catch (falha) {
      erro.value = (falha as Error).message || 'não consegui ligar os avisos'
    }
    finally {
      ocupado.value = false
    }
  }

  async function desligar(): Promise<void> {
    if (ocupado.value) return
    ocupado.value = true
    erro.value = ''

    try {
      const registro = await navigator.serviceWorker.ready
      const inscricao = await registro.pushManager.getSubscription()
      if (inscricao) {
        const chaves = chavesDa(inscricao)
        // Avisa o servidor ANTES de cancelar no navegador. Na ordem inversa,
        // uma falha de rede deixaria o aparelho cancelado aqui e vivo lá — e
        // o servidor tentaria entregar num endereço morto até o navegador
        // responder 410.
        if (chaves) {
          await apiFetch('/v1/notifications/push/cancelar', {
            method: 'POST',
            body: { endpoint: inscricao.endpoint, ...chaves },
          }).catch(() => {})
        }
        await inscricao.unsubscribe()
      }
      estado.value = 'desligado'
    }
    catch (falha) {
      erro.value = (falha as Error).message || 'não consegui desligar os avisos'
    }
    finally {
      ocupado.value = false
    }
  }

  return { estado, ocupado, erro, verificar, ligar, desligar }
}
