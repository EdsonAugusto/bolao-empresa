<script setup lang="ts">
definePageMeta({ middleware: ['auth', 'admin'] })

/**
 * Como o pessoal entra pelo celular.
 *
 * Esta tela existe porque a alternativa é o organizador rodar `ipconfig`,
 * decifrar qual dos endereços é o certo e ditar em voz alta.
 */

interface Lan {
  hostname: string
  addresses: string[]
  port: number
  urls: string[]
  hint: string
}

interface Info {
  project_name: string
  environment: string
  provider: string
  notification_channels: string[]
  users: number
  pools: number
  real_money: boolean
}

const { data: lan } = useApiData<Lan>('lan', '/v1/system/lan')
const { data: info } = useApiData<Info>('info', '/v1/system/info')

const copiado = ref('')

useHead({ title: 'Acesso pela rede' })

async function copiar(url: string) {
  try {
    await navigator.clipboard.writeText(url)
    copiado.value = url
    setTimeout(() => (copiado.value = ''), 2000)
  }
  catch {
    copiado.value = ''
  }
}
</script>

<template>
  <div class="estreito">
    <h1>Acesso pela rede</h1>
    <p class="fraco">
      A plataforma roda neste computador. Quem estiver no mesmo Wi-Fi entra por
      um destes endereços.
    </p>

    <div
      v-if="lan?.urls.length"
      class="pilha"
    >
      <div
        v-for="url in lan.urls"
        :key="url"
        class="card linha"
      >
        <code class="url">{{ url }}</code>
        <button
          type="button"
          class="btn pequeno empurra"
          @click="copiar(url)"
        >
          {{ copiado === url ? 'copiado!' : 'copiar' }}
        </button>
      </div>
    </div>

    <div
      v-else
      class="card aviso aviso--atencao"
    >
      Não foi possível descobrir o endereço da máquina na rede local.
      Rode <code>ipconfig</code> (Windows) ou <code>ip addr</code> (Linux) e use
      o endereço que começa com 192.168, 10. ou 172.
    </div>

    <p
      v-if="lan"
      class="fraco pequeno"
    >
      {{ lan.hint }}
    </p>

    <div
      v-if="info"
      class="card"
    >
      <h2>Esta instalação</h2>
      <dl class="dados">
        <dt>Máquina</dt><dd>{{ lan?.hostname ?? '—' }}</dd>
        <dt>Ambiente</dt><dd>{{ info.environment }}</dd>
        <dt>Dados dos jogos</dt><dd>{{ info.provider }}</dd>
        <dt>Avisos por</dt><dd>{{ info.notification_channels.join(', ') }}</dd>
        <dt>Contas</dt><dd class="num">
          {{ info.users }}
        </dd>
        <dt>Bolões</dt><dd class="num">
          {{ info.pools }}
        </dd>
      </dl>
    </div>

    <div class="card">
      <h2>Instalar no celular</h2>
      <p class="fraco pequeno">
        Abra o endereço no navegador do celular e use "Adicionar à tela de
        início". A plataforma abre como aplicativo, em tela cheia.
      </p>
    </div>
  </div>
</template>

<style scoped>
.estreito { max-width: 36rem; margin-inline: auto; }
.url { font-size: 1.05rem; font-weight: 700; }
code { background: var(--superficie-2); padding: 0.15rem 0.35rem; border-radius: 4px; }
.dados { display: grid; grid-template-columns: auto 1fr; gap: 0.3rem 1rem; margin: 0; }
.dados dt { color: var(--texto-fraco); font-size: 0.9rem; }
.dados dd { margin: 0; font-weight: 600; }
</style>
