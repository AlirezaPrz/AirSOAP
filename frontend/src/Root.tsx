import { useCallback, useEffect, useState } from 'react'
import App from './App'
import { AdminArea } from './admin/AdminShell'
import { createDefaultServices, type Services } from './api'
import type { Session } from './api/auth'
import { toApiError } from './api/client'
import { isMockApi } from './api/mockClient'
import { ChangePasswordScreen } from './auth/ChangePasswordScreen'
import { LoginScreen, type SignedOutNotice } from './auth/LoginScreen'
import { RoleChooserScreen } from './auth/RoleChooserScreen'
import { useSessionKeepAlive } from './auth/useSessionKeepAlive'
import { Button } from './components/Button'
import { Spinner } from './components/icons'
import type { AccountInfo } from './components/AccountSummary'
import { Alert } from './components/ui'

type AuthState =
  | { status: 'checking' }
  | { status: 'unreachable'; message: string }
  | { status: 'signed-out'; notice?: SignedOutNotice }
  | { status: 'signed-in'; session: Session }

interface RootProps {
  services?: Services
}

/**
 * Decides what to show from the server's view of the session: sign-in, temporary-password
 * change, role choice, the clinical workspace or the account screens. Nothing about the
 * session is stored in the browser; the HttpOnly cookie is the only credential.
 */
export function Root({ services: provided }: RootProps) {
  const [services] = useState(() => provided ?? createDefaultServices())
  const { auth, api } = services
  const [state, setState] = useState<AuthState>({ status: 'checking' })
  // Changes on every sign-in, so a new session never inherits the previous session's screen state.
  const [generation, setGeneration] = useState(0)

  const [attempt, setAttempt] = useState(0)

  useEffect(() => {
    let current = true
    auth.me().then(
      (session) => {
        if (current) setState(session ? { status: 'signed-in', session } : { status: 'signed-out' })
      },
      (caught: unknown) => {
        if (!current) return
        const { message } = toApiError(caught, {
          code: 'UNAVAILABLE',
          message: 'The AirSOAP backend is not reachable.',
          retryable: true,
        })
        setState({ status: 'unreachable', message })
      },
    )
    return () => {
      current = false
    }
  }, [auth, attempt])

  const retry = () => {
    setState({ status: 'checking' })
    setAttempt((n) => n + 1)
  }

  /** The server says the session is over. Dropping to the sign-in screen unmounts the encounter, discarding it. */
  const sessionEnded = useCallback((notice: SignedOutNotice) => {
    setState((current) => (current.status === 'signed-in' ? { status: 'signed-out', notice } : current))
  }, [])

  useEffect(
    () => services.onSessionEnded((code) => sessionEnded(code === 'ACCOUNT_DISABLED' ? 'disabled' : 'expired')),
    [services, sessionEnded],
  )

  const session = state.status === 'signed-in' ? state.session : null
  useSessionKeepAlive(
    auth,
    session,
    (refreshed) => setState((current) => (current.status === 'signed-in' ? { status: 'signed-in', session: refreshed } : current)),
    () => sessionEnded('expired'),
  )

  const signedIn = (next: Session) => {
    setGeneration((n) => n + 1)
    setState({ status: 'signed-in', session: next })
  }

  const signOut = () => {
    setState({ status: 'signed-out', notice: 'signed-out' })
    // The server also discards this account's recording and jobs.
    auth.logout().catch(() => {
      // Already signed out, or unreachable: the screen is cleared either way.
    })
  }

  if (state.status === 'checking') {
    return (
      <div className="flex min-h-dvh items-center justify-center bg-ground text-ink-2">
        <p className="flex items-center gap-2 text-[15px]" role="status">
          <Spinner className="size-4" /> Checking your session…
        </p>
      </div>
    )
  }

  if (state.status === 'unreachable') {
    return (
      <div className="flex min-h-dvh items-center justify-center bg-ground px-5">
        <div className="w-full max-w-lg">
          <Alert
            tone="error"
            title="Cannot reach AirSOAP"
            action={
              <Button variant="secondary" onClick={retry}>
                Try again
              </Button>
            }
          >
            {state.message}
          </Alert>
        </div>
      </div>
    )
  }

  if (state.status === 'signed-out' || !session) {
    return (
      <LoginScreen
        auth={auth}
        notice={state.status === 'signed-out' ? state.notice : undefined}
        demo={isMockApi(api)}
        onSignedIn={signedIn}
      />
    )
  }

  if (session.mustChangePassword) {
    return <ChangePasswordScreen auth={auth} session={session} onChanged={signedIn} onSignOut={signOut} />
  }

  if (session.activeRole === null) {
    return <RoleChooserScreen auth={auth} session={session} onChosen={signedIn} onSignOut={signOut} />
  }

  const account: AccountInfo = {
    name: session.user.displayName,
    email: session.user.email,
    role: session.activeRole,
    onSignOut: signOut,
  }

  if (session.activeRole === 'admin') {
    return (
      <AdminArea key={generation} auth={auth} account={account} currentUserId={session.user.id} />
    )
  }

  return <App key={generation} api={api} account={account} />
}
