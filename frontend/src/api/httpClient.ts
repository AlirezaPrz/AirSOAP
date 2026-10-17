import {
  ApiRequestError,
  cancelledError,
  type AirSoapApi,
  type JobOptions,
  type RequestOptions,
} from './client'
import { createMockApi } from './mockClient'
import type {
  ApiError,
  ApiErrorCode,
  DiscardRecordingRequest,
  SoapNote,
  StartRecordingRequest,
  StartRecordingResponse,
  StopRecordingRequest,
  StopRecordingResponse,
  Transcript,
  TranscriptionJobResponse,
  TranscriptionRequest,
  TranscriptionResponse,
} from './types'

/** Shown in every SOAP field until local note generation exists. */
export const SOAP_PLACEHOLDER = 'Placeholder, SOAP generation is not connected yet (Phase 4)'

export interface HttpApiOptions {
  /** '' means same origin: the Vite dev proxy, or the backend serving the built frontend. */
  baseUrl?: string
  pollIntervalMs?: number
  fetch?: typeof fetch
}

const UNREACHABLE: ApiError = {
  code: 'UNAVAILABLE',
  message: 'The AirSOAP backend is not reachable. Check that it is running on this device.',
  retryable: true,
}

function readError(payload: unknown, status: number): ApiError {
  const error = (payload as { error?: Partial<ApiError> } | null)?.error
  if (error && typeof error.code === 'string' && typeof error.message === 'string') {
    return { code: error.code as ApiErrorCode, message: error.message, retryable: error.retryable === true }
  }
  return { code: 'UNKNOWN', message: `The backend returned an unexpected error (HTTP ${status}).`, retryable: false }
}

function wait(ms: number, signal?: AbortSignal): Promise<void> {
  return new Promise((resolve, reject) => {
    if (signal?.aborted) return reject(cancelledError())
    const onAbort = () => {
      clearTimeout(timer)
      reject(cancelledError())
    }
    const timer = setTimeout(() => {
      signal?.removeEventListener('abort', onAbort)
      resolve()
    }, ms)
    signal?.addEventListener('abort', onAbort, { once: true })
  })
}

function toTranscript(wire: NonNullable<TranscriptionJobResponse['transcript']>): Transcript {
  return {
    language: wire.language,
    durationMs: wire.durationMs,
    segments: wire.segments.map((segment) => ({
      id: segment.id,
      speaker: 'unassigned',
      startMs: segment.startMs,
      endMs: segment.endMs,
      text: segment.text,
    })),
  }
}

/**
 * Talks to the local FastAPI backend for recording and transcription.
 * SOAP generation is not connected yet (Phase 4) and demo submission stays local.
 */
export function createHttpApi({
  baseUrl = '',
  pollIntervalMs = 750,
  fetch: fetchImpl = (input, init) => globalThis.fetch(input, init),
}: HttpApiOptions = {}): AirSoapApi {
  const recording = new Set<string>()
  // Transcription job for each stopped recording, until the job finishes.
  const jobs = new Map<string, string>()
  const local = createMockApi()

  async function request<T>(method: 'GET' | 'POST', path: string, body?: unknown, signal?: AbortSignal): Promise<T> {
    let response: Response
    try {
      response = await fetchImpl(`${baseUrl}${path}`, {
        method,
        signal,
        headers: body === undefined ? undefined : { 'Content-Type': 'application/json' },
        body: body === undefined ? undefined : JSON.stringify(body),
      })
    } catch {
      throw signal?.aborted ? cancelledError() : new ApiRequestError(UNREACHABLE)
    }
    const payload: unknown = await response.json().catch(() => null)
    if (!response.ok) throw new ApiRequestError(readError(payload, response.status))
    return payload as T
  }

  const cancelJob = (jobId: string) =>
    request('POST', `/api/transcriptions/${jobId}/cancel`).catch(() => {
      // Best effort: the job may already be finished or expired.
    })

  return {
    async startRecording(_request: StartRecordingRequest, options?: RequestOptions) {
      // The encounter ID stays in the browser; the backend issues its own recording ID.
      const started = await request<StartRecordingResponse>('POST', '/api/recording/start', undefined, options?.signal)
      recording.add(started.recordingId)
      return started
    },

    async stopRecording({ recordingId }: StopRecordingRequest, options?: RequestOptions) {
      const stopped = await request<StopRecordingResponse>(
        'POST',
        '/api/recording/stop',
        { recordingId },
        options?.signal,
      )
      recording.delete(recordingId)
      if (stopped.jobId) jobs.set(recordingId, stopped.jobId)
      return stopped
    },

    async discardRecording({ recordingId }: DiscardRecordingRequest) {
      const jobId = jobs.get(recordingId)
      if (jobId) {
        jobs.delete(recordingId)
        await cancelJob(jobId)
      } else if (recording.delete(recordingId)) {
        await request('POST', '/api/recording/cancel', { recordingId })
      }
    },

    async transcribe({ recordingId }: TranscriptionRequest, { signal, onProgress }: JobOptions = {}) {
      const jobId = jobs.get(recordingId)
      if (!jobId) {
        throw new ApiRequestError({
          code: 'JOB_NOT_FOUND',
          message: 'No transcription is waiting for this recording.',
          retryable: false,
        })
      }
      const onAbort = () => void cancelJob(jobId)
      signal?.addEventListener('abort', onAbort, { once: true })
      try {
        for (;;) {
          const job = await request<TranscriptionJobResponse>('GET', `/api/transcriptions/${jobId}`, undefined, signal)
          if (job.status === 'completed' && job.transcript) {
            jobs.delete(recordingId)
            onProgress?.(1)
            return { transcript: toTranscript(job.transcript), model: 'whisper.cpp small.en' } satisfies TranscriptionResponse
          }
          if (job.status === 'error' || job.status === 'completed') {
            jobs.delete(recordingId)
            throw new ApiRequestError(
              job.error ?? { code: 'TRANSCRIPTION_FAILED', message: 'Transcription failed.', retryable: false },
            )
          }
          if (job.status === 'cancelled') {
            jobs.delete(recordingId)
            throw cancelledError()
          }
          onProgress?.(job.progress === null ? null : job.progress / 100)
          await wait(pollIntervalMs, signal)
        }
      } finally {
        signal?.removeEventListener('abort', onAbort)
      }
    },

    async generateSoap(_request, options) {
      if (options?.signal?.aborted) throw cancelledError()
      options?.onProgress?.(1)
      const soap: SoapNote = {
        subjective: SOAP_PLACEHOLDER,
        objective: SOAP_PLACEHOLDER,
        assessment: SOAP_PLACEHOLDER,
        plan: SOAP_PLACEHOLDER,
      }
      return { soap, model: 'not connected (Phase 4)', generatedAt: new Date().toISOString() }
    },

    submitDemo: (submitRequest, options) => local.submitDemo(submitRequest, options),
  }
}
