import { describe, expect, it } from 'vitest'

import {
  formatKickoff,
  hasKickedOff,
  kickoffDayKey,
  kickoffTime,
  minutesUntilKickoff,
} from '~/utils/datetime'

describe('kickoffDayKey', () => {
  it('coloca o jogo no dia certo quando o UTC já virou', () => {
    // Sábado 21:30 em São Paulo = domingo 00:30 em UTC.
    // Agrupar pela data UTC jogaria a partida para o dia seguinte.
    expect(kickoffDayKey('2026-03-15T00:30:00Z')).toBe('2026-03-14')
  })

  it('mantém o dia quando não há virada', () => {
    expect(kickoffDayKey('2026-03-14T19:00:00Z')).toBe('2026-03-14')
  })

  it('respeita o fuso informado', () => {
    expect(kickoffDayKey('2026-03-15T00:30:00Z', 'UTC')).toBe('2026-03-15')
  })
})

describe('kickoffTime', () => {
  it('converte para o horário de Brasília', () => {
    expect(kickoffTime('2026-03-15T00:30:00Z')).toBe('21:30')
  })

  it('normaliza meia-noite para 00:00', () => {
    // 03:00Z = 00:00 em São Paulo (UTC-3).
    expect(kickoffTime('2026-03-15T03:00:00Z')).toBe('00:00')
  })
})

describe('formatKickoff', () => {
  it('monta o rótulo curto do card', () => {
    expect(formatKickoff('2026-03-15T00:30:00Z')).toBe('14/03 21:30')
  })

  it('rejeita data inválida em vez de renderizar NaN', () => {
    expect(() => formatKickoff('nao-e-data')).toThrow(RangeError)
  })
})

describe('hasKickedOff', () => {
  const kickoff = '2026-03-15T00:30:00Z'

  it('é falso um minuto antes', () => {
    expect(hasKickedOff(kickoff, new Date('2026-03-15T00:29:00Z'))).toBe(false)
  })

  it('é verdadeiro no instante exato do apito', () => {
    expect(hasKickedOff(kickoff, new Date('2026-03-15T00:30:00Z'))).toBe(true)
  })

  it('é verdadeiro depois', () => {
    expect(hasKickedOff(kickoff, new Date('2026-03-15T00:31:00Z'))).toBe(true)
  })
})

describe('minutesUntilKickoff', () => {
  it('conta os minutos que faltam', () => {
    expect(
      minutesUntilKickoff('2026-03-15T00:30:00Z', new Date('2026-03-14T23:00:00Z')),
    ).toBe(90)
  })

  it('não devolve negativo depois do apito', () => {
    expect(
      minutesUntilKickoff('2026-03-15T00:30:00Z', new Date('2026-03-15T02:00:00Z')),
    ).toBe(0)
  })
})
