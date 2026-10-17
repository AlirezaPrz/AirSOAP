import { describe, expect, it } from 'vitest'
import { ApiRequestError, isCancellation } from './client'
import { createMockApi } from './mockClient'

const request = { recordingId: 'rec-1' }

describe('createMockApi', () => {
  it('reports monotonically increasing progress up to 1', async () => {
    const api = createMockApi({ latencyScale: 0 })
    const progress: number[] = []
    const { transcript } = await api.transcribe(request, { onProgress: (p) => {
        if (p !== null) progress.push(p)
      }, })

    expect(transcript.segments.length).toBeGreaterThan(0)
    expect(progress.at(-1)).toBe(1)
    expect(progress).toEqual([...progress].sort((a, b) => a - b))
  })

  it('labels every transcript segment with a speaker', async () => {
    const api = createMockApi({ latencyScale: 0 })
    const { transcript } = await api.transcribe(request)
    const speakers = new Set(transcript.segments.map((s) => s.speaker))
    expect(speakers).toEqual(new Set(['clinician', 'patient']))
  })

  it('rejects with a cancellation error when aborted mid-job', async () => {
    const api = createMockApi({ latencyScale: 0 })
    const controller = new AbortController()
    const promise = api.transcribe(request, {
      signal: controller.signal,
      onProgress: () => controller.abort(),
    })
    await expect(promise).rejects.toSatisfy(isCancellation)
  })

  it('rejects immediately when the signal is already aborted', async () => {
    const api = createMockApi({ latencyScale: 0 })
    const controller = new AbortController()
    controller.abort()
    await expect(api.submitDemo({} as never, { signal: controller.signal })).rejects.toSatisfy(isCancellation)
  })

  it('fails once on demand with a retryable error, then succeeds', async () => {
    const api = createMockApi({ latencyScale: 0 })
    api.failNext('soap-generation')
    const soapRequest = { transcript: { segments: [], language: 'en', durationMs: 0 }, context: { patientAge: 40, encounterType: 'acute' as const } }

    const failure = await api.generateSoap(soapRequest).catch((e: unknown) => e)
    expect(failure).toBeInstanceOf(ApiRequestError)
    expect(failure).toMatchObject({ code: 'SOAP_GENERATION_FAILED', retryable: true })

    const { soap } = await api.generateSoap(soapRequest)
    expect(soap.subjective).toContain('40-year-old')
  })

  it('reports that demo submissions never touch an EMR', async () => {
    const api = createMockApi({ latencyScale: 0 })
    const result = await api.submitDemo({} as never)
    expect(result).toMatchObject({ emrConnected: false, recordModified: false })
  })
})
