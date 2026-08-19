<script setup lang="ts">
/**
 * Layout da tela de entrada.
 *
 * Separado do padrão de propósito: o cabeçalho com navegação e o rodapé de
 * conteúdo não fazem sentido para quem ainda não entrou, e cortavam a cena do
 * estádio em três faixas. Aqui a página é uma tela só.
 */
</script>

<template>
  <div class="acesso">
    <EstadioFundo />

    <main class="acesso__conteudo">
      <slot />
    </main>
  </div>
</template>

<style scoped>
.acesso {
  position: relative;
  min-height: 100dvh;
  display: flex;
  flex-direction: column;
}

/* A cor clara vale só para o texto SOLTO sobre a cena. Pô-la em `.acesso`
   fazia ela descer por herança até dentro do card — e o título "Entrar" e os
   rótulos dos campos ficavam cinza-claro sobre branco, quase invisíveis. */

.acesso__conteudo {
  position: relative;
  z-index: 1;
  flex: 1;
  display: flex;
  align-items: center;
  justify-content: center;
  width: 100%;
  padding:
    calc(var(--e5) + env(safe-area-inset-top, 0px))
    calc(var(--e4) + env(safe-area-inset-right, 0px))
    var(--e5)
    calc(var(--e4) + env(safe-area-inset-left, 0px));
}

/* Em tela baixa (celular deitado) o card precisa poder rolar em vez de ser
   centralizado e cortado pelas duas pontas. */
@media (height <= 620px) {
  .acesso__conteudo { align-items: flex-start; }
}
</style>
