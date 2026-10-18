import type { ClinicalReview, Evidence, ReviewSuggestion, TranscriptSegment } from '../api/types'
import { SOAP_FIELD_META } from '../lib/format'
import type { ClinicalReviewState } from '../workflow/reducer'
import { Button } from './Button'
import { Spinner } from './icons'
import { Alert, Panel } from './ui'

const SECTIONS: { key: Exclude<keyof ClinicalReview, 'omittedUnverified'>; title: string; empty: string }[] = [
  { key: 'informationGaps', title: 'Information gaps', empty: 'No information gaps were suggested.' },
  { key: 'followUpQuestions', title: 'Follow-up questions', empty: 'No follow-up questions were suggested.' },
  {
    key: 'differentialConsiderations',
    title: 'Differential considerations',
    empty: 'Nothing in the documentation supported a differential suggestion.',
  },
  {
    key: 'documentationInconsistencies',
    title: 'Documentation consistency',
    empty: 'No inconsistencies between the transcript and the note were suggested.',
  },
]

const GAP_LABELS = {
  not_asked: 'May not have been asked in this transcript',
  asked_not_answered: 'Asked, but not clearly answered',
  omitted_from_note: 'In the transcript, but not in the note',
  uncertain: 'Status unclear',
} as const

/** The heading's ID, so the review steps can jump to it. */
export const CLINICAL_REVIEW_HEADING = 'clinical-review-heading'

const UNCERTAINTY_LABELS = { low: 'Low', medium: 'Medium', high: 'High' } as const

interface ClinicalReviewPanelProps {
  review: ClinicalReviewState
  /** The draft's edit counter; a result from an older version is marked out of date. */
  version: number
  segments: TranscriptSegment[]
  /** The note has text and is not being generated. */
  canRun: boolean
  onRun: () => void
  onCancel: () => void
  onDismiss: (suggestionId: string) => void
}

function evidenceLabel(evidence: Evidence, segments: TranscriptSegment[]): string {
  if (evidence.source === 'note') {
    return evidence.field ? `Note, ${SOAP_FIELD_META[evidence.field].label}` : 'Note'
  }
  const index = evidence.segmentId ? segments.findIndex((segment) => segment.id === evidence.segmentId) : -1
  return index >= 0 ? `Transcript, turn ${index + 1}` : 'Transcript'
}

function SuggestionCard({
  suggestion,
  segments,
  onDismiss,
}: {
  suggestion: ReviewSuggestion
  segments: TranscriptSegment[]
  onDismiss: (id: string) => void
}) {
  return (
    <li className="rounded-(--radius-panel) bg-sunken p-4">
      <div className="flex items-start justify-between gap-4">
        <p className="text-[15px] font-semibold text-ink">{suggestion.description}</p>
        <Button
          variant="ghost"
          className="-mt-1 -mr-2 h-8 px-2 text-sm"
          onClick={() => onDismiss(suggestion.id)}
          aria-label={`Dismiss suggestion: ${suggestion.description}`}
        >
          Dismiss
        </Button>
      </div>
      <p className="mt-1 text-[15px] text-ink-2">{suggestion.rationale}</p>
      {suggestion.gapType && <p className="mt-2 text-sm text-ink-2">{GAP_LABELS[suggestion.gapType]}</p>}
      {suggestion.missingInformation && (
        <p className="mt-2 text-sm text-ink-2">
          <span className="font-medium text-ink">Missing or pointing against: </span>
          {suggestion.missingInformation}
        </p>
      )}
      <ul className="mt-3 space-y-2" aria-label="Supporting text">
        {suggestion.evidence.map((evidence) => (
          <li key={`${evidence.source}-${evidence.quote}`} className="border-l-2 border-field pl-3 text-sm">
            <span className="text-ink-3">{evidenceLabel(evidence, segments)}: </span>
            <q className="text-ink">{evidence.quote}</q>
          </li>
        ))}
      </ul>
      <p className="mt-3 text-sm text-ink-3">Uncertainty: {UNCERTAINTY_LABELS[suggestion.uncertainty]}</p>
    </li>
  )
}

/**
 * Optional second opinion on the clinician's edited note. It runs only when asked, shows its
 * suggestions apart from the note, and never changes the note.
 */
export function ClinicalReviewPanel({
  review,
  version,
  segments,
  canRun,
  onRun,
  onCancel,
  onDismiss,
}: ClinicalReviewPanelProps) {
  const running = review.status === 'running'
  const result = review.result
  const stale = result !== null && result.basedOnVersion !== version

  return (
    <Panel className="px-6 py-6 sm:px-8 sm:py-7">
      <section aria-labelledby={CLINICAL_REVIEW_HEADING}>
        <div className="flex flex-col gap-4 sm:flex-row sm:items-start sm:justify-between">
          <div className="max-w-2xl">
            <h2
              id={CLINICAL_REVIEW_HEADING}
              tabIndex={-1}
              className="scroll-mt-56 lg:scroll-mt-4 text-lg font-semibold focus:outline-none"
            >
              Clinical review
            </h2>
            <p className="mt-1 text-[15px] text-ink-2">
              Optional. After you have reviewed and edited the transcript and note, run a second look. It uses your
              speaker labels and your current note, and suggests; it never changes the note.
            </p>
          </div>
          <Button variant="secondary" onClick={onRun} disabled={!canRun || running}>
            Run Clinical Review
          </Button>
        </div>

        <div className="mt-5 space-y-4">
          {running && (
            <Alert
              tone="info"
              action={
                <Button variant="secondary" onClick={onCancel}>
                  Cancel review
                </Button>
              }
            >
              <span className="flex items-center gap-2">
                <Spinner className="size-4" />
                Running the clinical review on this device. This can take a minute or two. You can keep editing.
              </span>
            </Alert>
          )}

          {review.status === 'failed' && review.message && <Alert tone="error">{review.message}</Alert>}

          {result && (
            <>
              <Alert tone="warning" title="AI-generated suggestions">
                These are suggestions for your clinical judgment. They are not part of the note, and nothing has been
                added to it.
              </Alert>
              {stale && (
                <Alert tone="info">
                  These suggestions are based on an earlier version. Your edits since then are not reflected. Run the
                  review again to refresh them.
                </Alert>
              )}

              {SECTIONS.map(({ key, title, empty }) => {
                const visible = result.review[key].filter((suggestion) => !review.dismissed.includes(suggestion.id))
                const dismissedCount = result.review[key].length - visible.length
                return (
                  <section key={key} aria-label={title}>
                    <h3 className="text-[17px] font-semibold">{title}</h3>
                    {visible.length > 0 ? (
                      <ul className="mt-3 space-y-3">
                        {visible.map((suggestion) => (
                          <SuggestionCard
                            key={suggestion.id}
                            suggestion={suggestion}
                            segments={segments}
                            onDismiss={onDismiss}
                          />
                        ))}
                      </ul>
                    ) : (
                      <p className="mt-2 text-[15px] text-ink-3">
                        {dismissedCount > 0 ? 'All suggestions here were dismissed.' : empty}
                      </p>
                    )}
                  </section>
                )
              })}

              {result.review.omittedUnverified > 0 && (
                <p className="text-sm text-ink-3">
                  {result.review.omittedUnverified}{' '}
                  {result.review.omittedUnverified === 1 ? 'suggestion was' : 'suggestions were'} left out because the
                  text it relied on could not be found in the transcript or note.
                </p>
              )}
            </>
          )}
        </div>
      </section>
    </Panel>
  )
}
