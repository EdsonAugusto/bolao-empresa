<script setup lang="ts">
/**
 * Estádio à noite, atrás da tela de entrada.
 *
 * Por que não Three.js
 * --------------------
 * Uma cena assim em WebGL custaria ~150 KB de biblioteca e um laço de
 * renderização contínuo — num celular de entrada isso é bateria e calor por um
 * plano de fundo que ninguém veio ver. Aqui tudo é CSS: as camadas são
 * gradientes, a perspectiva do gramado é uma transformação 3D do próprio
 * navegador, e as **únicas** propriedades animadas são `opacity` e `transform`,
 * que o compositor resolve na GPU sem tocar no layout nem repintar.
 *
 * Custo real: zero JavaScript rodando, ~50 nós no DOM, nenhuma dependência.
 *
 * O que faz a cena parecer viva
 * -----------------------------
 * Flashes de câmera na arquibancada. É o detalhe que o olho reconhece como
 * "estádio à noite" — mais do que o gramado ou os refletores, que poderiam ser
 * um desenho parado.
 *
 * As posições dos flashes são **determinísticas**, calculadas de um gerador
 * pseudoaleatório semeado pelo índice. `Math.random()` daria valores diferentes
 * no servidor e no navegador, e a hidratação quebraria — é a mesma armadilha
 * que já custou um diagnóstico nesta base.
 */

/** Gerador com semente: mesmo índice, mesmo número, nos dois lados. */
function aleatorio(semente: number): number {
  const x = Math.sin(semente * 12.9898) * 43758.5453
  return x - Math.floor(x)
}

interface Flash {
  esquerda: number
  topo: number
  atraso: number
  duracao: number
  tamanho: number
}

/**
 * Distribuídos pela faixa da arquibancada, não uniformemente: mais densos no
 * meio, onde a torcida é mais próxima da câmera, como numa foto de verdade.
 */
const flashes = computed<Flash[]>(() =>
  Array.from({ length: 34 }, (_, i) => {
    const a = aleatorio(i + 1)
    const b = aleatorio(i + 101)
    const c = aleatorio(i + 201)
    return {
      // `a**0.7` puxa a distribuição para o centro sem deixar as bordas vazias.
      esquerda: 2 + a ** 0.7 * 96,
      topo: 6 + b * 66,
      atraso: c * 9,
      duracao: 3.4 + a * 3.2,
      tamanho: 2.2 + b * 2.4,
    }
  }),
)

/** Quatro torres, uma por canto — como num estádio de verdade. */
const torres = [
  { classe: 'torre--eo', lado: 'esquerda' },
  { classe: 'torre--ee', lado: 'esquerda' },
  { classe: 'torre--do', lado: 'direita' },
  { classe: 'torre--de', lado: 'direita' },
] as const
</script>

<template>
  <!-- `aria-hidden`: é atmosfera, não conteúdo. Um leitor de tela anunciando
       "estádio" antes do formulário só atrapalharia quem veio entrar. -->
  <div
    class="cena"
    aria-hidden="true"
  >
    <div class="ceu" />

    <!-- Arquibancada: silhueta escura com textura de multidão. A textura é um
         gradiente radial repetido, não uma imagem — não pesa nem um byte. -->
    <div class="arquibancada">
      <span
        v-for="(flash, i) in flashes"
        :key="i"
        class="flash"
        :style="{
          left: `${flash.esquerda}%`,
          top: `${flash.topo}%`,
          width: `${flash.tamanho}px`,
          height: `${flash.tamanho}px`,
          animationDelay: `${flash.atraso}s`,
          animationDuration: `${flash.duracao}s`,
        }"
      />
    </div>

    <div
      v-for="torre in torres"
      :key="torre.classe"
      class="torre"
      :class="torre.classe"
    >
      <span class="torre__mastro" />
      <span class="torre__lampada" />
      <span class="torre__facho" />
    </div>

    <!-- O gramado é um plano inclinado em 3D. As listras do corte são um
         gradiente repetido no plano ANTES da transformação, então elas
         convergem sozinhas com a perspectiva, como convergiriam de verdade. -->
    <div class="campo">
      <div class="campo__plano">
        <span class="linha linha--fundo" />
        <span class="linha linha--meio" />
        <span class="circulo" />
        <span class="area area--esquerda" />
        <span class="area--direita area" />
      </div>
    </div>

    <div class="bruma" />
    <div class="vinheta" />
  </div>
</template>

<style scoped>
.cena {
  position: fixed;
  inset: 0;
  z-index: 0;
  overflow: hidden;
  /* Atmosfera não recebe clique: o formulário está por cima. */
  pointer-events: none;
  background: #05080c;
}

/* --- céu ------------------------------------------------------------------
   Não é preto: é azul de noite, com o brilho quente que os refletores jogam no
   ar acima do estádio. É esse halo que diferencia "à noite" de "no escuro". */
.ceu {
  position: absolute;
  inset: 0;
  background:
    radial-gradient(140% 60% at 50% 74%, rgb(24 84 60 / 30%) 0%, transparent 66%),
    radial-gradient(70% 44% at 18% 6%, rgb(46 78 128 / 18%) 0%, transparent 72%),
    radial-gradient(70% 44% at 82% 6%, rgb(46 78 128 / 15%) 0%, transparent 72%),
    linear-gradient(180deg, #030710 0%, #050c14 44%, #041016 100%);
}

/* --- arquibancada ---------------------------------------------------------
   A "multidão" é um gradiente radial repetido em dois tamanhos, com um terceiro
   por cima escurecendo o fundo. Lido de longe vira textura de gente. */
.arquibancada {
  position: absolute;
  left: -6%;
  right: -6%;
  top: 14%;
  height: 42%;
  transform: perspective(140vmin) rotateX(-11deg);
  transform-origin: bottom center;
  background:
    radial-gradient(circle at 50% 50%, rgb(150 178 200 / 9%) 0.6px, transparent 1px) 0 0 / 7px 5px,
    radial-gradient(circle at 50% 50%, rgb(190 170 140 / 7%) 0.6px, transparent 1px) 3px 2px / 11px 8px,
    linear-gradient(180deg, rgb(6 12 18 / 92%) 0%, rgb(8 16 22 / 74%) 55%, rgb(5 11 15 / 96%) 100%);
  mask-image: linear-gradient(180deg, transparent 0%, #000 22%, #000 78%, transparent 100%);
}

/* Flash de câmera. Só `opacity` anima — o compositor resolve sem repintar.
   A sombra faz o brilho parecer luz, não um ponto branco colado. */
.flash {
  position: absolute;
  border-radius: 50%;
  background: #fff;
  box-shadow: 0 0 9px 3px rgb(255 250 235 / 80%);
  opacity: 0;
  will-change: opacity;
  animation-name: piscar;
  animation-iteration-count: infinite;
  animation-timing-function: ease-out;
}

@keyframes piscar {
  0%, 92% { opacity: 0; }
  93% { opacity: 0.95; }
  96% { opacity: 0.35; }
  100% { opacity: 0; }
}

/* --- refletores -----------------------------------------------------------
   O facho é um trapézio recortado com `clip-path` e desfocado. Volume de luz
   custa caro em WebGL e sai de graça aqui. */
.torre {
  position: absolute;
  top: 8%;
  width: 1px;
  height: 24%;
}

.torre--eo { left: 12%; }
.torre--ee { left: 27%; }
.torre--do { right: 12%; }
.torre--de { right: 27%; }

.torre__mastro {
  position: absolute;
  inset: 0;
  width: 2px;
  background: linear-gradient(180deg, rgb(120 140 160 / 30%), rgb(120 140 160 / 6%));
}

.torre__lampada {
  position: absolute;
  top: -10px;
  left: -13px;
  width: 28px;
  height: 12px;
  border-radius: 3px;
  background: linear-gradient(180deg, #f4f6df, #b9c4a8);
  box-shadow: 0 0 18px 6px rgb(240 246 215 / 45%);
}

.torre__facho {
  position: absolute;
  top: 0;
  left: -46vmin;
  width: 92vmin;
  height: 78vmin;
  clip-path: polygon(48% 0%, 52% 0%, 100% 100%, 0% 100%);
  background: linear-gradient(180deg, rgb(238 245 214 / 13%) 0%, rgb(238 245 214 / 0%) 78%);
  filter: blur(26px);
  opacity: 0.62;
  will-change: opacity;
  animation: respirar 9s ease-in-out infinite;
}

/* Cada torre respira fora de fase; juntas dariam um pulso de discoteca. */
.torre--ee .torre__facho { animation-delay: -2.4s; }
.torre--do .torre__facho { animation-delay: -4.8s; }
.torre--de .torre__facho { animation-delay: -7.1s; }

@keyframes respirar {
  0%, 100% { opacity: 0.58; }
  50% { opacity: 0.82; }
}

/* --- gramado --------------------------------------------------------------
   `rotateX` num plano com gradiente repetido dá a convergência correta de
   graça: é o próprio navegador fazendo a projeção. */
.campo {
  position: absolute;
  left: -30%;
  right: -30%;
  bottom: -10%;
  height: 46%;
  perspective: 52vmin;
  perspective-origin: 50% 0%;
}

.campo__plano {
  position: absolute;
  inset: 0;
  transform: rotateX(64deg);
  transform-origin: bottom center;
  background:
    /* Clarões sob cada torre: é o que faz o gramado parecer iluminado por
       quatro pontos, e não pintado de verde por igual. */
    radial-gradient(38% 46% at 22% 8%, rgb(180 220 190 / 9%) 0%, transparent 70%),
    radial-gradient(38% 46% at 78% 8%, rgb(180 220 190 / 9%) 0%, transparent 70%),
    repeating-linear-gradient(
      90deg,
      rgb(255 255 255 / 2.6%) 0 6%,
      transparent 6% 12%
    ),
    linear-gradient(180deg, #082a19 0%, #0a3520 38%, #051d11 100%);
  box-shadow: inset 0 60px 120px rgb(0 0 0 / 68%);
  mask-image: linear-gradient(180deg, transparent 0%, #000 22%, #000 100%);
}

.linha {
  position: absolute;
  background: rgb(235 245 238 / 26%);
}

.linha--fundo { left: 12%; right: 12%; top: 24%; height: 2px; }
.linha--meio { left: 8%; right: 8%; top: 58%; height: 2px; }

.circulo {
  position: absolute;
  left: 50%;
  top: 58%;
  width: 26%;
  height: 20%;
  transform: translate(-50%, -50%);
  border: 2px solid rgb(235 245 238 / 22%);
  border-radius: 50%;
}

.area {
  position: absolute;
  top: 24%;
  height: 16%;
  width: 16%;
  border: 2px solid rgb(235 245 238 / 18%);
  border-top: none;
}

.area--esquerda { left: 26%; }
.area--direita { right: 26%; }

/* --- acabamento -----------------------------------------------------------
   A bruma separa o gramado da arquibancada e disfarça a emenda entre os dois
   planos 3D; a vinheta fecha as bordas e joga o olho para o centro, onde está
   o formulário. */
.bruma {
  position: absolute;
  left: 0;
  right: 0;
  top: 48%;
  height: 22%;
  background: linear-gradient(180deg, transparent, rgb(18 46 38 / 30%), transparent);
  filter: blur(18px);
}

.vinheta {
  position: absolute;
  inset: 0;
  background:
    radial-gradient(85% 65% at 50% 46%, transparent 12%, rgb(1 4 7 / 55%) 62%, rgb(1 3 6 / 86%) 100%);
}

/* --- celular --------------------------------------------------------------
   Em retrato o gramado tem que ocupar menos altura, senão empurra o formulário
   para fora da tela; e os refletores se aproximam para caber. */
@media (width <= 640px) {
  .campo { height: 38%; bottom: -8%; perspective: 40vmin; }
  .campo__plano { transform: rotateX(68deg); }
  .arquibancada { top: 10%; height: 40%; }
  .torre { height: 20%; }
  .torre--eo { left: 6%; }
  .torre--ee { left: 24%; }
  .torre--do { right: 6%; }
  .torre--de { right: 24%; }
  .torre__facho { width: 130vmin; left: -65vmin; }
}

/* Tela baixa e larga (celular deitado): o gramado sobe demais e cobre o card. */
@media (height <= 520px) {
  .campo { height: 34%; }
  .arquibancada { top: 14%; height: 26%; }
}

/* --- movimento reduzido ---------------------------------------------------
   Quem pediu menos movimento no sistema recebe a mesma cena, parada. Não é uma
   versão pobre: os flashes ficam acesos num brilho fraco, e o estádio continua
   de pé. */
@media (prefers-reduced-motion: reduce) {
  .flash {
    animation: none;
    opacity: 0.22;
  }

  .torre__facho {
    animation: none;
    opacity: 0.7;
  }
}
</style>
