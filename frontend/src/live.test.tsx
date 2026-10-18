import { act, render, screen, waitFor, within } from '@testing-library/react'
import userEvent from '@testing-library/user-event'
import { describe, expect, it, vi } from 'vitest'
import App from './App'
import type { LiveHandlers } from './api/client'
import { createHttpApi, type LiveEventSource } from './api/httpClient'
import { createMockApi } from './api/mockClient'
import type { LiveSnapshot } from './api/types'
import { LiveTranscript } from './components/LiveTranscript'
import { createMockTranscript } from './mocks/encounter'
import { initialWorkflowState, workflowReducer, type Draft, type WorkflowAction, type WorkflowState } from './workflow/reducer'

const transcript = createMockTranscript()
const encounter = {
  id: 'enc-1',
  patient: { fullName: 'Jordan Testpatient', dateOfBirth: '1991-04-12' },
  encounterType: 'acute' as const,
}
const toReview: WorkflowAction[] = [
  { type: 'START_ENCOUNTER', encounter },
  { type: 'RECORDING_STARTED', encounterId: 'enc-1', recordingId: 'rec-1', startedAt: 0, live: true },
  { type: 'RECORDING_STOPPED', recordingId: 'rec-1' },
  { type: 'TRANSCRIPT_READY', transcript },
]
const run = (actions: WorkflowAction[], from: WorkflowState = initialWorkflowState) => actions.reduce(workflowReducer, from)
const draftOf = (state: WorkflowState): Draft => {
  if (state.status !== 'review') throw new Error(`expected review, got ${state.status}`)
  return state.draft
}
const sentText = Object.fromEntries(transcript.segments.map((s) => [s.id, s.text]))
const [first, second, third] = transcript.segments

describe('speaker suggestions in the workflow', () => {
  it('fills labels the clinician has not set, marked as AI', () => {
    const state = run([
      ...toReview,
      { type: 'SPEAKERS_REQUESTED', requestId: 7 },
      {
        type: 'SPEAKERS_SUCCEEDED',
        requestId: 7,
        sentText,
        suggestions: [
          { id: first.id, speaker: 'clinician' },
          { id: second.id, speaker: 'patient' },
        ],
      },
    ])
    const [a, b] = draftOf(state).transcript.segments
    expect([a.speaker, a.speakerSource]).toEqual(['clinician', 'ai'])
    expect([b.speaker, b.speakerSource]).toEqual(['patient', 'ai'])
    expect(draftOf(state).speakers.applied).toBe(2)
  })

  it('never replaces a label the clinician chose, or a turn they edited after asking', () => {
    const state = run([
      ...toReview,
      { type: 'SPEAKERS_REQUESTED', requestId: 7 },
      { type: 'EDIT_TRANSCRIPT_SEGMENT', segmentId: first.id, changes: { speaker: 'patient' } },
      { type: 'EDIT_TRANSCRIPT_SEGMENT', segmentId: second.id, changes: { text: 'Corrected words.' } },
      {
        type: 'SPEAKERS_SUCCEEDED',
        requestId: 7,
        sentText,
        suggestions: [
          { id: first.id, speaker: 'clinician' },
          { id: second.id, speaker: 'clinician' },
          { id: third.id, speaker: 'unknown' },
        ],
      },
    ])
    const [a, b, c] = draftOf(state).transcript.segments
    expect([a.speaker, a.speakerSource]).toEqual(['patient', 'clinician'])
    expect(b.speaker).toBe('unknown')
    expect(c.speakerSource).toBeUndefined()
    expect(draftOf(state).speakers.applied).toBe(0)
  })

  it('ignores an answer to an older request', () => {
    const state = run([
      ...toReview,
      { type: 'SPEAKERS_REQUESTED', requestId: 7 },
      { type: 'SPEAKERS_CANCELLED' },
      { type: 'SPEAKERS_REQUESTED', requestId: 8 },
      { type: 'SPEAKERS_SUCCEEDED', requestId: 7, sentText, suggestions: [{ id: first.id, speaker: 'clinician' }] },
    ])
    expect(draftOf(state).transcript.segments[0].speaker).toBe('unknown')
    expect(draftOf(state).speakers.status).toBe('running')
  })
})

/** A stand-in for the browser's EventSource. */
function fakeEventSource() {
  const sources: FakeSource[] = []
  class FakeSource implements LiveEventSource {
    onopen: (() => void) | null = null
    onerror: (() => void) | null = null
    closed = false
    listeners: ((event: MessageEvent<string>) => void)[] = []
    readonly url: string
    constructor(url: string) {
      this.url = url
      sources.push(this)
    }
    addEventListener(_type: 'snapshot', listener: (event: MessageEvent<string>) => void) {
      this.listeners.push(listener)
    }
    send(snapshot: Partial<LiveSnapshot>) {
      const data = JSON.stringify({ recordingId: 'r', revision: 1, status: 'listening', behindMs: 0, segments: [], ...snapshot })
      for (const listener of this.listeners) listener({ data } as MessageEvent<string>)
    }
    close() {
      this.closed = true
    }
  }
  return { sources, factory: (url: string) => new FakeSource(url) }
}

describe('following the live stream', () => {
  it('delivers each snapshot and stops when the recording ends', () => {
    const { sources, factory } = fakeEventSource()
    const api = createHttpApi({ eventSource: factory })
    const snapshots: LiveSnapshot[] = []
    const states: string[] = []
    api.followLive!('a'.repeat(32), { onSnapshot: (s) => snapshots.push(s), onConnection: (s) => states.push(s) })
    const [source] = sources
    expect(source.url).toBe(`/api/recording/${'a'.repeat(32)}/live`)
    source.onopen?.()
    source.send({ revision: 1, segments: [{ id: 'seg-1', startMs: 0, endMs: 1000, text: 'Good morning.' }] })
    source.onerror?.()
    source.send({ revision: 2, status: 'finishing' })
    expect(snapshots.map((s) => s.revision)).toEqual([1, 2])
    expect(states).toEqual(['open', 'reconnecting'])
    expect(source.closed).toBe(true) // no reconnecting to a finished recording
  })

  it('closes the stream when the screen stops listening', () => {
    const { sources, factory } = fakeEventSource()
    const stop = createHttpApi({ eventSource: factory }).followLive!('b'.repeat(32), { onSnapshot: () => {} })
    stop()
    expect(sources[0].closed).toBe(true)
  })
})

describe('the live transcript panel', () => {
  function withSubscription() {
    let handlers: LiveHandlers | null = null
    const subscribe = vi.fn((h: LiveHandlers) => {
      handlers = h
      return () => {}
    })
    const push = (snapshot: Partial<LiveSnapshot>) =>
      act(() =>
        handlers!.onSnapshot({ recordingId: 'r', revision: 1, status: 'listening', behindMs: 0, segments: [], ...snapshot }),
      )
    return { subscribe, push, handlers: () => handlers! }
  }

  it('shows lines as they arrive, read only, with the status in words', () => {
    const { subscribe, push } = withSubscription()
    render(<LiveTranscript subscribe={subscribe} simulated={false} />)
    expect(screen.getByText(/getting the speech model ready/i)).toBeInTheDocument()
    push({ segments: [{ id: 'seg-1', startMs: 4000, endMs: 6000, text: 'Good morning. What brings you in?' }] })
    const log = screen.getByRole('log', { name: 'Live transcript lines' })
    expect(within(log).getByText('Good morning. What brings you in?')).toBeInTheDocument()
    expect(within(log).getByText('00:04')).toBeInTheDocument()
    expect(within(log).queryByRole('textbox')).not.toBeInTheDocument()
    expect(screen.getByText(/text appears a few seconds after each pause/i)).toBeInTheDocument()
  })

  it('says plainly when it is behind, when it failed and when it is reconnecting', () => {
    const { subscribe, push, handlers } = withSubscription()
    render(<LiveTranscript subscribe={subscribe} simulated={false} />)
    push({ status: 'behind', behindMs: 18_000 })
    expect(screen.getByText(/running behind/i)).toHaveTextContent('18 s waiting')
    push({ status: 'failed' })
    expect(screen.getByText(/live text has stopped/i)).toHaveTextContent(/transcribed when you stop/)
    act(() => handlers().onConnection?.('reconnecting'))
    expect(screen.getByText(/reconnecting/i)).toHaveTextContent('The recording is not affected.')
  })
})

describe('an encounter with live transcription (demo backend)', () => {
  it('shows the transcript while recording, then suggests speakers in review', async () => {
    const user = userEvent.setup()
    render(<App api={createMockApi({ latencyScale: 0.02 })} />)
    await user.click(screen.getByRole('button', { name: 'Use sample patient' }))
    await user.click(screen.getByRole('button', { name: 'Start encounter' }))
    await user.click(await screen.findByRole('checkbox', { name: /consented/i }))
    await user.click(screen.getByRole('button', { name: /start recording/i }))

    const log = await screen.findByRole('log', { name: 'Live transcript lines' })
    await waitFor(() => expect(within(log).getAllByRole('listitem').length).toBeGreaterThan(1))
    expect(screen.getByRole('heading', { name: /live transcript/i })).toHaveTextContent('(simulated)')

    await user.click(screen.getByRole('button', { name: /stop and transcribe/i }))
    await screen.findByRole('heading', { level: 1, name: 'Review draft note' }, { timeout: 3000 })

    await user.click(screen.getByRole('button', { name: 'Suggest speakers' }))
    await screen.findByText(/marked/i, {}, { timeout: 3000 })
    const segments = screen.getByRole('list', { name: 'Transcript segments' })
    expect(within(segments).getAllByText('AI').length).toBeGreaterThan(0)

    const accept = screen.getByRole('button', { name: /accept \d+ ai label/i })
    await user.click(accept)
    expect(within(segments).queryAllByText('AI')).toHaveLength(0)
  })
})
