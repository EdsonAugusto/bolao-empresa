<script setup lang="ts">
/**
 * Gravador de áudio para relato.
 *
 * A restrição que manda aqui não é de código: **o navegador só libera o
 * microfone em contexto seguro** — HTTPS ou localhost. Esta plataforma é
 * acessada por IP na rede local, em HTTP, então na prática o microfone fica
 * bloqueado no celular e nos outros computadores da casa.
 *
 * Fingir que funciona seria pior do que não ter: a pessoa aperta, nada
 * acontece, e ela conclui que a plataforma está quebrada. Então a tela detecta
 * o caso, explica em uma frase, e oferece o caminho que sempre funciona —
 * gravar no aplicativo de gravador do próprio telefone e anexar o arquivo.
 */
const emit = defineEmits<{ gravado: [File] }>()

const gravando = ref(false)
const segundos = ref(0)
const erro = ref('')
const previa = ref<string | null>(null)

let recorder: MediaRecorder | null = null
let pedacos: Blob[] = []
let relogio: ReturnType<typeof setInterval> | null = null

/** Contexto seguro: HTTPS ou localhost. Sem isso não há microfone. */
const podeGravar = computed(() => {
  if (!import.meta.client) return false
  return typeof MediaRecorder !== 'undefined'
    && !!navigator.mediaDevices?.getUserMedia
    && window.isSecureContext
})

const MAX_SEGUNDOS = 120

function pararRelogio() {
  if (relogio) {
    clearInterval(relogio)
    relogio = null
  }
}

async function comecar() {
  erro.value = ''
  try {
    const trilha = await navigator.mediaDevices.getUserMedia({ audio: true })

    // O formato varia por navegador: Chrome grava webm/opus, Safari grava mp4.
    // Deixar o navegador escolher e detectar o tipo no servidor é mais robusto
    // do que impor um formato que metade deles não sabe produzir.
    recorder = new MediaRecorder(trilha)
    pedacos = []

    recorder.ondataavailable = (evento) => {
      if (evento.data.size) pedacos.push(evento.data)
    }
    recorder.onstop = () => {
      // Solta o microfone. Sem isto o indicador de gravação fica aceso no
      // navegador depois de a pessoa terminar, o que assusta com razão.
      trilha.getTracks().forEach(faixa => faixa.stop())

      const blob = new Blob(pedacos, { type: recorder?.mimeType || 'audio/webm' })
      const extensao = blob.type.includes('mp4') ? 'm4a' : 'webm'
      previa.value = URL.createObjectURL(blob)
      emit('gravado', new File([blob], `relato.${extensao}`, { type: blob.type }))
    }

    recorder.start()
    gravando.value = true
    segundos.value = 0
    relogio = setInterval(() => {
      segundos.value += 1
      if (segundos.value >= MAX_SEGUNDOS) parar()
    }, 1000)
  }
  catch (falha) {
    const nome = (falha as Error).name
    erro.value = nome === 'NotAllowedError'
      ? 'Você precisa permitir o microfone no navegador.'
      : `Não consegui abrir o microfone (${nome}).`
  }
}

function parar() {
  pararRelogio()
  gravando.value = false
  if (recorder?.state === 'recording') recorder.stop()
}

function descartar() {
  if (previa.value) URL.revokeObjectURL(previa.value)
  previa.value = null
}

onBeforeUnmount(() => {
  pararRelogio()
  if (recorder?.state === 'recording') recorder.stop()
  if (previa.value) URL.revokeObjectURL(previa.value)
})

const relogioFormatado = computed(() => {
  const minutos = Math.floor(segundos.value / 60)
  return `${minutos}:${String(segundos.value % 60).padStart(2, '0')}`
})
</script>

<template>
  <div class="gravador">
    <template v-if="podeGravar">
      <button
        v-if="!gravando"
        type="button"
        class="btn pequeno"
        @click="comecar"
      >
        ● gravar áudio
      </button>
      <button
        v-else
        type="button"
        class="btn pequeno btn--perigo"
        @click="parar"
      >
        <span class="pulso" /> parar — {{ relogioFormatado }}
      </button>
      <span
        v-if="gravando"
        class="fraco pequeno"
      >máximo {{ MAX_SEGUNDOS }}s</span>
    </template>

    <p
      v-else
      class="fraco pequeno bloqueado"
    >
      O navegador só libera o microfone em <strong>HTTPS ou localhost</strong>, e
      esta plataforma roda por IP na rede local. Grave no gravador do seu
      telefone e anexe o arquivo aqui do lado — funciona igual.
    </p>

    <div
      v-if="previa"
      class="previa"
    >
      <audio
        :src="previa"
        controls
      />
      <button
        type="button"
        class="btn btn--fantasma pequeno"
        @click="descartar"
      >
        descartar
      </button>
    </div>

    <p
      v-if="erro"
      class="aviso aviso--erro pequeno"
    >
      {{ erro }}
    </p>
  </div>
</template>

<style scoped>
.gravador { display: flex; flex-wrap: wrap; align-items: center; gap: var(--e2); }
.bloqueado { margin: 0; line-height: 1.5; max-width: 34rem; }
.previa { display: flex; align-items: center; gap: var(--e2); width: 100%; }
.previa audio { flex: 1; max-width: 22rem; height: 2.2rem; }
</style>
