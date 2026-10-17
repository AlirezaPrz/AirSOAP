import { useEffect, useRef, type ReactNode } from 'react'
import logo from '../assets/airsoap-logo.webp'
import { Logo } from '../components/Logo'

const FACTS: { title: string; body: string }[] = [
  {
    title: 'Stays on this device',
    body: 'Recording, transcription and note drafting all run on the Raspberry Pi. Nothing is sent to the cloud.',
  },
  {
    title: 'Accounts come from your administrator',
    body: 'There is no sign-up. Ask your administrator for an account or a new password.',
  },
  {
    title: 'Ends when you step away',
    body: 'After 30 minutes without activity you are signed out, and any open encounter is discarded.',
  },
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
      <aside className="hidden flex-col justify-between gap-12 bg-rail px-12 py-12 lg:flex">
        <img src={logo} alt="AirSOAP" width={720} height={541} className="h-auto w-full max-w-[300px]" />
        <div className="max-w-sm">
          <p className="text-[22px] leading-snug font-semibold tracking-[-0.01em] text-balance text-brand-navy">
            From the conversation to a reviewed SOAP note, on the device in the room.
          </p>
          <dl className="mt-8 space-y-5">
            {FACTS.map((fact) => (
              <div key={fact.title}>
                <dt className="text-[15px] font-semibold text-ink">{fact.title}</dt>
                <dd className="mt-0.5 text-[15px] text-ink-2">{fact.body}</dd>
              </div>
            ))}
          </dl>
        </div>
      </aside>

      <header className="bg-rail px-5 py-4 sm:px-8 lg:hidden">
        <Logo size="sm" />
      </header>

      <main className="flex items-start justify-center px-5 py-10 sm:px-8 lg:items-center lg:py-12">
        <div className="w-full max-w-[420px]">
          <h1
            ref={headingRef}
            tabIndex={-1}
            className="text-[28px] leading-tight font-semibold tracking-[-0.02em] text-balance focus:outline-none"
          >
            {title}
          </h1>
          {description && <div className="mt-2 text-base text-ink-2">{description}</div>}
          <div className="mt-8">{children}</div>
          {footer && <div className="mt-8 text-[15px] text-ink-2">{footer}</div>}
          <dl className="mt-12 space-y-3 text-sm lg:hidden">
            {FACTS.map((fact) => (
              <div key={fact.title}>
                <dt className="font-semibold text-ink-2">{fact.title}</dt>
                <dd className="text-ink-3">{fact.body}</dd>
              </div>
            ))}
          </dl>
        </div>
      </main>
    </div>
  )
}
