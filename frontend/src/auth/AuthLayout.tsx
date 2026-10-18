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

// The form floats above the wash, so it is the one surface here with a shadow.
const CARD_SHADOW = 'shadow-[0_24px_60px_-28px_rgb(9_48_75/0.45)]'

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
    <div className="wash min-h-dvh text-ink">
      <div className="mx-auto grid min-h-dvh max-w-[1320px] gap-10 px-5 py-6 sm:px-8 lg:grid-cols-[minmax(0,1fr)_minmax(0,480px)] lg:items-center lg:gap-16 lg:px-12 lg:py-12 xl:gap-24">
        <div className="hidden h-full max-h-[860px] min-h-[620px] flex-col gap-8 lg:flex">
          <img src={logo} alt="AirSOAP" width={720} height={541} className="h-auto w-[148px] shrink-0" />
          <Showcase />
        </div>

        <header className="lg:hidden">
          <Logo size="sm" />
        </header>

        <main className="flex flex-col justify-center">
          {/* key: each step of signing in arrives instead of swapping in place */}
          <div
            key={title}
            className={`arrive w-full rounded-[10px] bg-surface px-6 py-8 sm:px-10 sm:py-10 ${CARD_SHADOW}`}
          >
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
          </div>
          <dl className="mt-8 space-y-3 px-1 text-sm lg:hidden">
            {FACTS.map((fact) => (
              <div key={fact.title}>
                <dt className="font-semibold text-ink">{fact.title}</dt>
                <dd className="text-ink-2">{fact.body}</dd>
              </div>
            ))}
          </dl>
        </main>
      </div>
    </div>
  )
}
