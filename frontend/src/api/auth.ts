import { ApiRequestError } from './client'
import { createRequester, type RequesterOptions } from './request'

export type Role = 'admin' | 'clinician'

export const ROLE_LABELS: Record<Role, string> = { admin: 'Administrator', clinician: 'Clinician' }

export interface Account {
  id: string
  email: string
  displayName: string
  roles: Role[]
  isActive: boolean
  mustChangePassword: boolean
  createdAt: string
  lastLoginAt: string | null
}

/** The signed-in person and what this sign-in may do. Holds no secret: the session is an HttpOnly cookie. */
export interface Session {
  user: Account
  /** null until a dual-role account chooses, or while a temporary password must be changed. */
  activeRole: Role | null
  mustChangePassword: boolean
  sessionExpiresAt: string
}

export interface NewAccount {
  email: string
  displayName: string
  roles: Role[]
}

export interface AccountChanges {
  displayName?: string
  roles?: Role[]
  isActive?: boolean
}

/** A temporary password is returned once, shown once, and never kept by the app. */
export interface TemporaryPassword {
  user: Account
  temporaryPassword: string
}

export interface AuthApi {
  /** The current session, or null when signed out. */
  me(): Promise<Session | null>
  login(email: string, password: string): Promise<Session>
  logout(): Promise<void>
  selectRole(role: Role): Promise<Session>
  changePassword(currentPassword: string, newPassword: string): Promise<Session>
  listAccounts(): Promise<Account[]>
  createAccount(account: NewAccount): Promise<TemporaryPassword>
  updateAccount(id: string, changes: AccountChanges): Promise<Account>
  resetPassword(id: string): Promise<TemporaryPassword>
}

export function createHttpAuthApi(options: RequesterOptions = {}): AuthApi {
  const request = createRequester(options)
  return {
    async me() {
      try {
        return await request<Session>('GET', '/api/auth/me', undefined, { reportSessionEnd: false })
      } catch (error) {
        if (error instanceof ApiRequestError && ['AUTH_REQUIRED', 'SESSION_EXPIRED', 'ACCOUNT_DISABLED'].includes(error.code)) {
          return null
        }
        throw error
      }
    },
    login: (email, password) =>
      request<Session>('POST', '/api/auth/login', { email, password }, { reportSessionEnd: false }),
    logout: () => request<void>('POST', '/api/auth/logout', undefined, { reportSessionEnd: false }),
    selectRole: (role) => request<Session>('POST', '/api/auth/select-role', { role }),
    changePassword: (currentPassword, newPassword) =>
      request<Session>('POST', '/api/auth/password', { currentPassword, newPassword }),
    listAccounts: () => request<Account[]>('GET', '/api/admin/users'),
    createAccount: (account) => request<TemporaryPassword>('POST', '/api/admin/users', account),
    updateAccount: (id, changes) => request<Account>('PATCH', `/api/admin/users/${encodeURIComponent(id)}`, changes),
    resetPassword: (id) =>
      request<TemporaryPassword>('POST', `/api/admin/users/${encodeURIComponent(id)}/reset-password`),
  }
}

// --- Mock: the fully simulated demo, no backend ------------------------------------------------

const MIN_PASSWORD = 12
const SESSION_MINUTES = 30

const fail = (code: ApiRequestError['code'], message: string): never => {
  throw new ApiRequestError({ code, message, retryable: false })
}

function randomId(): string {
  return Array.from(crypto.getRandomValues(new Uint8Array(16)), (b) => b.toString(16).padStart(2, '0')).join('')
}

function temporaryPassword(): string {
  const alphabet = 'ABCDEFGHJKLMNPQRSTUVWXYZabcdefghijkmnopqrstuvwxyz23456789'
  return Array.from(crypto.getRandomValues(new Uint8Array(16)), (b) => alphabet[b % alphabet.length]).join('')
}

/**
 * In-memory stand-in for the backend's accounts. Any email and password sign in (there is no
 * password to know); an email that starts with "admin" gets both roles, so the role choice
 * and the account screens can be tried. Everything is forgotten on reload.
 */
export function createMockAuthApi({ startSignedIn = false }: { startSignedIn?: boolean } = {}): AuthApi {
  const accounts = new Map<string, Account>()
  let session: Session | null = null

  const now = () => new Date().toISOString()
  const expiry = () => new Date(Date.now() + SESSION_MINUTES * 60_000).toISOString()
  const toSession = (user: Account, activeRole: Role | null): Session => ({
    user,
    activeRole,
    mustChangePassword: user.mustChangePassword,
    sessionExpiresAt: expiry(),
  })
  const initialRole = (user: Account): Role | null =>
    user.mustChangePassword || user.roles.length !== 1 ? null : user.roles[0]
  const requireAdmin = (): Session => {
    if (!session) return fail('AUTH_REQUIRED', 'Please sign in.')
    if (session.activeRole !== 'admin') return fail('FORBIDDEN', 'Your current role does not allow this.')
    return session
  }
  const find = (id: string): Account => accounts.get(id) ?? fail('USER_NOT_FOUND', 'This account does not exist.')
  const activeAdmins = () => [...accounts.values()].filter((a) => a.isActive && a.roles.includes('admin'))

  function accountFor(email: string): Account {
    const normalized = email.trim().toLowerCase()
    const existing = [...accounts.values()].find((a) => a.email === normalized)
    if (existing) return existing
    const roles: Role[] = normalized.startsWith('admin') ? ['admin', 'clinician'] : ['clinician']
    const created: Account = {
      id: randomId(),
      email: normalized,
      displayName: normalized.startsWith('admin') ? 'Demo Administrator' : 'Demo Clinician',
      roles,
      isActive: true,
      mustChangePassword: false,
      createdAt: now(),
      lastLoginAt: null,
    }
    accounts.set(created.id, created)
    return created
  }

  if (startSignedIn) session = toSession(accountFor('clinician@demo.local'), 'clinician')

  const settle = <T>(value: T) => new Promise<T>((resolve) => setTimeout(() => resolve(value), 150))

  return {
    me: () => settle(session && { ...session, sessionExpiresAt: expiry() }),

    async login(email, password) {
      if (!email.trim() || !password) fail('INVALID_CREDENTIALS', 'Incorrect email or password.')
      const user = accountFor(email)
      if (!user.isActive) fail('INVALID_CREDENTIALS', 'Incorrect email or password.')
      const signedIn = { ...user, lastLoginAt: now() }
      accounts.set(user.id, signedIn)
      session = toSession(signedIn, initialRole(signedIn))
      return settle(session)
    },

    async logout() {
      session = null
      return settle(undefined)
    },

    async selectRole(role) {
      if (!session) return fail('SESSION_EXPIRED', 'Your session has ended. Please sign in again.')
      if (session.mustChangePassword) fail('PASSWORD_CHANGE_REQUIRED', 'Change your temporary password to continue.')
      if (session.activeRole) fail('ROLE_ALREADY_SELECTED', 'Sign out to switch roles.')
      if (!session.user.roles.includes(role)) fail('FORBIDDEN', 'Your account does not have this role.')
      session = toSession(session.user, role)
      return settle(session)
    },

    async changePassword(currentPassword, newPassword) {
      if (!session) return fail('SESSION_EXPIRED', 'Your session has ended. Please sign in again.')
      if (!currentPassword) fail('WRONG_PASSWORD', 'The current password is incorrect.')
      if (newPassword.length < MIN_PASSWORD) fail('WEAK_PASSWORD', `Use at least ${MIN_PASSWORD} characters.`)
      if (newPassword === currentPassword) fail('WEAK_PASSWORD', 'Choose a password different from the current one.')
      const user = { ...session.user, mustChangePassword: false }
      accounts.set(user.id, user)
      session = toSession(user, initialRole(user))
      return settle(session)
    },

    async listAccounts() {
      requireAdmin()
      return settle([...accounts.values()].sort((a, b) => a.displayName.localeCompare(b.displayName)))
    },

    async createAccount({ email, displayName, roles }) {
      requireAdmin()
      const normalized = email.trim().toLowerCase()
      if (!/^[^@\s]+@[^@\s]+\.[^@\s]+$/.test(normalized)) fail('INVALID_EMAIL', 'Enter a valid email address.')
      if (!displayName.trim()) fail('INVALID_DISPLAY_NAME', 'Enter a name between 1 and 100 characters.')
      if (roles.length === 0) fail('ROLE_REQUIRED', 'Choose at least one role.')
      if ([...accounts.values()].some((a) => a.email === normalized)) {
        fail('EMAIL_TAKEN', 'An account with this email already exists.')
      }
      const user: Account = {
        id: randomId(),
        email: normalized,
        displayName: displayName.trim(),
        roles: (['admin', 'clinician'] as const).filter((r) => roles.includes(r)),
        isActive: true,
        mustChangePassword: true,
        createdAt: now(),
        lastLoginAt: null,
      }
      accounts.set(user.id, user)
      return settle({ user, temporaryPassword: temporaryPassword() })
    },

    async updateAccount(id, changes) {
      const actor = requireAdmin()
      const user = find(id)
      if (changes.isActive === false && id === actor.user.id) {
        fail('CANNOT_DISABLE_SELF', 'You cannot disable your own account.')
      }
      const next: Account = {
        ...user,
        displayName: changes.displayName?.trim() || user.displayName,
        roles: changes.roles ? (['admin', 'clinician'] as const).filter((r) => changes.roles?.includes(r)) : user.roles,
        isActive: changes.isActive ?? user.isActive,
      }
      if (next.roles.length === 0) fail('ROLE_REQUIRED', 'Choose at least one role.')
      const admins = activeAdmins()
      const wasAdmin = admins.some((a) => a.id === id)
      if (wasAdmin && !(next.isActive && next.roles.includes('admin')) && admins.length <= 1) {
        fail('LAST_ADMIN', 'This is the last active administrator. Make another account an administrator first.')
      }
      accounts.set(id, next)
      // Like the backend: changing your own roles ends your session.
      if (id === actor.user.id && changes.roles && next.roles.join() !== user.roles.join()) session = null
      return settle(next)
    },

    async resetPassword(id) {
      requireAdmin()
      const user = { ...find(id), mustChangePassword: true }
      accounts.set(id, user)
      return settle({ user, temporaryPassword: temporaryPassword() })
    },
  }
}
