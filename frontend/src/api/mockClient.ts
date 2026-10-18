import { createMockReview, createMockSoap, createMockTranscript } from '../mocks/encounter'
import { ApiRequestError, cancelledError, type AirSoapApi, type JobOptions } from './client'
import type { ApiError, LiveStatus, Speaker } from './types'

export type FailableOperation = 'transcription' | 'soap-generation' | 'clinical-review' | 'submission'

export interface MockAirSoapApi extends AirSoapApi {
  /** Makes the next call of `operation` fail with a retryable error. */
  failNext(operation: FailableOperation): void
  clearFailures(): void
}

export interface MockApiOptions {
  /** Multiplies every simulated latency. 0 resolves on the next tick (tests). */
  latencyScale?: number
  /** Simulate live transcription while recording (default true). */
  live?: boolean
}

const LATENCY_MS = {
  startRecording: 400,
  stopRecording: 400,
  discardRecording: 100,
  transcription: 3000,
  soapGeneration: 3500,
  clinicalReview: 2500,
  speakerSuggestion: 1800,
  liveLine: 2600,
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
  'clinical-review': {
    code: 'CLINICAL_REVIEW_FAILED',
    message: 'The clinical review timed out before finishing. Your note was not changed.',
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

/** Simulated speaker suggestions (no model): questions look like the clinician, short replies stay unknown. */
export function mockSpeaker(text: string): Speaker {
  if (text.trim().split(/\s+/).length <= 2) return 'unknown'
  return text.trim().endsWith('?') ? 'clinician' : 'patient'
}

export function createMockApi({ latencyScale = 1, live = true }: MockApiOptions = {}): MockAirSoapApi {
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
      return { recordingId, startedAt: new Date().toISOString(), live, liveSimulated: live }
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
      return {
        soap: createMockSoap(context),
        warnings: [],
        model: 'local-llm (mock)',
        generatedAt: new Date().toISOString(),
      }
    },

    async runClinicalReview(request, options) {
      await runJob('clinical-review', LATENCY_MS.clinicalReview, options)
      return { review: createMockReview(request), model: 'local-llm (mock)', generatedAt: new Date().toISOString() }
    },

    async suggestSpeakers({ segments }, options) {
      await wait(LATENCY_MS.speakerSuggestion, options?.signal)
      return {
        suggestions: segments.map((segment) => ({ id: segment.id, speaker: mockSpeaker(segment.text) })),
        model: 'local-llm (mock)',
      }
    },

    /** Simulated: the mock transcript's lines appear one by one, as they would after each pause. */
    followLive(recordingId, { onSnapshot, onConnection }) {
      const lines = createMockTranscript().segments
      let revision = 0
      let shown = 0
      let stopped = false
      const emit = (status: LiveStatus) =>
        onSnapshot({
          recordingId,
          revision: ++revision,
          status,
          behindMs: 0,
          segments: lines.slice(0, shown).map(({ id, startMs, endMs, text }) => ({ id, startMs, endMs, text })),
        })
      onConnection?.('open')
      emit('listening')
      const interval = Math.max(50, LATENCY_MS.liveLine * latencyScale)
      const timer = setInterval(() => {
        if (stopped || shown >= lines.length) return
        shown += 1
        emit('listening')
      }, interval)
      return () => {
        stopped = true
        clearInterval(timer)
      }
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
