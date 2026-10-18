import { describe, expect, it } from 'vitest'
import type { ClinicalReview, DemoSubmitResponse, SoapNote } from '../api/types'
import { createMockSoap, createMockTranscript } from '../mocks/encounter'
import {
  initialWorkflowState,
  workflowReducer,
  type Draft,
  type Encounter,
  type WorkflowAction,
  type WorkflowState,
} from './reducer'

const encounter: Encounter = {
  id: 'enc-1',
  patient: { fullName: 'Jordan Testpatient', dateOfBirth: '1991-04-12' },
  encounterType: 'acute',
}
const transcript = createMockTranscript()
const aiSoap = createMockSoap({ patientAge: 35, encounterType: 'acute' })
const submission: DemoSubmitResponse = {
  submissionId: 'demo-1',
  submittedAt: '2026-10-17T10:00:00.000Z',
  emrConnected: false,
  recordModified: false,
}
const emptyReview = (overrides: Partial<ClinicalReview> = {}): ClinicalReview => ({
  informationGaps: [],
  documentationInconsistencies: [],
  omittedUnverified: 0,
  ...overrides,
})
const suggestion = (id: string) => ({
  id,
  category: 'information_gap' as const,
  description: `Question ${id}?`,
  rationale: 'Because.',
  uncertainty: 'low' as const,
  evidence: [{ source: 'transcript' as const, quote: 'sore throat', segmentId: 'seg-2' }],
})

const run = (actions: WorkflowAction[], from: WorkflowState = initialWorkflowState) =>
  actions.reduce(workflowReducer, from)

function draftOf(state: WorkflowState): Draft {
  if (state.status !== 'review' && state.status !== 'confirming' && state.status !== 'submitting') {
    throw new Error(`expected a draft, got ${state.status}`)
  }
  return state.draft
}

const toRecording: WorkflowAction[] = [
  { type: 'START_ENCOUNTER', encounter },
  { type: 'RECORDING_STARTED', encounterId: 'enc-1', recordingId: 'rec-1', startedAt: 1000 },
]
const toProcessing: WorkflowAction[] = [...toRecording, { type: 'RECORDING_STOPPED', recordingId: 'rec-1' }]
/** Transcription done: the clinician is reviewing, and no SOAP note exists yet. */
const toReview: WorkflowAction[] = [...toProcessing, { type: 'TRANSCRIPT_READY', transcript }]
/** The SOAP note has been generated and applied. */
const toDrafted: WorkflowAction[] = [
  ...toReview,
  { type: 'SOAP_REQUESTED', requestId: 1 },
  { type: 'SOAP_SUCCEEDED', requestId: 1, soap: aiSoap, warnings: [] },
]
const toConfirming: WorkflowAction[] = [...toDrafted, { type: 'REQUEST_SUBMIT' }]
const toCompleted: WorkflowAction[] = [
  ...toConfirming,
  { type: 'CONFIRM_SUBMIT' },
  { type: 'SUBMIT_SUCCEEDED', submission },
]

describe('workflowReducer — happy path', () => {
  it('walks idle → ready → recording → processing → review → confirming → submitting → completed', () => {
    const statuses: string[] = []
    let state = initialWorkflowState
    for (const action of toCompleted) {
      state = workflowReducer(state, action)
      statuses.push(state.status)
    }
    expect(statuses).toEqual([
      'ready',
      'recording',
      'processing',
      'review', // transcription finished
      'review', // SOAP requested
      'review', // SOAP applied
      'confirming',
      'submitting',
      'completed',
    ])
  })

  it('goes straight from transcription to review with an empty note and unknown speakers', () => {
    const draft = draftOf(run(toReview))
    expect(draft.soap).toEqual({ subjective: '', objective: '', assessment: '', plan: '' })
    expect(draft.transcript.segments.every((segment) => segment.speaker === 'unknown')).toBe(true)
    expect(draft.soapGeneration).toEqual({ status: 'idle' })
    expect(draft.review).toMatchObject({ status: 'idle', result: null, dismissed: [] })
  })

  it('reports transcription progress, including unknown progress, and clamps it', () => {
    const processing = run(toProcessing)
    const half = workflowReducer(processing, { type: 'PROCESSING_PROGRESS', stage: 'transcription', progress: 0.5 })
    expect(half).toMatchObject({ stage: 'transcription', progress: 0.5 })
    expect(workflowReducer(processing, { type: 'PROCESSING_PROGRESS', stage: 'transcription', progress: 4 })).toMatchObject({
      progress: 1,
    })
    expect(workflowReducer(processing, { type: 'PROCESSING_PROGRESS', stage: 'transcription', progress: null })).toMatchObject({
      progress: null,
    })
  })

  it('drops all encounter data on completion', () => {
    const state = run(toCompleted)
    expect(state).toEqual({ status: 'completed', submission })
    expect(JSON.stringify(state)).not.toContain('Jordan')
  })

  it('returns to a fresh idle state from completed', () => {
    expect(run([...toCompleted, { type: 'RETURN_TO_DASHBOARD' }])).toEqual({ status: 'idle' })
  })
})

describe('workflowReducer — edits', () => {
  it('edits a single SOAP field without touching the others', () => {
    const before = run(toDrafted)
    const after = workflowReducer(before, { type: 'EDIT_SOAP_FIELD', field: 'plan', value: 'Revised plan' })
    expect(draftOf(after).soap.plan).toBe('Revised plan')
    expect(draftOf(after).soap.subjective).toBe(draftOf(before).soap.subjective)
    expect(draftOf(before).soap.plan).toBe(aiSoap.plan) // previous state not mutated
  })

  it('lets the clinician label speakers, and keeps the labels', () => {
    const state = run([
      ...toReview,
      { type: 'EDIT_TRANSCRIPT_SEGMENT', segmentId: 'seg-2', changes: { speaker: 'patient' } },
      { type: 'EDIT_TRANSCRIPT_SEGMENT', segmentId: 'seg-1', changes: { text: 'Corrected', speaker: 'clinician' } },
    ])
    const { segments } = draftOf(state).transcript
    expect(segments[0]).toMatchObject({ text: 'Corrected', speaker: 'clinician' })
    expect(segments[1]).toMatchObject({ speaker: 'patient', text: transcript.segments[1].text })
    expect(segments[2]).toEqual(transcript.segments[2]) // untouched
  })

  it('alternates speakers as a pattern that AI suggestions may refine, unlike clinician labels', () => {
    const alternated = run([...toReview, { type: 'ALTERNATE_SPEAKERS', first: 'patient' }])
    const { segments } = draftOf(alternated).transcript
    expect(segments.slice(0, 3).map((segment) => segment.speaker)).toEqual(['patient', 'clinician', 'patient'])
    expect(segments.every((segment) => segment.speakerSource === 'alternate')).toBe(true)

    const sentText = Object.fromEntries(segments.map((segment) => [segment.id, segment.text]))
    const suggested = run(
      [
        { type: 'EDIT_TRANSCRIPT_SEGMENT', segmentId: 'seg-2', changes: { speaker: 'patient' } },
        { type: 'SPEAKERS_REQUESTED', requestId: 1 },
        {
          type: 'SPEAKERS_SUCCEEDED',
          requestId: 1,
          sentText,
          suggestions: [
            { id: 'seg-1', speaker: 'clinician' },
            { id: 'seg-2', speaker: 'clinician' },
          ],
        },
      ],
      alternated,
    )
    const [first, second] = draftOf(suggested).transcript.segments
    expect(first).toMatchObject({ speaker: 'clinician', speakerSource: 'ai' })
    expect(second).toMatchObject({ speaker: 'patient', speakerSource: 'clinician' })
  })

  it('ignores edits outside the review state', () => {
    const confirming = run(toConfirming)
    expect(workflowReducer(confirming, { type: 'EDIT_SOAP_FIELD', field: 'plan', value: 'x' })).toBe(confirming)
  })

  it('blocks submit until every SOAP section has text', () => {
    expect(workflowReducer(run(toReview), { type: 'REQUEST_SUBMIT' })).toMatchObject({ status: 'review' })
    const blank = run([...toDrafted, { type: 'EDIT_SOAP_FIELD', field: 'assessment', value: '   ' }])
    expect(workflowReducer(blank, { type: 'REQUEST_SUBMIT' })).toBe(blank)
  })

  it('returns from confirming to review with the draft intact', () => {
    const state = run([...toConfirming, { type: 'CANCEL_SUBMIT' }])
    expect(state.status).toBe('review')
    expect(draftOf(state).soap).toEqual(aiSoap)
  })
})

describe('workflowReducer — SOAP generation', () => {
  it('fills all four sections when nothing was edited meanwhile', () => {
    const draft = draftOf(run(toDrafted))
    expect(draft.soap).toEqual(aiSoap)
    expect(draft.soapGeneration).toEqual({ status: 'idle' })
  })

  it('keeps sections the clinician typed while the AI was working', () => {
    const state = run([
      ...toReview,
      { type: 'SOAP_REQUESTED', requestId: 1 },
      { type: 'EDIT_SOAP_FIELD', field: 'plan', value: 'My own plan' },
      { type: 'SOAP_SUCCEEDED', requestId: 1, soap: aiSoap, warnings: [] },
    ])
    const { soap } = draftOf(state)
    expect(soap.plan).toBe('My own plan') // never overwritten by the late answer
    expect(soap.subjective).toBe(aiSoap.subjective) // untouched sections take the AI text
  })

  it('does not overwrite an edit made after a late result either: a stale request is ignored', () => {
    const before = run([
      ...toReview,
      { type: 'SOAP_REQUESTED', requestId: 1 },
      { type: 'SOAP_CANCELLED' },
      { type: 'EDIT_SOAP_FIELD', field: 'subjective', value: 'Typed after cancelling' },
    ])
    const after = workflowReducer(before, { type: 'SOAP_SUCCEEDED', requestId: 1, soap: aiSoap, warnings: [] })
    expect(after).toBe(before)
    expect(draftOf(after).soap.subjective).toBe('Typed after cancelling')
  })

  it('ignores an answer for an older request', () => {
    const running = run([...toReview, { type: 'SOAP_REQUESTED', requestId: 2 }])
    expect(workflowReducer(running, { type: 'SOAP_SUCCEEDED', requestId: 1, soap: aiSoap, warnings: [] })).toBe(running)
    expect(workflowReducer(running, { type: 'SOAP_FAILED', requestId: 1, message: 'x', retryable: true })).toBe(running)
  })

  it('does not start a second generation while one is running', () => {
    const running = run([...toReview, { type: 'SOAP_REQUESTED', requestId: 1 }])
    expect(workflowReducer(running, { type: 'SOAP_REQUESTED', requestId: 2 })).toBe(running)
  })

  it('records a failure and allows trying again', () => {
    const failed = run([
      ...toReview,
      { type: 'SOAP_REQUESTED', requestId: 1 },
      { type: 'SOAP_FAILED', requestId: 1, message: 'The AI model took too long to answer.', retryable: true },
    ])
    expect(draftOf(failed).soapGeneration).toEqual({
      status: 'failed',
      message: 'The AI model took too long to answer.',
      retryable: true,
    })
    expect(draftOf(failed).soap.subjective).toBe('') // a failure never puts text in the note
    expect(draftOf(workflowReducer(failed, { type: 'SOAP_REQUESTED', requestId: 2 })).soapGeneration.status).toBe('running')
  })

  it('shows warnings only for text the AI wrote, and clears them when the clinician edits that section', () => {
    const warnings = [
      { field: 'plan' as const, message: 'plan value' },
      { field: 'objective' as const, message: 'objective value' },
    ]
    const generated = run([
      ...toReview,
      { type: 'SOAP_REQUESTED', requestId: 1 },
      { type: 'EDIT_SOAP_FIELD', field: 'objective', value: 'mine' },
      { type: 'SOAP_SUCCEEDED', requestId: 1, soap: aiSoap, warnings },
    ])
    expect(draftOf(generated).warnings).toEqual([{ field: 'plan', message: 'plan value' }])
    const edited = workflowReducer(generated, { type: 'EDIT_SOAP_FIELD', field: 'plan', value: 'new plan' })
    expect(draftOf(edited).warnings).toEqual([])
  })

  it('cannot submit while a generation is still running', () => {
    const running = run([...toDrafted, { type: 'SOAP_REQUESTED', requestId: 2 }])
    expect(workflowReducer(running, { type: 'REQUEST_SUBMIT' })).toBe(running)
  })

  it('ignores AI answers once the note is being submitted', () => {
    const confirming = run(toConfirming)
    expect(workflowReducer(confirming, { type: 'SOAP_SUCCEEDED', requestId: 1, soap: aiSoap, warnings: [] })).toBe(confirming)
  })
})

describe('workflowReducer — clinical review', () => {
  const reviewed = (review: ClinicalReview, requestId = 1) => [
    ...toDrafted,
    { type: 'REVIEW_REQUESTED', requestId } as WorkflowAction,
    { type: 'REVIEW_SUCCEEDED', requestId, review, basedOnVersion: draftOf(run(toDrafted)).version } as WorkflowAction,
  ]

  it('stores the suggestions without touching the note or the transcript', () => {
    const before = draftOf(run(toDrafted))
    const state = run(reviewed(emptyReview({ informationGaps: [suggestion('a')] })))
    const draft = draftOf(state)
    expect(draft.review.status).toBe('ready')
    expect(draft.review.result?.review.informationGaps).toHaveLength(1)
    expect(draft.soap).toEqual(before.soap)
    expect(draft.transcript).toEqual(before.transcript)
    expect(draft.version).toBe(before.version)
  })

  it('is marked out of date by later edits through the version counter', () => {
    const fresh = draftOf(run(reviewed(emptyReview())))
    expect(fresh.review.result?.basedOnVersion).toBe(fresh.version)
    const afterNoteEdit = draftOf(
      run([{ type: 'EDIT_SOAP_FIELD', field: 'plan', value: 'changed' }], run(reviewed(emptyReview()))),
    )
    expect(afterNoteEdit.review.result?.basedOnVersion).not.toBe(afterNoteEdit.version)
    const afterLabelEdit = draftOf(
      run(
        [{ type: 'EDIT_TRANSCRIPT_SEGMENT', segmentId: 'seg-1', changes: { speaker: 'clinician' } }],
        run(reviewed(emptyReview())),
      ),
    )
    expect(afterLabelEdit.review.result?.basedOnVersion).not.toBe(afterLabelEdit.version)
  })

  it('keeps dismissed suggestions dismissed, including after a rerun', () => {
    const dismissed = run([{ type: 'REVIEW_DISMISSED', suggestionId: 'a' }], run(reviewed(emptyReview({ informationGaps: [suggestion('a'), suggestion('b')] }))))
    expect(draftOf(dismissed).review.dismissed).toEqual(['a'])
    // Dismissing twice changes nothing.
    expect(workflowReducer(dismissed, { type: 'REVIEW_DISMISSED', suggestionId: 'a' })).toBe(dismissed)

    const rerun = run(
      [
        { type: 'EDIT_SOAP_FIELD', field: 'plan', value: 'edited again' },
        { type: 'REVIEW_REQUESTED', requestId: 2 },
        {
          type: 'REVIEW_SUCCEEDED',
          requestId: 2,
          review: emptyReview({ informationGaps: [suggestion('a'), suggestion('b')] }), // the same stable IDs
          basedOnVersion: 99,
        },
      ],
      dismissed,
    )
    expect(draftOf(rerun).review.dismissed).toEqual(['a'])
    expect(draftOf(rerun).review.status).toBe('ready')
  })

  it('keeps the last result while a rerun is running and when it fails', () => {
    const ready = run(reviewed(emptyReview({ informationGaps: [suggestion('a')] })))
    const rerunning = workflowReducer(ready, { type: 'REVIEW_REQUESTED', requestId: 2 })
    expect(draftOf(rerunning).review).toMatchObject({ status: 'running' })
    expect(draftOf(rerunning).review.result).not.toBeNull()
    const failed = workflowReducer(rerunning, { type: 'REVIEW_FAILED', requestId: 2, message: 'Timed out.' })
    expect(draftOf(failed).review).toMatchObject({ status: 'failed', message: 'Timed out.' })
    expect(draftOf(failed).review.result).not.toBeNull()
  })

  it('settles back to the previous state when cancelled', () => {
    const first = run([...toDrafted, { type: 'REVIEW_REQUESTED', requestId: 1 }, { type: 'REVIEW_CANCELLED' }])
    expect(draftOf(first).review.status).toBe('idle')
    const ready = run(reviewed(emptyReview()))
    const again = run([{ type: 'REVIEW_REQUESTED', requestId: 2 }, { type: 'REVIEW_CANCELLED' }], ready)
    expect(draftOf(again).review.status).toBe('ready')
  })

  it('ignores answers from an older or cancelled request', () => {
    const running = run([...toDrafted, { type: 'REVIEW_REQUESTED', requestId: 2 }])
    const stale: WorkflowAction = { type: 'REVIEW_SUCCEEDED', requestId: 1, review: emptyReview(), basedOnVersion: 0 }
    expect(workflowReducer(running, stale)).toBe(running)
    const cancelled = workflowReducer(running, { type: 'REVIEW_CANCELLED' })
    expect(workflowReducer(cancelled, { type: 'REVIEW_SUCCEEDED', requestId: 2, review: emptyReview(), basedOnVersion: 0 })).toBe(cancelled)
  })

  it('abandons a review that is still running when the note is submitted', () => {
    const state = run([...toDrafted, { type: 'REVIEW_REQUESTED', requestId: 1 }, { type: 'REQUEST_SUBMIT' }])
    expect(state.status).toBe('confirming')
    expect(draftOf(state).review.status).toBe('idle')
    const late: WorkflowAction = { type: 'REVIEW_SUCCEEDED', requestId: 1, review: emptyReview(), basedOnVersion: 0 }
    expect(workflowReducer(state, late)).toBe(state)
  })

  it('does not run a second review at the same time', () => {
    const running = run([...toDrafted, { type: 'REVIEW_REQUESTED', requestId: 1 }])
    expect(workflowReducer(running, { type: 'REVIEW_REQUESTED', requestId: 2 })).toBe(running)
  })

  it('clears every AI input and output when the encounter is cancelled', () => {
    const state = run([...reviewed(emptyReview({ informationGaps: [suggestion('a')] })), { type: 'CANCEL_ENCOUNTER' }])
    expect(state).toEqual({ status: 'idle' })
  })
})

describe('workflowReducer — errors and retry', () => {
  it('records a transcription failure and retries it', () => {
    const failed = run([
      ...toProcessing,
      { type: 'PROCESSING_FAILED', error: { stage: 'transcription', message: 'x', retryable: true } },
    ])
    expect(failed).toMatchObject({ status: 'processing-error', attempt: 1 })
    expect(workflowReducer(failed, { type: 'RETRY_PROCESSING' })).toMatchObject({
      status: 'processing',
      stage: 'transcription',
      attempt: 2,
      progress: 0,
    })
  })

  it('does not retry non-retryable errors', () => {
    const state = run([
      ...toProcessing,
      { type: 'PROCESSING_FAILED', error: { stage: 'transcription', message: 'x', retryable: false } },
    ])
    expect(workflowReducer(state, { type: 'RETRY_PROCESSING' })).toBe(state)
  })

  it('returns to confirming with an error message when submission fails', () => {
    const state = run([...toConfirming, { type: 'CONFIRM_SUBMIT' }, { type: 'SUBMIT_FAILED', message: 'down' }])
    expect(state).toMatchObject({ status: 'confirming', submitError: 'down' })
  })
})

describe('workflowReducer — cancellation and guards', () => {
  it.each([
    ['ready', [{ type: 'START_ENCOUNTER', encounter }]],
    ['recording', toRecording],
    ['processing', toProcessing],
    ['review', toReview],
    ['confirming', toConfirming],
  ] as [string, WorkflowAction[]][])('cancels from %s back to an empty idle state', (_status, actions) => {
    expect(run([...actions, { type: 'CANCEL_ENCOUNTER' }])).toEqual({ status: 'idle' })
  })

  it('cannot cancel while a submission is in flight', () => {
    const submitting = run([...toConfirming, { type: 'CONFIRM_SUBMIT' }])
    expect(workflowReducer(submitting, { type: 'CANCEL_ENCOUNTER' })).toBe(submitting)
  })

  it('ignores actions that are invalid for the current state', () => {
    const idle = initialWorkflowState
    const noSoap: SoapNote = { subjective: 'a', objective: 'b', assessment: 'c', plan: 'd' }
    const invalid: WorkflowAction[] = [
      { type: 'RECORDING_STARTED', encounterId: 'enc-1', recordingId: 'rec-1', startedAt: 0 },
      { type: 'RECORDING_STOPPED', recordingId: 'rec-1' },
      { type: 'TRANSCRIPT_READY', transcript },
      { type: 'SOAP_REQUESTED', requestId: 1 },
      { type: 'SOAP_SUCCEEDED', requestId: 1, soap: noSoap, warnings: [] },
      { type: 'REVIEW_REQUESTED', requestId: 1 },
      { type: 'REVIEW_DISMISSED', suggestionId: 'a' },
      { type: 'REQUEST_SUBMIT' },
      { type: 'CONFIRM_SUBMIT' },
      { type: 'SUBMIT_SUCCEEDED', submission },
      { type: 'RETURN_TO_DASHBOARD' },
      { type: 'CANCEL_ENCOUNTER' },
    ]
    for (const action of invalid) expect(workflowReducer(idle, action)).toBe(idle)

    const ready = run([{ type: 'START_ENCOUNTER', encounter }])
    expect(workflowReducer(ready, { type: 'START_ENCOUNTER', encounter: { ...encounter, id: 'enc-2' } })).toBe(ready)
  })

  it('ignores stale async results from a different encounter or recording', () => {
    const ready = run([{ type: 'START_ENCOUNTER', encounter }])
    expect(
      workflowReducer(ready, { type: 'RECORDING_STARTED', encounterId: 'other', recordingId: 'rec-x', startedAt: 0 }),
    ).toBe(ready)

    const recording = run(toRecording)
    expect(workflowReducer(recording, { type: 'RECORDING_STOPPED', recordingId: 'rec-other' })).toBe(recording)
  })

  it('ignores a transcript that arrives after the encounter was cancelled', () => {
    const cancelled = run([...toProcessing, { type: 'CANCEL_ENCOUNTER' }])
    expect(workflowReducer(cancelled, { type: 'TRANSCRIPT_READY', transcript })).toBe(cancelled)
  })
})
