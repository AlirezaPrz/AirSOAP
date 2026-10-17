import type { DemoSubmitResponse } from '../api/types'
import { Button } from '../components/Button'
import { PageHeader, Panel } from '../components/ui'

interface CompletionScreenProps {
  submission: DemoSubmitResponse
  onReturn: () => void
}

export function CompletionScreen({ submission, onReturn }: CompletionScreenProps) {
  const submittedAt = new Date(submission.submittedAt).toLocaleString('en-US', {
    dateStyle: 'medium',
    timeStyle: 'short',
  })

  return (
    <>
      <PageHeader
        title="Demo submission complete"
        description="The reviewed note was accepted by the local demo endpoint."
        actions={
          <Button size="lg" onClick={onReturn}>
            Return to dashboard
          </Button>
        }
      />

      <div className="grid items-start gap-6 lg:grid-cols-[minmax(0,1fr)_360px]">
        <section className="rounded-(--radius-panel) bg-accent-soft p-6 text-accent-ink sm:p-10">
          <p className="text-[24px] leading-snug font-semibold tracking-[-0.01em] text-balance sm:text-[28px]">
            No external EMR is connected. No medical record has been created or modified.
          </p>
          <p className="mt-5 max-w-prose text-base">
            This prototype sends nothing outside this device. The patient details, transcript and SOAP note from this
            encounter have been cleared from memory.
          </p>
        </section>

        <Panel className="p-6 sm:p-8">
          <h2 className="text-base font-semibold">Demo receipt</h2>
          <dl className="mt-4 space-y-4 text-[15px]">
            <div>
              <dt className="text-sm text-ink-3">Reference</dt>
              <dd className="mt-0.5 font-medium tabular-nums">{submission.submissionId}</dd>
            </div>
            <div>
              <dt className="text-sm text-ink-3">Submitted</dt>
              <dd className="mt-0.5 font-medium">{submittedAt}</dd>
            </div>
            <div>
              <dt className="text-sm text-ink-3">Destination</dt>
              <dd className="mt-0.5 font-medium">Local demo endpoint, no EMR</dd>
            </div>
          </dl>
        </Panel>
      </div>
    </>
  )
}
