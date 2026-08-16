import { mount } from '@vue/test-utils'
import { describe, expect, it } from 'vitest'

import KickoffTime from '~/components/KickoffTime.vue'

const KICKOFF = '2026-03-15T00:30:00Z'

describe('KickoffTime', () => {
  it('mostra o horário de Brasília, não o UTC', () => {
    const wrapper = mount(KickoffTime, {
      props: { kickoffAt: KICKOFF, now: new Date('2026-03-14T12:00:00Z') },
    })

    expect(wrapper.text()).toContain('14/03 21:30')
  })

  it('não marca como fechado antes do apito', () => {
    const wrapper = mount(KickoffTime, {
      props: { kickoffAt: KICKOFF, now: new Date('2026-03-15T00:29:59Z') },
    })

    expect(wrapper.text()).not.toContain('palpite fechado')
    expect(wrapper.classes()).not.toContain('kickoff--closed')
  })

  it('marca como fechado a partir do apito', () => {
    const wrapper = mount(KickoffTime, {
      props: { kickoffAt: KICKOFF, now: new Date('2026-03-15T00:30:00Z') },
    })

    expect(wrapper.text()).toContain('palpite fechado')
    expect(wrapper.classes()).toContain('kickoff--closed')
  })

  it('expõe o instante em UTC no atributo datetime, para máquinas', () => {
    const wrapper = mount(KickoffTime, { props: { kickoffAt: KICKOFF } })

    expect(wrapper.attributes('datetime')).toBe(KICKOFF)
  })
})
