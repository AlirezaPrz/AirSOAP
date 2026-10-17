import type {
  DemoSubmitResponse,
  EncounterType,
  PatientIdentity,
  ProcessingStage,
  SoapField,
  SoapNote,
  Transcript,
  TranscriptSegment,
} from '../api/types'

export interface Encounter {
  id: string
  patient: PatientIdentity
  encounterType: EncounterType
}

export interface Draft {
  transcript: Transcript
  soap: SoapNote
}

export interface ProcessingError {
  stage: ProcessingStage
  message: string
  retryable: boolean
}

export type WorkflowState =
  | { status: 'idle' }
  | { status: 'ready'; encounter: Encounter }
  | { status: 'recording'; encounter: Encounter; recordingId: string; startedAt: number }
  | {
      status: 'processing'
      encounter: Encounter
      recordingId: string
      stage: ProcessingStage
      /** 0–1, or null while the backend cannot report progress. */
      progress: number | null
      attempt: number
      /** Kept once transcription succeeds so a retry resumes at SOAP generation. */
      transcript: Transcript | null
    }
  | {
      status: 'processing-error'
      encounter: Encounter
      recordingId: string
      attempt: number
      transcript: Transcript | null
      error: ProcessingError
    }
  | { status: 'review'; encounter: Encounter; recordingId: string; draft: Draft }
  | { status: 'confirming'; encounter: Encounter; recordingId: string; draft: Draft; submitError: string | null }
  | { status: 'submitting'; encounter: Encounter; recordingId: string; draft: Draft }
  /** Encounter data is dropped here; only the demo receipt remains. */
  | { status: 'completed'; submission: DemoSubmitResponse }

export type WorkflowStatus = WorkflowState['status']
export type StateOf<S extends WorkflowStatus> = Extract<WorkflowState, { status: S }>

export type WorkflowAction =
  | { type: 'START_ENCOUNTER'; encounter: Encounter }
  | { type: 'RECORDING_STARTED'; encounterId: string; recordingId: string; startedAt: number }
  | { type: 'RECORDING_STOPPED'; recordingId: string }
  | { type: 'PROCESSING_PROGRESS'; stage: ProcessingStage; progress: number | null }
  | { type: 'TRANSCRIPT_READY'; transcript: Transcript }
  | { type: 'SOAP_READY'; soap: SoapNote }
  | { type: 'PROCESSING_FAILED'; error: ProcessingError }
  | { type: 'RETRY_PROCESSING' }
  | { type: 'EDIT_TRANSCRIPT_SEGMENT'; segmentId: string; changes: Partial<Pick<TranscriptSegment, 'text' | 'speaker'>> }
  | { type: 'EDIT_SOAP_FIELD'; field: SoapField; value: string }
  | { type: 'REQUEST_SUBMIT' }
  | { type: 'CANCEL_SUBMIT' }
  | { type: 'CONFIRM_SUBMIT' }
  | { type: 'SUBMIT_SUCCEEDED'; submission: DemoSubmitResponse }
  | { type: 'SUBMIT_FAILED'; message: string }
  | { type: 'CANCEL_ENCOUNTER' }
  | { type: 'RETURN_TO_DASHBOARD' }

export const initialWorkflowState: WorkflowState = { status: 'idle' }

export const SOAP_FIELDS: readonly SoapField[] = ['subjective', 'objective', 'assessment', 'plan']

const CANCELLABLE: ReadonlySet<WorkflowStatus> = new Set([
  'ready',
  'recording',
  'processing',
  'processing-error',
  'review',
  'confirming',
])

export function canCancel(state: WorkflowState): boolean {
  return CANCELLABLE.has(state.status)
}

export function missingSoapFields(soap: SoapNote): SoapField[] {
  return SOAP_FIELDS.filter((field) => soap[field].trim() === '')
}

export function getRecordingId(state: WorkflowState): string | null {
  return 'recordingId' in state ? state.recordingId : null
}

const clamp01 = (value: number) => Math.min(1, Math.max(0, value))

/**
 * Pure state machine for one encounter. Actions that are not valid in the
 * current state return the state unchanged.
 */
export function workflowReducer(state: WorkflowState, action: WorkflowAction): WorkflowState {
  switch (action.type) {
    case 'START_ENCOUNTER':
      return state.status === 'idle' ? { status: 'ready', encounter: action.encounter } : state

    case 'RECORDING_STARTED':
      if (state.status !== 'ready' || state.encounter.id !== action.encounterId) return state
      return {
        status: 'recording',
        encounter: state.encounter,
        recordingId: action.recordingId,
        startedAt: action.startedAt,
      }

    case 'RECORDING_STOPPED':
      if (state.status !== 'recording' || state.recordingId !== action.recordingId) return state
      return {
        status: 'processing',
        encounter: state.encounter,
        recordingId: state.recordingId,
        stage: 'transcription',
        progress: 0,
        attempt: 1,
        transcript: null,
      }

    case 'PROCESSING_PROGRESS':
      if (state.status !== 'processing' || state.stage !== action.stage) return state
      return { ...state, progress: action.progress === null ? null : clamp01(action.progress) }

    case 'TRANSCRIPT_READY':
      if (state.status !== 'processing' || state.stage !== 'transcription') return state
      return { ...state, transcript: action.transcript, stage: 'soap-generation', progress: 0 }

    case 'SOAP_READY':
      if (state.status !== 'processing' || state.stage !== 'soap-generation' || !state.transcript) return state
      return {
        status: 'review',
        encounter: state.encounter,
        recordingId: state.recordingId,
        draft: { transcript: state.transcript, soap: action.soap },
      }

    case 'PROCESSING_FAILED':
      if (state.status !== 'processing') return state
      return {
        status: 'processing-error',
        encounter: state.encounter,
        recordingId: state.recordingId,
        attempt: state.attempt,
        transcript: state.transcript,
        error: action.error,
      }

    case 'RETRY_PROCESSING':
      if (state.status !== 'processing-error' || !state.error.retryable) return state
      return {
        status: 'processing',
        encounter: state.encounter,
        recordingId: state.recordingId,
        stage: state.transcript ? 'soap-generation' : 'transcription',
        progress: 0,
        attempt: state.attempt + 1,
        transcript: state.transcript,
      }

    case 'EDIT_TRANSCRIPT_SEGMENT': {
      if (state.status !== 'review') return state
      const segments = state.draft.transcript.segments.map((segment) =>
        segment.id === action.segmentId ? { ...segment, ...action.changes } : segment,
      )
      return { ...state, draft: { ...state.draft, transcript: { ...state.draft.transcript, segments } } }
    }

    case 'EDIT_SOAP_FIELD':
      if (state.status !== 'review') return state
      return { ...state, draft: { ...state.draft, soap: { ...state.draft.soap, [action.field]: action.value } } }

    case 'REQUEST_SUBMIT':
      if (state.status !== 'review' || missingSoapFields(state.draft.soap).length > 0) return state
      return { ...state, status: 'confirming', submitError: null }

    case 'CANCEL_SUBMIT':
      if (state.status !== 'confirming') return state
      return { status: 'review', encounter: state.encounter, recordingId: state.recordingId, draft: state.draft }

    case 'CONFIRM_SUBMIT':
      if (state.status !== 'confirming') return state
      return { status: 'submitting', encounter: state.encounter, recordingId: state.recordingId, draft: state.draft }

    case 'SUBMIT_SUCCEEDED':
      return state.status === 'submitting' ? { status: 'completed', submission: action.submission } : state

    case 'SUBMIT_FAILED':
      return state.status === 'submitting' ? { ...state, status: 'confirming', submitError: action.message } : state

    case 'CANCEL_ENCOUNTER':
      return canCancel(state) ? { status: 'idle' } : state

    case 'RETURN_TO_DASHBOARD':
      return state.status === 'completed' ? { status: 'idle' } : state
  }
}
