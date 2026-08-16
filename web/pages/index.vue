<script setup lang="ts">
definePageMeta({ middleware: 'auth' })

interface Bolao {
  id: number
  slug: string
  name: string
  description: string | null
  status: string
  visibility: string
  season: string
  competition: string
  members: number
  max_participants: number | null
  my_role: string | null
  my_position: number | null
  my_points: number | null
}

const { data: boloes, pending } = useApiData<Bolao[]>('meus-boloes', '/v1/pools?mine=true')

const codigo = ref('')
const erroEntrada = ref('')
const entrando = ref(false)

useHead({ title: 'Meus bolões' })

async function entrarPorCodigo() {
  erroEntrada.value = ''
  entrando.value = true
  try {
    const bolao = await apiFetch<Bolao>('/v1/pools/join', {
      method: 'POST',
      body: { invite_code: codigo.value.trim().toUpperCase() },
    })
    await navigateTo(`/b/${bolao.slug}`)
  }
  catch (error) {
    erroEntrada.value = (error as Error).message
  }
  finally {
    entrando.value = false
  }
}
</script>

<template>
  <div>
    <!-- Oferta única, dispensável. Só aparece onde o navegador realmente
         consegue instalar, e some para sempre ao ser recusada. -->
    <InstalarApp formato="faixa" />

    <div
      class="linha"
      style="margin-bottom: 1rem"
    >
      <h1>Meus bolões</h1>
      <NuxtLink
        to="/boloes/novo"
        class="btn btn--principal empurra"
      >
        Criar bolão
      </NuxtLink>
    </div>

    <div
      v-if="pending"
      class="vazio"
    >
      carregando…
    </div>

    <div
      v-else-if="!boloes?.length"
      class="card vazio"
    >
      <p><strong>Você ainda não está em nenhum bolão.</strong></p>
      <p class="fraco">
        Crie um novo ou entre com o código que alguém te passou.
      </p>
    </div>

    <div
      v-else
      class="pilha"
    >
      <NuxtLink
        v-for="bolao in boloes"
        :key="bolao.id"
        :to="`/b/${bolao.slug}`"
        class="card bolao"
      >
        <div class="linha">
          <h2 style="margin: 0">{{ bolao.name }}</h2>
          <span
            v-if="bolao.my_role !== 'player'"
            class="tag tag--verde"
          >organizador</span>
          <span
            v-if="bolao.status === 'finished'"
            class="tag"
          >encerrado</span>
        </div>
        <p
          class="fraco pequeno"
          style="margin: 0.25rem 0 0.6rem"
        >
          {{ bolao.competition }} {{ bolao.season }} · {{ bolao.members }}
          {{ bolao.members === 1 ? 'participante' : 'participantes' }}
        </p>
        <div class="linha pequeno">
          <span
            v-if="bolao.my_position"
            class="num"
          >
            <strong>{{ bolao.my_position }}º</strong> lugar
          </span>
          <span
            v-if="bolao.my_points !== null"
            class="num fraco"
          >
            {{ bolao.my_points }} pts
          </span>
          <span
            v-else
            class="fraco"
          >ainda sem pontuação</span>
        </div>
      </NuxtLink>
    </div>

    <div
      class="card"
      style="margin-top: 1.5rem"
    >
      <h2>Entrar com código</h2>
      <p class="fraco pequeno">
        O organizador do bolão te passa um código de 8 letras.
      </p>
      <form
        class="linha"
        @submit.prevent="entrarPorCodigo"
      >
        <input
          v-model="codigo"
          placeholder="ABCD2345"
          maxlength="12"
          style="max-width: 12rem; text-transform: uppercase; letter-spacing: 0.1em"
          aria-label="Código de convite"
        >
        <button
          type="submit"
          class="btn"
          :disabled="entrando || codigo.trim().length < 4"
        >
          Entrar
        </button>
      </form>
      <p
        v-if="erroEntrada"
        class="aviso aviso--erro"
        style="margin-top: 0.6rem"
      >
        {{ erroEntrada }}
      </p>
    </div>
  </div>
</template>

<style scoped>
.bolao { text-decoration: none; color: inherit; display: block; }
.bolao:hover { border-color: var(--verde); }
</style>
