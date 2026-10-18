import { useId, useState } from 'react'
import type { SoapField, Speaker, TranscriptSegment } from '../api/types'
import { Button } from '../components/Button'
import { ConfirmDialog } from '../components/ConfirmDialog'
import { ClinicalReviewPanel } from '../components/ClinicalReviewPanel'
import { CheckIcon, Spinner } from '../components/icons'
import { Alert, PageHeader, Panel, inputClass } from '../components/ui'
import { ENCOUNTER_TYPE_LABELS, SOAP_FIELD_META, SPEAKER_LABELS, formatDate, formatDuration } from '../lib/format'
import { calculateAge, toModelAge } from '../workflow/privacy'
import { SOAP_FIELDS, hasSoapContent, missingSoapFields, type StateOf } from '../workflow/reducer'

interface ReviewScreenProps {
  state: StateOf<'review'> | StateOf<'confirming'> | StateOf<'submitting'>
  onEditSegment: (segmentId: string, changes: Partial<Pick<TranscriptSegment, 'text' | 'speaker'>>) => void
  onEditSoap: (field: SoapField, value: string) => void
  onGenerateSoap: () => void
  onCancelSoap: () => void
  onRunReview: () => void
  onCancelReview: () => void
  onDismissSuggestion: (suggestionId: string) => void
  onRequestSubmit: () => void
  onCancelSubmit: () => void
  onConfirmSubmit: () => void
  onDiscard: () => void
  onSuggestSpeakers: () => void
  onCancelSpeakers: () => void
}

const SPEAKER_STYLES: Record<Speaker, string> = {
  clinician: 'bg-accent-soft text-accent-ink',
  patient: 'bg-rail text-ink',
  unknown: 'bg-warn-soft text-warn-ink',
}

function SegmentEditor({
  segment,
  index,
  editable,
  onEdit,
}: {
  segment: TranscriptSegment
  index: number
  editable: boolean
  onEdit: ReviewScreenProps['onEditSegment']
}) {
  const id = useId()
  const speakerLabel = SPEAKER_LABELS[segment.speaker]
  return (
    <li className="grid gap-x-4 gap-y-1.5 sm:grid-cols-[104px_minmax(0,1fr)]">
      <div className="flex items-center gap-3 sm:flex-col sm:items-start sm:gap-1 sm:pt-1">
        <label htmlFor={`${id}-speaker`} className="sr-only">
          Speaker for segment {index + 1}
        </label>
        <select
          id={`${id}-speaker`}
          value={segment.speaker}
          disabled={!editable}
          onChange={(e) => onEdit(segment.id, { speaker: e.target.value as Speaker })}
          className={`cursor-pointer rounded-(--radius-control) py-1 pr-1 pl-2 text-sm font-medium focus:ring-2 focus:ring-accent/40 focus:outline-none disabled:cursor-default ${SPEAKER_STYLES[segment.speaker]}`}
        >
          {Object.entries(SPEAKER_LABELS).map(([value, label]) => (
            <option key={value} value={value}>
              {label}
            </option>
          ))}
        </select>
        <span className="flex items-center gap-1.5 text-sm text-ink-3 tabular-nums">
          {formatDuration(segment.startMs)}
          {segment.speakerSource === 'ai' && (
            <span
              title="Suggested by AI. Choose a speaker to confirm it."
              className="rounded-sm bg-brand-navy px-1 text-[11px] font-semibold tracking-wide text-white"
            >
              AI
              <span className="sr-only"> suggestion, not confirmed</span>
            </span>
          )}
        </span>
      </div>
      <label htmlFor={`${id}-text`} className="sr-only">
        Segment {index + 1} text ({speakerLabel}, {formatDuration(segment.startMs)})
      </label>
      <textarea
        id={`${id}-text`}
        rows={2}
        value={segment.text}
        readOnly={!editable}
        onChange={(e) => onEdit(segment.id, { text: e.target.value })}
        className="-mx-2 block w-[calc(100%+1rem)] resize-none rounded-(--radius-control) bg-transparent px-2 py-1 text-[15px] leading-relaxed text-ink transition-colors [field-sizing:content] hover:bg-sunken focus:bg-surface focus:ring-2 focus:ring-accent/40 focus:outline-none read-only:hover:bg-transparent"
      />
    </li>
  )
}

/** "Suggest speakers": the 3B model labels turns it can tell apart from the words; the clinician checks them. */
function SpeakerSuggestions({
  status,
  message,
  applied,
  unconfirmed,
  onSuggest,
  onCancel,
  onConfirm,
}: {
  status: 'idle' | 'running' | 'failed'
  message: string | null
  applied: number | null
  unconfirmed: number
  onSuggest: () => void
  onCancel: () => void
  onConfirm: () => void
}) {
  if (status === 'running') {
    return (
      <div className="mt-3">
        <Alert
          tone="info"
          action={
            <Button variant="secondary" className="h-8 px-3 text-sm" onClick={onCancel}>
              Cancel
            </Button>
          }
        >
          <span className="flex items-center gap-2">
            <Spinner className="size-4" />
            Suggesting speakers from the words… A long visit can take a few minutes. You can keep editing.
          </span>
        </Alert>
      </div>
    )
  }
  return (
    <div className="mt-3 space-y-2">
      {status === 'failed' && message && <Alert tone="error">{message}</Alert>}
      {applied !== null && unconfirmed > 0 && (
        <p className="text-sm text-ink-2" role="status">
          The AI suggested {applied} {applied === 1 ? 'label' : 'labels'}, marked <span className="font-semibold">AI</span>.
          Check each one; turns it could not tell apart stay Unknown, and your own labels were kept.
        </p>
      )}
      {applied === 0 && (
        <p className="text-sm text-ink-2" role="status">
          The AI could not tell any more speakers apart from the words alone.
        </p>
      )}
      <div className="flex flex-wrap gap-2">
        <Button variant="secondary" className="h-8 px-3 text-sm" onClick={onSuggest}>
          Suggest speakers
        </Button>
        {unconfirmed > 0 && (
          <Button variant="secondary" className="h-8 px-3 text-sm" onClick={onConfirm}>
            Accept {unconfirmed} AI {unconfirmed === 1 ? 'label' : 'labels'}
          </Button>
        )}
      </div>
    </div>
  )
}

type StepState = 'done' | 'current' | 'todo'

/**
 * The three things that make a note ready, always in view while scrolling. Each step shows its
 * state and jumps to its section.
 */
function ReviewProgress({ steps }: { steps: { target: string; label: string; detail: string; state: StepState }[] }) {
  const jump = (id: string) => {
    const target = document.getElementById(id)
    target?.scrollIntoView({ behavior: 'smooth', block: 'start' })
    target?.focus({ preventScroll: true })
  }
  return (
    <nav aria-label="Review steps" className="-mt-3 mb-5">
      <ol className="grid grid-cols-3 gap-1.5 sm:gap-2">
        {steps.map((step, index) => (
          <li key={step.label}>
            <button
              type="button"
              onClick={() => jump(step.target)}
              aria-current={step.state === 'current' ? 'step' : undefined}
              className={`group flex w-full flex-col items-start gap-1.5 rounded-(--radius-control) px-2 py-2 text-left sm:flex-row sm:items-center sm:gap-3 sm:px-3 sm:py-2.5 transition-colors focus-visible:outline-2 focus-visible:outline-offset-2 focus-visible:outline-accent ${
                step.state === 'current' ? 'bg-surface' : 'hover:bg-rail'
              }`}
            >
              <span
                aria-hidden
                className={`flex size-6 shrink-0 items-center justify-center rounded-full text-[13px] font-semibold tabular-nums transition-colors duration-300 ${
                  step.state === 'done'
                    ? 'bg-accent text-white'
                    : step.state === 'current'
                      ? 'bg-accent-soft text-accent-ink'
                      : 'bg-rail text-ink-3'
                }`}
              >
                {step.state === 'done' ? <CheckIcon className="size-3.5" strokeWidth={3} /> : index + 1}
              </span>
              <span className="min-w-0">
                <span className={`block text-sm leading-tight font-medium sm:text-[15px] ${step.state === 'todo' ? 'text-ink-2' : 'text-ink'}`}>
                  {step.label}
                </span>
                <span className="hidden truncate text-sm text-ink-3 sm:block">{step.detail}</span>
              </span>
              <span className="sr-only">({step.state === 'done' ? 'done' : step.state === 'current' ? 'next' : 'not yet'})</span>
            </button>
          </li>
        ))}
      </ol>
    </nav>
  )
}

export function ReviewScreen({
  state,
  onEditSegment,
  onEditSoap,
  onGenerateSoap,
  onCancelSoap,
  onRunReview,
  onCancelReview,
  onDismissSuggestion,
  onRequestSubmit,
  onCancelSubmit,
  onConfirmSubmit,
  onDiscard,
  onSuggestSpeakers,
  onCancelSpeakers,
}: ReviewScreenProps) {
  const [confirmDiscard, setConfirmDiscard] = useState(false)
  const [confirmRegenerate, setConfirmRegenerate] = useState(false)
  const id = useId()
  const editable = state.status === 'review'
  const { transcript, soap, soapGeneration, warnings, review, version } = state.draft
  const missing = missingSoapFields(soap)
  const hasContent = hasSoapContent(soap)
  const generating = soapGeneration.status === 'running'
  const { patient, encounterType } = state.encounter
  const age = calculateAge(patient.dateOfBirth)
  const labelled = transcript.segments.filter((segment) => segment.speaker !== 'unknown').length
  const total = transcript.segments.length
  const allLabelled = total > 0 && labelled === total
  const unconfirmed = transcript.segments.filter((segment) => segment.speakerSource === 'ai')
  const { speakers } = state.draft
  const suggesting = speakers.status === 'running'
  const complete = hasContent && missing.length === 0
  const steps: { target: string; label: string; detail: string; state: StepState }[] = [
    {
      target: `${id}-transcript`,
      label: 'Label speakers',
      detail: unconfirmed.length
        ? `${unconfirmed.length} AI ${unconfirmed.length === 1 ? 'label' : 'labels'} to check`
        : allLabelled
          ? 'Every turn labelled'
          : `${labelled} of ${total} turns`,
      state: allLabelled && unconfirmed.length === 0 ? 'done' : 'current',
    },
    {
      target: `${id}-soap`,
      label: 'Draft the note',
      detail: generating ? 'Drafting…' : hasContent ? 'Draft ready' : 'Not drafted yet',
      state: hasContent ? 'done' : allLabelled ? 'current' : 'todo',
    },
    {
      target: `${id}-soap`,
      label: 'Check every section',
      detail: complete ? 'Ready to submit' : hasContent ? `${missing.length} empty` : 'After the draft',
      state: complete ? 'done' : hasContent ? 'current' : 'todo',
    },
  ]

  /** Two-person visits usually alternate; one click gets most labels right, then fix the odd one. */
  const alternate = () =>
    transcript.segments.forEach((segment, index) =>
      onEditSegment(segment.id, { speaker: index % 2 === 0 ? 'clinician' : 'patient' }),
    )
  /** The clinician has looked at the AI's labels and accepts them as theirs. */
  const confirmSuggestions = () =>
    unconfirmed.forEach((segment) => onEditSegment(segment.id, { speaker: segment.speaker }))
  const fillRemaining = (speaker: Speaker) =>
    transcript.segments.forEach((segment) => {
      if (segment.speaker === 'unknown') onEditSegment(segment.id, { speaker })
    })

  return (
    <>
      <div className="z-10 -mx-5 flow-root bg-ground px-5 sm:-mx-8 sm:px-8 lg:sticky lg:top-0 lg:-mx-12 lg:px-12 lg:pt-12 lg:-mt-12">
        <PageHeader
          title="Review draft note"
          description="Edit the transcript and each section. You are responsible for the final note."
          actions={
            <>
              <Button variant="secondary" onClick={() => setConfirmDiscard(true)} disabled={!editable}>
                Discard
              </Button>
              <Button onClick={onRequestSubmit} disabled={!editable || generating || missing.length > 0}>
                Submit note (demo)
              </Button>
            </>
          }
        />
        <ReviewProgress steps={steps} />
      </div>

      {(hasContent || missing.length < 4) && (
        <div className="mb-6 space-y-3">
          <Alert tone="warning" title="AI-generated draft">
            Check every section against the visit before submitting. The draft may contain errors or omissions.
          </Alert>
          {hasContent && missing.length > 0 && (
            <p className="text-[15px] font-medium text-danger" role="status">
              Complete all four sections to submit.
            </p>
          )}
        </div>
      )}

      <div className="grid items-start gap-6 lg:grid-cols-[minmax(0,5fr)_minmax(0,7fr)]">
        <Panel>
          <div className="flex items-baseline justify-between gap-4 px-6 pt-6 sm:px-8 sm:pt-7">
            <h2 id={`${id}-transcript`} tabIndex={-1} className="scroll-mt-56 text-lg font-semibold focus:outline-none">
              Transcript
            </h2>
            <p className="text-sm text-ink-3 tabular-nums">
              {transcript.segments.length} {transcript.segments.length === 1 ? 'turn' : 'turns'} ·{' '}
              {formatDuration(transcript.durationMs)}
            </p>
          </div>
          <div className="mt-4 space-y-3 px-6 sm:px-8">
            {labelled === 0 && (
              <Alert tone="info" title="Set who spoke">
                Whisper cannot tell speakers apart, so every turn starts as Unknown. Set each one to Clinician or
                Patient. The note and the clinical review use your labels.
              </Alert>
            )}
            <div>
              <div className="flex items-baseline justify-between text-sm">
                <span className="text-ink-2">Speakers labelled</span>
                <span className="text-ink-3 tabular-nums">
                  {labelled} of {total} turns labelled
                </span>
              </div>
              <div
                role="progressbar"
                aria-label="Speakers labelled"
                aria-valuemin={0}
                aria-valuemax={total}
                aria-valuenow={labelled}
                className="mt-2 h-1.5 overflow-hidden rounded-full bg-rail"
              >
                <div
                  className="h-full rounded-full bg-accent transition-[width] duration-500 ease-(--ease-out-expo)"
                  style={{ width: `${total ? (labelled / total) * 100 : 0}%` }}
                />
              </div>
              {editable && (!allLabelled || unconfirmed.length > 0) && (
                <SpeakerSuggestions
                  status={speakers.status}
                  message={speakers.message}
                  applied={speakers.applied}
                  unconfirmed={unconfirmed.length}
                  onSuggest={onSuggestSpeakers}
                  onCancel={onCancelSpeakers}
                  onConfirm={confirmSuggestions}
                />
              )}
              {editable && !allLabelled && total > 1 && !suggesting && (
                <div className="mt-3 flex flex-wrap gap-2">
                  <Button variant="secondary" className="h-8 px-3 text-sm" onClick={alternate}>
                    Alternate, clinician first
                  </Button>
                  {labelled > 0 && (
                    <>
                      <Button variant="secondary" className="h-8 px-3 text-sm" onClick={() => fillRemaining('patient')}>
                        Rest are patient
                      </Button>
                      <Button variant="secondary" className="h-8 px-3 text-sm" onClick={() => fillRemaining('clinician')}>
                        Rest are clinician
                      </Button>
                    </>
                  )}
                </div>
              )}
            </div>
          </div>
          <ol aria-label="Transcript segments" className="mt-5 space-y-5 px-6 pb-7 sm:px-8">
            {transcript.segments.map((segment, index) => (
              <SegmentEditor key={segment.id} segment={segment} index={index} editable={editable} onEdit={onEditSegment} />
            ))}
          </ol>
        </Panel>

        <Panel className="px-6 py-6 sm:px-8 sm:py-7">
          <div className="flex items-center justify-between gap-4">
            <h2 id={`${id}-soap`} tabIndex={-1} className="scroll-mt-56 text-lg font-semibold focus:outline-none">
              SOAP note
            </h2>
            {hasContent && !generating && (
              <Button variant="secondary" onClick={() => setConfirmRegenerate(true)} disabled={!editable}>
                Regenerate
              </Button>
            )}
          </div>

          <div className="mt-4 space-y-3">
            {!hasContent && !generating && (
              <div className="rounded-(--radius-panel) bg-accent-soft p-5 text-accent-ink">
                <p className="text-[15px]">
                  Label who spoke in the transcript, then generate a draft. A model on this device writes it from the
                  transcript and your labels. You edit it before anything is submitted.
                </p>
                <dl className="mt-3 grid gap-2 text-sm sm:grid-cols-[auto_1fr] sm:gap-x-4">
                  <dt className="font-semibold">Sent to the model</dt>
                  <dd>
                    Labelled transcript with identifiers removed, age
                    {age === null ? '' : ` (${toModelAge(age)} years)`}, encounter type
                  </dd>
                  <dt className="font-semibold">Never shared</dt>
                  <dd>Name, date of birth, MRN</dd>
                </dl>
                {unconfirmed.length > 0 && (
                  <p className="mt-3 text-sm font-medium">
                    {unconfirmed.length} speaker {unconfirmed.length === 1 ? 'label is' : 'labels are'} AI suggestions
                    you have not checked. The draft will use them as they are.
                  </p>
                )}
                <Button className="mt-4" onClick={onGenerateSoap} disabled={!editable || suggesting}>
                  Generate SOAP note
                </Button>
              </div>
            )}

            {generating && (
              <Alert
                tone="info"
                action={
                  <Button variant="secondary" onClick={onCancelSoap}>
                    Cancel
                  </Button>
                }
              >
                <span className="flex items-center gap-2">
                  <Spinner className="size-4" />
                  Generating the SOAP note on this device. This can take a minute or two. You can keep editing;
                  sections you type in are kept.
                </span>
              </Alert>
            )}

            {soapGeneration.status === 'failed' && (
              <Alert
                tone="error"
                title="The note could not be generated"
                action={
                  soapGeneration.retryable ? (
                    <Button variant="secondary" onClick={onGenerateSoap}>
                      Try again
                    </Button>
                  ) : undefined
                }
              >
                {soapGeneration.message}
              </Alert>
            )}

            {warnings.length > 0 && (
              <Alert tone="warning" title="Check these values against the visit">
                <ul className="list-disc pl-5">
                  {warnings.map((warning) => (
                    <li key={`${warning.field}-${warning.message}`}>{warning.message}</li>
                  ))}
                </ul>
              </Alert>
            )}
          </div>

          <div className="mt-6 space-y-8">
            {SOAP_FIELDS.map((field) => {
              const { label, hint } = SOAP_FIELD_META[field]
              const empty = hasContent && missing.includes(field)
              const fieldId = `${id}-${field}`
              return (
                <div key={field}>
                  <label htmlFor={fieldId} className="block text-[17px] font-semibold text-ink">
                    {label}
                  </label>
                  <p id={`${fieldId}-hint`} className="mt-0.5 text-sm text-ink-3">
                    {hint}
                  </p>
                  <textarea
                    id={fieldId}
                    rows={4}
                    value={soap[field]}
                    readOnly={!editable}
                    onChange={(e) => onEditSoap(field, e.target.value)}
                    aria-invalid={empty || undefined}
                    aria-describedby={`${fieldId}-hint${empty ? ` ${fieldId}-error` : ''}`}
                    className={`mt-3 ${inputClass} min-h-28 resize-y leading-relaxed [field-sizing:content]`}
                  />
                  <div className="mt-1.5 flex justify-between gap-4 text-sm">
                    <span id={`${fieldId}-error`} className="font-medium text-danger">
                      {empty ? `${label} cannot be empty.` : ''}
                    </span>
                    <span className="text-ink-3 tabular-nums">{soap[field].length} characters</span>
                  </div>
                </div>
              )
            })}
          </div>
        </Panel>
      </div>

      <div className="mt-6">
        <ClinicalReviewPanel
          review={review}
          version={version}
          segments={transcript.segments}
          canRun={editable && hasContent && !generating}
          onRun={onRunReview}
          onCancel={onCancelReview}
          onDismiss={onDismissSuggestion}
        />
      </div>

      {(state.status === 'confirming' || state.status === 'submitting') && (
        <ConfirmDialog
          title="Submit demo note?"
          confirmLabel="Submit demo note"
          cancelLabel="Back to review"
          busy={state.status === 'submitting'}
          error={state.status === 'confirming' ? state.submitError : null}
          onConfirm={onConfirmSubmit}
          onCancel={onCancelSubmit}
        >
          <p>
            This is a demonstration. <strong className="font-semibold text-ink">No EMR is connected</strong> and no
            medical record will be created or modified.
          </p>
          <dl className="grid grid-cols-[auto_1fr] gap-x-6 gap-y-2 rounded-(--radius-panel) bg-sunken p-4">
            <dt className="text-ink-3">Patient</dt>
            <dd className="font-medium text-ink">{patient.fullName}</dd>
            <dt className="text-ink-3">Date of birth</dt>
            <dd className="text-ink">{formatDate(patient.dateOfBirth)}</dd>
            <dt className="text-ink-3">Encounter</dt>
            <dd className="text-ink">{ENCOUNTER_TYPE_LABELS[encounterType]}</dd>
            <dt className="text-ink-3">Note</dt>
            <dd className="text-ink">All four sections complete</dd>
            <dt className="text-ink-3">Transcript</dt>
            <dd className="text-ink">
              {transcript.segments.length} {transcript.segments.length === 1 ? 'turn' : 'turns'}
            </dd>
          </dl>
        </ConfirmDialog>
      )}

      {confirmRegenerate && (
        <ConfirmDialog
          title="Replace the note with a new draft?"
          confirmLabel="Regenerate"
          cancelLabel="Keep my note"
          onConfirm={() => {
            setConfirmRegenerate(false)
            onGenerateSoap()
          }}
          onCancel={() => setConfirmRegenerate(false)}
        >
          <p>
            A new draft will replace the text in the four sections. Any section you edit while it is being generated
            keeps your text.
          </p>
        </ConfirmDialog>
      )}

      {confirmDiscard && (
        <ConfirmDialog
          title="Discard this encounter?"
          tone="danger"
          confirmLabel="Discard encounter"
          cancelLabel="Keep editing"
          onConfirm={onDiscard}
          onCancel={() => setConfirmDiscard(false)}
        >
          <p>The transcript, SOAP draft and your edits will be permanently cleared. This cannot be undone.</p>
        </ConfirmDialog>
      )}
    </>
  )
}
