<script setup lang="ts">
import { computed } from 'vue'

import { DEFAULT_TIMEZONE, formatKickoff, hasKickedOff } from '~/utils/datetime'

const props = withDefaults(
  defineProps<{
    /** Instante do apito inicial, sempre em UTC (ISO 8601). */
    kickoffAt: string
    /** Referência de "agora". Injetada para manter o componente determinístico. */
    now?: Date
    timezone?: string
  }>(),
  { now: () => new Date(), timezone: DEFAULT_TIMEZONE },
)

const label = computed(() => formatKickoff(props.kickoffAt, props.timezone))
const closed = computed(() => hasKickedOff(props.kickoffAt, props.now))
</script>

<template>
  <time
    :datetime="kickoffAt"
    :class="['kickoff', { 'kickoff--closed': closed }]"
  >
    {{ label }}
    <span
      v-if="closed"
      class="kickoff__badge"
    >palpite fechado</span>
  </time>
</template>

<style scoped>
.kickoff {
  display: inline-flex;
  align-items: center;
  gap: 0.5rem;
  font-variant-numeric: tabular-nums;
}

.kickoff--closed {
  color: var(--color-muted);
}

.kickoff__badge {
  font-size: 0.75rem;
  padding: 0.1rem 0.4rem;
  border-radius: 999px;
  border: 1px solid currentColor;
}
</style>
