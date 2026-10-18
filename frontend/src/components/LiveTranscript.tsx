import { useEffect, useLayoutEffect, useRef, useState } from 'react'
import type { LiveHandlers } from '../api/client'
import type { LiveSnapshot, LiveStatus } from '../api/types'
import { formatDuration } from '../lib/format'
import { AlertIcon, ChevronDownIcon, Spinner } from './icons'
import { Panel } from './ui'

type Subscribe = (handlers: LiveHandlers) => () => void

const STATUS: Record<LiveStatus, { text: string; tone: 'calm' | 'busy' | 'warn' }> = {
  starting: { text: 'Getting the speech model ready…', tone: 'busy' },
  listening: { text: 'Listening. Text appears a few seconds after each pause.', tone: 'calm' },
  transcribing: { text: 'Writing down what was just said…', tone: 'busy' },
  behind: { text: 'Running behind. Nothing is lost; it catches up during pauses.', tone: 'warn' },
  failed: {
    text: 'Live text has stopped. The recording continues, and the whole visit is transcribed when you stop.',
    tone: 'warn',
  },
  finishing: { text: 'Finishing the last part…', tone: 'busy' },
  done: { text: 'Finished.', tone: 'calm' },
}

/** Close enough to the bottom to keep following new text. */
const STICK_PX = 48

/**
 * The transcript as it is produced while recording. Read only: the clinician checks the words
 * and marks who spoke after stopping. New lines never move the view while the clinician has
 * scrolled up to read something.
 */
export function LiveTranscript({ subscribe, simulated }: { subscribe: Subscribe; simulated: boolean }) {
  const [snapshot, setSnapshot] = useState<LiveSnapshot | null>(null)
  const [connection, setConnection] = useState<'open' | 'reconnecting'>('open')
  const [following, setFollowing] = useState(true)
  const [seen, setSeen] = useState(0)
  const listRef = useRef<HTMLOListElement>(null)

  useEffect(
    () =>
      subscribe({
        onSnapshot: (next) => {
          setConnection('open')
          setSnapshot(next)
        },
        onConnection: setConnection,
      }),
    [subscribe],
  )

  const segments = snapshot?.segments ?? []
  const count = segments.length

  useLayoutEffect(() => {
    const list = listRef.current
    if (!list || !following) return
    list.scrollTop = list.scrollHeight
  }, [count, following])

  const visibleCount = following ? count : seen
  const unseen = count - visibleCount

  const onScroll = () => {
    const list = listRef.current
    if (!list) return
    const atBottom = list.scrollHeight - list.scrollTop - list.clientHeight < STICK_PX
    if (atBottom !== following) {
      setFollowing(atBottom)
      if (!atBottom) setSeen(count)
    }
  }

  const jumpToLatest = () => {
    setFollowing(true)
    const list = listRef.current
    list?.scrollTo({ top: list.scrollHeight, behavior: 'smooth' })
  }

  const status = connection === 'reconnecting' ? null : STATUS[snapshot?.status ?? 'starting']
  const behindSeconds = snapshot && snapshot.status === 'behind' ? Math.round(snapshot.behindMs / 1000) : null

  return (
    <Panel className="mt-6 px-6 py-6 sm:px-8">
      <section aria-labelledby="live-transcript-heading">
        <div className="flex flex-wrap items-baseline justify-between gap-x-4 gap-y-1">
          <h2 id="live-transcript-heading" className="text-lg font-semibold">
            Live transcript
            {simulated && <span className="ml-2 text-sm font-normal text-ink-3">(simulated)</span>}
          </h2>
          <p className="text-sm text-ink-3 tabular-nums">
            {count} {count === 1 ? 'line' : 'lines'}
          </p>
        </div>

        <p
          className={`mt-2 flex items-start gap-2 text-[15px] ${
            status?.tone === 'warn' || connection === 'reconnecting' ? 'text-warn-ink' : 'text-ink-2'
          }`}
          aria-live="polite"
        >
          {connection === 'reconnecting' ? (
            <>
              <Spinner className="mt-0.5 size-4 shrink-0" /> Reconnecting… The recording is not affected.
            </>
          ) : (
            <>
              {status?.tone === 'busy' && <Spinner className="mt-0.5 size-4 shrink-0 text-accent" />}
              {status?.tone === 'warn' && <AlertIcon className="mt-0.5 size-4 shrink-0" />}
              {status?.tone === 'calm' && (
                <span aria-hidden className="relative mt-1.5 flex size-2 shrink-0">
                  <span className="absolute inset-0 animate-ping rounded-full bg-accent/50" />
                  <span className="relative size-2 rounded-full bg-accent" />
                </span>
              )}
              <span>
                {status?.text}
                {behindSeconds !== null && ` (${behindSeconds} s waiting)`}
              </span>
            </>
          )}
        </p>

        <div className="relative mt-4">
          <ol
            ref={listRef}
            onScroll={onScroll}
            role="log"
            aria-label="Live transcript lines"
            className="max-h-80 space-y-3 overflow-y-auto rounded-(--radius-control) bg-sunken px-4 py-4"
          >
            {count === 0 && (
              <li className="text-[15px] text-ink-3">
                Nothing yet. The first words appear here shortly after someone pauses.
              </li>
            )}
            {segments.map((segment) => (
              <li key={segment.id} className="arrive grid grid-cols-[3.5rem_minmax(0,1fr)] gap-3 text-[15px]">
                <span className="pt-0.5 text-sm text-ink-3 tabular-nums">{formatDuration(segment.startMs)}</span>
                <span className="text-ink">{segment.text}</span>
              </li>
            ))}
          </ol>
          {unseen > 0 && (
            <button
              type="button"
              onClick={jumpToLatest}
              className="toast-in absolute bottom-3 left-1/2 flex -translate-x-1/2 items-center gap-1.5 rounded-full bg-brand-navy px-3.5 py-1.5 text-sm font-medium text-white shadow-[0_8px_20px_-8px_rgb(9_48_75/0.6)] focus-visible:outline-2 focus-visible:outline-offset-2 focus-visible:outline-accent"
            >
              {unseen} new {unseen === 1 ? 'line' : 'lines'}
              <ChevronDownIcon className="size-4" />
            </button>
          )}
        </div>

        <p className="mt-3 text-sm text-ink-3">
          This is the transcript of the visit. After you stop, you check the words and mark who spoke each turn.
        </p>
      </section>
    </Panel>
  )
}
