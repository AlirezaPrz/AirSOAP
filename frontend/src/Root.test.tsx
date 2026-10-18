import { render, screen, waitFor, within } from '@testing-library/react'
import userEvent from '@testing-library/user-event'
import { afterEach, describe, expect, it, vi } from 'vitest'
import { Root } from './Root'
import { createDefaultServices, type Services } from './api'
import { createMockAuthApi, type AuthApi, type Session } from './api/auth'
import { createMockApi } from './api/mockClient'
import { fillTestPatient } from './test/patient'

function mockServices(auth: AuthApi = createMockAuthApi()): Services {
  return { api: createMockApi({ latencyScale: 0 }), auth, onSessionEnded: () => () => {} }
}

async function signIn(email: string, password = 'synthetic-password-1') {
  const user = userEvent.setup()
  await screen.findByRole('heading', { level: 1, name: 'Sign in to AirSOAP' })
  await user.type(screen.getByLabelText('Email'), email)
  await user.type(screen.getByLabelText('Password'), password)
  await user.click(screen.getByRole('button', { name: 'Sign in' }))
  return user
}

afterEach(() => {
  vi.unstubAllGlobals()
})

describe('signing in (demo backend)', () => {
  it('shows the sign-in screen first, with no way to register', async () => {
    render(<Root services={mockServices()} />)
    await screen.findByRole('heading', { level: 1, name: 'Sign in to AirSOAP' })
    expect(screen.queryByRole('link', { name: /sign up|register|create account/i })).not.toBeInTheDocument()
    expect(screen.queryByRole('button', { name: /sign up|register/i })).not.toBeInTheDocument()
    expect(screen.getByLabelText('Password')).toHaveAttribute('type', 'password')
    expect(screen.getByLabelText('Password')).toHaveAttribute('autocomplete', 'current-password')
  })

  it('takes a single-role clinician straight to the workspace, with their name and a way out', async () => {
    render(<Root services={mockServices()} />)
    const user = await signIn('doctor@example.test')
    await screen.findByRole('heading', { level: 1, name: 'New encounter' })
    const account = screen.getByRole('region', { name: 'Signed in' })
    expect(within(account).getByText('Demo Clinician')).toBeInTheDocument()
    expect(within(account).getByText('Clinician')).toBeInTheDocument()

    await user.click(within(account).getByRole('button', { name: 'Sign out' }))
    await screen.findByRole('heading', { level: 1, name: 'Sign in to AirSOAP' })
    expect(screen.getByText(/you have signed out/i)).toBeInTheDocument()
  })

  it('asks a dual-role account which role to use, then shows the account screen for admin', async () => {
    render(<Root services={mockServices()} />)
    const user = await signIn('admin@example.test')
    await screen.findByRole('heading', { level: 1, name: /welcome/i })
    await user.click(screen.getByRole('button', { name: /administration/i }))
    await screen.findByRole('heading', { level: 1, name: 'Accounts' })
    expect(await screen.findByText('admin@example.test')).toBeInTheDocument()
    expect(screen.queryByRole('heading', { name: 'New encounter' })).not.toBeInTheDocument()
  })

  it('lets an administrator create an account and shows the temporary password once', async () => {
    render(<Root services={mockServices()} />)
    const user = await signIn('admin@example.test')
    await user.click(await screen.findByRole('button', { name: /administration/i }))
    await user.click(await screen.findByRole('button', { name: 'New account' }))

    const dialog = screen.getByRole('dialog', { name: 'New account' })
    await user.type(within(dialog).getByLabelText('Email'), 'new.doctor@example.test')
    await user.type(within(dialog).getByLabelText('Name'), 'Dr New')
    expect(within(dialog).getByRole('checkbox', { name: /clinician/i })).toBeChecked()
    await user.click(within(dialog).getByRole('button', { name: 'Create account' }))

    const shown = await screen.findByLabelText('Temporary password')
    const temporary = shown.textContent ?? ''
    expect(temporary).toHaveLength(16)
    expect(screen.getByText(/only time it is shown/i)).toBeInTheDocument()
    await user.click(screen.getByRole('button', { name: 'Done' }))

    expect(screen.queryByText(temporary)).not.toBeInTheDocument()
    const row = (await screen.findByText('new.doctor@example.test')).closest('li') as HTMLElement
    expect(within(row).getByText('Temporary password')).toBeInTheDocument()
  })

  it('refuses to remove the last administrator, with the reason', async () => {
    render(<Root services={mockServices()} />)
    const user = await signIn('admin@example.test')
    await user.click(await screen.findByRole('button', { name: /administration/i }))
    await user.click(await screen.findByRole('button', { name: 'Edit Demo Administrator' }))
    const dialog = screen.getByRole('dialog')
    expect(within(dialog).getByRole('checkbox', { name: /can sign in/i })).toBeDisabled()
    await user.click(within(dialog).getByRole('checkbox', { name: /administrator/i }))
    expect(within(dialog).getByText(/changing your own roles signs you out/i)).toBeInTheDocument()
    await user.click(within(dialog).getByRole('button', { name: 'Save changes' }))
    expect(await within(dialog).findByText(/last active administrator/i)).toBeInTheDocument()
  })

  it('makes a new account replace its temporary password before anything else', async () => {
    const auth = createMockAuthApi()
    const changed = vi.spyOn(auth, 'changePassword')
    render(<Root services={mockServices(auth)} />)
    let user = await signIn('admin@example.test')
    await user.click(await screen.findByRole('button', { name: /administration/i }))
    await user.click(await screen.findByRole('button', { name: 'New account' }))
    const dialog = screen.getByRole('dialog', { name: 'New account' })
    await user.type(within(dialog).getByLabelText('Email'), 'new.doctor@example.test')
    await user.type(within(dialog).getByLabelText('Name'), 'Dr New')
    await user.click(within(dialog).getByRole('button', { name: 'Create account' }))
    const temporary = (await screen.findByLabelText('Temporary password')).textContent ?? ''
    await user.click(screen.getByRole('button', { name: 'Done' }))
    await user.click(within(screen.getByRole('region', { name: 'Signed in' })).getByRole('button', { name: 'Sign out' }))

    user = await signIn('new.doctor@example.test', temporary)
    await screen.findByRole('heading', { level: 1, name: 'Choose your own password' })
    await user.type(screen.getByLabelText('Temporary password'), temporary)
    await user.type(screen.getByLabelText('New password'), 'too-short')
    await user.type(screen.getByLabelText('Repeat the new password'), 'too-short')
    const rules = screen.getByRole('list', { name: 'Password requirements' })
    expect(within(rules).getByText('At least 12 characters').parentElement).toHaveTextContent('(not yet)')
    await user.click(screen.getByRole('button', { name: /save password/i }))
    expect(await screen.findByText('Use at least 12 characters.')).toBeInTheDocument()
    expect(changed).not.toHaveBeenCalled()

    await user.clear(screen.getByLabelText('New password'))
    await user.clear(screen.getByLabelText('Repeat the new password'))
    await user.type(screen.getByLabelText('New password'), 'a-long-new-password')
    await user.type(screen.getByLabelText('Repeat the new password'), 'a-different-password')
    await user.click(screen.getByRole('button', { name: /save password/i }))
    expect(await screen.findByText(/do not match/i)).toBeInTheDocument()

    await user.clear(screen.getByLabelText('Repeat the new password'))
    await user.type(screen.getByLabelText('Repeat the new password'), 'a-long-new-password')
    await user.click(screen.getByRole('button', { name: /save password/i }))
    await screen.findByRole('heading', { level: 1, name: 'New encounter' })
    expect(changed).toHaveBeenCalledWith(temporary, 'a-long-new-password')
  })

  it('asks before signing out of an encounter in progress', async () => {
    render(<Root services={mockServices()} />)
    const user = await signIn('doctor@example.test')
    await fillTestPatient(user)
    await user.click(screen.getByRole('button', { name: 'Start encounter' }))
    await screen.findByRole('heading', { level: 1, name: 'Ready to record' })

    const account = screen.getByRole('region', { name: 'Signed in' })
    await user.click(within(account).getByRole('button', { name: 'Sign out' }))
    const dialog = screen.getByRole('dialog', { name: /discard this encounter/i })
    await user.click(within(dialog).getByRole('button', { name: 'Keep working' }))
    expect(screen.getByRole('heading', { level: 1, name: 'Ready to record' })).toBeInTheDocument()

    await user.click(within(account).getByRole('button', { name: 'Sign out' }))
    await user.click(screen.getByRole('button', { name: 'Sign out and discard' }))
    await screen.findByRole('heading', { level: 1, name: 'Sign in to AirSOAP' })
    expect(screen.queryByText(/Testpatient/)).not.toBeInTheDocument()
  })

  it('a new sign-in starts from a clean workspace', async () => {
    render(<Root services={mockServices()} />)
    let user = await signIn('doctor@example.test')
    await fillTestPatient(user)
    await user.click(screen.getByRole('button', { name: 'Start encounter' }))
    await screen.findByRole('heading', { level: 1, name: 'Ready to record' })
    await user.click(within(screen.getByRole('region', { name: 'Signed in' })).getByRole('button', { name: 'Sign out' }))
    await user.click(screen.getByRole('button', { name: 'Sign out and discard' }))

    user = await signIn('doctor@example.test')
    await screen.findByRole('heading', { level: 1, name: 'New encounter' })
    expect(screen.getByLabelText(/full name/i)).toHaveValue('')
  })
})

describe('interactive helpers', () => {
  it('reveals and hides the password on request', async () => {
    const user = userEvent.setup()
    render(<Root services={mockServices()} />)
    const field = await screen.findByLabelText('Password')
    expect(field).toHaveAttribute('type', 'password')
    await user.click(screen.getByRole('button', { name: 'Show password' }))
    expect(field).toHaveAttribute('type', 'text')
    await user.click(screen.getByRole('button', { name: 'Hide password' }))
    expect(field).toHaveAttribute('type', 'password')
  })

  it('filters and searches the account list', async () => {
    render(<Root services={mockServices()} />)
    const user = await signIn('admin@example.test')
    await user.click(await screen.findByRole('button', { name: /administration/i }))
    for (const [email, name] of [
      ['a.one@example.test', 'Dr One'],
      ['b.two@example.test', 'Dr Two'],
    ]) {
      await user.click(await screen.findByRole('button', { name: 'New account' }))
      const dialog = screen.getByRole('dialog', { name: 'New account' })
      await user.type(within(dialog).getByLabelText('Email'), email)
      await user.type(within(dialog).getByLabelText('Name'), name)
      await user.click(within(dialog).getByRole('button', { name: 'Create account' }))
      await user.click(await screen.findByRole('button', { name: 'Done' }))
    }
    const list = () => screen.getByRole('list', { name: 'Accounts' })
    expect(within(list()).getAllByRole('listitem')).toHaveLength(3)

    await user.click(screen.getByRole('button', { name: /temporary password/i }))
    expect(within(list()).getAllByRole('listitem')).toHaveLength(2)

    await user.type(screen.getByLabelText('Search accounts'), 'two')
    expect(within(list()).getAllByRole('listitem')).toHaveLength(1)
    expect(within(list()).getByText('Dr Two')).toBeInTheDocument()

    await user.clear(screen.getByLabelText('Search accounts'))
    await user.type(screen.getByLabelText('Search accounts'), 'nobody')
    await user.click(screen.getByRole('button', { name: 'Show all accounts' }))
    expect(within(list()).getAllByRole('listitem')).toHaveLength(3)
  })
})

// --- against the HTTP client, with a scripted backend ----------------------------------------

const CLINICIAN_SESSION: Session = {
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
  sessionExpiresAt: new Date(Date.now() + 30 * 60_000).toISOString(),
}

function json(status: number, payload: unknown) {
  return new Response(status === 204 ? null : JSON.stringify(payload), {
    status,
    headers: { 'Content-Type': 'application/json' },
  })
}

function stubBackend(handle: (route: string) => Response | undefined) {
  const fetch = vi.fn(async (input: RequestInfo | URL, init?: RequestInit) => {
    const route = `${init?.method ?? 'GET'} ${String(input)}`
    return handle(route) ?? json(404, { detail: 'Not Found' })
  })
  vi.stubGlobal('fetch', fetch)
  return fetch
}

describe('the session against the backend', () => {
  it('returns to sign-in and discards the encounter when the session has expired', async () => {
    stubBackend((route) => {
      if (route === 'GET /api/auth/me') return json(200, CLINICIAN_SESSION)
      if (route === 'POST /api/recording/start') {
        return json(401, { error: { code: 'SESSION_EXPIRED', message: 'Ended.', retryable: false } })
      }
      if (route === 'POST /api/auth/logout') return json(204, null)
    })
    const user = userEvent.setup()
    render(<Root services={createDefaultServices({ VITE_AIRSOAP_API: 'http' })} />)
    await fillTestPatient(user)
    await user.click(screen.getByRole('button', { name: 'Start encounter' }))
    await screen.findByRole('heading', { level: 1, name: 'Ready to record' })
    await user.click(screen.getByRole('checkbox', { name: /consented/i }))
    await user.click(screen.getByRole('button', { name: /start recording/i }))

    await screen.findByRole('heading', { level: 1, name: 'Sign in to AirSOAP' })
    expect(screen.getByText(/encounter that was open has been discarded/i)).toBeInTheDocument()
    expect(screen.queryByText(/Testpatient/)).not.toBeInTheDocument()
  })

  it('tells a disabled account why it was signed out', async () => {
    stubBackend((route) => {
      if (route === 'GET /api/auth/me') return json(200, CLINICIAN_SESSION)
      if (route === 'POST /api/recording/start') {
        return json(401, { error: { code: 'ACCOUNT_DISABLED', message: 'Disabled.', retryable: false } })
      }
    })
    const user = userEvent.setup()
    render(<Root services={createDefaultServices({ VITE_AIRSOAP_API: 'http' })} />)
    await fillTestPatient(user)
    await user.click(screen.getByRole('button', { name: 'Start encounter' }))
    await user.click(await screen.findByRole('checkbox', { name: /consented/i }))
    await user.click(screen.getByRole('button', { name: /start recording/i }))
    expect(await screen.findByText(/your account has been disabled/i)).toBeInTheDocument()
  })

  it('shows the generic error for a wrong password and clears the password field', async () => {
    stubBackend((route) => {
      if (route === 'GET /api/auth/me') {
        return json(401, { error: { code: 'AUTH_REQUIRED', message: 'Please sign in.', retryable: false } })
      }
      if (route === 'POST /api/auth/login') {
        return json(401, { error: { code: 'INVALID_CREDENTIALS', message: 'Incorrect email or password.', retryable: false } })
      }
    })
    render(<Root services={createDefaultServices({ VITE_AIRSOAP_API: 'http' })} />)
    await signIn('dr.test@example.test', 'not-the-password')
    expect(await screen.findByText('Incorrect email or password.')).toBeInTheDocument()
    expect(screen.getByLabelText('Password')).toHaveValue('')
    expect(screen.queryByText(/demo mode/i)).not.toBeInTheDocument()
  })

  it('offers a retry when the backend cannot be reached', async () => {
    let up = false
    stubBackend((route) => {
      if (route !== 'GET /api/auth/me') return undefined
      if (!up) throw new TypeError('Failed to fetch')
      return json(401, { error: { code: 'AUTH_REQUIRED', message: 'Please sign in.', retryable: false } })
    })
    const user = userEvent.setup()
    render(<Root services={createDefaultServices({ VITE_AIRSOAP_API: 'http' })} />)
    await screen.findByText(/cannot reach airsoap/i)
    up = true
    await user.click(screen.getByRole('button', { name: 'Try again' }))
    await screen.findByRole('heading', { level: 1, name: 'Sign in to AirSOAP' })
  })

  it('signs out on the server and keeps no credential in the page', async () => {
    const fetch = stubBackend((route) => {
      if (route === 'GET /api/auth/me') return json(200, CLINICIAN_SESSION)
      if (route === 'POST /api/auth/logout') return json(204, null)
    })
    const user = userEvent.setup()
    render(<Root services={createDefaultServices({ VITE_AIRSOAP_API: 'http' })} />)
    await screen.findByRole('heading', { level: 1, name: 'New encounter' })
    await user.click(within(screen.getByRole('region', { name: 'Signed in' })).getByRole('button', { name: 'Sign out' }))
    await screen.findByRole('heading', { level: 1, name: 'Sign in to AirSOAP' })
    await waitFor(() =>
      expect(fetch.mock.calls.map(([url, init]) => `${init?.method ?? 'GET'} ${String(url)}`)).toContain(
        'POST /api/auth/logout',
      ),
    )
    expect(document.cookie).toBe('')
  })
})
