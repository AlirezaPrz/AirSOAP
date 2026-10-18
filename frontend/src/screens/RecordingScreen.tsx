import { useEffect, useId, useState } from 'react'
import { toApiError } from '../api/client'
import { Button } from '../components/Button'
import { ConfirmDialog } from '../components/ConfirmDialog'
import { LiveTranscript } from '../components/LiveTranscript'
import type { LiveHandlers } from '../api/client'
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
  /** Present while a live recording runs: follows its transcript. */
  followLive?: (handlers: LiveHandlers) => () => void
}

const GUIDANCE = [
  'Speak naturally. After the recording you mark which turns were yours and which were the patient’s.',
  'Say vitals and exam findings out loud, so they can go into the Objective section.',
  'Say your impression and plan out loud too. The draft only includes what was said.',
  'Avoid saying the patient’s full name or date of birth.',
  'Nothing is final: you review and edit everything before it is submitted.',
]

// Fixed, deterministic bar heights and timings for the simulated level meter. Enough bars to span
// the panel; a quieter envelope at both ends so the trace reads as a voice, not a block.
const BAR_COUNT = 96
const BARS = Array.from({ length: BAR_COUNT }, (_, i) => ({
  height: Math.round((22 + ((i * 47) % 78)) * (0.45 + 0.55 * Math.sin((Math.PI * (i + 0.5)) / BAR_COUNT))),
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
    <div aria-hidden className="flex h-full items-center justify-between gap-px overflow-hidden">
      {BARS.map((bar, i) => (
        <span
          key={i}
          className={`w-[3px] shrink-0 origin-center rounded-full max-sm:[&:nth-child(n+49)]:hidden ${active ? 'level-bar bg-accent' : 'bg-field'}`}
          style={{
            height: active ? `${bar.height}%` : '6%',
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
  followLive,
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
        <div className="flex min-w-0 flex-col items-center p-6 text-center sm:p-10">
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

          {/* The trace fills the room the guidance leaves, across the whole width of the panel. */}
          <div className="mt-8 h-20 w-full lg:h-auto lg:min-h-24 lg:flex-1">
            <LevelMeter active={recording} />
          </div>

          <div className="mt-8 flex w-full flex-col items-center gap-5 sm:flex-row sm:flex-wrap sm:justify-center">
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
                <div className="flex items-start gap-3 text-left">
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
            <div className="mt-6 w-full text-left">
              <Alert tone="error">{error}</Alert>
            </div>
          )}
        </div>

        {/* While a live transcript is on screen, a phone shows that first: the tips mattered before recording. */}
        <div className={`bg-sunken/70 p-6 sm:p-10 lg:p-8 ${recording && followLive ? 'hidden lg:block' : ''}`}>
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

      {recording && followLive && (
        <LiveTranscript subscribe={followLive} simulated={simulated || state.liveSimulated === true} />
      )}

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
