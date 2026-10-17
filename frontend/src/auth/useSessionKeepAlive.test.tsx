import { act, renderHook } from '@testing-library/react'
import { afterEach, beforeEach, describe, expect, it, vi } from 'vitest'
import type { AuthApi, Session } from '../api/auth'
import { ACTIVITY_REFRESH_MS, useSessionKeepAlive } from './useSessionKeepAlive'

const NOW = new Date('2026-10-17T10:00:00Z').getTime()

function session(minutesLeft: number): Session {
  return {
    user: {
      id: 'a'.repeat(32),
      email: 'dr.test@example.test',
      displayName: 'Dr Test',
      roles: ['clinician'],
      isActive: true,
      mustChangePassword: false,
      createdAt: '2026-10-01T09:00:00Z',
      lastLoginAt: null,
    },
    activeRole: 'clinician',
    mustChangePassword: false,
    sessionExpiresAt: new Date(Date.now() + minutesLeft * 60_000).toISOString(),
  }
}

function fakeAuth(me: () => Promise<Session | null>): AuthApi {
  return { me: vi.fn(me) } as unknown as AuthApi
}

beforeEach(() => {
  vi.useFakeTimers()
  vi.setSystemTime(NOW)
})

afterEach(() => {
  vi.useRealTimers()
})

describe('useSessionKeepAlive', () => {
  it('refreshes the session on activity, at most every two minutes', async () => {
    const auth = fakeAuth(async () => session(30))
    const onRefreshed = vi.fn()
    renderHook(() => useSessionKeepAlive(auth, session(30), onRefreshed, vi.fn()))

    window.dispatchEvent(new KeyboardEvent('keydown'))
    expect(auth.me).not.toHaveBeenCalled() // just signed in

    await act(async () => {
      vi.advanceTimersByTime(ACTIVITY_REFRESH_MS)
      window.dispatchEvent(new KeyboardEvent('keydown'))
      window.dispatchEvent(new PointerEvent('pointerdown'))
    })
    expect(auth.me).toHaveBeenCalledTimes(1)
    expect(onRefreshed).toHaveBeenCalledTimes(1)
  })

  it('signs out when the expiry passes without activity and the server agrees', async () => {
    const auth = fakeAuth(async () => null)
    const onEnded = vi.fn()
    renderHook(() => useSessionKeepAlive(auth, session(30), vi.fn(), onEnded))
    await act(async () => {
      vi.advanceTimersByTime(30 * 60_000 + 1_500)
    })
    expect(auth.me).toHaveBeenCalledTimes(1)
    expect(onEnded).toHaveBeenCalledTimes(1)
  })

  it('keeps the screen when the backend is unreachable', async () => {
    const auth = fakeAuth(async () => {
      throw new Error('unreachable')
    })
    const onEnded = vi.fn()
    renderHook(() => useSessionKeepAlive(auth, session(1), vi.fn(), onEnded))
    await act(async () => {
      vi.advanceTimersByTime(2 * 60_000)
    })
    expect(onEnded).not.toHaveBeenCalled()
  })

  it('does nothing while signed out', async () => {
    const auth = fakeAuth(async () => null)
    renderHook(() => useSessionKeepAlive(auth, null, vi.fn(), vi.fn()))
    await act(async () => {
      vi.advanceTimersByTime(60 * 60_000)
      window.dispatchEvent(new KeyboardEvent('keydown'))
    })
    expect(auth.me).not.toHaveBeenCalled()
  })
})
