import { useId, useState, type FormEvent } from 'react'
import type { AuthApi, Session } from '../api/auth'
import { toApiError } from '../api/client'
import { Button } from '../components/Button'
import { Alert, inputClass, labelClass } from '../components/ui'
import { AuthLayout } from './AuthLayout'

export type SignedOutNotice = 'expired' | 'disabled' | 'signed-out'

const NOTICES: Record<SignedOutNotice, { tone: 'info' | 'warning'; text: string }> = {
  expired: {
    tone: 'warning',
    text: 'Your session ended, so the encounter that was open has been discarded. Sign in again to continue.',
  },
  disabled: { tone: 'warning', text: 'Your account has been disabled. Contact your administrator.' },
  'signed-out': { tone: 'info', text: 'You have signed out. Nothing from the encounter was kept.' },
}

interface LoginScreenProps {
  auth: AuthApi
  notice?: SignedOutNotice
  /** The fully simulated demo: any email and password work. */
  demo: boolean
  onSignedIn: (session: Session) => void
}

export function LoginScreen({ auth, notice, demo, onSignedIn }: LoginScreenProps) {
  const [email, setEmail] = useState('')
  const [password, setPassword] = useState('')
  const [error, setError] = useState<string | null>(null)
  const [busy, setBusy] = useState(false)
  const id = useId()

  const handleSubmit = async (event: FormEvent) => {
    event.preventDefault()
    if (!email.trim() || !password) {
      setError('Enter your email and password.')
      return
    }
    setBusy(true)
    setError(null)
    try {
      const session = await auth.login(email, password)
      setPassword('')
      onSignedIn(session)
    } catch (caught) {
      setPassword('')
      setError(toApiError(caught, { code: 'UNKNOWN', message: 'Sign-in failed. Try again.', retryable: true }).message)
      setBusy(false)
    }
  }

  return (
    <AuthLayout title="Sign in" description="Use the account your administrator created for you.">
      <div className="space-y-6">
        {notice && !error && <Alert tone={NOTICES[notice].tone}>{NOTICES[notice].text}</Alert>}
        {error && <Alert tone="error">{error}</Alert>}
        {demo && (
          <Alert tone="info" title="Demo mode">
            No backend is connected. Any email and password sign in. Use an email that starts with “admin” to see
            the administrator view.
          </Alert>
        )}
        <form noValidate onSubmit={handleSubmit} aria-label="Sign in" className="space-y-5">
          <div>
            <label htmlFor={`${id}-email`} className={labelClass}>
              Email
            </label>
            <input
              id={`${id}-email`}
              type="email"
              autoComplete="username"
              autoCapitalize="none"
              spellCheck={false}
              value={email}
              onChange={(e) => setEmail(e.target.value)}
              className={`mt-2 ${inputClass}`}
            />
          </div>
          <div>
            <label htmlFor={`${id}-password`} className={labelClass}>
              Password
            </label>
            <input
              id={`${id}-password`}
              type="password"
              autoComplete="current-password"
              value={password}
              onChange={(e) => setPassword(e.target.value)}
              className={`mt-2 ${inputClass}`}
            />
          </div>
          <Button type="submit" size="lg" loading={busy} className="w-full">
            Sign in
          </Button>
        </form>
        <p className="text-sm text-ink-3">Forgot your password? Ask your administrator to reset it.</p>
      </div>
    </AuthLayout>
  )
}
