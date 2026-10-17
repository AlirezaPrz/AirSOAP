/**
 * Request/response contracts for the planned AirSOAP backend (local services
 * running on the Raspberry Pi). The UI only talks to the `AirSoapApi`
 * interface in `client.ts`; today it is backed by `mockClient.ts`, later by an
 * HTTP client that implements the same contracts.
 */

/** ISO-8601 timestamp, e.g. "2026-10-17T14:03:00.000Z". */
export type IsoDateTime = string

export type Speaker = 'clinician' | 'patient'

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
  /** 0–1 */
  progress: number
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

export interface ApiError {
  code: ApiErrorCode
  /** Safe to show to the user; must not contain patient data. */
  message: string
  retryable: boolean
}
