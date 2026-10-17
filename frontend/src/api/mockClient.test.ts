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
    const soapRequest = {
      segments: [{ id: 's1', speaker: 'unknown' as const, text: 'Hello.' }],
      context: { patientAge: 40, encounterType: 'acute' as const },
    }

    const failure = await api.generateSoap(soapRequest).catch((e: unknown) => e)
    expect(failure).toBeInstanceOf(ApiRequestError)
    expect(failure).toMatchObject({ code: 'SOAP_GENERATION_FAILED', retryable: true })

    const { soap } = await api.generateSoap(soapRequest)
    expect(soap.subjective).toContain('40-year-old')
  })

  it('returns unknown speakers, like real Whisper output', async () => {
    const { transcript } = await createMockApi({ latencyScale: 0 }).transcribe({ recordingId: 'rec-1' })
    expect(new Set(transcript.segments.map((s) => s.speaker))).toEqual(new Set(['unknown']))
  })

  it('runs a clinical review whose suggestions quote the request’s own text', async () => {
    const api = createMockApi({ latencyScale: 0 })
    const request = {
      segments: [
        { id: 's1', speaker: 'clinician' as const, text: 'What brings you in today?' },
        { id: 's2', speaker: 'patient' as const, text: 'I have had a sore throat for three days.' },
      ],
      soap: { subjective: 'Sore throat.', objective: 'Not documented.', assessment: 'Pharyngitis.', plan: 'Fluids and rest.' },
      context: { patientAge: 40, encounterType: 'acute' as const },
    }
    const { review } = await api.runClinicalReview(request)
    const suggestions = [...review.informationGaps, ...review.followUpQuestions, ...review.differentialConsiderations, ...review.documentationInconsistencies]
    expect(suggestions.length).toBeGreaterThan(0)
    const text = JSON.stringify(request)
    for (const suggestion of suggestions) {
      expect(suggestion.evidence.length).toBeGreaterThan(0)
      for (const evidence of suggestion.evidence) expect(text).toContain(evidence.quote)
    }
    expect(review.followUpQuestions[0].id).toBe((await api.runClinicalReview(request)).review.followUpQuestions[0].id)
  })

  it('can make the clinical review fail once, then succeed', async () => {
    const api = createMockApi({ latencyScale: 0 })
    const request = {
      segments: [{ id: 's1', speaker: 'unknown' as const, text: 'Hello there.' }],
      soap: { subjective: 'x', objective: 'x', assessment: 'x', plan: 'x' },
      context: { patientAge: 40, encounterType: 'acute' as const },
    }
    api.failNext('clinical-review')
    await expect(api.runClinicalReview(request)).rejects.toMatchObject({ code: 'CLINICAL_REVIEW_FAILED', retryable: true })
    await expect(api.runClinicalReview(request)).resolves.toBeDefined()
  })

  it('reports that demo submissions never touch an EMR', async () => {
    const api = createMockApi({ latencyScale: 0 })
    const result = await api.submitDemo({} as never)
    expect(result).toMatchObject({ emrConnected: false, recordModified: false })
  })
})
