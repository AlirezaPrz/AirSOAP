import { describe, expect, it, vi } from 'vitest'
import { createHttpAuthApi, createMockAuthApi, type Session } from './auth'

const SESSION: Session = {
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
  sessionExpiresAt: '2026-10-17T10:30:00Z',
}

function backend(routes: Record<string, [number, unknown]>) {
  const fetch = vi.fn(async (input: RequestInfo | URL, init?: RequestInit) => {
    const [status, payload] = routes[`${init?.method ?? 'GET'} ${String(input)}`] ?? [404, { detail: 'Not Found' }]
    return status === 204
      ? new Response(null, { status })
      : new Response(JSON.stringify(payload), { status, headers: { 'Content-Type': 'application/json' } })
  })
  return fetch
}

const asFetch = (fetch: ReturnType<typeof backend>) => fetch as unknown as typeof globalThis.fetch

describe('createHttpAuthApi', () => {
  it('reports "signed out" as no session rather than an error', async () => {
    const fetch = backend({
      'GET /api/auth/me': [401, { error: { code: 'AUTH_REQUIRED', message: 'Please sign in.', retryable: false } }],
    })
    const onSessionEnded = vi.fn()
    await expect(createHttpAuthApi({ fetch: asFetch(fetch), onSessionEnded }).me()).resolves.toBeNull()
    expect(onSessionEnded).not.toHaveBeenCalled()
  })

  it('sends the password only in the body of a POST, never in a URL', async () => {
    const fetch = backend({ 'POST /api/auth/login': [200, SESSION], 'POST /api/auth/password': [200, SESSION] })
    const auth = createHttpAuthApi({ fetch: asFetch(fetch) })
    await auth.login('dr.test@example.test', 'synthetic-password-1')
    await auth.changePassword('synthetic-password-1', 'synthetic-password-2')
    for (const [url, init] of fetch.mock.calls) {
      expect(String(url)).not.toMatch(/synthetic-password/)
      expect(init?.method).toBe('POST')
    }
    expect(JSON.parse(String(fetch.mock.calls[0][1]?.body))).toEqual({
      email: 'dr.test@example.test',
      password: 'synthetic-password-1',
    })
  })

  it('maps the account calls onto the admin endpoints', async () => {
    const id = 'b'.repeat(32)
    const fetch = backend({
      'GET /api/admin/users': [200, []],
      'POST /api/admin/users': [201, { user: SESSION.user, temporaryPassword: 'x' }],
      [`PATCH /api/admin/users/${id}`]: [200, SESSION.user],
      [`POST /api/admin/users/${id}/reset-password`]: [200, { user: SESSION.user, temporaryPassword: 'y' }],
    })
    const auth = createHttpAuthApi({ fetch: asFetch(fetch) })
    await auth.listAccounts()
    await auth.createAccount({ email: 'n@example.test', displayName: 'N', roles: ['clinician'] })
    await auth.updateAccount(id, { isActive: false })
    await auth.resetPassword(id)
    expect(fetch.mock.calls.map(([url, init]) => `${init?.method} ${String(url)}`)).toEqual([
      'GET /api/admin/users',
      'POST /api/admin/users',
      `PATCH /api/admin/users/${id}`,
      `POST /api/admin/users/${id}/reset-password`,
    ])
  })

  it('tells the app when an admin call finds the session has ended', async () => {
    const fetch = backend({
      'GET /api/admin/users': [401, { error: { code: 'SESSION_EXPIRED', message: 'Ended.', retryable: false } }],
    })
    const onSessionEnded = vi.fn()
    await expect(createHttpAuthApi({ fetch: asFetch(fetch), onSessionEnded }).listAccounts()).rejects.toBeTruthy()
    expect(onSessionEnded).toHaveBeenCalledWith('SESSION_EXPIRED')
  })
})

describe('createMockAuthApi', () => {
  it('starts signed out and signs in with any credentials', async () => {
    const auth = createMockAuthApi()
    expect(await auth.me()).toBeNull()
    const session = await auth.login('someone@example.test', 'anything')
    expect(session.activeRole).toBe('clinician')
    expect(await auth.me()).not.toBeNull()
  })

  it('gives an "admin…" email both roles, so the role choice can be tried', async () => {
    const auth = createMockAuthApi()
    const session = await auth.login('admin@example.test', 'anything')
    expect(session.user.roles).toEqual(['admin', 'clinician'])
    expect(session.activeRole).toBeNull()
    expect((await auth.selectRole('admin')).activeRole).toBe('admin')
  })

  it('creates accounts with a random temporary password and protects the last administrator', async () => {
    const auth = createMockAuthApi()
    await auth.login('admin@example.test', 'anything')
    const me = await auth.selectRole('admin')
    const first = await auth.createAccount({ email: 'a@example.test', displayName: 'A', roles: ['clinician'] })
    const second = await auth.createAccount({ email: 'b@example.test', displayName: 'B', roles: ['clinician'] })
    expect(first.temporaryPassword).toHaveLength(16)
    expect(first.temporaryPassword).not.toBe(second.temporaryPassword)
    expect(first.user.mustChangePassword).toBe(true)
    await expect(auth.updateAccount(me.user.id, { roles: ['clinician'] })).rejects.toMatchObject({ code: 'LAST_ADMIN' })
    await expect(auth.updateAccount(me.user.id, { isActive: false })).rejects.toMatchObject({
      code: 'CANNOT_DISABLE_SELF',
    })
    await expect(
      auth.createAccount({ email: 'A@example.test', displayName: 'Again', roles: ['clinician'] }),
    ).rejects.toMatchObject({ code: 'EMAIL_TAKEN' })
  })

  it('refuses account management to a clinician session', async () => {
    const auth = createMockAuthApi()
    await auth.login('doctor@example.test', 'anything')
    await expect(auth.listAccounts()).rejects.toMatchObject({ code: 'FORBIDDEN' })
  })
})
