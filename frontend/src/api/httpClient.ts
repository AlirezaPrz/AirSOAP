import {
  ApiRequestError,
  cancelledError,
  type AirSoapApi,
  type JobOptions,
  type RequestOptions,
} from './client'
import { createMockApi } from './mockClient'
import type {
  AiJobResponse,
  ApiError,
  ApiErrorCode,
  ClinicalReviewRequest,
  DiscardRecordingRequest,
  SoapGenerationRequest,
  StartRecordingRequest,
  StartRecordingResponse,
  StopRecordingRequest,
  StopRecordingResponse,
  Transcript,
  TranscriptionJobResponse,
  TranscriptionRequest,
  TranscriptionResponse,
} from './types'

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
      speaker: 'unknown',
      startMs: segment.startMs,
      endMs: segment.endMs,
      text: segment.text,
    })),
  }
}

/**
 * Talks to the local FastAPI backend for recording, transcription, SOAP generation and
 * Clinical Review. Demo submission stays local: no EMR is connected.
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

  async function request<T>(method: 'GET' | 'POST' | 'DELETE', path: string, body?: unknown, signal?: AbortSignal): Promise<T> {
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

  /** Drops an AI job (cancelling it if it is still running) so its result does not linger in backend memory. */
  const deleteAiJob = (jobId: string) =>
    request('DELETE', `/api/ai/jobs/${jobId}`).catch(() => {
      // Best effort: the job may already have expired.
    })

  /** Starts an AI task and polls until it finishes. Aborting cancels the backend job. */
  async function runAiJob(path: string, body: unknown, signal: AbortSignal | undefined): Promise<AiJobResponse> {
    const { jobId } = await request<{ jobId: string }>('POST', path, body, signal)
    const onAbort = () => void deleteAiJob(jobId)
    signal?.addEventListener('abort', onAbort, { once: true })
    try {
      for (;;) {
        const job = await request<AiJobResponse>('GET', `/api/ai/jobs/${jobId}`, undefined, signal)
        if (job.status === 'completed') return job
        if (job.status === 'error') {
          throw new ApiRequestError(
            job.error ?? { code: 'AI_FAILED', message: 'The AI task failed.', retryable: true },
          )
        }
        if (job.status === 'cancelled') throw cancelledError()
        await wait(pollIntervalMs, signal)
      }
    } finally {
      signal?.removeEventListener('abort', onAbort)
      if (!signal?.aborted) void deleteAiJob(jobId) // we have the result (or the failure): forget it
    }
  }

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

    async generateSoap(request: SoapGenerationRequest, options?: RequestOptions) {
      const job = await runAiJob('/api/ai/soap', request, options?.signal)
      if (!job.soapResult) {
        throw new ApiRequestError({ code: 'AI_FAILED', message: 'The AI task returned no note.', retryable: true })
      }
      return {
        soap: job.soapResult.soap,
        warnings: job.soapResult.warnings,
        model: 'local Qwen 2.5 3B',
        generatedAt: new Date().toISOString(),
      }
    },

    async runClinicalReview(request: ClinicalReviewRequest, options?: RequestOptions) {
      const job = await runAiJob('/api/ai/clinical-review', request, options?.signal)
      if (!job.reviewResult) {
        throw new ApiRequestError({ code: 'AI_FAILED', message: 'The AI task returned no review.', retryable: true })
      }
      return { review: job.reviewResult, model: 'local Qwen 2.5 3B', generatedAt: new Date().toISOString() }
    },

    submitDemo: (submitRequest, options) => local.submitDemo(submitRequest, options),
  }
}
