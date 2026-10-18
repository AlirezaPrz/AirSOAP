import type { ReactNode } from 'react'
import { ENCOUNTER_TYPE_LABELS, formatDate } from '../lib/format'
import { calculateAge } from '../workflow/privacy'
import type { Encounter, WorkflowStatus } from '../workflow/reducer'
import { Ambient } from './Ambient'
import { AccountSummary, MobileSignOut, type AccountInfo } from './AccountSummary'
import { CheckIcon } from './icons'
import { Logo } from './Logo'

const STEPS = ['Patient', 'Record', 'Process', 'Review', 'Done'] as const

const STEP_INDEX: Record<WorkflowStatus, number> = {
  idle: 0,
  ready: 1,
  recording: 1,
  processing: 2,
  'processing-error': 2,
  review: 3,
  confirming: 3,
  submitting: 3,
  completed: 4,
}

const PRIVACY_NOTE = 'A prototype for fictional patients. Runs on this device, connects to no EMR and never saves an encounter.'


function StepList({ current }: { current: number }) {
  return (
    <nav aria-label="Encounter progress">
      <ol className="space-y-0.5">
        {STEPS.map((label, index) => {
          const done = index < current
          const active = index === current
          return (
            <li
              key={label}
              aria-current={active ? 'step' : undefined}
              className={`flex h-10 items-center gap-3 rounded-(--radius-control) px-3 text-[15px] ${
                active ? 'bg-surface font-medium text-ink' : done ? 'text-ink-2' : 'text-ink-3'
              }`}
            >
              <span aria-hidden className="flex size-4 items-center justify-center">
                {done ? (
                  <CheckIcon className="size-4 text-accent" strokeWidth={2.5} />
                ) : active ? (
                  <span className="size-2 rounded-full bg-accent" />
                ) : (
                  <span className="size-2 rounded-full bg-field" />
                )}
              </span>
              {label}
              {done && <span className="sr-only">(completed)</span>}
            </li>
          )
        })}
      </ol>
    </nav>
  )
}

function PatientContext({ encounter }: { encounter: Encounter }) {
  const { patient, encounterType } = encounter
  const age = calculateAge(patient.dateOfBirth)
  const rows: [string, string][] = [
    ['Born', formatDate(patient.dateOfBirth)],
    ['Age', age === null ? 'Unknown' : `${age} years`],
    ['MRN', patient.mrn || 'Not provided'],
    ['Visit', ENCOUNTER_TYPE_LABELS[encounterType]],
  ]
  return (
    <section aria-label="Current patient" className="rounded-(--radius-panel) bg-surface p-4">
      <p className="text-sm text-ink-3">Current patient</p>
      <p className="mt-0.5 text-base font-semibold text-ink">{patient.fullName}</p>
      <dl className="mt-3 grid grid-cols-[auto_1fr] gap-x-4 gap-y-1.5 text-sm">
        {rows.map(([term, value]) => (
          <div key={term} className="contents">
            <dt className="text-ink-3">{term}</dt>
            <dd className="text-ink tabular-nums">{value}</dd>
          </div>
        ))}
      </dl>
    </section>
  )
}

function MobileBar({
  current,
  encounter,
  account,
}: {
  current: number
  encounter: Encounter | null
  account?: AccountInfo
}) {
  const age = encounter ? calculateAge(encounter.patient.dateOfBirth) : null
  return (
    <header className="wash-soft px-5 pt-4 pb-4 lg:hidden">
      <div className="flex items-center justify-between gap-4">
        <Logo size="sm" />
        {account && <MobileSignOut account={account} />}
      </div>
      <nav aria-label="Encounter progress" className="mt-4">
        <p className="text-sm text-ink-2">
          <span className="font-medium text-ink">{STEPS[current]}</span>
          {current < STEPS.length - 1 && <span> · next: {STEPS[current + 1]}</span>}
        </p>
        <ol className="mt-2 flex gap-1" aria-hidden>
          {STEPS.map((label, index) => (
            <li key={label} className={`h-1 flex-1 rounded-full ${index <= current ? 'bg-accent' : 'bg-field'}`} />
          ))}
        </ol>
      </nav>
      {encounter && (
        <p className="mt-3 truncate text-sm text-ink-2">
          <span className="font-medium text-ink">{encounter.patient.fullName}</span>
          {age !== null && ` · ${age} y`} · {ENCOUNTER_TYPE_LABELS[encounter.encounterType]}
        </p>
      )}
    </header>
  )
}

interface AppShellProps {
  status: WorkflowStatus
  encounter: Encounter | null
  /** The signed-in clinician; absent in the stand-alone demo and in tests. */
  account?: AccountInfo
  children: ReactNode
}

export function AppShell({ status, encounter, account, children }: AppShellProps) {
  const current = STEP_INDEX[status]
  return (
    <div className="relative isolate min-h-dvh text-ink">
      <Ambient active={status === 'recording'} />
      <aside className="fixed inset-y-0 left-0 hidden w-[272px] flex-col gap-8 overflow-y-auto wash-soft px-5 py-7 lg:flex">
        <div className="px-3">
          <Logo subtitle="Clinical workspace" />
        </div>
        <StepList current={current} />
        {encounter && <PatientContext encounter={encounter} />}
        <div className="mt-auto space-y-5">
          {account && <AccountSummary account={account} />}
          <p className="px-3 text-sm leading-relaxed text-ink-3">{PRIVACY_NOTE}</p>
        </div>
      </aside>

      <MobileBar current={current} encounter={encounter} account={account} />

      <main className="min-w-0 px-5 py-8 sm:px-8 lg:ml-[272px] lg:px-12 lg:py-12">
        <div className="mx-auto max-w-[1120px]">{children}</div>
        <p className="mx-auto mt-12 max-w-[1120px] text-sm text-ink-3 lg:hidden">{PRIVACY_NOTE}</p>
      </main>
    </div>
  )
}
