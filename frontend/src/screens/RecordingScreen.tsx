import { useEffect, useId, useState } from 'react'
import { toApiError } from '../api/client'
import { Button } from '../components/Button'
import { ConfirmDialog } from '../components/ConfirmDialog'
import { MicIcon, StopIcon } from '../components/icons'
import { Alert, PageHeader, Panel } from '../components/ui'
import { formatDuration } from '../lib/format'
import type { StateOf } from '../workflow/reducer'

interface RecordingScreenProps {
  state: StateOf<'ready'> | StateOf<'recording'>
  /** True with the mock API: no microphone is used. */
  simulated: boolean
  onStartRecording: () => Promise<void>
  onStopRecording: () => Promise<void>
  onCancel: () => void
}

const GUIDANCE = [
  'Speak naturally. After the recording you mark which turns were yours and which were the patient’s.',
  'Say vitals and exam findings out loud, so they can go into the Objective section.',
  'Say your impression and plan out loud too. The draft only includes what was said.',
  'Avoid saying the patient’s full name or date of birth.',
  'Nothing is final: you review and edit everything before it is submitted.',
]

// Fixed, deterministic bar heights and timings for the simulated level meter.
const BARS = Array.from({ length: 48 }, (_, i) => ({
  height: 30 + ((i * 47) % 70),
  duration: 700 + ((i * 131) % 600),
  delay: (i * 53) % 400,
}))

function useElapsed(startedAt: number | null) {
  const [now, setNow] = useState(() => Date.now())
  useEffect(() => {
    if (startedAt === null) return
    const timer = setInterval(() => setNow(Date.now()), 500)
    return () => clearInterval(timer)
  }, [startedAt])
  return startedAt === null ? 0 : Math.max(0, now - startedAt)
}

function LevelMeter({ active }: { active: boolean }) {
  return (
    <div aria-hidden className="flex h-14 items-center gap-[3px] overflow-hidden">
      {BARS.map((bar, i) => (
        <span
          key={i}
          className={`max-w-1 min-w-0 flex-1 origin-center rounded-full ${active ? 'level-bar bg-accent' : 'bg-field'}`}
          style={{
            height: active ? `${bar.height}%` : '12%',
            animationDuration: `${bar.duration}ms`,
            animationDelay: `${bar.delay}ms`,
          }}
        />
      ))}
    </div>
  )
}

export function RecordingScreen({
  state,
  simulated,
  onStartRecording,
  onStopRecording,
  onCancel,
}: RecordingScreenProps) {
  const recording = state.status === 'recording'
  const [consent, setConsent] = useState(false)
  const [pending, setPending] = useState(false)
  const [error, setError] = useState<string | null>(null)
  const [confirmCancel, setConfirmCancel] = useState(false)
  const elapsed = useElapsed(recording ? state.startedAt : null)
  const consentId = useId()

  const run = async (action: () => Promise<void>, fallback: string) => {
    setPending(true)
    setError(null)
    try {
      await action()
    } catch (err) {
      setError(toApiError(err, { code: 'RECORDING_FAILED', message: fallback, retryable: true }).message)
    } finally {
      setPending(false)
    }
  }

  return (
    <>
      <PageHeader
        title={recording ? 'Recording in progress' : 'Ready to record'}
        description={
          simulated
            ? 'Audio capture is simulated in this prototype. No microphone is accessed.'
            : 'The microphone on this device records the visit. The audio never leaves the device and is deleted once it is transcribed.'
        }
        actions={
          <Button variant="secondary" onClick={() => setConfirmCancel(true)} disabled={pending}>
            Cancel encounter
          </Button>
        }
      />

      <Panel className="grid grid-cols-1 overflow-hidden lg:grid-cols-[minmax(0,1fr)_340px]">
        <div className="min-w-0 p-6 sm:p-10">
          <p className={`flex items-center gap-2 text-[15px] font-medium ${recording ? 'text-danger' : 'text-ink-3'}`}>
            <span aria-hidden className="relative flex size-2.5">
              {recording && <span className="absolute inset-0 animate-ping rounded-full bg-danger/60" />}
              <span className={`relative size-2.5 rounded-full ${recording ? 'bg-danger' : 'bg-field'}`} />
            </span>
            {recording ? 'Recording' : 'Not recording'}
          </p>
          <p
            role="timer"
            aria-label="Elapsed recording time"
            className={`mt-2 text-[64px] leading-none font-semibold tracking-[-0.03em] tabular-nums sm:text-[80px] ${
              recording ? 'text-ink' : 'text-ink-3/50'
            }`}
          >
            {formatDuration(elapsed)}
          </p>

          <div className="mt-8">
            <LevelMeter active={recording} />
          </div>

          <div className="mt-8 flex flex-col gap-5 sm:flex-row sm:items-center">
            {recording ? (
              <Button
                variant="danger"
                size="lg"
                loading={pending}
                onClick={() => run(onStopRecording, 'Could not stop the recording. Please try again.')}
              >
                {!pending && <StopIcon className="size-4" />}
                Stop and transcribe
              </Button>
            ) : (
              <>
                <Button
                  size="lg"
                  disabled={!consent}
                  loading={pending}
                  onClick={() => run(onStartRecording, 'Could not start the recorder. Please try again.')}
                >
                  {!pending && <MicIcon className="size-4" />}
                  Start recording
                </Button>
                <div className="flex items-start gap-3">
                  <input
                    id={consentId}
                    type="checkbox"
                    checked={consent}
                    onChange={(e) => setConsent(e.target.checked)}
                    className="mt-0.5 size-[18px] shrink-0 accent-accent"
                  />
                  <label htmlFor={consentId} className="text-[15px] text-ink">
                    The patient has consented to this visit being recorded.
                  </label>
                </div>
              </>
            )}
          </div>

          {error && (
            <div className="mt-6">
              <Alert tone="error">{error}</Alert>
            </div>
          )}
        </div>

        <div className="bg-sunken/70 p-6 sm:p-10 lg:p-8">
          <h2 className="text-base font-semibold">For a better draft</h2>
          <ul className="mt-4 space-y-3.5 text-[15px] text-ink-2">
            {GUIDANCE.map((tip) => (
              <li key={tip} className="flex gap-3">
                <span aria-hidden className="mt-2 size-1.5 shrink-0 rounded-full bg-accent" />
                {tip}
              </li>
            ))}
          </ul>
        </div>
      </Panel>

      {confirmCancel && (
        <ConfirmDialog
          title="Cancel this encounter?"
          tone="danger"
          confirmLabel="Discard encounter"
          cancelLabel="Keep going"
          onConfirm={onCancel}
          onCancel={() => setConfirmCancel(false)}
        >
          <p>
            {recording ? 'The recording will be stopped and discarded. ' : ''}
            Patient details will be cleared and you will return to the dashboard.
          </p>
        </ConfirmDialog>
      )}
    </>
  )
}
