/**
 * Conversão de data/hora entre o banco e a tela.
 *
 * Regra do projeto: o backend só fala UTC. Toda conversão para horário local
 * acontece aqui, na borda. É o ponto exato onde a maioria dos bolões erra —
 * um jogo às 21:30 de sábado em São Paulo é 00:30 de *domingo* em UTC, e
 * agrupar a rodada pela data UTC coloca o jogo no dia errado.
 *
 * Todas as funções são puras e recebem o fuso explicitamente, para que o fuso
 * da máquina que roda o código nunca influencie o resultado.
 */

export const DEFAULT_TIMEZONE = 'America/Sao_Paulo'

interface DateParts {
  year: string
  month: string
  day: string
  hour: string
  minute: string
}

function extractParts(utcIso: string, timeZone: string): DateParts {
  const date = new Date(utcIso)
  if (Number.isNaN(date.getTime())) {
    throw new RangeError(`Data inválida: ${utcIso}`)
  }

  const formatted = new Intl.DateTimeFormat('en-CA', {
    timeZone,
    year: 'numeric',
    month: '2-digit',
    day: '2-digit',
    hour: '2-digit',
    minute: '2-digit',
    hour12: false,
  }).formatToParts(date)

  const lookup = (type: Intl.DateTimeFormatPartTypes): string =>
    formatted.find(part => part.type === type)?.value ?? ''

  // Intl devolve "24" para meia-noite em parte dos runtimes quando hour12
  // está desligado. Normalizar evita "24:30" na tela.
  const hour = lookup('hour') === '24' ? '00' : lookup('hour')

  return {
    year: lookup('year'),
    month: lookup('month'),
    day: lookup('day'),
    hour,
    minute: lookup('minute'),
  }
}

/**
 * Chave de agrupamento por dia no fuso do usuário: `YYYY-MM-DD`.
 * É isso que define em que dia da rodada o jogo aparece na lista.
 */
export function kickoffDayKey(utcIso: string, timeZone = DEFAULT_TIMEZONE): string {
  const { year, month, day } = extractParts(utcIso, timeZone)
  return `${year}-${month}-${day}`
}

/** Hora local no formato `HH:mm`. */
export function kickoffTime(utcIso: string, timeZone = DEFAULT_TIMEZONE): string {
  const { hour, minute } = extractParts(utcIso, timeZone)
  return `${hour}:${minute}`
}

/** Rótulo curto para card de jogo: `14/03 21:30`. */
export function formatKickoff(utcIso: string, timeZone = DEFAULT_TIMEZONE): string {
  const { day, month, hour, minute } = extractParts(utcIso, timeZone)
  return `${day}/${month} ${hour}:${minute}`
}

/**
 * O jogo já começou?
 *
 * Recebe `now` como parâmetro de propósito: a função é pura e testável, e o
 * relógio do cliente nunca é a autoridade. O servidor é quem trava o palpite;
 * isto é apenas para a UI não oferecer um campo que vai ser recusado.
 */
export function hasKickedOff(kickoffUtcIso: string, now: Date): boolean {
  const kickoff = new Date(kickoffUtcIso)
  if (Number.isNaN(kickoff.getTime())) {
    throw new RangeError(`Data inválida: ${kickoffUtcIso}`)
  }
  return now.getTime() >= kickoff.getTime()
}

/** Minutos restantes até o fechamento do palpite (0 se já fechou). */
export function minutesUntilKickoff(kickoffUtcIso: string, now: Date): number {
  const diffMs = new Date(kickoffUtcIso).getTime() - now.getTime()
  return diffMs <= 0 ? 0 : Math.floor(diffMs / 60_000)
}
