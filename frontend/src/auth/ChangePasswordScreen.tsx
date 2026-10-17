import { useId, useState, type FormEvent } from 'react'
import type { AuthApi, Session } from '../api/auth'
import { toApiError } from '../api/client'
import { Button } from '../components/Button'
import { Alert, inputClass, labelClass } from '../components/ui'
import { AuthLayout } from './AuthLayout'
import { MIN_PASSWORD_LENGTH, passwordProblem } from './passwordPolicy'

interface ChangePasswordScreenProps {
  auth: AuthApi
  session: Session
  onChanged: (session: Session) => void
  onSignOut: () => void
}

export function ChangePasswordScreen({ auth, session, onChanged, onSignOut }: ChangePasswordScreenProps) {
  const [current, setCurrent] = useState('')
  const [next, setNext] = useState('')
  const [confirm, setConfirm] = useState('')
  const [error, setError] = useState<string | null>(null)
  const [busy, setBusy] = useState(false)
  const id = useId()

  const handleSubmit = async (event: FormEvent) => {
    event.preventDefault()
    const problem = !current
      ? 'Enter the temporary password you were given.'
      : passwordProblem(next, session.user.email) ?? (next !== confirm ? 'The new passwords do not match.' : null)
    if (problem) {
      setError(problem)
      return
    }
    setBusy(true)
    setError(null)
    try {
      onChanged(await auth.changePassword(current, next))
    } catch (caught) {
      setError(toApiError(caught, { code: 'UNKNOWN', message: 'The password was not changed. Try again.', retryable: true }).message)
      setBusy(false)
    }
  }

  return (
    <AuthLayout
      title="Choose a new password"
      description={
        <>
          You signed in with a temporary password. Replace it with one only you know before you continue.
        </>
      }
      footer={
        <p>
          Signed in as <span className="font-medium text-ink">{session.user.email}</span>.{' '}
          <button type="button" onClick={onSignOut} className="font-medium text-accent underline-offset-4 hover:underline">
            Sign out
          </button>
        </p>
      }
    >
      <form noValidate onSubmit={handleSubmit} aria-label="Change password" className="space-y-5">
        {error && <Alert tone="error">{error}</Alert>}
        {/* Lets password managers attach the new password to the right account. */}
        <input type="email" autoComplete="username" value={session.user.email} readOnly hidden />
        <div>
          <label htmlFor={`${id}-current`} className={labelClass}>
            Temporary password
          </label>
          <input
            id={`${id}-current`}
            type="password"
            autoComplete="current-password"
            value={current}
            onChange={(e) => setCurrent(e.target.value)}
            className={`mt-2 ${inputClass}`}
          />
        </div>
        <div>
          <label htmlFor={`${id}-new`} className={labelClass}>
            New password
          </label>
          <input
            id={`${id}-new`}
            type="password"
            autoComplete="new-password"
            aria-describedby={`${id}-rules`}
            value={next}
            onChange={(e) => setNext(e.target.value)}
            className={`mt-2 ${inputClass}`}
          />
          <p id={`${id}-rules`} className="mt-2 text-sm text-ink-3">
            At least {MIN_PASSWORD_LENGTH} characters. A short phrase of unrelated words works well.
          </p>
        </div>
        <div>
          <label htmlFor={`${id}-confirm`} className={labelClass}>
            Repeat the new password
          </label>
          <input
            id={`${id}-confirm`}
            type="password"
            autoComplete="new-password"
            value={confirm}
            onChange={(e) => setConfirm(e.target.value)}
            className={`mt-2 ${inputClass}`}
          />
        </div>
        <Button type="submit" size="lg" loading={busy} className="w-full">
          Save password and continue
        </Button>
      </form>
    </AuthLayout>
  )
}
