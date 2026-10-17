/**
 * Request/response contracts for the AirSOAP backend (local services running
 * on the Raspberry Pi). The UI only talks to the `AirSoapApi` interface in
 * `client.ts`, backed by `mockClient.ts` or by `httpClient.ts`.
 */

/** ISO-8601 timestamp, e.g. "2026-10-17T14:03:00.000Z". */
export type IsoDateTime = string

/** Whisper does not identify speakers, so real transcripts start as `unassigned`. */
export type Speaker = 'clinician' | 'patient' | 'unassigned'

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

/**
 * Everything the language model receives. Deliberately has no name, date of
 * birth or MRN fields. Build it only with `buildSoapRequest()`, which also
 * redacts identifiers from the transcript text.
 */
export interface SoapGenerationRequest {
  transcript: Transcript
  context: SoapGenerationContext
}

export interface SoapGenerationResponse {
  soap: SoapNote
  model: string
  generatedAt: IsoDateTime
}

export type ProcessingStage = 'transcription' | 'soap-generation'

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

export interface ApiError {
  code: ApiErrorCode
  /** Safe to show to the user; must not contain patient data. */
  message: string
  retryable: boolean
}
