import { useId, useRef, useState, type FormEvent } from 'react'
import type { FailableOperation } from '../api/mockClient'
import type { EncounterType } from '../api/types'
import { Button } from '../components/Button'
import { CheckIcon, ChevronDownIcon, XIcon } from '../components/icons'
import { PageHeader, Panel, inputClass, labelClass } from '../components/ui'
import { ENCOUNTER_TYPE_LABELS } from '../lib/format'
import { SAMPLE_PATIENT } from '../mocks/encounter'
import { calculateAge, toModelAge } from '../workflow/privacy'
import type { StartEncounterInput } from '../workflow/useEncounterWorkflow'
import {
  EMPTY_PATIENT_FORM,
  validatePatientForm,
  type PatientFormErrors,
  type PatientFormValues,
} from '../workflow/validation'

const STEPS: { title: string; body: string }[] = [
  { title: 'Identify the patient', body: 'Name, date of birth and visit type. Only the age and visit type are shared with the AI.' },
  { title: 'Record the visit', body: 'Confirm consent, then record. Stop when the visit is over; the audio is transcribed on this device.' },
  { title: 'Check the transcript', body: 'Correct any words and mark each turn as Clinician or Patient.' },
  { title: 'Draft and review the note', body: 'Generate the SOAP draft, edit every section, and optionally run a clinical review for suggestions.' },
  { title: 'Submit', body: 'Confirm the note. In this prototype nothing is sent to an EMR, and the encounter is cleared.' },
]

/**
 * "What the AI sees", live: as the clinician types, each detail moves to the side it goes to.
 * The point is to show, with this patient's own details, that identity never reaches the model.
 */
function AiPreview({ values, age }: { values: PatientFormValues; age: number | null }) {
  const visit = values.encounterType ? ENCOUNTER_TYPE_LABELS[values.encounterType as EncounterType] : null
  const shared: { label: string; value: string | null; placeholder: string }[] = [
    { label: 'Age', value: age === null ? null : `${toModelAge(age)} years`, placeholder: 'from the date of birth' },
    { label: 'Visit type', value: visit, placeholder: 'once you choose one' },
    { label: 'Transcript', value: null, placeholder: 'after recording, names removed' },
  ]
  const withheld: { label: string; value: string }[] = [
    { label: 'Name', value: values.fullName.trim() },
    { label: 'Date of birth', value: values.dateOfBirth },
    { label: 'MRN', value: values.mrn.trim() },
  ]

  return (
    <section aria-labelledby="ai-preview" className="rounded-(--radius-panel) bg-accent-soft p-6 text-accent-ink lg:row-span-2">
      <h2 id="ai-preview" className="text-lg font-semibold">
        What the AI sees
      </h2>
      <p className="mt-1 text-[15px]">
        A local model drafts the note on this device. Fill in the form and watch where each detail goes.
      </p>

      <h3 className="mt-6 flex items-center gap-2 text-sm font-semibold">
        <CheckIcon className="size-4" strokeWidth={2.5} /> Shared with the model
      </h3>
      <dl className="mt-2 divide-y divide-accent/15 rounded-(--radius-control) bg-surface/70 px-3.5">
        {shared.map((row) => (
          <div key={row.label} className="flex items-baseline justify-between gap-4 py-2.5 text-[15px]">
            <dt>{row.label}</dt>
            <dd
              key={row.value ?? 'empty'}
              className={row.value ? 'arrive font-semibold text-ink tabular-nums' : 'text-right text-sm text-accent-ink/75'}
            >
              {row.value ?? row.placeholder}
            </dd>
          </div>
        ))}
      </dl>

      <h3 className="mt-6 flex items-center gap-2 text-sm font-semibold">
        <XIcon className="size-4" strokeWidth={2.5} /> Never leaves this screen
      </h3>
      <dl className="mt-2 divide-y divide-accent/15 rounded-(--radius-control) bg-surface/70 px-3.5">
        {withheld.map((row) => (
          <div key={row.label} className="flex items-baseline justify-between gap-4 py-2.5 text-[15px]">
            <dt>{row.label}</dt>
            <dd className="min-w-0 truncate text-right">
              {row.value ? (
                <span className="text-ink-2 line-through decoration-danger/70 decoration-2">{row.value}</span>
              ) : (
                <span className="text-sm text-accent-ink/75">not entered</span>
              )}
            </dd>
          </div>
        ))}
      </dl>
      <p className="mt-4 text-sm">
        If a name is spoken during the visit, it is removed from the transcript before the model sees it.
      </p>
    </section>
  )
}

/** A short, collapsible walkthrough of one encounter, for first-time users. */
function HowItWorks() {
  return (
    <details className="group rounded-(--radius-panel) bg-surface">
      <summary className="flex cursor-pointer list-none items-center justify-between gap-4 rounded-(--radius-panel) px-6 py-5 focus-visible:outline-2 focus-visible:outline-offset-2 focus-visible:outline-accent sm:px-8 [&::-webkit-details-marker]:hidden">
        <span>
          <span className="block text-base font-semibold text-ink">How an encounter works</span>
          <span className="block text-sm text-ink-3">Five steps, about as long as the visit itself</span>
        </span>
        <ChevronDownIcon className="size-5 shrink-0 text-ink-3 transition-transform duration-200 group-open:rotate-180" />
      </summary>
      <ol className="grid gap-x-8 gap-y-5 px-6 pb-7 sm:px-8 md:grid-cols-2">
        {STEPS.map((step, index) => (
          <li key={step.title} className="flex gap-3.5">
            <span
              aria-hidden
              className="flex size-7 shrink-0 items-center justify-center rounded-full bg-accent-soft text-sm font-semibold text-accent-ink tabular-nums"
            >
              {index + 1}
            </span>
            <span>
              <span className="block text-[15px] font-semibold text-ink">{step.title}</span>
              <span className="mt-0.5 block text-[15px] text-ink-2">{step.body}</span>
            </span>
          </li>
        ))}
      </ol>
    </details>
  )
}

export type SimulatedFailure = FailableOperation | 'none'

interface DashboardScreenProps {
  onStart: (input: StartEncounterInput, simulatedFailure: SimulatedFailure) => void
  showDemoControls: boolean
}

type FieldName = keyof PatientFormValues

const FIELD_ORDER: FieldName[] = ['fullName', 'dateOfBirth', 'mrn', 'encounterType']

export function DashboardScreen({ onStart, showDemoControls }: DashboardScreenProps) {
  const [values, setValues] = useState<PatientFormValues>(EMPTY_PATIENT_FORM)
  const [errors, setErrors] = useState<PatientFormErrors>({})
  const [submitted, setSubmitted] = useState(false)
  const [simulatedFailure, setSimulatedFailure] = useState<SimulatedFailure>('none')
  const fieldRefs = useRef<Partial<Record<FieldName, HTMLInputElement | null>>>({})
  const id = useId()

  const update = (field: FieldName, value: string) => {
    const next = { ...values, [field]: value }
    setValues(next)
    // Re-validate live only after the first submit attempt.
    if (submitted) setErrors(validatePatientForm(next))
  }

  const handleSubmit = (event: FormEvent) => {
    event.preventDefault()
    setSubmitted(true)
    const nextErrors = validatePatientForm(values)
    setErrors(nextErrors)
    const firstInvalid = FIELD_ORDER.find((field) => nextErrors[field])
    if (firstInvalid) {
      fieldRefs.current[firstInvalid]?.focus()
      return
    }
    onStart(
      {
        patient: {
          fullName: values.fullName.trim(),
          dateOfBirth: values.dateOfBirth,
          mrn: values.mrn.trim() || undefined,
        },
        encounterType: values.encounterType as EncounterType,
      },
      simulatedFailure,
    )
  }

  const fillSample = () => {
    const { encounterType, mrn = '', ...rest } = SAMPLE_PATIENT
    const sample = { ...rest, mrn, encounterType }
    setValues(sample)
    if (submitted) setErrors(validatePatientForm(sample))
  }

  const age = errors.dateOfBirth ? null : calculateAge(values.dateOfBirth)
  const fieldId = (field: FieldName) => `${id}-${field}`
  const errorId = (field: FieldName) => `${id}-${field}-error`
  const describedBy = (field: FieldName, ...extra: string[]) =>
    [errors[field] ? errorId(field) : '', ...extra].filter(Boolean).join(' ') || undefined
  const fieldError = (field: FieldName) =>
    errors[field] && (
      <p id={errorId(field)} className="mt-2 text-sm font-medium text-danger">
        {errors[field]}
      </p>
    )
  const required = <span className="text-danger" aria-hidden> *</span>

  return (
    <>
      <PageHeader
        title="New encounter"
        description="Identify the patient, then record the visit. Use fictional details only."
        actions={
          <Button variant="secondary" onClick={fillSample}>
            Use sample patient
          </Button>
        }
      />

      <div className="grid items-start gap-6 lg:grid-cols-[minmax(0,1fr)_360px]">
        <Panel>
          <form noValidate onSubmit={handleSubmit} aria-label="Patient identification">
            <div className="grid gap-x-5 gap-y-6 p-6 sm:grid-cols-2 sm:p-8">
              <div className="sm:col-span-2">
                <label htmlFor={fieldId('fullName')} className={labelClass}>
                  Full name{required}
                </label>
                <input
                  ref={(el) => {
                    fieldRefs.current.fullName = el
                  }}
                  id={fieldId('fullName')}
                  type="text"
                  autoComplete="off"
                  required
                  value={values.fullName}
                  onChange={(e) => update('fullName', e.target.value)}
                  aria-invalid={errors.fullName ? true : undefined}
                  aria-describedby={describedBy('fullName')}
                  className={`mt-2 ${inputClass}`}
                />
                {fieldError('fullName')}
              </div>

              <div>
                <label htmlFor={fieldId('dateOfBirth')} className={labelClass}>
                  Date of birth{required}
                </label>
                <input
                  ref={(el) => {
                    fieldRefs.current.dateOfBirth = el
                  }}
                  id={fieldId('dateOfBirth')}
                  type="date"
                  required
                  value={values.dateOfBirth}
                  onChange={(e) => update('dateOfBirth', e.target.value)}
                  aria-invalid={errors.dateOfBirth ? true : undefined}
                  aria-describedby={describedBy('dateOfBirth', `${id}-age`)}
                  className={`mt-2 ${inputClass}`}
                />
                {fieldError('dateOfBirth')}
                <p id={`${id}-age`} className="mt-2 text-sm text-ink-3" aria-live="polite">
                  {age !== null ? `Age ${toModelAge(age)}. Only the age is shared with the AI.` : ''}
                </p>
              </div>

              <div>
                <label htmlFor={fieldId('mrn')} className={labelClass}>
                  MRN <span className="font-normal text-ink-3">(optional)</span>
                </label>
                <input
                  ref={(el) => {
                    fieldRefs.current.mrn = el
                  }}
                  id={fieldId('mrn')}
                  type="text"
                  autoComplete="off"
                  value={values.mrn}
                  onChange={(e) => update('mrn', e.target.value)}
                  aria-invalid={errors.mrn ? true : undefined}
                  aria-describedby={describedBy('mrn')}
                  className={`mt-2 ${inputClass}`}
                />
                {fieldError('mrn')}
              </div>

              <fieldset className="sm:col-span-2" aria-describedby={describedBy('encounterType')}>
                <legend className={labelClass}>Encounter type{required}</legend>
                <div className="mt-2 grid grid-cols-2 gap-2 md:grid-cols-4">
                  {Object.entries(ENCOUNTER_TYPE_LABELS).map(([value, label], index) => (
                    <label
                      key={value}
                      className="flex cursor-pointer items-center gap-2.5 rounded-(--radius-control) bg-sunken px-3 py-3 text-[15px] text-ink-2 transition-colors hover:bg-rail has-checked:bg-accent-soft has-checked:font-medium has-checked:text-accent-ink has-focus-visible:outline-2 has-focus-visible:outline-offset-2 has-focus-visible:outline-accent"
                    >
                      <input
                        ref={
                          index === 0
                            ? (el) => {
                                fieldRefs.current.encounterType = el
                              }
                            : undefined
                        }
                        type="radio"
                        name={`${id}-encounterType`}
                        value={value}
                        checked={values.encounterType === value}
                        onChange={(e) => update('encounterType', e.target.value)}
                        className="size-4 shrink-0 accent-accent"
                      />
                      {label}
                    </label>
                  ))}
                </div>
                {fieldError('encounterType')}
              </fieldset>
            </div>

            <div className="flex flex-col-reverse gap-4 rounded-b-(--radius-panel) bg-sunken/60 px-6 py-5 sm:flex-row sm:items-center sm:justify-between sm:px-8">
              <p className="text-sm text-ink-3">Name, date of birth and MRN stay on this device.</p>
              <Button type="submit" size="lg">
                Start encounter
              </Button>
            </div>
          </form>
        </Panel>

        <AiPreview values={values} age={age} />

        <HowItWorks />

        {showDemoControls && (
          <section className="flex flex-col gap-3 rounded-(--radius-panel) bg-rail px-6 py-5 sm:flex-row sm:items-center sm:justify-between sm:px-8">
            <div>
              <h2 className="text-base font-semibold">Demo controls</h2>
              <label htmlFor={`${id}-failure`} className="block text-sm text-ink-2">
                Simulate a service failure
              </label>
            </div>
            <select
              id={`${id}-failure`}
              value={simulatedFailure}
              onChange={(e) => setSimulatedFailure(e.target.value as SimulatedFailure)}
              className={`${inputClass} sm:w-72`}
            >
              <option value="none">None</option>
              <option value="transcription">Transcription fails once</option>
              <option value="soap-generation">SOAP generation fails once</option>
              <option value="clinical-review">Clinical review fails once</option>
              <option value="submission">Demo submission fails once</option>
            </select>
          </section>
        )}
      </div>
    </>
  )
}
