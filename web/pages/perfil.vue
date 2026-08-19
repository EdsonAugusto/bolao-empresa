<script setup lang="ts">
definePageMeta({ middleware: 'auth' })

const { user, updateProfile, logout } = useAuth()
const {
  estado: estadoPush,
  ocupado: mexendoNoPush,
  erro: erroPush,
  verificar: verificarPush,
  ligar: ligarPush,
  desligar: desligarPush,
} = useNotificacaoPush()

// Só no navegador: `Notification` e `navigator.serviceWorker` não existem no
// servidor, e o estado depende de uma pergunta ao próprio aparelho.
onMounted(() => {
  void verificarPush()
})

const nome = ref('')
const fuso = ref('America/Sao_Paulo')
const inApp = ref(true)
const telegram = ref(false)
const chatId = ref('')
const silencioInicio = ref(23)
const silencioFim = ref(8)
const mensagem = ref('')
const erro = ref('')

useHead({ title: 'Perfil' })

watch(user, (valor) => {
  if (!valor) return
  nome.value = valor.display_name
  fuso.value = valor.timezone
  inApp.value = valor.notify_in_app
  telegram.value = valor.notify_telegram
  chatId.value = valor.telegram_chat_id ?? ''
  silencioInicio.value = valor.quiet_hours_start
  silencioFim.value = valor.quiet_hours_end
}, { immediate: true })

async function salvar() {
  erro.value = ''
  mensagem.value = ''
  try {
    await updateProfile({
      display_name: nome.value,
      timezone: fuso.value,
      notify_in_app: inApp.value,
      notify_telegram: telegram.value,
      telegram_chat_id: chatId.value || null,
      quiet_hours_start: silencioInicio.value,
      quiet_hours_end: silencioFim.value,
    })
    mensagem.value = 'Perfil atualizado.'
  }
  catch (error_) {
    erro.value = (error_ as Error).message
  }
}

async function excluirConta() {
  if (!confirm(
    'Isso remove seus dados pessoais e desativa o acesso. '
    + 'Seus palpites permanecem no histórico dos bolões, sem identificação. Confirmar?',
  )) return
  await apiFetch('/v1/auth/me', { method: 'DELETE' })
  await logout()
}
</script>

<template>
  <div class="estreito">
    <h1>Perfil</h1>

    <InstalarApp />

    <form
      class="card"
      @submit.prevent="salvar"
    >
      <div class="campo">
        <label for="nome">Nome no ranking</label>
        <input
          id="nome"
          v-model="nome"
          required
          minlength="2"
        >
      </div>

      <div class="campo">
        <label for="fuso">Fuso horário</label>
        <select
          id="fuso"
          v-model="fuso"
        >
          <option value="America/Sao_Paulo">
            Brasília (America/Sao_Paulo)
          </option>
          <option value="America/Manaus">
            Manaus (America/Manaus)
          </option>
          <option value="America/Rio_Branco">
            Rio Branco (America/Rio_Branco)
          </option>
          <option value="America/Belem">
            Belém (America/Belem)
          </option>
          <option value="UTC">
            UTC
          </option>
        </select>
        <span class="dica">
          Os horários dos jogos são convertidos para este fuso na tela.
        </span>
      </div>

      <h2>Avisos</h2>

      <label class="opcao">
        <input
          v-model="inApp"
          type="checkbox"
        >
        Receber avisos aqui na plataforma
      </label>

      <div class="push">
        <div>
          <strong>Avisar no celular</strong>
          <p
            class="fraco pequeno"
            style="margin: 0.2rem 0 0"
          >
            <template v-if="estadoPush === 'ligado'">
              Este aparelho recebe o lembrete de palpite mesmo com o app fechado.
            </template>
            <template v-else-if="estadoPush === 'bloqueado'">
              Você bloqueou as notificações deste site no navegador. Só dá para
              reverter nas permissões do site, no próprio navegador.
            </template>
            <template v-else-if="estadoPush === 'indisponivel'">
              Não disponível aqui — este recurso precisa de HTTPS e de um
              navegador que o suporte. Os avisos continuam na plataforma.
            </template>
            <template v-else>
              Um lembrete de manhã e outro 30 minutos antes de cada jogo que
              você ainda não palpitou. Precisa autorizar no aparelho.
            </template>
          </p>
          <p
            v-if="erroPush"
            class="pequeno"
            style="color: var(--alerta); margin: 0.3rem 0 0"
          >
            {{ erroPush }}
          </p>
        </div>

        <button
          v-if="estadoPush === 'desligado' || estadoPush === 'ligado'"
          type="button"
          class="btn pequeno empurra"
          :disabled="mexendoNoPush"
          @click="estadoPush === 'ligado' ? desligarPush() : ligarPush()"
        >
          {{ mexendoNoPush
            ? 'um instante…'
            : (estadoPush === 'ligado' ? 'Desligar neste aparelho' : 'Ligar neste aparelho') }}
        </button>
      </div>

      <label class="opcao">
        <input
          v-model="telegram"
          type="checkbox"
        >
        Receber também no Telegram
      </label>

      <div
        v-if="telegram"
        class="campo"
      >
        <label for="chat">ID do chat no Telegram</label>
        <input
          id="chat"
          v-model="chatId"
          placeholder="123456789"
        >
        <span class="dica">
          Mande <code>/start</code> para o bot da instalação e cole aqui o número
          que ele responder. O Telegram é gratuito e não precisa de IP fixo.
        </span>
      </div>

      <div
        class="linha"
        style="gap: 0.75rem"
      >
        <div
          class="campo"
          style="width: 8rem"
        >
          <label for="ini">Silêncio das</label>
          <input
            id="ini"
            v-model.number="silencioInicio"
            type="number"
            min="0"
            max="23"
          >
        </div>
        <div
          class="campo"
          style="width: 8rem"
        >
          <label for="fim">até</label>
          <input
            id="fim"
            v-model.number="silencioFim"
            type="number"
            min="0"
            max="23"
          >
        </div>
      </div>
      <p class="fraco pequeno">
        Nesse intervalo nada é enviado para fora — os avisos ficam esperando aqui.
      </p>

      <p
        v-if="erro"
        class="aviso aviso--erro"
      >
        {{ erro }}
      </p>
      <p
        v-if="mensagem"
        class="aviso aviso--ok"
      >
        {{ mensagem }}
      </p>

      <button
        type="submit"
        class="btn btn--principal"
      >
        Salvar
      </button>
    </form>

    <div class="card">
      <h2>Excluir conta</h2>
      <p class="fraco pequeno">
        Seus dados pessoais são apagados e o acesso é desativado. Os palpites
        continuam no histórico dos bolões sem identificação, para que o ranking
        das rodadas passadas continue fechando.
      </p>
      <button
        type="button"
        class="btn btn--perigo"
        @click="excluirConta"
      >
        Excluir minha conta
      </button>
    </div>
  </div>
</template>

<style scoped>
/* Cada aparelho se inscreve sozinho, então isto é um estado do aparelho e não
   uma preferência da conta — por isso o bloco tem cara própria, separado das
   caixas de seleção que o botão Salvar controla. */
.push {
  display: flex;
  align-items: flex-start;
  gap: var(--e3);
  flex-wrap: wrap;
  margin: var(--e2) 0;
  padding: var(--e3);
  border: 1px solid var(--borda);
  border-radius: var(--raio-p);
}

.estreito { max-width: 36rem; margin-inline: auto; }
.opcao { display: flex; align-items: center; gap: 0.5rem; margin-bottom: 0.6rem; }
.opcao input { width: auto; }
code { background: var(--superficie-2); padding: 0 0.25rem; border-radius: 4px; }
</style>
