import { describe, expect, it, vi } from 'vitest'
import { ApiRequestError } from './client'
import { createRequester } from './request'

function respond(status: number, payload?: unknown) {
  return vi.fn(async () =>
    status === 204
      ? new Response(null, { status })
      : new Response(JSON.stringify(payload), { status, headers: { 'Content-Type': 'application/json' } }),
  )
}

const error = (code: string) => ({ error: { code, message: 'Synthetic message.', retryable: false } })

describe('createRequester', () => {
  it('marks every request as coming from the app and keeps the cookie same-origin', async () => {
    const fetch = respond(200, { ok: true })
    const request = createRequester({ fetch: fetch as unknown as typeof globalThis.fetch })
    await request('GET', '/api/auth/me')
    await request('POST', '/api/recording/start', { a: 1 })
    for (const [, init] of fetch.mock.calls as unknown as [string, RequestInit][]) {
      expect(init.headers).toMatchObject({ 'X-AirSOAP-Request': '1' })
      expect(init.credentials).toBe('same-origin')
      expect(init.cache).toBe('no-store')
    }
    const [, post] = fetch.mock.calls[1] as unknown as [string, RequestInit]
    expect(post.headers).toMatchObject({ 'Content-Type': 'application/json' })
    expect(post.body).toBe('{"a":1}')
  })

  it.each(['SESSION_EXPIRED', 'ACCOUNT_DISABLED', 'AUTH_REQUIRED'])('reports %s as the end of the session', async (code) => {
    const onSessionEnded = vi.fn()
    const request = createRequester({ fetch: respond(401, error(code)) as unknown as typeof globalThis.fetch, onSessionEnded })
    await expect(request('POST', '/api/recording/start')).rejects.toMatchObject({ code })
    expect(onSessionEnded).toHaveBeenCalledWith(code)
  })

  it('does not report a wrong password or a forbidden action as the end of the session', async () => {
    const onSessionEnded = vi.fn()
    for (const [status, code] of [
      [401, 'INVALID_CREDENTIALS'],
      [403, 'FORBIDDEN'],
      [403, 'REQUEST_REJECTED'],
    ] as const) {
      const request = createRequester({ fetch: respond(status, error(code)) as unknown as typeof globalThis.fetch, onSessionEnded })
      await expect(request('POST', '/api/auth/login')).rejects.toBeInstanceOf(ApiRequestError)
    }
    expect(onSessionEnded).not.toHaveBeenCalled()
  })

  it('stays quiet when "not signed in" is an expected answer', async () => {
    const onSessionEnded = vi.fn()
    const request = createRequester({ fetch: respond(401, error('AUTH_REQUIRED')) as unknown as typeof globalThis.fetch, onSessionEnded })
    await expect(request('GET', '/api/auth/me', undefined, { reportSessionEnd: false })).rejects.toBeInstanceOf(ApiRequestError)
    expect(onSessionEnded).not.toHaveBeenCalled()
  })

  it('accepts an empty 204 answer', async () => {
    const request = createRequester({ fetch: respond(204) as unknown as typeof globalThis.fetch })
    await expect(request('POST', '/api/auth/logout')).resolves.toBeNull()
  })

  it('turns a network failure into a readable error', async () => {
    const fetch = vi.fn(async () => {
      throw new TypeError('Failed to fetch')
    })
    const request = createRequester({ fetch: fetch as unknown as typeof globalThis.fetch })
    await expect(request('GET', '/api/auth/me')).rejects.toMatchObject({ code: 'UNAVAILABLE' })
  })
})
