import { describe, expect, it } from 'vitest'
import type { DemoSubmitResponse } from '../api/types'
import { createMockSoap, createMockTranscript } from '../mocks/encounter'
import {
  initialWorkflowState,
  workflowReducer,
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
const soap = createMockSoap({ patientAge: 35, encounterType: 'acute' })
const submission: DemoSubmitResponse = {
  submissionId: 'demo-1',
  submittedAt: '2026-10-17T10:00:00.000Z',
  emrConnected: false,
  recordModified: false,
}

const run = (actions: WorkflowAction[], from: WorkflowState = initialWorkflowState) =>
  actions.reduce(workflowReducer, from)

const toRecording: WorkflowAction[] = [
  { type: 'START_ENCOUNTER', encounter },
  { type: 'RECORDING_STARTED', encounterId: 'enc-1', recordingId: 'rec-1', startedAt: 1000 },
]
const toProcessing: WorkflowAction[] = [...toRecording, { type: 'RECORDING_STOPPED', recordingId: 'rec-1' }]
const toReview: WorkflowAction[] = [
  ...toProcessing,
  { type: 'TRANSCRIPT_READY', transcript },
  { type: 'SOAP_READY', soap },
]
const toConfirming: WorkflowAction[] = [...toReview, { type: 'REQUEST_SUBMIT' }]
const toCompleted: WorkflowAction[] = [
  ...toConfirming,
  { type: 'CONFIRM_SUBMIT' },
  { type: 'SUBMIT_SUCCEEDED', submission },
]

describe('workflowReducer — happy path', () => {
  it('walks idle → ready → recording → processing → review → confirming → submitting → completed', () => {
    const statuses: string[] = []
    let state = initialWorkflowState
    for (const action of [...toCompleted]) {
      state = workflowReducer(state, action)
      statuses.push(state.status)
    }
    expect(statuses).toEqual([
      'ready',
      'recording',
      'processing',
      'processing',
      'review',
      'confirming',
      'submitting',
      'completed',
    ])
  })

  it('moves from transcription to SOAP generation and resets progress', () => {
    let state = run(toProcessing)
    state = workflowReducer(state, { type: 'PROCESSING_PROGRESS', stage: 'transcription', progress: 0.5 })
    expect(state).toMatchObject({ stage: 'transcription', progress: 0.5 })
    state = workflowReducer(state, { type: 'TRANSCRIPT_READY', transcript })
    expect(state).toMatchObject({ stage: 'soap-generation', progress: 0, transcript })
  })

  it('accepts unknown progress while the backend is working', () => {
    const state = workflowReducer(run(toProcessing), { type: 'PROCESSING_PROGRESS', stage: 'transcription', progress: null })
    expect(state).toMatchObject({ status: 'processing', progress: null })
  })

  it('clamps progress and ignores progress for a different stage', () => {
    const state = run(toProcessing)
    expect(workflowReducer(state, { type: 'PROCESSING_PROGRESS', stage: 'transcription', progress: 4 })).toMatchObject({
      progress: 1,
    })
    expect(workflowReducer(state, { type: 'PROCESSING_PROGRESS', stage: 'soap-generation', progress: 0.3 })).toBe(state)
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
    const before = run(toReview)
    const after = workflowReducer(before, { type: 'EDIT_SOAP_FIELD', field: 'plan', value: 'Revised plan' })
    if (after.status !== 'review' || before.status !== 'review') throw new Error('expected review')
    expect(after.draft.soap.plan).toBe('Revised plan')
    expect(after.draft.soap.subjective).toBe(before.draft.soap.subjective)
    expect(before.draft.soap.plan).toBe(soap.plan) // previous state not mutated
  })

  it('edits one transcript segment’s text and speaker', () => {
    const state = run([
      ...toReview,
      { type: 'EDIT_TRANSCRIPT_SEGMENT', segmentId: 'seg-2', changes: { text: 'Corrected', speaker: 'clinician' } },
    ])
    if (state.status !== 'review') throw new Error('expected review')
    expect(state.draft.transcript.segments[1]).toMatchObject({ text: 'Corrected', speaker: 'clinician' })
    expect(state.draft.transcript.segments[0]).toEqual(transcript.segments[0])
  })

  it('ignores edits outside the review state', () => {
    const confirming = run(toConfirming)
    expect(workflowReducer(confirming, { type: 'EDIT_SOAP_FIELD', field: 'plan', value: 'x' })).toBe(confirming)
  })

  it('blocks submit while any SOAP section is empty', () => {
    const state = run([...toReview, { type: 'EDIT_SOAP_FIELD', field: 'assessment', value: '   ' }])
    expect(workflowReducer(state, { type: 'REQUEST_SUBMIT' })).toBe(state)
  })

  it('returns from confirming to review with the draft intact', () => {
    const state = run([...toConfirming, { type: 'CANCEL_SUBMIT' }])
    expect(state.status).toBe('review')
    if (state.status === 'review') expect(state.draft.soap).toEqual(soap)
  })
})

describe('workflowReducer — errors and retry', () => {
  it('records a processing failure and keeps the transcript when SOAP generation fails', () => {
    const state = run([
      ...toProcessing,
      { type: 'TRANSCRIPT_READY', transcript },
      { type: 'PROCESSING_FAILED', error: { stage: 'soap-generation', message: 'timeout', retryable: true } },
    ])
    expect(state).toMatchObject({ status: 'processing-error', transcript, attempt: 1 })
  })

  it('retries at SOAP generation when the transcript exists, otherwise at transcription', () => {
    const afterSoapFailure = run([
      ...toProcessing,
      { type: 'TRANSCRIPT_READY', transcript },
      { type: 'PROCESSING_FAILED', error: { stage: 'soap-generation', message: 'x', retryable: true } },
      { type: 'RETRY_PROCESSING' },
    ])
    expect(afterSoapFailure).toMatchObject({ status: 'processing', stage: 'soap-generation', attempt: 2, progress: 0 })

    const afterTranscriptionFailure = run([
      ...toProcessing,
      { type: 'PROCESSING_FAILED', error: { stage: 'transcription', message: 'x', retryable: true } },
      { type: 'RETRY_PROCESSING' },
    ])
    expect(afterTranscriptionFailure).toMatchObject({ status: 'processing', stage: 'transcription', attempt: 2 })
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
    const invalid: WorkflowAction[] = [
      { type: 'RECORDING_STARTED', encounterId: 'enc-1', recordingId: 'rec-1', startedAt: 0 },
      { type: 'RECORDING_STOPPED', recordingId: 'rec-1' },
      { type: 'SOAP_READY', soap },
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

  it('ignores SOAP_READY before the transcript is available', () => {
    const processing = run(toProcessing)
    expect(workflowReducer(processing, { type: 'SOAP_READY', soap })).toBe(processing)
  })
})
