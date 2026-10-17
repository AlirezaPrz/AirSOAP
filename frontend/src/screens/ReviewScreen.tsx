import { useId, useState } from 'react'
import type { SoapField, Speaker, TranscriptSegment } from '../api/types'
import { Button } from '../components/Button'
import { ConfirmDialog } from '../components/ConfirmDialog'
import { Alert, PageHeader, Panel, inputClass } from '../components/ui'
import { ENCOUNTER_TYPE_LABELS, SOAP_FIELD_META, SPEAKER_LABELS, formatDate, formatDuration } from '../lib/format'
import { SOAP_FIELDS, missingSoapFields, type StateOf } from '../workflow/reducer'

interface ReviewScreenProps {
  state: StateOf<'review'> | StateOf<'confirming'> | StateOf<'submitting'>
  onEditSegment: (segmentId: string, changes: Partial<Pick<TranscriptSegment, 'text' | 'speaker'>>) => void
  onEditSoap: (field: SoapField, value: string) => void
  onRequestSubmit: () => void
  onCancelSubmit: () => void
  onConfirmSubmit: () => void
  onDiscard: () => void
}

const SPEAKER_STYLES: Record<Speaker, string> = {
  clinician: 'bg-accent-soft text-accent-ink',
  patient: 'bg-rail text-ink',
  unassigned: 'bg-warn-soft text-warn-ink',
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
  onRequestSubmit,
  onCancelSubmit,
  onConfirmSubmit,
  onDiscard,
}: ReviewScreenProps) {
  const [confirmDiscard, setConfirmDiscard] = useState(false)
  const id = useId()
  const editable = state.status === 'review'
  const { transcript, soap } = state.draft
  const missing = missingSoapFields(soap)
  const { patient, encounterType } = state.encounter

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
              <Button onClick={onRequestSubmit} disabled={!editable || missing.length > 0}>
                Submit note (demo)
              </Button>
            </>
          }
        />
      </div>

      <div className="mb-6 space-y-3">
        <Alert tone="warning" title="AI-generated draft">
          Check every section against the visit before submitting. The draft may contain errors or omissions.
        </Alert>
        {missing.length > 0 && (
          <p className="text-[15px] font-medium text-danger" role="status">
            Complete all four sections to submit.
          </p>
        )}
      </div>

      <div className="grid items-start gap-6 lg:grid-cols-[minmax(0,5fr)_minmax(0,7fr)]">
        <Panel>
          <div className="flex items-baseline justify-between gap-4 px-6 pt-6 sm:px-8 sm:pt-7">
            <h2 className="text-lg font-semibold">Transcript</h2>
            <p className="text-sm text-ink-3 tabular-nums">
              {transcript.segments.length} turns · {formatDuration(transcript.durationMs)}
            </p>
          </div>
          <ol
            aria-label="Transcript segments"
            className="mt-5 space-y-5 px-6 pb-7 sm:px-8"
          >
            {transcript.segments.map((segment, index) => (
              <SegmentEditor key={segment.id} segment={segment} index={index} editable={editable} onEdit={onEditSegment} />
            ))}
          </ol>
        </Panel>

        <Panel className="px-6 py-6 sm:px-8 sm:py-7">
          <h2 className="text-lg font-semibold">SOAP note</h2>
          <div className="mt-5 space-y-8">
            {SOAP_FIELDS.map((field) => {
              const { label, hint } = SOAP_FIELD_META[field]
              const empty = missing.includes(field)
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
            <dd className="text-ink">{transcript.segments.length} turns</dd>
          </dl>
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
