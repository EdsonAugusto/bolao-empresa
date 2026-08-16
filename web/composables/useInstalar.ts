/**
 * Instalar a plataforma no aparelho.
 *
 * Os navegadores discordam bastante sobre isto, e o composable existe para
 * esconder a discordância da tela:
 *
 * - **Chrome, Edge, Samsung Internet, Android** disparam `beforeinstallprompt`
 *   quando acham que a instalação faz sentido. Guardamos o evento e chamamos
 *   `prompt()` quando a pessoa clicar — é a única forma de ter um botão nosso
 *   em vez do aviso discreto do navegador, que quase ninguém vê.
 * - **Safari, no iPhone e no iPad**, não dispara nada e não tem API. A única
 *   saída é ensinar o caminho: Compartilhar → Adicionar à Tela de Início.
 * - **Firefox no Android** instala pelo menu, sem evento.
 *
 * Por isso `podeInstalar` e `precisaDeInstrucao` são coisas diferentes: uma dá
 * um botão que funciona, a outra dá um texto que explica.
 */

interface EventoDeInstalacao extends Event {
  prompt: () => Promise<void>
  userChoice: Promise<{ outcome: 'accepted' | 'dismissed' }>
}

export function useInstalar() {
  const evento = useState<EventoDeInstalacao | null>('pwa:evento', () => null)
  const instalado = useState<boolean>('pwa:instalado', () => false)
  const sistema = useState<'ios' | 'outro'>('pwa:sistema', () => 'outro')
  const pronto = useState<boolean>('pwa:pronto', () => false)

  /** Já está rodando como aplicativo, então não há o que oferecer. */
  const jaEAplicativo = computed(() => instalado.value)

  /** Existe um botão que realmente instala. */
  const podeInstalar = computed(() => Boolean(evento.value) && !instalado.value)

  /** Não há botão possível; resta explicar o caminho. */
  const precisaDeInstrucao = computed(
    () => pronto.value && sistema.value === 'ios' && !instalado.value && !evento.value,
  )

  async function instalar(): Promise<'aceitou' | 'recusou' | 'indisponivel'> {
    if (!evento.value) return 'indisponivel'
    const pedido = evento.value
    // O evento é de uso único: depois de `prompt()` ele não serve mais, e
    // guardá-lo daria um botão que não faz nada na segunda tentativa.
    evento.value = null
    await pedido.prompt()
    const escolha = await pedido.userChoice
    if (escolha.outcome === 'accepted') instalado.value = true
    return escolha.outcome === 'accepted' ? 'aceitou' : 'recusou'
  }

  return { podeInstalar, precisaDeInstrucao, jaEAplicativo, instalar }
}
