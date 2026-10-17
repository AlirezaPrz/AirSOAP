import { useState } from 'react'
import type { AuthApi, Role, Session } from '../api/auth'
import { toApiError } from '../api/client'
import { Spinner } from '../components/icons'
import { Alert } from '../components/ui'
import { AuthLayout } from './AuthLayout'

const CHOICES: { role: Role; title: string; description: string }[] = [
  {
    role: 'clinician',
    title: 'Clinical workspace',
    description: 'Record an encounter, review the transcript and draft the SOAP note.',
  },
  {
    role: 'admin',
    title: 'Administration',
    description: 'Create accounts, assign roles, disable access and reset passwords.',
  },
]

interface RoleChooserScreenProps {
  auth: AuthApi
  session: Session
  onChosen: (session: Session) => void
  onSignOut: () => void
}

/** For accounts with both roles. Each sign-in uses one role; switching means signing in again. */
export function RoleChooserScreen({ auth, session, onChosen, onSignOut }: RoleChooserScreenProps) {
  const [pending, setPending] = useState<Role | null>(null)
  const [error, setError] = useState<string | null>(null)

  const choose = async (role: Role) => {
    setPending(role)
    setError(null)
    try {
      onChosen(await auth.selectRole(role))
    } catch (caught) {
      setError(toApiError(caught, { code: 'UNKNOWN', message: 'That did not work. Try again.', retryable: true }).message)
      setPending(null)
    }
  }

  const choices = CHOICES.filter((choice) => session.user.roles.includes(choice.role))

  return (
    <AuthLayout
      title={`Welcome, ${session.user.displayName}`}
      description="How do you want to work in this session? To switch later, sign out and sign in again."
      footer={
        <p>
          Not you?{' '}
          <button type="button" onClick={onSignOut} className="font-medium text-accent underline-offset-4 hover:underline">
            Sign out
          </button>
        </p>
      }
    >
      {error && (
        <div className="mb-5">
          <Alert tone="error">{error}</Alert>
        </div>
      )}
      <ul className="space-y-3">
        {choices.map(({ role, title, description }) => (
          <li key={role}>
            <button
              type="button"
              onClick={() => choose(role)}
              disabled={pending !== null}
              className="group flex w-full items-center justify-between gap-4 rounded-(--radius-panel) bg-surface px-5 py-4 text-left transition-colors hover:bg-accent-soft focus-visible:outline-2 focus-visible:outline-offset-2 focus-visible:outline-accent disabled:cursor-wait disabled:opacity-60"
            >
              <span>
                <span className="block text-[17px] font-semibold text-ink group-hover:text-accent-ink">{title}</span>
                <span className="mt-0.5 block text-[15px] text-ink-2">{description}</span>
              </span>
              {pending === role ? (
                <Spinner className="size-5 shrink-0 text-accent" />
              ) : (
                <span aria-hidden className="text-xl text-ink-3 group-hover:text-accent-ink">
                  →
                </span>
              )}
            </button>
          </li>
        ))}
      </ul>
    </AuthLayout>
  )
}
