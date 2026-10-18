import { useState } from 'react'
import type { AuthApi, Role, Session } from '../api/auth'
import { toApiError } from '../api/client'
import { MicIcon, ShieldIcon, Spinner } from '../components/icons'
import { ChevronDownIcon } from '../components/icons'
import { Alert } from '../components/ui'
import { AuthLayout } from './AuthLayout'

const CHOICES: { role: Role; title: string; description: string; Icon: typeof MicIcon }[] = [
  {
    role: 'clinician',
    Icon: MicIcon,
    title: 'Clinical workspace',
    description: 'Record an encounter, review the transcript and draft the SOAP note.',
  },
  {
    role: 'admin',
    Icon: ShieldIcon,
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

const ChevronRight = (props: { className?: string }) => <ChevronDownIcon {...props} style={{ rotate: '-90deg' }} />

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
      description="Your account has two roles. Choose one for this session; to switch later, sign out and sign in again."
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
        {choices.map(({ role, title, description, Icon }) => (
          <li key={role}>
            <button
              type="button"
              onClick={() => choose(role)}
              disabled={pending !== null}
              className="group flex w-full items-center gap-4 rounded-(--radius-panel) bg-sunken px-5 py-4 text-left transition-colors hover:bg-accent-soft focus-visible:outline-2 focus-visible:outline-offset-2 focus-visible:outline-accent disabled:cursor-wait disabled:opacity-60"
            >
              <span
                aria-hidden
                className="flex size-11 shrink-0 items-center justify-center rounded-full bg-surface text-accent transition-colors group-hover:bg-accent group-hover:text-white"
              >
                <Icon className="size-5" />
              </span>
              <span className="min-w-0 flex-1">
                <span className="block text-[17px] font-semibold text-ink group-hover:text-accent-ink">{title}</span>
                <span className="mt-0.5 block text-[15px] text-ink-2">{description}</span>
              </span>
              {pending === role ? (
                <Spinner className="size-5 shrink-0 text-accent" />
              ) : (
                <ChevronRight className="size-5 shrink-0 text-ink-3 transition-transform group-hover:translate-x-0.5 group-hover:text-accent-ink" />
              )}
            </button>
          </li>
        ))}
      </ul>
    </AuthLayout>
  )
}
