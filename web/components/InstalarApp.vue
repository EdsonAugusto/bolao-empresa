<script setup lang="ts">
/**
 * Oferece instalar a plataforma no aparelho.
 *
 * Dois formatos, porque são dois momentos diferentes:
 *
 * - `faixa` — aparece sozinha uma vez, na tela inicial. Some ao ser dispensada
 *   e não volta: oferta que insiste vira propaganda.
 * - `cartao` — mora no perfil, sempre disponível para quem foi procurar.
 */
const props = withDefaults(defineProps<{ formato?: 'faixa' | 'cartao' }>(), {
  formato: 'cartao',
})

const { podeInstalar, precisaDeInstrucao, jaEAplicativo, instalar } = useInstalar()

const CHAVE_DISPENSADA = 'bolao:instalar-dispensado'
const dispensada = ref(true)
const instrucaoAberta = ref(false)
const resultado = ref('')

onMounted(() => {
  dispensada.value = localStorage.getItem(CHAVE_DISPENSADA) === '1'
})

function dispensar() {
  dispensada.value = true
  localStorage.setItem(CHAVE_DISPENSADA, '1')
}

async function aoInstalar() {
  const escolha = await instalar()
  if (escolha === 'aceitou') resultado.value = 'Pronto: o atalho está na sua tela de início.'
  else if (escolha === 'recusou') dispensar()
}

/** Há algo a oferecer nesta combinação de aparelho e navegador. */
const temOferta = computed(() => !jaEAplicativo.value && (podeInstalar.value || precisaDeInstrucao.value))
const visivel = computed(() => temOferta.value && (props.formato === 'cartao' || !dispensada.value))
</script>

<template>
  <div
    v-if="visivel"
    :class="formato === 'faixa' ? 'faixa' : 'card instalar'"
  >
    <div class="instalar__texto">
      <strong>Tenha o Bolão na tela de início</strong>
      <p class="fraco pequeno">
        Abre como aplicativo, sem barra de endereço, e fica junto dos outros
        ícones do celular. Não ocupa espaço: é um atalho para este mesmo site.
      </p>
    </div>

    <div class="instalar__acoes">
      <button
        v-if="podeInstalar"
        type="button"
        class="btn"
        @click="aoInstalar"
      >
        Instalar
      </button>
      <button
        v-else-if="precisaDeInstrucao"
        type="button"
        class="btn"
        :aria-expanded="instrucaoAberta"
        @click="instrucaoAberta = !instrucaoAberta"
      >
        Como instalar
      </button>
      <button
        v-if="formato === 'faixa'"
        type="button"
        class="btn btn--fantasma pequeno"
        @click="dispensar"
      >
        Agora não
      </button>
    </div>

    <!-- O Safari não tem API de instalação; o jeito é ensinar o caminho. -->
    <ol
      v-if="instrucaoAberta"
      class="passos pequeno"
    >
      <li>
        Toque em <strong>Compartilhar</strong>
        <span aria-hidden="true"> (o quadrado com a seta para cima)</span>
      </li>
      <li>Role e escolha <strong>Adicionar à Tela de Início</strong></li>
      <li>Confirme em <strong>Adicionar</strong></li>
    </ol>

    <p
      v-if="resultado"
      class="aviso aviso--ok pequeno"
    >
      {{ resultado }}
    </p>
  </div>
</template>

<style scoped>
.instalar, .faixa {
  display: grid;
  gap: var(--e3);
}

.faixa {
  border: 1px solid var(--verde-borda);
  background: var(--verde-fraco);
  border-radius: var(--raio);
  padding: var(--e3) var(--e4);
  margin-bottom: var(--e4);
}

.instalar__texto p { margin: 0.2rem 0 0; }
.instalar__acoes { display: flex; gap: var(--e2); flex-wrap: wrap; }

.passos {
  margin: 0;
  padding-left: 1.2rem;
  display: grid;
  gap: 0.3rem;
  color: var(--texto-fraco);
}

@media (width >= 40rem) {
  .faixa {
    grid-template-columns: 1fr auto;
    align-items: center;
  }

  .faixa .passos, .faixa .aviso { grid-column: 1 / -1; }
}
</style>
