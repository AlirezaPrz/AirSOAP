import { describe, expect, it, vi } from 'vitest'
import { createDefaultApi } from '.'
import { ApiRequestError, isCancellation } from './client'
import { createHttpApi } from './httpClient'
import { isMockApi } from './mockClient'
import type { TranscriptionJobResponse } from './types'

const REC = 'a'.repeat(32)
const JOB = 'b'.repeat(32)

type Handler = (body: unknown) => [number, unknown]

/** A scripted backend: each route returns the next response in its list (the last one repeats). */
function fakeBackend(routes: Record<string, Handler | Handler[]>) {
  const calls: { method: string; path: string; body: unknown }[] = []
  const counters = new Map<string, number>()
  const fetch = vi.fn(async (input: RequestInfo | URL, init?: RequestInit) => {
    const method = init?.method ?? 'GET'
    const path = String(input)
    const body = init?.body ? JSON.parse(String(init.body)) : undefined
    calls.push({ method, path, body })
    const key = `${method} ${path}`
    const route = routes[key]
    if (!route) return new Response(JSON.stringify({ detail: 'Not Found' }), { status: 404 })
    const handlers = Array.isArray(route) ? route : [route]
    const index = counters.get(key) ?? 0
    counters.set(key, index + 1)
    const [status, payload] = handlers[Math.min(index, handlers.length - 1)](body)
    return new Response(JSON.stringify(payload), { status, headers: { 'Content-Type': 'application/json' } })
  })
  return { fetch: fetch as unknown as typeof globalThis.fetch, calls }
}

const job = (overrides: Partial<TranscriptionJobResponse>): TranscriptionJobResponse => ({
  jobId: JOB,
  status: 'queued',
  progress: 0,
  audioDurationMs: 4200,
  transcript: null,
  error: null,
  ...overrides,
})

const completed = job({
  status: 'completed',
  progress: 100,
  transcript: {
    text: 'Hello there. How are you?',
    language: 'en',
    durationMs: 4200,
    segments: [
      { id: 'seg-1', startMs: 0, endMs: 2000, text: 'Hello there.' },
      { id: 'seg-2', startMs: 2000, endMs: 4200, text: 'How are you?' },
    ],
  },
})

const recordingRoutes = {
  'POST /api/recording/start': () => [200, { recordingId: REC, startedAt: '2026-10-17T10:00:00Z' }] as [number, unknown],
  'POST /api/recording/stop': () => [200, { recordingId: REC, durationMs: 4200, jobId: JOB }] as [number, unknown],
}

describe('createHttpApi', () => {
  it('records, stops, then polls one job until the whole transcript is ready', async () => {
    const backend = fakeBackend({
      ...recordingRoutes,
      [`GET /api/transcriptions/${JOB}`]: [
        () => [200, job({})],
        () => [200, job({ status: 'processing', progress: null })],
        () => [200, completed],
      ],
    })
    const api = createHttpApi({ fetch: backend.fetch, pollIntervalMs: 1 })
    const progress: (number | null)[] = []

    const started = await api.startRecording({ encounterId: 'enc-1' })
    const stopped = await api.stopRecording({ recordingId: started.recordingId })
    const { transcript } = await api.transcribe({ recordingId: REC }, { onProgress: (p) => progress.push(p) })

    expect(stopped).toEqual({ recordingId: REC, durationMs: 4200, jobId: JOB })
    expect(progress).toEqual([0, null, 1])
    expect(transcript.durationMs).toBe(4200)
    expect(transcript.segments).toEqual([
      { id: 'seg-1', speaker: 'unknown', startMs: 0, endMs: 2000, text: 'Hello there.' },
      { id: 'seg-2', speaker: 'unknown', startMs: 2000, endMs: 4200, text: 'How are you?' },
    ])
    // The encounter ID (and anything about the patient) never goes to the backend.
    expect(backend.calls[0]).toEqual({ method: 'POST', path: '/api/recording/start', body: undefined })
    expect(backend.calls[1].body).toEqual({ recordingId: REC })
  })

  it('surfaces backend error bodies as ApiRequestError', async () => {
    const backend = fakeBackend({
      'POST /api/recording/start': () => [
        409,
        { error: { code: 'RECORDING_IN_PROGRESS', message: 'A recording is already in progress.', retryable: false } },
      ],
    })
    const api = createHttpApi({ fetch: backend.fetch })
    const error = await api.startRecording({ encounterId: 'enc-1' }).catch((e: unknown) => e)
    expect(error).toBeInstanceOf(ApiRequestError)
    expect(error).toMatchObject({ code: 'RECORDING_IN_PROGRESS', message: 'A recording is already in progress.' })
  })

  it('reports an unreachable backend clearly', async () => {
    const api = createHttpApi({ fetch: vi.fn().mockRejectedValue(new TypeError('Failed to fetch')) })
    await expect(api.startRecording({ encounterId: 'enc-1' })).rejects.toMatchObject({ code: 'UNAVAILABLE' })
  })

  it('fails the transcription when the job fails, without retry', async () => {
    const backend = fakeBackend({
      ...recordingRoutes,
      [`GET /api/transcriptions/${JOB}`]: () => [
        200,
        job({
          status: 'error',
          progress: null,
          error: {
            code: 'TRANSCRIBER_UNAVAILABLE',
            message: 'The local transcription service is not running.',
            retryable: false,
          },
        }),
      ],
    })
    const api = createHttpApi({ fetch: backend.fetch, pollIntervalMs: 1 })
    await api.startRecording({ encounterId: 'enc-1' })
    await api.stopRecording({ recordingId: REC })
    await expect(api.transcribe({ recordingId: REC })).rejects.toMatchObject({
      code: 'TRANSCRIBER_UNAVAILABLE',
      retryable: false,
    })
  })

  it('cancels the backend job when the transcription is aborted', async () => {
    const backend = fakeBackend({
      ...recordingRoutes,
      [`GET /api/transcriptions/${JOB}`]: () => [200, job({ status: 'processing', progress: null })],
      [`POST /api/transcriptions/${JOB}/cancel`]: () => [200, job({ status: 'cancelled', progress: null })],
    })
    const api = createHttpApi({ fetch: backend.fetch, pollIntervalMs: 50 })
    await api.startRecording({ encounterId: 'enc-1' })
    await api.stopRecording({ recordingId: REC })

    const controller = new AbortController()
    const pending = api.transcribe({ recordingId: REC }, { signal: controller.signal, onProgress: () => controller.abort() })
    await expect(pending).rejects.toSatisfy(isCancellation)
    await vi.waitFor(() =>
      expect(backend.calls).toContainEqual({ method: 'POST', path: `/api/transcriptions/${JOB}/cancel`, body: undefined }),
    )
  })

  it('discards an active recording, or the job of a stopped one', async () => {
    const backend = fakeBackend({
      ...recordingRoutes,
      'POST /api/recording/cancel': () => [200, { recordingId: REC, status: 'cancelled' }],
      [`POST /api/transcriptions/${JOB}/cancel`]: () => [200, job({ status: 'cancelled', progress: null })],
    })
    const api = createHttpApi({ fetch: backend.fetch })

    await api.startRecording({ encounterId: 'enc-1' })
    await api.discardRecording({ recordingId: REC })
    expect(backend.calls.at(-1)).toEqual({ method: 'POST', path: '/api/recording/cancel', body: { recordingId: REC } })

    await api.startRecording({ encounterId: 'enc-2' })
    await api.stopRecording({ recordingId: REC })
    await api.discardRecording({ recordingId: REC })
    expect(backend.calls.at(-1)).toMatchObject({ method: 'POST', path: `/api/transcriptions/${JOB}/cancel` })

    const before = backend.calls.length
    await api.discardRecording({ recordingId: REC }) // nothing left to discard
    expect(backend.calls.length).toBe(before)
  })

  describe('AI tasks', () => {
    const segments = [
      { id: 'seg-1', speaker: 'clinician' as const, text: 'What brings you in today?' },
      { id: 'seg-2', speaker: 'patient' as const, text: 'A sore throat.' },
    ]
    const context = { patientAge: 35, encounterType: 'acute' as const }
    const soap = { subjective: 'S', objective: 'O', assessment: 'A', plan: 'P' }
    const AI_JOB = 'c'.repeat(32)
    const aiJob = (overrides: object) => ({
      jobId: AI_JOB,
      kind: 'soap',
      status: 'processing',
      soapResult: null,
      reviewResult: null,
      error: null,
      ...overrides,
    })
    const aiRoutes = (result: object | object[]) => ({
      'POST /api/ai/soap': () => [202, { jobId: AI_JOB, kind: 'soap' }] as [number, unknown],
      'POST /api/ai/clinical-review': () => [202, { jobId: AI_JOB, kind: 'clinical-review' }] as [number, unknown],
      [`GET /api/ai/jobs/${AI_JOB}`]: (Array.isArray(result) ? result : [result]).map(
        (body) => () => [200, body] as [number, unknown],
      ),
      [`DELETE /api/ai/jobs/${AI_JOB}`]: () => [204, null] as [number, unknown],
    })

    it('generates the SOAP note from the labelled segments and then deletes the job', async () => {
      const backend = fakeBackend(
        aiRoutes([
          aiJob({}),
          aiJob({ status: 'completed', soapResult: { soap, warnings: [{ field: 'plan', message: 'check 14' }] } }),
        ]),
      )
      const api = createHttpApi({ fetch: backend.fetch, pollIntervalMs: 1 })
      const result = await api.generateSoap({ segments, context })

      expect(result.soap).toEqual(soap)
      expect(result.warnings).toEqual([{ field: 'plan', message: 'check 14' }])
      expect(backend.calls[0]).toEqual({ method: 'POST', path: '/api/ai/soap', body: { segments, context } })
      await vi.waitFor(() =>
        expect(backend.calls).toContainEqual({ method: 'DELETE', path: `/api/ai/jobs/${AI_JOB}`, body: undefined }),
      )
    })

    it('sends the speaker labels and the edited note to the clinical review', async () => {
      const review = {
        informationGaps: [],
        followUpQuestions: [],
        differentialConsiderations: [],
        documentationInconsistencies: [],
        omittedUnverified: 2,
      }
      const backend = fakeBackend(aiRoutes(aiJob({ kind: 'clinical-review', status: 'completed', reviewResult: review })))
      const api = createHttpApi({ fetch: backend.fetch, pollIntervalMs: 1 })
      const result = await api.runClinicalReview({ segments, soap, context })

      expect(result.review).toEqual(review)
      expect(backend.calls[0]).toEqual({
        method: 'POST',
        path: '/api/ai/clinical-review',
        body: { segments, soap, context },
      })
      expect(backend.calls[0].body).toMatchObject({ segments: [{ speaker: 'clinician' }, { speaker: 'patient' }] })
    })

    it('reports a failed job with the backend’s safe message', async () => {
      const error = { code: 'AI_TIMEOUT', message: 'The AI model took too long to answer.', retryable: true }
      const backend = fakeBackend(aiRoutes(aiJob({ status: 'error', error })))
      const api = createHttpApi({ fetch: backend.fetch, pollIntervalMs: 1 })
      await expect(api.generateSoap({ segments, context })).rejects.toMatchObject(error)
    })

    it('reports request errors from the backend', async () => {
      const backend = fakeBackend({
        'POST /api/ai/soap': () => [
          413,
          { error: { code: 'REQUEST_TOO_LARGE', message: 'The transcript and note are too long.', retryable: false } },
        ],
      })
      const api = createHttpApi({ fetch: backend.fetch })
      await expect(api.generateSoap({ segments, context })).rejects.toMatchObject({
        code: 'REQUEST_TOO_LARGE',
        retryable: false,
      })
    })

    it('reports an unreachable backend', async () => {
      const api = createHttpApi({ fetch: vi.fn().mockRejectedValue(new TypeError('Failed to fetch')) })
      await expect(api.runClinicalReview({ segments, soap, context })).rejects.toMatchObject({ code: 'UNAVAILABLE' })
    })

    it('cancels the backend job when the request is aborted', async () => {
      const backend = fakeBackend(aiRoutes(aiJob({})))
      const api = createHttpApi({ fetch: backend.fetch, pollIntervalMs: 50 })
      const controller = new AbortController()
      const pending = api.generateSoap({ segments, context }, { signal: controller.signal })
      await vi.waitFor(() => expect(backend.calls.some((call) => call.method === 'GET')).toBe(true))
      controller.abort()
      await expect(pending).rejects.toSatisfy(isCancellation)
      await vi.waitFor(() =>
        expect(backend.calls).toContainEqual({ method: 'DELETE', path: `/api/ai/jobs/${AI_JOB}`, body: undefined }),
      )
    })
  })
})

describe('createDefaultApi', () => {
  it('uses the mock unless VITE_AIRSOAP_API is http', () => {
    expect(isMockApi(createDefaultApi({}))).toBe(true)
    expect(isMockApi(createDefaultApi({ VITE_AIRSOAP_API: 'mock' }))).toBe(true)
    expect(isMockApi(createDefaultApi({ VITE_AIRSOAP_API: 'http' }))).toBe(false)
  })
})
