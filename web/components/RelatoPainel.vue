<script setup lang="ts">
/**
 * Painel de relato, disponível em qualquer tela.
 *
 * Duas decisões que mudam quanto o relato serve:
 *
 * 1. **O contexto é capturado, não digitado.** Página, navegador e tamanho de
 *    tela vão junto sem ninguém precisar lembrar. É o que separa um bug que se
 *    reproduz de um "não consegui reproduzir".
 * 2. **Colar captura de tela funciona.** `Ctrl+V` com um print na área de
 *    transferência anexa direto — é como as pessoas realmente reportam bug, e
 *    o evento de colar funciona em HTTP, ao contrário da API de clipboard.
 */
const aberto = defineModel<boolean>({ default: false })

const route = useRoute()

const tipo = ref<'bug' | 'feedback' | 'ideia'>('bug')
const gravidade = ref<'baixa' | 'media' | 'alta' | 'critica'>('media')
const titulo = ref('')
const corpo = ref('')
const arquivos = ref<File[]>([])
const enviando = ref(false)
const erro = ref('')
const enviado = ref('')

const MAX_ANEXOS = 5

function adicionar(novos: FileList | File[] | null) {
  if (!novos) return
  for (const arquivo of Array.from(novos)) {
    if (arquivos.value.length >= MAX_ANEXOS) {
      erro.value = `Máximo de ${MAX_ANEXOS} anexos.`
      return
    }
    arquivos.value.push(arquivo)
  }
}

function remover(indice: number) {
  arquivos.value.splice(indice, 1)
}

/** Colar print da área de transferência — o jeito real de reportar bug. */
function aoColar(evento: ClipboardEvent) {
  const itens = evento.clipboardData?.files
  if (itens?.length) {
    evento.preventDefault()
    adicionar(itens)
  }
}

function aoSoltar(evento: DragEvent) {
  evento.preventDefault()
  adicionar(evento.dataTransfer?.files ?? null)
}

function ehImagem(arquivo: File) {
  return arquivo.type.startsWith('image/')
}

function previaDe(arquivo: File) {
  return URL.createObjectURL(arquivo)
}

function limpar() {
  titulo.value = ''
  corpo.value = ''
  arquivos.value = []
  erro.value = ''
  enviado.value = ''
}

async function enviar() {
  erro.value = ''
  if (titulo.value.trim().length < 4) {
    erro.value = 'Escreva em poucas palavras o que aconteceu.'
    return
  }

  enviando.value = true
  try {
    const relato = await apiFetch<{ code: string }>('/v1/reports', {
      method: 'POST',
      body: {
        kind: tipo.value,
        severity: gravidade.value,
        title: titulo.value,
        body: corpo.value,
        // Capturado, não digitado.
        page_url: route.fullPath,
        user_agent: import.meta.client ? navigator.userAgent : '',
        viewport: import.meta.client ? `${window.innerWidth}x${window.innerHeight}` : '',
      },
    })

    // Os anexos vão um a um: assim uma imagem recusada não derruba o relato
    // inteiro, e a pessoa vê exatamente qual arquivo teve problema.
    const recusados: string[] = []
    for (const arquivo of arquivos.value) {
      const forma = new FormData()
      forma.append('file', arquivo)
      try {
        await apiFetch(`/v1/reports/${relato.code}/attachments`, {
          method: 'POST',
          body: forma,
        })
      }
      catch (falha) {
        recusados.push(`${arquivo.name}: ${(falha as Error).message}`)
      }
    }

    enviado.value = relato.code
    erro.value = recusados.join(' · ')
    titulo.value = ''
    corpo.value = ''
    arquivos.value = []
  }
  catch (falha) {
    erro.value = (falha as Error).message
  }
  finally {
    enviando.value = false
  }
}

function fechar() {
  aberto.value = false
  if (enviado.value) limpar()
}

/* --- Contrato de diálogo -------------------------------------------------
   `role="dialog"` promete três coisas a quem navega por teclado ou leitor de
   tela: fecha com Esc, o foco entra e fica preso dentro, e volta de onde veio
   ao fechar. Sem elas o Tab sai por baixo do painel e passeia pela página
   coberta, que continua lá — o que é pior do que não ter papel nenhum. */
const painel = ref<HTMLElement | null>(null)
let focoAnterior: HTMLElement | null = null

const FOCAVEIS = 'button, [href], input, select, textarea, [tabindex]:not([tabindex="-1"])'

function focaveis(): HTMLElement[] {
  return Array.from(painel.value?.querySelectorAll<HTMLElement>(FOCAVEIS) ?? [])
    .filter(elemento => !elemento.hasAttribute('disabled') && elemento.offsetParent !== null)
}

function prenderTab(evento: KeyboardEvent) {
  if (evento.key !== 'Tab') return
  const itens = focaveis()
  if (!itens.length) return

  const primeiro = itens[0]!
  const ultimo = itens[itens.length - 1]!
  const atual = document.activeElement

  // `atual === painel.value` cobre o instante logo depois de abrir: o foco
  // está no container, e sem esta condição o primeiro Shift+Tab saía do painel
  // para a página coberta — que é exatamente o que a armadilha existe para
  // impedir.
  if (
    evento.shiftKey
    && (atual === primeiro || atual === painel.value || !painel.value?.contains(atual))
  ) {
    evento.preventDefault()
    ultimo.focus()
  }
  else if (!evento.shiftKey && atual === ultimo) {
    evento.preventDefault()
    primeiro.focus()
  }
}

watch(aberto, async (estaAberto) => {
  if (estaAberto) {
    focoAnterior = document.activeElement as HTMLElement | null
    // Trava a rolagem de trás: sem isto, rolar dentro do painel arrasta a
    // página coberta assim que a lista acaba.
    document.body.style.overflow = 'hidden'
    await nextTick()
    painel.value?.focus()
  }
  else {
    document.body.style.overflow = ''
    focoAnterior?.focus()
    focoAnterior = null
  }
})

onBeforeUnmount(() => {
  document.body.style.overflow = ''
})
</script>

<template>
  <div
    v-if="aberto"
    class="fundo"
    @click.self="fechar"
  >
    <section
      ref="painel"
      class="painel card"
      role="dialog"
      aria-modal="true"
      aria-labelledby="relato-cabecalho"
      tabindex="-1"
      @paste="aoColar"
      @dragover.prevent
      @drop="aoSoltar"
      @keydown.esc="fechar"
      @keydown="prenderTab"
    >
      <div class="linha">
        <h2 id="relato-cabecalho">
          Relatar
        </h2>
        <button
          type="button"
          class="btn btn--fantasma empurra"
          aria-label="Fechar"
          @click="fechar"
        >
          ✕
        </button>
      </div>

      <template v-if="enviado">
        <p class="aviso aviso--ok">
          Registrado como <strong>{{ enviado }}</strong>. Obrigado — dá para
          acompanhar em <NuxtLink
            to="/relatos"
            @click="fechar"
          >
            Relatos
          </NuxtLink>.
        </p>
        <p
          v-if="erro"
          class="aviso aviso--atencao pequeno"
        >
          Alguns anexos não entraram — {{ erro }}
        </p>
        <div class="linha">
          <button
            type="button"
            class="btn"
            @click="limpar"
          >
            Relatar outra coisa
          </button>
          <button
            type="button"
            class="btn btn--principal empurra"
            @click="fechar"
          >
            Fechar
          </button>
        </div>
      </template>

      <template v-else>
        <div class="tipos">
          <label
            v-for="opcao in (['bug', 'feedback', 'ideia'] as const)"
            :key="opcao"
            class="chip"
            :class="{ 'chip--on': tipo === opcao }"
          >
            <input
              v-model="tipo"
              type="radio"
              :value="opcao"
            >
            {{ opcao === 'bug' ? '🐞 Problema' : opcao === 'feedback' ? '💬 Retorno' : '💡 Ideia' }}
          </label>
        </div>

        <div class="campo">
          <label for="relato-titulo">O que aconteceu</label>
          <input
            id="relato-titulo"
            v-model="titulo"
            maxlength="160"
            placeholder="O placar do PSV não atualizou depois do jogo"
          >
        </div>

        <div
          v-if="tipo === 'bug'"
          class="campo"
        >
          <label for="relato-gravidade">O quanto atrapalha</label>
          <select
            id="relato-gravidade"
            v-model="gravidade"
          >
            <option value="baixa">
              Baixa — incomoda, mas dá para usar
            </option>
            <option value="media">
              Média — atrapalha
            </option>
            <option value="alta">
              Alta — não consigo fazer o que queria
            </option>
            <option value="critica">
              Crítica — a plataforma parou
            </option>
          </select>
        </div>

        <div class="campo">
          <label for="relato-corpo">Detalhes</label>
          <textarea
            id="relato-corpo"
            v-model="corpo"
            rows="4"
            maxlength="8000"
            placeholder="O que você fez, o que esperava e o que apareceu."
          />
          <span class="dica">
            A tela em que você está, o navegador e o tamanho do monitor vão
            junto automaticamente.
          </span>
        </div>

        <div class="campo">
          <span class="rotulo">Imagens e áudio</span>
          <div class="linha">
            <label class="btn pequeno">
              anexar arquivo
              <input
                type="file"
                accept="image/*,audio/*"
                multiple
                hidden
                @change="adicionar(($event.target as HTMLInputElement).files)"
              >
            </label>
            <GravadorAudio @gravado="adicionar([$event])" />
          </div>
          <span class="dica">
            Dá para colar uma captura de tela aqui com Ctrl+V, ou arrastar o
            arquivo para dentro desta janela.
          </span>

          <ul
            v-if="arquivos.length"
            class="anexos"
          >
            <li
              v-for="(arquivo, indice) in arquivos"
              :key="indice"
            >
              <img
                v-if="ehImagem(arquivo)"
                :src="previaDe(arquivo)"
                alt=""
              >
              <span
                v-else
                class="som"
                aria-hidden="true"
              >♪</span>
              <span class="nome pequeno">{{ arquivo.name }}</span>
              <span class="fraco pequeno">{{ Math.round(arquivo.size / 1024) }} KB</span>
              <button
                type="button"
                class="btn btn--fantasma pequeno empurra"
                :aria-label="`Remover ${arquivo.name}`"
                @click="remover(indice)"
              >
                ✕
              </button>
            </li>
          </ul>
        </div>

        <p
          v-if="erro"
          class="aviso aviso--erro pequeno"
        >
          {{ erro }}
        </p>

        <button
          type="button"
          class="btn btn--principal btn--bloco"
          :disabled="enviando"
          @click="enviar"
        >
          {{ enviando ? 'enviando…' : 'Enviar' }}
        </button>
      </template>
    </section>
  </div>
</template>

<style scoped>
.fundo {
  position: fixed;
  inset: 0;
  background: rgb(0 0 0 / 45%);
  display: flex;
  align-items: flex-end;
  justify-content: center;
  z-index: 40;
  padding: var(--e3);
  padding-bottom: calc(var(--e3) + env(safe-area-inset-bottom, 0px));
}

.painel {
  width: min(38rem, 100%);
  /* `dvh` e não `vh`: o painel é ancorado embaixo, e `vh` é sempre a viewport
     grande — com o teclado virtual aberto o botão Enviar ficava fora da tela,
     justo no formulário em que a pessoa digitou tudo. */
  max-height: 88dvh;
  overflow-y: auto;
  box-shadow: var(--sombra-alta);
}

.painel:focus { outline: none; }

@media (width > 700px) {
  .fundo { align-items: center; }
}

.tipos { display: flex; gap: var(--e2); flex-wrap: wrap; margin-bottom: var(--e3); }
.rotulo { font-weight: 650; font-size: 0.9rem; }

.chip {
  display: inline-flex;
  align-items: center;
  gap: var(--e1);
  padding: 0.4rem 0.8rem;
  border: 1px solid var(--borda-forte);
  border-radius: var(--raio-pill);
  cursor: pointer;
  font-size: 0.88rem;
  font-weight: 600;
}

.chip input { width: 0; height: 0; opacity: 0; position: absolute; }
.chip--on { background: var(--verde-fraco); border-color: var(--verde); color: var(--verde); }
.chip:focus-within { outline: 2px solid var(--verde-claro); outline-offset: 2px; }

.anexos { list-style: none; padding: 0; margin: var(--e2) 0 0; display: grid; gap: var(--e1); }

.anexos li {
  display: flex;
  align-items: center;
  gap: var(--e2);
  padding: 0.3rem 0.4rem;
  border: 1px solid var(--borda);
  border-radius: var(--raio-p);
}

.anexos img { width: 2.4rem; height: 2.4rem; object-fit: cover; border-radius: 4px; }
.som { width: 2.4rem; text-align: center; font-size: 1.2rem; color: var(--texto-fraco); }
.nome { overflow: hidden; text-overflow: ellipsis; white-space: nowrap; max-width: 14rem; }
</style>
