<script setup lang="ts">
definePageMeta({ middleware: 'auth' })

const { avisos, carregando, carregar, marcarLidos } = useNotificacoes()

useHead({ title: 'Avisos' })

onMounted(carregar)

async function lerTodos() {
  await marcarLidos(avisos.value.map(aviso => aviso.id))
}
</script>

<template>
  <div>
    <div class="linha">
      <h1>Avisos</h1>
      <button
        v-if="avisos.length"
        type="button"
        class="btn pequeno empurra"
        @click="lerTodos"
      >
        marcar todos como lidos
      </button>
    </div>

    <div
      v-if="carregando"
      class="vazio"
    >
      carregando…
    </div>

    <div
      v-else-if="!avisos.length"
      class="card vazio"
    >
      Nada por aqui. Você é avisado quando uma rodada abre, quando faltam
      palpites seus e quando o ranking muda.
    </div>

    <div
      v-else
      class="pilha"
    >
      <article
        v-for="aviso in avisos"
        :key="aviso.id"
        class="card"
      >
        <div class="linha">
          <strong>{{ aviso.title }}</strong>
          <button
            type="button"
            class="btn btn--fantasma pequeno empurra"
            @click="marcarLidos([aviso.id])"
          >
            ok
          </button>
        </div>
        <p style="margin: 0.25rem 0 0">
          {{ aviso.body }}
        </p>
      </article>
    </div>
  </div>
</template>
