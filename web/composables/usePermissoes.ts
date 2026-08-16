/**
 * O que a conta desta sessão pode fazer.
 *
 * Carregado uma vez e compartilhado por `useState`, porque quase toda tela
 * pergunta alguma coisa a ele — o menu, os botões de importar, o acesso ao
 * painel. Uma requisição por navegação seria desperdício visível.
 *
 * **Isto é para esconder botão, não para autorizar.** Toda decisão de verdade
 * é do servidor: a tela pode estar desatualizada por minutos depois de alguém
 * mudar um nível, e um `v-if` nunca impediu ninguém de chamar a API.
 */
export type Permissao =
  | 'usuarios.ver'
  | 'usuarios.gerenciar'
  | 'grupos.gerenciar'
  | 'campeonatos.importar'
  | 'campeonatos.placar'
  | 'boloes.criar'
  | 'rodadas.montar'
  | 'relatos.triar'
  | 'plataforma.configurar'

interface MeuAcesso {
  nivel: string
  nivel_rotulo: string
  permissoes: string[]
}

export function usePermissoes() {
  const acesso = useState<MeuAcesso | null>('permissoes:eu', () => null)
  const { isLoggedIn } = useAuth()

  async function carregar(): Promise<void> {
    if (!isLoggedIn.value) {
      acesso.value = null
      return
    }
    try {
      acesso.value = await apiFetch<MeuAcesso>('/v1/usuarios/eu')
    }
    catch {
      // Sem acesso carregado, a tela mostra só o que todo mundo vê. Falhar
      // fechado aqui é melhor do que oferecer um botão que devolve 403.
      acesso.value = null
    }
  }

  const pode = (permissao: Permissao) =>
    Boolean(acesso.value?.permissoes.includes(permissao))

  return {
    acesso,
    carregar,
    pode,
    nivel: computed(() => acesso.value?.nivel ?? ''),
    nivelRotulo: computed(() => acesso.value?.nivel_rotulo ?? ''),
  }
}
