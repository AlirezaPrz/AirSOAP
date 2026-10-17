import type {
  ApiError,
  ApiErrorCode,
  DemoSubmitRequest,
  DemoSubmitResponse,
  DiscardRecordingRequest,
  SoapGenerationRequest,
  SoapGenerationResponse,
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
  generateSoap(request: SoapGenerationRequest, options?: JobOptions): Promise<SoapGenerationResponse>
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
