import { createMockSoap, createMockTranscript } from '../mocks/encounter'
import { ApiRequestError, cancelledError, type AirSoapApi, type JobOptions } from './client'
import type { ApiError } from './types'

export type FailableOperation = 'transcription' | 'soap-generation' | 'submission'

export interface MockAirSoapApi extends AirSoapApi {
  /** Makes the next call of `operation` fail with a retryable error. */
  failNext(operation: FailableOperation): void
  clearFailures(): void
}

export interface MockApiOptions {
  /** Multiplies every simulated latency. 0 resolves on the next tick (tests). */
  latencyScale?: number
}

const LATENCY_MS = {
  startRecording: 400,
  stopRecording: 400,
  discardRecording: 100,
  transcription: 3000,
  soapGeneration: 3500,
  submission: 1200,
}

const PROGRESS_STEPS = 12

const FAILURES: Record<FailableOperation, ApiError> = {
  transcription: {
    code: 'TRANSCRIPTION_FAILED',
    message: 'The speech-to-text service stopped responding. The recording is still available on this device.',
    retryable: true,
  },
  'soap-generation': {
    code: 'SOAP_GENERATION_FAILED',
    message: 'The note generator timed out before finishing the SOAP draft.',
    retryable: true,
  },
  submission: {
    code: 'SUBMISSION_FAILED',
    message: 'The demo submission endpoint did not respond. Nothing was submitted.',
    retryable: true,
  },
}

function delay(ms: number, signal?: AbortSignal): Promise<void> {
  return new Promise((resolve, reject) => {
    if (signal?.aborted) {
      reject(cancelledError())
      return
    }
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

export function isMockApi(api: AirSoapApi): api is MockAirSoapApi {
  return 'failNext' in api
}

export function createMockApi({ latencyScale = 1 }: MockApiOptions = {}): MockAirSoapApi {
  const pendingFailures = new Set<FailableOperation>()
  const recordingStarts = new Map<string, number>()

  const wait = (ms: number, signal?: AbortSignal) => delay(ms * latencyScale, signal)

  /** Simulates a job with progress; a planned failure stops it halfway. */
  async function runJob(operation: FailableOperation, totalMs: number, options: JobOptions = {}) {
    const shouldFail = pendingFailures.delete(operation)
    const steps = shouldFail ? PROGRESS_STEPS / 2 : PROGRESS_STEPS
    for (let step = 1; step <= steps; step++) {
      await wait(totalMs / PROGRESS_STEPS, options.signal)
      options.onProgress?.(step / PROGRESS_STEPS)
    }
    if (shouldFail) throw new ApiRequestError(FAILURES[operation])
  }

  return {
    failNext(operation) {
      pendingFailures.add(operation)
    },

    clearFailures() {
      pendingFailures.clear()
    },

    async startRecording(_request, options) {
      await wait(LATENCY_MS.startRecording, options?.signal)
      const recordingId = `rec-${crypto.randomUUID()}`
      recordingStarts.set(recordingId, Date.now())
      return { recordingId, startedAt: new Date().toISOString() }
    },

    async stopRecording({ recordingId }, options) {
      await wait(LATENCY_MS.stopRecording, options?.signal)
      const startedAt = recordingStarts.get(recordingId)
      if (startedAt === undefined) {
        throw new ApiRequestError({ code: 'RECORDING_FAILED', message: 'Recording not found.', retryable: false })
      }
      return { recordingId, durationMs: Date.now() - startedAt }
    },

    async discardRecording({ recordingId }, options) {
      await wait(LATENCY_MS.discardRecording, options?.signal)
      recordingStarts.delete(recordingId)
    },

    async transcribe(_request, options) {
      await runJob('transcription', LATENCY_MS.transcription, options)
      return { transcript: createMockTranscript(), model: 'whisper (mock)' }
    },

    async generateSoap({ context }, options) {
      await runJob('soap-generation', LATENCY_MS.soapGeneration, options)
      return { soap: createMockSoap(context), model: 'local-llm (mock)', generatedAt: new Date().toISOString() }
    },

    async submitDemo(_request, options) {
      await runJob('submission', LATENCY_MS.submission, options)
      return {
        submissionId: `demo-${crypto.randomUUID().slice(0, 8)}`,
        submittedAt: new Date().toISOString(),
        emrConnected: false,
        recordModified: false,
      }
    },
  }
}
