/** Formatação de apresentação. Toda conversão de fuso passa por aqui. */

import { DEFAULT_TIMEZONE, formatKickoff, kickoffDayKey, kickoffTime } from '~/utils/datetime'

const DIAS = ['domingo', 'segunda', 'terça', 'quarta', 'quinta', 'sexta', 'sábado']
const MESES = [
  'janeiro', 'fevereiro', 'março', 'abril', 'maio', 'junho',
  'julho', 'agosto', 'setembro', 'outubro', 'novembro', 'dezembro',
]

export function useFormat() {
  const { user } = useAuth()
  const timezone = computed(() => user.value?.timezone || DEFAULT_TIMEZONE)

  function kickoff(utcIso: string): string {
    return formatKickoff(utcIso, timezone.value)
  }

  function hora(utcIso: string): string {
    return kickoffTime(utcIso, timezone.value)
  }

  function diaChave(utcIso: string): string {
    return kickoffDayKey(utcIso, timezone.value)
  }

  /** "sábado, 14 de março" — cabeçalho de grupo de jogos. */
  function diaExtenso(dayKey: string): string {
    const [year, month, day] = dayKey.split('-').map(Number)
    if (!year || !month || !day) return dayKey
    // Meio-dia UTC evita que o dia da semana vire por causa do fuso.
    const date = new Date(Date.UTC(year, month - 1, day, 12))
    return `${DIAS[date.getUTCDay()]}, ${day} de ${MESES[month - 1]}`
  }

  function pontos(valor: number): string {
    return `${valor} ${valor === 1 ? 'ponto' : 'pontos'}`
  }

  function movimento(valor: number): string {
    if (valor > 0) return `▲ ${valor}`
    if (valor < 0) return `▼ ${Math.abs(valor)}`
    return '—'
  }

  return { timezone, kickoff, hora, diaChave, diaExtenso, pontos, movimento }
}
