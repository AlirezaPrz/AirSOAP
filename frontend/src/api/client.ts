import type {
  ApiError,
  ClinicalReviewRequest,
  ClinicalReviewResponse,
  ApiErrorCode,
  DemoSubmitRequest,
  DemoSubmitResponse,
  DiscardRecordingRequest,
  LiveSnapshot,
  SoapGenerationRequest,
  SoapGenerationResponse,
  SpeakerSuggestionRequest,
  SpeakerSuggestionResponse,
  StartRecordingRequest,
  StartRecordingResponse,
  StopRecordingRequest,
  StopRecordingResponse,
  TranscriptionRequest,
  TranscriptionResponse,
} from './types'

export interface RequestOptions {
  signal?: AbortSignal
}

export interface LiveHandlers {
  onSnapshot: (snapshot: LiveSnapshot) => void
  /** 'reconnecting' while the connection is down; the next snapshot means it is back. */
  onConnection?: (state: 'open' | 'reconnecting') => void
}

export interface JobOptions extends RequestOptions {
  /** Called with values from 0 to 1 as the job advances, or null while progress is unknown. */
  onProgress?: (progress: number | null) => void
}

/** The only backend surface the UI depends on. */
export interface AirSoapApi {
  startRecording(request: StartRecordingRequest, options?: RequestOptions): Promise<StartRecordingResponse>
  stopRecording(request: StopRecordingRequest, options?: RequestOptions): Promise<StopRecordingResponse>
  discardRecording(request: DiscardRecordingRequest, options?: RequestOptions): Promise<void>
  transcribe(request: TranscriptionRequest, options?: JobOptions): Promise<TranscriptionResponse>
  /** Drafts a SOAP note from the labelled transcript. Never saves or submits anything. */
  generateSoap(request: SoapGenerationRequest, options?: RequestOptions): Promise<SoapGenerationResponse>
  /** Suggestions on the clinician's edited note. Never changes the note. */
  runClinicalReview(request: ClinicalReviewRequest, options?: RequestOptions): Promise<ClinicalReviewResponse>
  /** Who said each turn, from the words alone. Suggestions only; the clinician confirms them. */
  suggestSpeakers(request: SpeakerSuggestionRequest, options?: RequestOptions): Promise<SpeakerSuggestionResponse>
  /** Follow the live transcript of a recording that started with `live: true`. Returns an unsubscribe. */
  followLive?(recordingId: string, handlers: LiveHandlers): () => void
  submitDemo(request: DemoSubmitRequest, options?: RequestOptions): Promise<DemoSubmitResponse>
}

export class ApiRequestError extends Error implements ApiError {
  readonly code: ApiErrorCode
  readonly retryable: boolean

  constructor(error: ApiError) {
    super(error.message)
    this.name = 'ApiRequestError'
    this.code = error.code
    this.retryable = error.retryable
  }
}

export const cancelledError = () =>
  new ApiRequestError({ code: 'CANCELLED', message: 'The request was cancelled.', retryable: false })

export function isCancellation(error: unknown): boolean {
  return error instanceof ApiRequestError && error.code === 'CANCELLED'
}

export function toApiError(error: unknown, fallback: ApiError): ApiError {
  if (error instanceof ApiRequestError) {
    return { code: error.code, message: error.message, retryable: error.retryable }
  }
  return fallback
}
