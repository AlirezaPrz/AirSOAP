import type {
  ClinicalReview,
  DemoSubmitResponse,
  EncounterType,
  PatientIdentity,
  ProcessingStage,
  SoapField,
  SoapNote,
  SoapWarning,
  Transcript,
  TranscriptSegment,
} from '../api/types'

export interface Encounter {
  id: string
  patient: PatientIdentity
  encounterType: EncounterType
}

export type SoapGenerationState =
  | { status: 'idle' }
  /** `touched` records the fields the clinician typed in while the AI was working; those are kept. */
  | { status: 'running'; requestId: number; touched: Record<SoapField, boolean> }
  | { status: 'failed'; message: string; retryable: boolean }

export interface ClinicalReviewState {
  status: 'idle' | 'running' | 'ready' | 'failed'
  /** The latest request; answers carrying any other ID are ignored. */
  requestId: number
  message: string | null
  /** The last good result, kept while a rerun is running or has failed. */
  result: { review: ClinicalReview; basedOnVersion: number } | null
  /** Suggestion IDs the clinician dismissed. IDs are stable across reruns, so they stay dismissed. */
  dismissed: string[]
}

export interface Draft {
  transcript: Transcript
  soap: SoapNote
  /** Values in the generated note that the transcript never mentions. */
  warnings: SoapWarning[]
  soapGeneration: SoapGenerationState
  review: ClinicalReviewState
  /** Counts every clinician edit to the transcript or note; tells whether a review is out of date. */
  version: number
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
    }
  | {
      status: 'processing-error'
      encounter: Encounter
      recordingId: string
      attempt: number
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
  | { type: 'PROCESSING_FAILED'; error: ProcessingError }
  | { type: 'RETRY_PROCESSING' }
  | { type: 'EDIT_TRANSCRIPT_SEGMENT'; segmentId: string; changes: Partial<Pick<TranscriptSegment, 'text' | 'speaker'>> }
  | { type: 'EDIT_SOAP_FIELD'; field: SoapField; value: string }
  | { type: 'SOAP_REQUESTED'; requestId: number }
  | { type: 'SOAP_SUCCEEDED'; requestId: number; soap: SoapNote; warnings: SoapWarning[] }
  | { type: 'SOAP_FAILED'; requestId: number; message: string; retryable: boolean }
  | { type: 'SOAP_CANCELLED' }
  | { type: 'REVIEW_REQUESTED'; requestId: number }
  | { type: 'REVIEW_SUCCEEDED'; requestId: number; review: ClinicalReview; basedOnVersion: number }
  | { type: 'REVIEW_FAILED'; requestId: number; message: string }
  | { type: 'REVIEW_CANCELLED' }
  | { type: 'REVIEW_DISMISSED'; suggestionId: string }
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

/** True when at least one SOAP section has text. */
export function hasSoapContent(soap: SoapNote): boolean {
  return missingSoapFields(soap).length < SOAP_FIELDS.length
}

export function getRecordingId(state: WorkflowState): string | null {
  return 'recordingId' in state ? state.recordingId : null
}

const clamp01 = (value: number) => Math.min(1, Math.max(0, value))

const NOT_TOUCHED: Record<SoapField, boolean> = { subjective: false, objective: false, assessment: false, plan: false }

function newDraft(transcript: Transcript): Draft {
  return {
    transcript,
    soap: { subjective: '', objective: '', assessment: '', plan: '' },
    warnings: [],
    soapGeneration: { status: 'idle' },
    review: { status: 'idle', requestId: 0, message: null, result: null, dismissed: [] },
    version: 0,
  }
}

/** After a review stops without an answer, fall back to showing the last result, if any. */
function settledReview(review: ClinicalReviewState): ClinicalReviewState {
  return { ...review, status: review.result ? 'ready' : 'idle', message: null }
}

function inReview(state: WorkflowState): state is StateOf<'review'> {
  return state.status === 'review'
}

function withDraft(state: StateOf<'review'>, changes: Partial<Draft>): StateOf<'review'> {
  return { ...state, draft: { ...state.draft, ...changes } }
}

/**
 * Pure state machine for one encounter. Actions that are not valid in the
 * current state return the state unchanged. AI answers carry the ID of the
 * request that produced them and are ignored unless it is still the current one,
 * so a delayed result can never overwrite newer clinician edits.
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
      }

    case 'PROCESSING_PROGRESS':
      if (state.status !== 'processing' || state.stage !== action.stage) return state
      return { ...state, progress: action.progress === null ? null : clamp01(action.progress) }

    case 'TRANSCRIPT_READY':
      if (state.status !== 'processing') return state
      return {
        status: 'review',
        encounter: state.encounter,
        recordingId: state.recordingId,
        draft: newDraft(action.transcript),
      }

    case 'PROCESSING_FAILED':
      if (state.status !== 'processing') return state
      return {
        status: 'processing-error',
        encounter: state.encounter,
        recordingId: state.recordingId,
        attempt: state.attempt,
        error: action.error,
      }

    case 'RETRY_PROCESSING':
      if (state.status !== 'processing-error' || !state.error.retryable) return state
      return {
        status: 'processing',
        encounter: state.encounter,
        recordingId: state.recordingId,
        stage: 'transcription',
        progress: 0,
        attempt: state.attempt + 1,
      }

    case 'EDIT_TRANSCRIPT_SEGMENT': {
      if (!inReview(state)) return state
      const segments = state.draft.transcript.segments.map((segment) =>
        segment.id === action.segmentId ? { ...segment, ...action.changes } : segment,
      )
      return withDraft(state, {
        transcript: { ...state.draft.transcript, segments },
        version: state.draft.version + 1,
      })
    }

    case 'EDIT_SOAP_FIELD': {
      if (!inReview(state)) return state
      const { soapGeneration } = state.draft
      return withDraft(state, {
        soap: { ...state.draft.soap, [action.field]: action.value },
        // The clinician has taken over this section: a warning about the AI's text no longer applies,
        // and an AI answer still on its way must not replace what they typed.
        warnings: state.draft.warnings.filter((warning) => warning.field !== action.field),
        soapGeneration:
          soapGeneration.status === 'running'
            ? { ...soapGeneration, touched: { ...soapGeneration.touched, [action.field]: true } }
            : soapGeneration,
        version: state.draft.version + 1,
      })
    }

    case 'SOAP_REQUESTED':
      if (!inReview(state) || state.draft.soapGeneration.status === 'running') return state
      return withDraft(state, {
        warnings: [],
        soapGeneration: { status: 'running', requestId: action.requestId, touched: { ...NOT_TOUCHED } },
      })

    case 'SOAP_SUCCEEDED': {
      if (!inReview(state)) return state
      const { soapGeneration } = state.draft
      if (soapGeneration.status !== 'running' || soapGeneration.requestId !== action.requestId) return state
      const soap = { ...state.draft.soap }
      let applied = false
      for (const field of SOAP_FIELDS) {
        if (!soapGeneration.touched[field]) {
          soap[field] = action.soap[field]
          applied = true
        }
      }
      return withDraft(state, {
        soap,
        // Only warn about text the AI actually wrote; sections the clinician edited are theirs.
        warnings: action.warnings.filter((warning) => !soapGeneration.touched[warning.field]),
        soapGeneration: { status: 'idle' },
        version: applied ? state.draft.version + 1 : state.draft.version,
      })
    }

    case 'SOAP_FAILED': {
      if (!inReview(state)) return state
      const { soapGeneration } = state.draft
      if (soapGeneration.status !== 'running' || soapGeneration.requestId !== action.requestId) return state
      return withDraft(state, {
        soapGeneration: { status: 'failed', message: action.message, retryable: action.retryable },
      })
    }

    case 'SOAP_CANCELLED':
      if (!inReview(state) || state.draft.soapGeneration.status !== 'running') return state
      return withDraft(state, { soapGeneration: { status: 'idle' } })

    case 'REVIEW_REQUESTED':
      if (!inReview(state) || state.draft.review.status === 'running') return state
      return withDraft(state, {
        review: { ...state.draft.review, status: 'running', requestId: action.requestId, message: null },
      })

    case 'REVIEW_SUCCEEDED': {
      if (!inReview(state)) return state
      const { review } = state.draft
      if (review.status !== 'running' || review.requestId !== action.requestId) return state
      return withDraft(state, {
        review: {
          ...review,
          status: 'ready',
          message: null,
          result: { review: action.review, basedOnVersion: action.basedOnVersion },
        },
      })
    }

    case 'REVIEW_FAILED': {
      if (!inReview(state)) return state
      const { review } = state.draft
      if (review.status !== 'running' || review.requestId !== action.requestId) return state
      return withDraft(state, { review: { ...review, status: 'failed', message: action.message } })
    }

    case 'REVIEW_CANCELLED':
      if (!inReview(state) || state.draft.review.status !== 'running') return state
      return withDraft(state, { review: settledReview(state.draft.review) })

    case 'REVIEW_DISMISSED': {
      if (!inReview(state) || state.draft.review.dismissed.includes(action.suggestionId)) return state
      return withDraft(state, {
        review: { ...state.draft.review, dismissed: [...state.draft.review.dismissed, action.suggestionId] },
      })
    }

    case 'REQUEST_SUBMIT': {
      if (
        !inReview(state) ||
        state.draft.soapGeneration.status === 'running' ||
        missingSoapFields(state.draft.soap).length > 0
      ) {
        return state
      }
      // A review still running is abandoned: its answer would arrive after the note was submitted.
      const review = state.draft.review.status === 'running' ? settledReview(state.draft.review) : state.draft.review
      return { ...state, status: 'confirming', draft: { ...state.draft, review }, submitError: null }
    }

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
