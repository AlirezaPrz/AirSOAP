import { useState } from 'react'
import type { ProcessingStage } from '../api/types'
import { Button } from '../components/Button'
import { ConfirmDialog } from '../components/ConfirmDialog'
import { AlertIcon, CheckIcon, Spinner } from '../components/icons'
import { Alert, PageHeader, Panel, ProgressBar } from '../components/ui'
import { STAGE_LABELS } from '../lib/format'
import type { StateOf } from '../workflow/reducer'

type StepStatus = 'pending' | 'active' | 'done' | 'failed'

const STAGES: ProcessingStage[] = ['transcription']

const STAGE_DESCRIPTIONS: Record<ProcessingStage, string> = {
  transcription: 'Converting the whole recording to text on this device.',
}

const STATUS_TEXT: Record<StepStatus, string> = {
  pending: 'Waiting',
  active: '',
  done: 'Complete',
  failed: 'Failed',
}

function StageRow({
  stage,
  status,
  progress,
}: {
  stage: ProcessingStage
  status: StepStatus
  progress: number | null
}) {
  return (
    <li className="flex gap-5">
      <span
        aria-hidden
        className={`flex size-9 shrink-0 items-center justify-center rounded-full ${
          status === 'done'
            ? 'bg-accent text-white'
            : status === 'active'
              ? 'bg-accent-soft text-accent'
              : status === 'failed'
                ? 'bg-danger-soft text-danger'
                : 'bg-sunken text-ink-3'
        }`}
      >
        {status === 'done' && <CheckIcon className="size-4" strokeWidth={2.5} />}
        {status === 'active' && <Spinner className="size-4" />}
        {status === 'failed' && <AlertIcon className="size-4" />}
        {status === 'pending' && <span className="size-2 rounded-full bg-field" />}
      </span>
      <div className="min-w-0 flex-1 pt-1">
        <div className="flex items-baseline justify-between gap-4">
          <p className={`text-[17px] font-semibold ${status === 'pending' ? 'text-ink-3' : 'text-ink'}`}>
            {STAGE_LABELS[stage]}
          </p>
          <p
            className={`text-[15px] tabular-nums ${
              status === 'failed' ? 'font-medium text-danger' : status === 'active' ? 'font-medium text-ink' : 'text-ink-3'
            }`}
          >
            {status !== 'active' ? STATUS_TEXT[status] : progress === null ? 'Working…' : `${Math.round(progress * 100)}%`}
          </p>
        </div>
        <p className="mt-1 text-[15px] text-ink-2">{STAGE_DESCRIPTIONS[stage]}</p>
        <div className="mt-4">
          <ProgressBar
            label={STAGE_LABELS[stage]}
            tone={status === 'failed' ? 'danger' : 'accent'}
            value={status === 'done' ? 1 : status === 'active' ? progress : status === 'failed' ? 0.5 : 0}
          />
        </div>
      </div>
    </li>
  )
}

function stageStatus(stage: ProcessingStage, current: ProcessingStage, failed: boolean): StepStatus {
  const index = STAGES.indexOf(stage)
  const currentIndex = STAGES.indexOf(current)
  if (index < currentIndex) return 'done'
  if (index > currentIndex) return 'pending'
  return failed ? 'failed' : 'active'
}

interface ProcessingScreenProps {
  state: StateOf<'processing'> | StateOf<'processing-error'>
  onRetry: () => void
  onCancel: () => void
}

export function ProcessingScreen({ state, onRetry, onCancel }: ProcessingScreenProps) {
  const [confirmCancel, setConfirmCancel] = useState(false)
  const failed = state.status === 'processing-error'
  const stage = failed ? state.error.stage : state.stage
  const progress = failed ? 0 : state.progress

  return (
    <>
      <PageHeader
        title={failed ? 'Processing failed' : 'Processing the recording'}
        description={
          failed
            ? 'The recording is safe. Retry, or discard this encounter.'
            : 'Whisper is turning the recording into text on this device. Longer recordings take longer; keep this page open.'
        }
        actions={
          <>
            <Button variant="secondary" onClick={() => setConfirmCancel(true)}>
              {failed ? 'Discard encounter' : 'Cancel'}
            </Button>
            {failed && state.error.retryable && <Button onClick={onRetry}>Retry</Button>}
          </>
        }
      />

      <Panel className="p-6 sm:p-10">
        {failed && (
          <div className="mb-8">
            <Alert tone="error" title={`${STAGE_LABELS[state.error.stage]} failed`}>
              <p>{state.error.message}</p>
            </Alert>
          </div>
        )}

        <p className="sr-only" aria-live="polite">
          {failed ? '' : `${STAGE_LABELS[stage]} in progress`}
        </p>

        <ol className="space-y-10">
          {STAGES.map((s) => (
            <StageRow key={s} stage={s} status={stageStatus(s, stage, failed)} progress={progress} />
          ))}
        </ol>

        {state.attempt > 1 && !failed && <p className="mt-8 text-sm text-ink-3">Retry attempt {state.attempt}</p>}
      </Panel>

      <section className="mt-6 rounded-(--radius-panel) bg-accent-soft px-6 py-6 text-accent-ink sm:px-10">
        <h2 className="text-base font-semibold">What happens next</h2>
        <p className="mt-1 text-[15px]">
          You check the transcript and mark who spoke each turn. Then you ask for the SOAP draft, edit it, and, if you
          want a second look, run the optional clinical review.
        </p>
      </section>

      {confirmCancel && (
        <ConfirmDialog
          title={failed ? 'Discard this encounter?' : 'Cancel processing?'}
          tone="danger"
          confirmLabel="Discard encounter"
          cancelLabel="Keep going"
          onConfirm={onCancel}
          onCancel={() => setConfirmCancel(false)}
        >
          <p>The recording and any partial transcript will be discarded, and you will return to the dashboard.</p>
        </ConfirmDialog>
      )}
    </>
  )
}
