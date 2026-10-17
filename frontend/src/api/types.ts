/**
 * Request/response contracts for the AirSOAP backend (local services running
 * on the Raspberry Pi). The UI only talks to the `AirSoapApi` interface in
 * `client.ts`, backed by `mockClient.ts` or by `httpClient.ts`.
 */

/** ISO-8601 timestamp, e.g. "2026-10-17T14:03:00.000Z". */
export type IsoDateTime = string

/**
 * Whisper does not identify speakers, so every segment starts as `unknown` and the
 * clinician sets the labels by hand. Nothing guesses them.
 */
export type Speaker = 'clinician' | 'patient' | 'unknown'

export interface TranscriptSegment {
  id: string
  speaker: Speaker
  startMs: number
  endMs: number
  text: string
}

export interface Transcript {
  segments: TranscriptSegment[]
  /** BCP-47 language tag, e.g. "en". */
  language: string
  durationMs: number
}

export interface SoapNote {
  subjective: string
  objective: string
  assessment: string
  plan: string
}

export type SoapField = keyof SoapNote

export type EncounterType = 'acute' | 'follow-up' | 'new-patient' | 'wellness'

/**
 * Age in whole years. Ages of 90 and above are aggregated as '90+'
 * (HIPAA Safe Harbor), so the value can never narrow down a date of birth.
 */
export type PatientAge = number | '90+'

/** Identifying details. Stays on the device and never reaches the AI. */
export interface PatientIdentity {
  fullName: string
  /** YYYY-MM-DD */
  dateOfBirth: string
  mrn?: string
}

// ---------------------------------------------------------------------------
// Recording
// ---------------------------------------------------------------------------

export interface StartRecordingRequest {
  encounterId: string
}

export interface StartRecordingResponse {
  recordingId: string
  startedAt: IsoDateTime
}

export interface StopRecordingRequest {
  recordingId: string
}

export interface StopRecordingResponse {
  recordingId: string
  durationMs: number
  /** Transcription job queued by the backend when the recording stopped (absent in the mock). */
  jobId?: string
}

/** Deletes captured audio when an encounter is cancelled. */
export interface DiscardRecordingRequest {
  recordingId: string
}

// ---------------------------------------------------------------------------
// Transcription (Whisper)
// ---------------------------------------------------------------------------

export interface TranscriptionRequest {
  recordingId: string
  language?: string
}

export interface TranscriptionResponse {
  transcript: Transcript
  model: string
}

/** Lifecycle of a backend transcription job. */
export type TranscriptionJobStatus = 'queued' | 'processing' | 'completed' | 'cancelled' | 'error'

/** `GET /api/transcriptions/{jobId}`: the backend's wire format, polled by the HTTP client. */
export interface TranscriptionJobResponse {
  jobId: string
  status: TranscriptionJobStatus
  /** 0–100; null while processing, because whisper-server reports no progress. */
  progress: number | null
  audioDurationMs: number
  transcript: {
    text: string
    language: string
    durationMs: number
    segments: { id: string; startMs: number; endMs: number; text: string }[]
  } | null
  error: ApiError | null
}

// ---------------------------------------------------------------------------
// SOAP generation (local LLM via Ollama)
// ---------------------------------------------------------------------------

/** Non-identifying context the model may use for clinical reasoning. */
export interface SoapGenerationContext {
  patientAge: PatientAge
  encounterType: EncounterType
}

/** A transcript segment as the language model sees it: ID, the clinician's label, redacted text. */
export interface AiSegment {
  id: string
  speaker: Speaker
  text: string
}

/**
 * Everything the SOAP model receives. Deliberately has no name, date of birth or
 * MRN fields. Build it only with `buildSoapRequest()`, which also redacts
 * identifiers from the transcript text.
 */
export interface SoapGenerationRequest {
  segments: AiSegment[]
  context: SoapGenerationContext
}

/** A value in the generated note that the transcript never mentions. */
export interface SoapWarning {
  field: SoapField
  message: string
}

export interface SoapGenerationResponse {
  soap: SoapNote
  warnings: SoapWarning[]
  model: string
  generatedAt: IsoDateTime
}

// ---------------------------------------------------------------------------
// Clinical review (optional, run on demand after the clinician edits the note)
// ---------------------------------------------------------------------------

/** The labelled transcript and the clinician's current note, both with identifiers removed. */
export interface ClinicalReviewRequest {
  segments: AiSegment[]
  soap: SoapNote
  context: SoapGenerationContext
}

export type SuggestionCategory =
  | 'information_gap'
  | 'follow_up_question'
  | 'differential_consideration'
  | 'documentation_inconsistency'

export type Uncertainty = 'low' | 'medium' | 'high'

/** Text quoted from the transcript or the note; the backend verified that it exists there. */
export interface Evidence {
  source: 'transcript' | 'note'
  quote: string
  segmentId?: string | null
  field?: SoapField | null
}

export interface ReviewSuggestion {
  /** Stable across reruns, so a dismissed suggestion stays dismissed. */
  id: string
  category: SuggestionCategory
  description: string
  rationale: string
  uncertainty: Uncertainty
  evidence: Evidence[]
  gapType?: 'not_asked' | 'asked_not_answered' | 'omitted_from_note' | 'uncertain' | null
  missingInformation?: string | null
}

export interface ClinicalReview {
  informationGaps: ReviewSuggestion[]
  followUpQuestions: ReviewSuggestion[]
  differentialConsiderations: ReviewSuggestion[]
  documentationInconsistencies: ReviewSuggestion[]
  /** Suggestions left out because their quoted evidence could not be found. */
  omittedUnverified: number
}

export interface ClinicalReviewResponse {
  review: ClinicalReview
  model: string
  generatedAt: IsoDateTime
}

/** `GET /api/ai/jobs/{jobId}`: the backend's wire format for both AI tasks. */
export interface AiJobResponse {
  jobId: string
  kind: 'soap' | 'clinical-review'
  status: TranscriptionJobStatus
  soapResult: { soap: SoapNote; warnings: SoapWarning[] } | null
  reviewResult: ClinicalReview | null
  error: ApiError | null
}

export type ProcessingStage = 'transcription'

/** Progress event streamed by long-running jobs (e.g. over SSE). */
export interface ProgressUpdate {
  stage: ProcessingStage
  /** 0–1, or null while the amount of work left is unknown. */
  progress: number | null
}

// ---------------------------------------------------------------------------
// Demo submission (no EMR connected)
// ---------------------------------------------------------------------------

export interface DemoSubmitRequest {
  encounterId: string
  patient: PatientIdentity
  encounterType: EncounterType
  transcript: Transcript
  soap: SoapNote
}

export interface DemoSubmitResponse {
  submissionId: string
  submittedAt: IsoDateTime
  /** Always false in demo mode: nothing leaves the device. */
  emrConnected: false
  /** Always false in demo mode: no medical record is created or changed. */
  recordModified: false
}

// ---------------------------------------------------------------------------
// Errors
// ---------------------------------------------------------------------------

export type ApiErrorCode =
  | 'CANCELLED'
  | 'RECORDING_FAILED'
  | 'TRANSCRIPTION_FAILED'
  | 'SOAP_GENERATION_FAILED'
  | 'SUBMISSION_FAILED'
  | 'UNAVAILABLE'
  | 'UNKNOWN'
  // Returned by the backend:
  | 'RECORDING_IN_PROGRESS'
  | 'NO_ACTIVE_RECORDING'
  | 'RECORDER_UNAVAILABLE'
  | 'EMPTY_RECORDING'
  | 'QUEUE_FULL'
  | 'JOB_NOT_FOUND'
  | 'TRANSCRIBER_UNAVAILABLE'
  | 'TRANSCRIPTION_TIMEOUT'
  | 'CLINICAL_REVIEW_FAILED'
  | 'AI_UNAVAILABLE'
  | 'AI_TIMEOUT'
  | 'AI_FAILED'
  | 'AI_INVALID_OUTPUT'
  | 'AI_MODEL_MISSING'
  | 'AI_QUEUE_FULL'
  | 'REQUEST_TOO_LARGE'
  | 'INVALID_REQUEST'

export interface ApiError {
  code: ApiErrorCode
  /** Safe to show to the user; must not contain patient data. */
  message: string
  retryable: boolean
}
