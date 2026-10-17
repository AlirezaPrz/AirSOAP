import { useState, type FormEvent } from 'react'
import type { AuthApi, Session } from '../api/auth'
import { toApiError } from '../api/client'
import { Button } from '../components/Button'
import { CheckIcon } from '../components/icons'
import { Alert } from '../components/ui'
import { AuthLayout } from './AuthLayout'
import { PasswordField } from './PasswordField'
import { MAX_PASSWORD_LENGTH, MIN_PASSWORD_LENGTH, passwordProblem } from './passwordPolicy'

interface ChangePasswordScreenProps {
  auth: AuthApi
  session: Session
  onChanged: (session: Session) => void
  onSignOut: () => void
}

function Rule({ met, children }: { met: boolean; children: string }) {
  return (
    <li className={`flex items-center gap-2 transition-colors ${met ? 'text-accent-ink' : 'text-ink-3'}`}>
      <span
        aria-hidden
        className={`flex size-4 shrink-0 items-center justify-center rounded-full transition-colors ${
          met ? 'bg-accent text-white' : 'bg-rail'
        }`}
      >
        {met && <CheckIcon className="size-3" strokeWidth={3} />}
      </span>
      {children}
      <span className="sr-only">{met ? '(done)' : '(not yet)'}</span>
    </li>
  )
}

export function ChangePasswordScreen({ auth, session, onChanged, onSignOut }: ChangePasswordScreenProps) {
  const [current, setCurrent] = useState('')
  const [next, setNext] = useState('')
  const [confirm, setConfirm] = useState('')
  const [error, setError] = useState<string | null>(null)
  const [busy, setBusy] = useState(false)

  const email = session.user.email.toLowerCase()
  const lowered = next.trim().toLowerCase()
  const rules = {
    length: next.length >= MIN_PASSWORD_LENGTH && next.length <= MAX_PASSWORD_LENGTH,
    notEmail: next.length > 0 && lowered !== email && lowered !== email.split('@')[0],
    different: next.length > 0 && next !== current,
    matches: next.length > 0 && next === confirm,
  }

  const handleSubmit = async (event: FormEvent) => {
    event.preventDefault()
    const problem = !current
      ? 'Enter the temporary password you were given.'
      : (passwordProblem(next, session.user.email) ??
        (next === current
          ? 'Choose a password different from the temporary one.'
          : next !== confirm
            ? 'The new passwords do not match.'
            : null))
    if (problem) {
      setError(problem)
      return
    }
    setBusy(true)
    setError(null)
    try {
      onChanged(await auth.changePassword(current, next))
    } catch (caught) {
      setError(
        toApiError(caught, { code: 'UNKNOWN', message: 'The password was not changed. Try again.', retryable: true })
          .message,
      )
      setBusy(false)
    }
  }

  return (
    <AuthLayout
      title="Choose your own password"
      description="You signed in with a temporary password. Replace it with one only you know; you will use it from now on."
      footer={
        <p>
          Signed in as <span className="font-medium text-ink">{session.user.email}</span>.{' '}
          <button
            type="button"
            onClick={onSignOut}
            className="font-medium text-accent underline-offset-4 hover:underline"
          >
            Sign out
          </button>
        </p>
      }
    >
      <form noValidate onSubmit={handleSubmit} aria-label="Change password" className="space-y-5">
        {error && <Alert tone="error">{error}</Alert>}
        {/* Lets password managers attach the new password to the right account. */}
        <input type="email" autoComplete="username" value={session.user.email} readOnly hidden />
        <PasswordField
          label="Temporary password"
          value={current}
          onChange={setCurrent}
          autoComplete="current-password"
        />
        <PasswordField
          label="New password"
          value={next}
          onChange={setNext}
          autoComplete="new-password"
          hint="A short phrase of three or four unrelated words is long, and easy to remember."
        />
        <PasswordField label="Repeat the new password" value={confirm} onChange={setConfirm} autoComplete="new-password" />
        <ul aria-label="Password requirements" className="space-y-1.5 text-sm">
          <Rule met={rules.length}>{`At least ${MIN_PASSWORD_LENGTH} characters`}</Rule>
          <Rule met={rules.notEmail}>Not your email address</Rule>
          <Rule met={rules.different}>Different from the temporary password</Rule>
          <Rule met={rules.matches}>Both new passwords match</Rule>
        </ul>
        <Button type="submit" size="lg" loading={busy} className="w-full">
          Save password and continue
        </Button>
      </form>
    </AuthLayout>
  )
}
