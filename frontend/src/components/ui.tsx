import { useEffect, useRef, type ReactNode } from 'react'
import { AlertIcon, InfoIcon } from './icons'

export const inputClass =
  'block w-full rounded-(--radius-control) border border-field bg-surface px-3 py-2.5 text-[15px] text-ink placeholder:text-ink-3 transition-colors hover:border-ink-3 focus:border-accent focus:ring-3 focus:ring-accent/15 focus:outline-none aria-invalid:border-danger aria-invalid:focus:ring-danger/15 read-only:bg-sunken'

export const labelClass = 'block text-[15px] font-medium text-ink'

export function Panel({ children, className = '' }: { children: ReactNode; className?: string }) {
  return <section className={`rounded-(--radius-panel) bg-surface ${className}`}>{children}</section>
}

/**
 * Page title row: title and one-line description on the left, actions on the
 * right. The title receives focus on mount so screen readers announce each step.
 */
export function PageHeader({
  title,
  description,
  actions,
}: {
  title: string
  description?: ReactNode
  actions?: ReactNode
}) {
  const ref = useRef<HTMLHeadingElement>(null)
  useEffect(() => {
    ref.current?.focus()
  }, [])
  return (
    <div className="mb-8 flex flex-col gap-4 sm:flex-row sm:items-end sm:justify-between">
      <div className="max-w-2xl">
        <h1 ref={ref} tabIndex={-1} className="text-[28px] leading-tight font-semibold tracking-[-0.02em] focus:outline-none sm:text-[32px]">
          {title}
        </h1>
        {description && <p className="mt-2 text-base text-ink-2">{description}</p>}
      </div>
      {actions && <div className="flex flex-wrap items-center gap-2">{actions}</div>}
    </div>
  )
}

type AlertTone = 'info' | 'warning' | 'error'

const ALERT_TONES: Record<AlertTone, string> = {
  info: 'wash-mint text-accent-ink',
  warning: 'bg-warn-soft text-warn-ink',
  error: 'bg-danger-soft text-[#7a1a12]',
}

export function Alert({
  tone,
  title,
  children,
  action,
}: {
  tone: AlertTone
  title?: string
  children: ReactNode
  action?: ReactNode
}) {
  const Icon = tone === 'info' ? InfoIcon : AlertIcon
  return (
    <div
      role={tone === 'error' ? 'alert' : undefined}
      className={`flex flex-col gap-3 rounded-(--radius-panel) px-4 py-3.5 text-[15px] sm:flex-row sm:items-center ${ALERT_TONES[tone]}`}
    >
      <div className="flex flex-1 gap-3">
        <Icon className="mt-0.5 size-5 shrink-0" />
        <div>
          {title && <p className="font-semibold">{title}</p>}
          <div>{children}</div>
        </div>
      </div>
      {action}
    </div>
  )
}

/** `value` is 0–1, or null for work of unknown length (an animated, indeterminate bar). */
export function ProgressBar({
  value,
  label,
  tone = 'accent',
}: {
  value: number | null
  label: string
  tone?: 'accent' | 'danger'
}) {
  const fill = tone === 'danger' ? 'bg-danger' : 'bg-accent'
  if (value === null) {
    return (
      <div role="progressbar" aria-label={label} className="h-1.5 w-full overflow-hidden rounded-full bg-rail">
        <div className={`progress-indeterminate h-full w-1/3 rounded-full ${fill}`} />
      </div>
    )
  }
  const percent = Math.round(value * 100)
  return (
    <div
      role="progressbar"
      aria-label={label}
      aria-valuemin={0}
      aria-valuemax={100}
      aria-valuenow={percent}
      className="h-1.5 w-full overflow-hidden rounded-full bg-rail"
    >
      <div
        className={`h-full rounded-full transition-[width] duration-300 ease-out ${fill}`}
        style={{ width: `${percent}%` }}
      />
    </div>
  )
}
