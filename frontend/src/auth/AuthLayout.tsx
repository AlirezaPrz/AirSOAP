import { useEffect, useRef, type ReactNode } from 'react'
import logo from '../assets/airsoap-logo.webp'
import { Logo } from '../components/Logo'
import { Showcase } from './Showcase'

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
      <aside className="sticky top-0 hidden h-dvh flex-col gap-8 bg-rail px-10 py-10 lg:flex xl:px-12">
        <img src={logo} alt="AirSOAP" width={720} height={541} className="h-auto w-[150px] shrink-0" />
        <Showcase />
      </aside>

      <header className="bg-rail px-5 py-4 sm:px-8 lg:hidden">
        <Logo size="sm" />
      </header>

      <main className="flex items-start justify-center px-5 py-10 sm:px-8 lg:items-center lg:py-12">
        {/* key: each step of signing in arrives instead of swapping in place */}
        <div key={title} className="arrive w-full max-w-[420px]">
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
