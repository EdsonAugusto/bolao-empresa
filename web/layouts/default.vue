<script setup lang="ts">
const { user, isLoggedIn, logout } = useAuth()
const { avisos, carregar } = useNotificacoes()
const menuAberto = ref(false)
const painelAberto = ref(false)
const route = useRoute()

/**
 * O menu segue as PERMISSÕES, não o cargo.
 *
 * "Campeonatos" importa tabela para a instalação inteira; "Rede" mostra o
 * endereço da máquina; "Pessoas" mexe nas contas. Cada um aparece para quem
 * pode aquilo — o organizador do bolão da firma vê Campeonatos sem ver Pessoas,
 * que é exatamente o arranjo que a hierarquia existe para permitir.
 */
const { pode } = usePermissoes()
const podeImportar = computed(() => pode('campeonatos.importar'))
const podeVerPessoas = computed(() => pode('usuarios.ver'))
const podeConfigurar = computed(() => pode('plataforma.configurar'))

watch(() => route.fullPath, () => {
  menuAberto.value = false
})

onMounted(() => {
  if (isLoggedIn.value) carregar()
})
</script>

<template>
  <div class="app">
    <header class="topo">
      <NuxtLink
        to="/"
        class="marca"
      >
        <img
          src="/marca-96.png"
          alt=""
          width="26"
          height="26"
          class="marca__icone"
        >
        <span>Bolão</span>
      </NuxtLink>

      <nav
        v-if="isLoggedIn"
        class="nav"
        :class="{ 'nav--aberto': menuAberto }"
      >
        <NuxtLink to="/">Meus bolões</NuxtLink>
        <NuxtLink
          v-if="podeImportar"
          to="/campeonatos"
        >
          Campeonatos
        </NuxtLink>
        <NuxtLink to="/avisos">
          Avisos
          <span
            v-if="avisos.length"
            class="badge"
          >{{ avisos.length }}</span>
        </NuxtLink>
        <NuxtLink
          v-if="podeVerPessoas"
          to="/pessoas"
        >
          Pessoas
        </NuxtLink>
        <NuxtLink
          v-if="podeConfigurar"
          to="/rede"
        >
          Rede
        </NuxtLink>
        <NuxtLink to="/relatos">Relatos</NuxtLink>
        <NuxtLink to="/perfil">{{ user?.display_name }}</NuxtLink>
        <button
          type="button"
          class="btn btn--fantasma pequeno"
          @click="logout()"
        >
          Sair
        </button>
      </nav>

      <button
        v-if="isLoggedIn"
        type="button"
        class="hamburguer"
        :aria-expanded="menuAberto"
        aria-label="Abrir menu"
        @click="menuAberto = !menuAberto"
      >
        ☰
      </button>
    </header>

    <main class="conteudo">
      <slot />
    </main>

    <!-- Disponível em qualquer tela: o bug é relatado onde ele aparece, e é
         isso que faz o contexto capturado valer alguma coisa. -->
    <button
      v-if="isLoggedIn"
      type="button"
      class="relatar"
      title="Relatar problema ou dar retorno"
      aria-label="Relatar problema ou dar retorno"
      @click="painelAberto = true"
    >
      🐞
    </button>
    <RelatoPainel
      v-if="isLoggedIn"
      v-model="painelAberto"
    />
  </div>
</template>

<style scoped>
.app { min-height: 100dvh; display: flex; flex-direction: column; }

.topo {
  display: flex;
  align-items: center;
  gap: 1rem;
  /* O respiro do topo soma a faixa do entalhe: com `viewport-fit=cover` o
     cabeçalho grudado passa por baixo da Dynamic Island se ninguém somar. */
  padding: calc(0.7rem + env(safe-area-inset-top, 0px)) 1rem 0.7rem;
  padding-left: calc(1rem + env(safe-area-inset-left, 0px));
  padding-right: calc(1rem + env(safe-area-inset-right, 0px));
  background: var(--superficie);
  border-bottom: 1px solid var(--borda);
  position: sticky;
  top: 0;
  z-index: 10;

  /* Listra de grama cortada, no contraste mais baixo que ainda se nota, mais
     a linha de cal do gramado embaixo. Decoração que não disputa com texto. */
  background-image:
    repeating-linear-gradient(
      90deg,
      rgb(16 113 74 / 3.5%) 0 56px,
      transparent 56px 112px
    );
  box-shadow: inset 0 -2px 0 -1px var(--verde-borda);
}

/* A marca é o caminho de volta para a lista de bolões — o alvo mais usado do
   cabeçalho depois do menu. Precisa ter altura de dedo. */
.marca {
  min-height: 44px;
  font-weight: 800;
  font-size: 1.1rem;
  letter-spacing: -0.02em;
  text-decoration: none;
  color: var(--texto);
  display: inline-flex;
  align-items: center;
  gap: 0.45rem;
}

.marca__icone { border-radius: 6px; display: block; }

.nav { display: flex; align-items: center; gap: 1.15rem; margin-left: auto; }

.nav a {
  color: var(--texto-fraco);
  text-decoration: none;
  font-size: 0.92rem;
  font-weight: 600;
  position: relative;
  padding-block: 0.15rem;
}

.nav a:hover, .nav a.router-link-active { color: var(--verde); }

/* Sublinhado da aba atual: some no menu vertical do celular. */
.nav a.router-link-active::after {
  content: '';
  position: absolute;
  left: 0;
  right: 0;
  bottom: -0.85rem;
  height: 2px;
  border-radius: 2px;
  background: var(--verde);
}

/* `--sobre-verde` e não `#fff`: no tema escuro o verde clareia e o branco em
   cima cai para 2,4:1 — o contador de avisos some justo quando tem aviso. */
.badge {
  background: var(--verde);
  color: var(--sobre-verde);
  border-radius: 999px;
  font-size: 0.7rem;
  padding: 0 0.35rem;
  margin-left: 0.2rem;
}

/* 44px é o mínimo que um dedo acerta sem mirar. O ☰ tinha 30×29 e é o único
   acesso ao menu no celular — errar nele é ficar sem navegação. */
.hamburguer {
  display: none;
  margin-left: auto;
  min-width: 44px;
  min-height: 44px;
  font-size: 1.4rem;
  background: none;
  border: none;
  border-radius: var(--raio-p);
  color: inherit;
  cursor: pointer;
}

.hamburguer:focus-visible { outline: 2px solid var(--verde); outline-offset: 2px; }

.conteudo {
  width: 100%;
  max-width: 72rem;
  margin-inline: auto;
  padding: var(--e5) calc(var(--e4) + env(safe-area-inset-right, 0px)) 4rem
    calc(var(--e4) + env(safe-area-inset-left, 0px));
  flex: 1;
}

/* Fica fora do fluxo e acima de tudo, menos do próprio painel. */
.relatar {
  position: fixed;
  right: 1rem;
  bottom: calc(1rem + env(safe-area-inset-bottom, 0px));
  z-index: 30;
  width: 3rem;
  height: 3rem;
  border-radius: 50%;
  border: 1px solid var(--borda-forte);
  background: var(--superficie);
  box-shadow: var(--sombra-alta);
  font-size: 1.3rem;
  line-height: 1;
  cursor: pointer;
  transition: transform 0.12s ease;
}

/* Enquanto a coluna de conteúdo encosta na borda direita da tela, o canto do
   balão cai exatamente sobre o canto do botão das barras grudadas — o "Salvar
   palpites" e o "Salvar rodada". Os dois usam `right/bottom: 1rem`, então a
   sobreposição não é aproximada: é o mesmo ponto, com o balão por cima.

   No celular isso é o polegar direito abrindo o formulário de bug em vez de
   salvar o palpite, minutos antes do jogo. 1280px = 72rem de conteúdo + 3rem
   do balão + folga para a barra de rolagem do desktop. */
@media (width < 1280px) {
  /* 5.5rem cobre o botão; a barra cresce quando aparece a mensagem de
     "N palpite(s) salvo(s)" logo acima dele. `--altura-barra` deixa a página
     dizer de quanto ela precisa; sem ela, vale o suficiente para o botão. */
  .relatar { bottom: calc(var(--altura-barra, 5.5rem) + env(safe-area-inset-bottom, 0px)); }
}

.relatar:hover { transform: scale(1.06); }
.relatar:focus-visible { outline: 2px solid var(--verde-claro); outline-offset: 2px; }

@media (width <= 700px) {
  .hamburguer { display: block; }

  .nav {
    display: none;
    position: absolute;
    top: 100%;
    left: 0;
    right: 0;
    flex-direction: column;
    align-items: stretch;
    gap: 0;
    background: var(--superficie);
    border-bottom: 1px solid var(--borda);
    padding: 0.5rem 1rem 1rem;
  }

  .nav--aberto { display: flex; }
  .nav a, .nav button { padding: 0.6rem 0; text-align: left; }

  /* No menu vertical o sublinhado ficaria solto no meio da lista. */
  .nav a.router-link-active::after { display: none; }
}
</style>
