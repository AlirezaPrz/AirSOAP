import { useEffect, useRef, type ReactNode } from 'react'
import { CheckIcon } from '../components/icons'

const FACTS = [
  'Recording, transcription and note drafting run on this device.',
  'Accounts are created by your administrator. There is no sign-up.',
  'You are signed out after 30 minutes without activity, and the open encounter is discarded.',
]

interface AuthLayoutProps {
  title: string
  description?: ReactNode
  children: ReactNode
  /** Shown under the form, e.g. "Sign out". */
  footer?: ReactNode
}

/** The frame for signing in, changing a temporary password and choosing a role. */
export function AuthLayout({ title, description, children, footer }: AuthLayoutProps) {
  const headingRef = useRef<HTMLHeadingElement>(null)
  useEffect(() => {
    headingRef.current?.focus()
  }, [title])

  return (
    <div className="min-h-dvh bg-ground text-ink lg:grid lg:grid-cols-[minmax(0,5fr)_minmax(0,7fr)]">
      <aside className="flex flex-col bg-rail px-5 py-6 sm:px-8 lg:px-12 lg:py-12">
        <div>
          <p className="text-lg font-semibold tracking-[-0.01em]">AirSOAP</p>
          <p className="text-sm text-ink-3">Local clinical documentation assistant</p>
        </div>
        <ul className="mt-auto hidden max-w-sm space-y-4 text-[15px] text-ink-2 lg:block">
          {FACTS.map((fact) => (
            <li key={fact} className="flex gap-3">
              <CheckIcon className="mt-1 size-4 shrink-0 text-accent" strokeWidth={2.5} />
              {fact}
            </li>
          ))}
        </ul>
      </aside>

      <main className="flex items-start justify-center px-5 py-10 sm:px-8 lg:items-center lg:py-12">
        <div className="w-full max-w-[420px]">
          <h1
            ref={headingRef}
            tabIndex={-1}
            className="text-[28px] leading-tight font-semibold tracking-[-0.02em] focus:outline-none"
          >
            {title}
          </h1>
          {description && <div className="mt-2 text-base text-ink-2">{description}</div>}
          <div className="mt-8">{children}</div>
          {footer && <div className="mt-8 text-[15px] text-ink-2">{footer}</div>}
          <p className="mt-12 text-sm text-ink-3 lg:hidden">{FACTS[0]} {FACTS[1]}</p>
        </div>
      </main>
    </div>
  )
}
