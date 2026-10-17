import { useId, useState } from 'react'
import type { SoapField, Speaker, TranscriptSegment } from '../api/types'
import { Button } from '../components/Button'
import { ConfirmDialog } from '../components/ConfirmDialog'
import { ClinicalReviewPanel } from '../components/ClinicalReviewPanel'
import { Spinner } from '../components/icons'
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
        <span className="text-sm text-ink-3 tabular-nums">{formatDuration(segment.startMs)}</span>
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
            <h2 className="text-lg font-semibold">Transcript</h2>
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
            {labelled > 0 && (
              <p className="text-sm text-ink-3 tabular-nums">
                {labelled} of {transcript.segments.length} turns labelled
              </p>
            )}
          </div>
          <ol aria-label="Transcript segments" className="mt-5 space-y-5 px-6 pb-7 sm:px-8">
            {transcript.segments.map((segment, index) => (
              <SegmentEditor key={segment.id} segment={segment} index={index} editable={editable} onEdit={onEditSegment} />
            ))}
          </ol>
        </Panel>

        <Panel className="px-6 py-6 sm:px-8 sm:py-7">
          <div className="flex items-center justify-between gap-4">
            <h2 className="text-lg font-semibold">SOAP note</h2>
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
                <Button className="mt-4" onClick={onGenerateSoap} disabled={!editable}>
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
