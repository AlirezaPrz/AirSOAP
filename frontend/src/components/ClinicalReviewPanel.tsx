import type { ClinicalReview, Evidence, InconsistencyKind, ReviewSuggestion, TranscriptSegment } from '../api/types'
import { SOAP_FIELD_META } from '../lib/format'
import type { ClinicalReviewState } from '../workflow/reducer'
import { Button } from './Button'
import { Spinner } from './icons'
import { Alert, Panel } from './ui'

const SECTIONS: { key: Exclude<keyof ClinicalReview, 'omittedUnverified'>; title: string; empty: string }[] = [
  { key: 'informationGaps', title: 'Information gaps', empty: 'No information gaps were suggested.' },
  {
    key: 'documentationInconsistencies',
    title: 'Documentation inconsistencies',
    empty: 'No differences between the transcript and the note were suggested.',
  },
]

const GAP_LABELS = {
  not_asked: 'May not have been asked in this transcript',
  asked_not_answered: 'Asked, but not clearly answered',
  omitted_from_note: 'In the transcript, but not in the note',
  uncertain: 'Status unclear',
} as const

const KIND_LABELS: Record<InconsistencyKind, string> = {
  number_or_unit: 'Number or unit',
  negation: 'Negation',
  laterality: 'Left or right',
  medication: 'Medication',
  plan: 'Plan',
  other: 'Other difference',
}

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
  const inconsistency = suggestion.category === 'documentation_inconsistency'
  const [said, written] = suggestion.evidence
  const kind = inconsistency
    ? suggestion.inconsistencyKind && KIND_LABELS[suggestion.inconsistencyKind]
    : suggestion.gapType && GAP_LABELS[suggestion.gapType]
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

      {suggestion.suggestedQuestion && (
        <p className="mt-3 rounded-(--radius-control) bg-surface px-3 py-2.5 text-[15px]">
          <span className="block text-sm text-ink-3">You could ask</span>
          <span className="text-ink">{suggestion.suggestedQuestion}</span>
        </p>
      )}

      {inconsistency && said && written ? (
        <dl className="mt-3 grid gap-2 sm:grid-cols-2">
          {[
            { term: 'Said', evidence: said },
            { term: 'Written', evidence: written },
          ].map(({ term, evidence }) => (
            <div key={term} className="rounded-(--radius-control) bg-surface px-3 py-2.5">
              <dt className="text-sm text-ink-3">
                <span className="font-medium text-ink-2">{term}</span> · {evidenceLabel(evidence, segments)}
              </dt>
              <dd className="mt-0.5 text-[15px] text-ink">
                <q>{evidence.quote}</q>
              </dd>
            </div>
          ))}
        </dl>
      ) : (
        <ul className="mt-3 space-y-2" aria-label="Supporting text">
          {suggestion.evidence.map((evidence) => (
            <li key={`${evidence.source}-${evidence.quote}`} className="border-l-2 border-field pl-3 text-sm">
              <span className="text-ink-3">{evidenceLabel(evidence, segments)}: </span>
              <q className="text-ink">{evidence.quote}</q>
            </li>
          ))}
        </ul>
      )}

      <p className="mt-3 text-sm text-ink-3">
        {kind && <>{kind} · </>}Uncertainty: {UNCERTAINTY_LABELS[suggestion.uncertainty]}
      </p>
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
              Optional. A second look at the documentation: details that seem missing or unclear, and places where
              the note differs from what was said. It uses your speaker labels and your current note, and suggests; it
              never changes the note.
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
                  {result.review.omittedUnverified === 1 ? 'suggestion was' : 'suggestions were'} left out because they
                  could not be checked against the transcript and note.
                </p>
              )}
            </>
          )}
        </div>
      </section>
    </Panel>
  )
}
