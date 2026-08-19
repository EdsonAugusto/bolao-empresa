<script setup lang="ts">
definePageMeta({ middleware: 'auth' })

const { user, updateProfile, logout, loadMe } = useAuth()
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
interface Avatar { id: string, url: string }
interface TimeBusca {
  id: number
  name: string
  short_name: string | null
  crest_url: string | null
}

const avatares = ref<Avatar[]>([])
const trocandoAvatar = ref(false)
const arquivoAvatar = ref<HTMLInputElement | null>(null)

const buscaTime = ref('')
const timesAchados = ref<TimeBusca[]>([])
const procurandoTime = ref(false)

const senhaAtual = ref('')
const senhaNova = ref('')
const senhaConfirma = ref('')
const trocandoSenha = ref(false)
const mensagemSenha = ref('')
const erroSenha = ref('')

const precisaTrocarSenha = computed(() => Boolean(user.value?.must_change_password))

onMounted(async () => {
  void verificarPush()
  try {
    avatares.value = await apiFetch<Avatar[]>('/v1/usuarios/avatares')
  }
  catch {
    // Sem catálogo a pessoa ainda pode enviar a própria foto. Um seletor vazio
    // é melhor do que a tela de perfil inteira falhar por causa de um enfeite.
    avatares.value = []
  }
})

async function escolherAvatar(url: string | null) {
  erro.value = ''
  mensagem.value = ''
  trocandoAvatar.value = true
  try {
    await updateProfile({ avatar_url: url })
    mensagem.value = url ? 'Avatar atualizado.' : 'Foto removida.'
  }
  catch (error_) {
    erro.value = (error_ as Error).message
  }
  finally {
    trocandoAvatar.value = false
  }
}

async function enviarFoto(evento: Event) {
  const alvo = evento.target as HTMLInputElement
  const arquivo = alvo.files?.[0]
  if (!arquivo) return

  erro.value = ''
  mensagem.value = ''
  trocandoAvatar.value = true
  try {
    const corpo = new FormData()
    corpo.append('file', arquivo)
    await apiFetch('/v1/usuarios/avatar', { method: 'POST', body: corpo })
    // O endereço da foto muda no servidor; recarregar a sessão é o que faz a
    // imagem nova aparecer sem a pessoa recarregar a página.
    await loadMe()
    mensagem.value = 'Foto de perfil atualizada.'
  }
  catch (error_) {
    erro.value = (error_ as Error).message
  }
  finally {
    trocandoAvatar.value = false
    // Zera o input: sem isso, escolher o MESMO arquivo de novo não dispara
    // `change` e parece que o botão parou de funcionar.
    alvo.value = ''
  }
}

let buscaEmEspera: ReturnType<typeof setTimeout> | null = null

function procurarTime() {
  if (buscaEmEspera) clearTimeout(buscaEmEspera)
  // Espera a pessoa parar de digitar: uma requisição por tecla faria dezenas
  // de consultas para uma busca só.
  buscaEmEspera = setTimeout(async () => {
    const termo = buscaTime.value.trim()
    if (termo.length < 2) {
      timesAchados.value = []
      return
    }
    procurandoTime.value = true
    try {
      timesAchados.value = await apiFetch<TimeBusca[]>(
        `/v1/catalog/times?busca=${encodeURIComponent(termo)}`,
      )
    }
    catch {
      timesAchados.value = []
    }
    finally {
      procurandoTime.value = false
    }
  }, 300)
}

async function escolherTime(id: number | null) {
  erro.value = ''
  mensagem.value = ''
  try {
    await updateProfile({ favorite_team_id: id })
    buscaTime.value = ''
    timesAchados.value = []
    mensagem.value = id ? 'Time do coração atualizado.' : 'Time removido do perfil.'
  }
  catch (error_) {
    erro.value = (error_ as Error).message
  }
}

async function trocarSenha() {
  erroSenha.value = ''
  mensagemSenha.value = ''

  if (senhaNova.value !== senhaConfirma.value) {
    erroSenha.value = 'A confirmação não confere com a senha nova.'
    return
  }

  trocandoSenha.value = true
  try {
    await apiFetch('/v1/auth/me/password', {
      method: 'POST',
      body: { current_password: senhaAtual.value, new_password: senhaNova.value },
    })
    senhaAtual.value = ''
    senhaNova.value = ''
    senhaConfirma.value = ''
    // Trocar a senha derruba as outras sessões no servidor; recarregar aqui é
    // o que desarma o aviso de "troque sua senha" sem pedir F5.
    await loadMe()
    mensagemSenha.value = 'Senha alterada. Os outros aparelhos vão pedir para entrar de novo.'
  }
  catch (error_) {
    erroSenha.value = (error_ as Error).message
  }
  finally {
    trocandoSenha.value = false
  }
}

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

    <div
      v-if="precisaTrocarSenha"
      class="card avisoSenha"
    >
      <strong>Escolha uma senha sua</strong>
      <p
        class="pequeno"
        style="margin: 0.3rem 0 0"
      >
        A senha atual foi definida por quem administra a plataforma — ou seja,
        outra pessoa a conhece. Troque abaixo para a conta voltar a ser só sua.
      </p>
    </div>

    <div class="card identidade">
      <AvatarPessoa
        :nome="user?.display_name ?? ''"
        :url="user?.avatar_url"
        :tamanho="96"
      />

      <div class="identidade__texto">
        <h2 style="margin: 0">
          {{ user?.display_name }}
        </h2>

        <div
          v-if="user?.favorite_team"
          class="torce"
        >
          <img
            v-if="user.favorite_team.crest_url"
            :src="user.favorite_team.crest_url"
            alt=""
            width="22"
            height="22"
          >
          <span>Torce pelo <strong>{{ user.favorite_team.name }}</strong></span>
        </div>

        <div
          v-if="(user?.titulos ?? 0) > 0"
          class="conquista"
        >
          <span aria-hidden="true">🏆</span>
          <span>
            <strong>{{ user?.titulos }}</strong>
            {{ user?.titulos === 1 ? 'vez campeão' : 'vezes campeão' }} do bolão
          </span>
        </div>
      </div>
    </div>

    <div class="card">
      <h2>Sua foto</h2>
      <p class="fraco pequeno">
        Envie uma imagem ou escolha um dos avatares. Ela aparece no ranking e na
        lista de participantes.
      </p>

      <div
        class="linha"
        style="gap: var(--e2); flex-wrap: wrap"
      >
        <button
          type="button"
          class="btn pequeno"
          :disabled="trocandoAvatar"
          @click="arquivoAvatar?.click()"
        >
          {{ trocandoAvatar ? 'enviando…' : 'Enviar imagem' }}
        </button>
        <button
          v-if="user?.avatar_url"
          type="button"
          class="btn pequeno btn--fantasma"
          :disabled="trocandoAvatar"
          @click="escolherAvatar(null)"
        >
          Remover
        </button>
        <input
          ref="arquivoAvatar"
          type="file"
          accept="image/png,image/jpeg,image/gif,image/webp"
          hidden
          @change="enviarFoto"
        >
      </div>

      <div
        v-if="avatares.length"
        class="avatares"
      >
        <button
          v-for="avatar in avatares"
          :key="avatar.id"
          type="button"
          class="avatares__opcao"
          :class="{ 'avatares__opcao--ativo': user?.avatar_url === avatar.url }"
          :disabled="trocandoAvatar"
          :aria-label="`Usar o avatar ${avatar.id}`"
          :aria-pressed="user?.avatar_url === avatar.url"
          @click="escolherAvatar(avatar.url)"
        >
          <img
            :src="avatar.url"
            alt=""
            width="52"
            height="52"
          >
        </button>
      </div>
    </div>

    <div class="card">
      <h2>Time do coração</h2>
      <p class="fraco pequeno">
        Aparece no seu perfil. Não influencia palpite, pontuação nem desempate.
      </p>

      <div
        v-if="user?.favorite_team"
        class="linha"
        style="gap: var(--e2); margin-bottom: var(--e2)"
      >
        <img
          v-if="user.favorite_team.crest_url"
          :src="user.favorite_team.crest_url"
          alt=""
          width="26"
          height="26"
        >
        <strong>{{ user.favorite_team.name }}</strong>
        <button
          type="button"
          class="btn pequeno btn--fantasma empurra"
          @click="escolherTime(null)"
        >
          Tirar
        </button>
      </div>

      <div class="campo">
        <label for="busca-time">Procurar time</label>
        <input
          id="busca-time"
          v-model="buscaTime"
          placeholder="Digite pelo menos duas letras"
          @input="procurarTime"
        >
        <span class="dica">
          Só os times dos campeonatos já importados aparecem aqui.
        </span>
      </div>

      <p
        v-if="procurandoTime"
        class="fraco pequeno"
      >
        procurando…
      </p>

      <ul
        v-else-if="timesAchados.length"
        class="times"
      >
        <li
          v-for="time in timesAchados"
          :key="time.id"
        >
          <button
            type="button"
            class="times__opcao"
            @click="escolherTime(time.id)"
          >
            <img
              v-if="time.crest_url"
              :src="time.crest_url"
              alt=""
              width="22"
              height="22"
            >
            <span>{{ time.name }}</span>
          </button>
        </li>
      </ul>

      <p
        v-else-if="buscaTime.trim().length >= 2"
        class="fraco pequeno"
      >
        Nenhum time com esse nome entre os campeonatos importados.
      </p>
    </div>

    <form
      class="card"
      @submit.prevent="trocarSenha"
    >
      <h2>Trocar a senha</h2>

      <div class="campo">
        <label for="senha-atual">Senha atual</label>
        <input
          id="senha-atual"
          v-model="senhaAtual"
          type="password"
          autocomplete="current-password"
          required
        >
      </div>

      <div class="campo">
        <label for="senha-nova">Senha nova</label>
        <input
          id="senha-nova"
          v-model="senhaNova"
          type="password"
          autocomplete="new-password"
          minlength="10"
          required
        >
        <span class="dica">Pelo menos 10 caracteres.</span>
      </div>

      <div class="campo">
        <label for="senha-confirma">Repita a senha nova</label>
        <input
          id="senha-confirma"
          v-model="senhaConfirma"
          type="password"
          autocomplete="new-password"
          minlength="10"
          required
        >
      </div>

      <p
        v-if="erroSenha"
        class="aviso aviso--erro"
      >
        {{ erroSenha }}
      </p>
      <p
        v-if="mensagemSenha"
        class="aviso aviso--ok"
      >
        {{ mensagemSenha }}
      </p>

      <button
        type="submit"
        class="btn"
        :disabled="trocandoSenha"
      >
        {{ trocandoSenha ? 'trocando…' : 'Trocar senha' }}
      </button>
    </form>

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
/* O aviso de senha imposta usa a cor de alerta, não a de erro: nada quebrou,
   mas a conta não é só da pessoa até ela trocar. */
.avisoSenha {
  border-color: var(--aviso);
  border-left-width: 4px;
}

.identidade {
  display: flex;
  align-items: center;
  gap: var(--e3);
  flex-wrap: wrap;
}

.identidade__texto {
  display: flex;
  flex-direction: column;
  gap: 0.35rem;
  min-width: 0;
}

.torce,
.conquista {
  display: flex;
  align-items: center;
  gap: 0.45rem;
  font-size: 0.92rem;
}

.torce img { border-radius: 4px; }

/* A conquista é o único elemento do perfil que celebra alguma coisa. Ganha
   fundo próprio para não se perder entre os dados de cadastro. */
.conquista {
  align-self: flex-start;
  padding: 0.25rem 0.6rem;
  border-radius: 999px;
  background: var(--superficie-3);
}

.avatares {
  display: flex;
  flex-wrap: wrap;
  gap: var(--e2);
  margin-top: var(--e3);
}

.avatares__opcao {
  padding: 3px;
  border: 2px solid transparent;
  border-radius: 50%;
  background: none;
  cursor: pointer;
  line-height: 0;
}

.avatares__opcao:hover:not(:disabled) { border-color: var(--borda-forte); }
.avatares__opcao--ativo { border-color: var(--verde); }
.avatares__opcao:disabled { opacity: 0.5; cursor: default; }
.avatares__opcao img { border-radius: 50%; display: block; }

.times {
  list-style: none;
  margin: var(--e2) 0 0;
  padding: 0;
  display: flex;
  flex-direction: column;
  gap: 2px;
  /* Sessenta resultados não cabem na tela; rolar aqui é melhor do que empurrar
     o resto do perfil para baixo. */
  max-height: 240px;
  overflow-y: auto;
}

.times__opcao {
  display: flex;
  align-items: center;
  gap: 0.5rem;
  width: 100%;
  padding: 0.45rem 0.6rem;
  border: none;
  border-radius: var(--raio-p);
  background: none;
  color: inherit;
  font: inherit;
  text-align: left;
  cursor: pointer;
}

.times__opcao:hover { background: var(--superficie-2); }
.times__opcao img { border-radius: 4px; flex-shrink: 0; }

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
