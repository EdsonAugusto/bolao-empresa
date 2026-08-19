<script setup lang="ts">
// Layout próprio: sem cabeçalho nem rodapé de conteúdo, para a cena do estádio
// ocupar a tela inteira em vez de aparecer em três faixas.
definePageMeta({ layout: 'acesso' })

const { login, register, loading } = useAuth()
const route = useRoute()

const modo = ref<'entrar' | 'criar'>('entrar')
const email = ref('')
const senha = ref('')
const nome = ref('')
const convite = ref('')
const erro = ref('')
const esqueci = ref(false)

/**
 * Se esta instalação exige código de convite para criar conta.
 *
 * Vem do servidor porque a resposta depende de como a plataforma foi instalada:
 * em rede local o cadastro é aberto (estar na rede já é o convite), num
 * servidor com domínio público o padrão é exigir o código do bolão. Adivinhar
 * daria os dois erros possíveis — pedir código onde ele não existe, ou não
 * pedir e deixar a pessoa levar um 403 depois de preencher tudo.
 */
const { data: entrada } = await useApiData<{
  needs_invite: boolean
  is_first_account: boolean
}>('entrada', () => '/v1/system/entrada')

const precisaDeConvite = computed(() => Boolean(entrada.value?.needs_invite))
const primeiraConta = computed(() => Boolean(entrada.value?.is_first_account))

// Link de convite abre a tela já no cadastro, com o código preenchido.
onMounted(() => {
  const codigo = String(route.query.convite ?? '').trim().toUpperCase()
  if (codigo) {
    convite.value = codigo
    modo.value = 'criar'
  }
})

useHead({ title: () => (modo.value === 'entrar' ? 'Entrar' : 'Criar conta') })

async function enviar() {
  erro.value = ''
  try {
    if (modo.value === 'entrar') {
      await login(email.value, senha.value)
    }
    else {
      await register({
        email: email.value,
        password: senha.value,
        display_name: nome.value,
        invite_code: convite.value.trim().toUpperCase(),
      })
    }
    await navigateTo((route.query.destino as string) || '/')
  }
  catch (error) {
    erro.value = (error as Error).message
  }
}
</script>

<template>
  <div class="janela">
    <div class="card cartao">
      <img
        src="/marca-96.png"
        alt=""
        width="52"
        height="52"
        class="simbolo"
      >

      <h1>{{ modo === 'entrar' ? 'Entrar' : 'Criar conta' }}</h1>
      <p class="fraco pequeno subtitulo">
        {{ modo === 'entrar'
          ? 'Seus palpites, o ranking e os jogos ao vivo.'
          : 'Leva um minuto. Depois é só palpitar.' }}
      </p>

      <p
        v-if="modo === 'criar' && primeiraConta"
        class="aviso"
      >
        Esta é a primeira conta desta instalação — ela administra a plataforma.
      </p>

      <form
        novalidate
        @submit.prevent="enviar"
      >
        <div
          v-if="modo === 'criar'"
          class="campo"
        >
          <label for="nome">Como você aparece no ranking</label>
          <input
            id="nome"
            v-model="nome"
            required
            minlength="2"
            autocomplete="nickname"
          >
        </div>

        <div
          v-if="modo === 'criar' && precisaDeConvite"
          class="campo"
        >
          <label for="convite">Código do bolão que te convidou</label>
          <input
            id="convite"
            v-model="convite"
            required
            maxlength="16"
            autocapitalize="characters"
            spellcheck="false"
            class="num"
            placeholder="ABCD1234"
          >
          <span class="dica">
            São 8 letras, no convite que o organizador mandou no grupo.
          </span>
        </div>

        <div class="campo">
          <label for="email">E-mail</label>
          <input
            id="email"
            v-model="email"
            type="email"
            required
            autocomplete="username"
            inputmode="email"
          >
          <span class="dica">
            Serve só como login — a plataforma não envia e-mail nenhum.
          </span>
        </div>

        <div class="campo">
          <label for="senha">Senha</label>
          <input
            id="senha"
            v-model="senha"
            type="password"
            required
            minlength="8"
            :autocomplete="modo === 'entrar' ? 'current-password' : 'new-password'"
          >
          <span
            v-if="modo === 'criar'"
            class="dica"
          >Pelo menos 8 caracteres. Comprimento vale mais que símbolo.</span>
        </div>

        <p
          v-if="erro"
          class="aviso aviso--erro"
          role="alert"
        >
          {{ erro }}
        </p>

        <button
          type="submit"
          class="btn btn--principal btn--bloco"
          :disabled="loading"
        >
          {{ loading ? 'aguarde…' : (modo === 'entrar' ? 'Entrar' : 'Criar conta') }}
        </button>
      </form>

      <p
        class="centro pequeno"
        style="margin-top: 1rem"
      >
        <button
          type="button"
          class="btn btn--fantasma pequeno"
          @click="modo = modo === 'entrar' ? 'criar' : 'entrar'; erro = ''"
        >
          {{ modo === 'entrar' ? 'Não tenho conta ainda' : 'Já tenho conta' }}
        </button>
      </p>

      <!-- A plataforma não envia e-mail, então não existe link de recuperação.
           Ficar em silêncio sobre isso deixa a pessoa tentando a senha até
           bater no limite de tentativas. -->
      <p
        v-if="modo === 'entrar'"
        class="centro pequeno"
      >
        <button
          type="button"
          class="botao-texto"
          :aria-expanded="esqueci"
          @click="esqueci = !esqueci"
        >
          Esqueci minha senha
        </button>
      </p>

      <div
        v-if="esqueci"
        class="aviso pequeno"
      >
        <p style="margin: 0 0 0.4rem">
          Esta plataforma não envia e-mail, então não há link de recuperação —
          é o preço de não depender de nenhum serviço pago.
        </p>
        <p style="margin: 0">
          Peça a quem administra para redefinir sua senha. Leva um comando no
          servidor, e todas as sessões abertas na sua conta são encerradas junto.
        </p>
      </div>
    </div>
  </div>
</template>

<style scoped>
.janela { width: 100%; max-width: 25rem; }

/* O card flutua sobre a cena, então precisa de peso próprio: sombra funda para
   descolar do fundo, e uma borda clara de um lado só, como se pegasse a luz
   dos refletores. */
.cartao {
  backdrop-filter: blur(14px) saturate(120%);
  border: 1px solid rgb(255 255 255 / 14%);
  box-shadow:
    0 24px 70px rgb(0 0 0 / 55%),
    0 2px 0 rgb(255 255 255 / 6%) inset;
  animation: entrar 0.5s cubic-bezier(0.2, 0.7, 0.3, 1) both;
}

/* Entrada discreta: sobe um pouco e aparece. Só `opacity` e `transform`. */
@keyframes entrar {
  from { opacity: 0; transform: translateY(14px); }
  to { opacity: 1; transform: none; }
}

.simbolo {
  display: block;
  margin: 0 auto 0.85rem;
  border-radius: 12px;
  box-shadow: 0 6px 22px rgb(16 113 74 / 45%);
}

.janela h1 { text-align: center; margin-bottom: 0.2rem; }
.subtitulo { text-align: center; margin-bottom: var(--e4); }

@media (prefers-reduced-motion: reduce) {
  .cartao { animation: none; }
}

.botao-texto {
  background: none;
  border: none;
  color: var(--texto-fraco);
  font: inherit;
  text-decoration: underline;
  text-underline-offset: 2px;
  cursor: pointer;
  padding: 0.4rem;
  min-height: 44px;
}

.botao-texto:hover { color: var(--verde); }
.botao-texto:focus-visible { outline: 2px solid var(--verde); outline-offset: 2px; }
</style>
