import { render, screen, waitFor, within } from '@testing-library/react'
import userEvent from '@testing-library/user-event'
import { describe, expect, it, vi } from 'vitest'
import App from './App'
import { createHttpApi } from './api/httpClient'
import { fillTestPatient } from './test/patient'

const REC = 'a'.repeat(32)
const JOB = 'b'.repeat(32)
const SOAP_JOB = 'c'.repeat(32)
const REVIEW_JOB = 'd'.repeat(32)

function json(status: number, payload: unknown) {
  return new Response(status === 204 ? null : JSON.stringify(payload), {
    status,
    headers: { 'Content-Type': 'application/json' },
  })
}

const SOAP = {
  subjective: 'Patient reports a sore throat.',
  objective: 'Temperature 38.3.',
  assessment: 'Not documented.',
  plan: 'Not documented.',
}

/** Just enough of the FastAPI backend for one encounter, including both AI tasks. */
function backendFetch() {
  let transcriptionPolls = 0
  return vi.fn(async (input: RequestInfo | URL, init?: RequestInit) => {
    const route = `${init?.method ?? 'GET'} ${String(input)}`
    switch (route) {
      case 'POST /api/recording/start':
        return json(200, { recordingId: REC, startedAt: '2026-10-17T10:00:00Z' })
      case 'POST /api/recording/stop':
        return json(200, { recordingId: REC, durationMs: 6000, jobId: JOB })
      case `GET /api/transcriptions/${JOB}`:
        transcriptionPolls += 1
        return transcriptionPolls < 3
          ? json(200, { jobId: JOB, status: 'processing', progress: null, audioDurationMs: 6000, transcript: null, error: null })
          : json(200, {
              jobId: JOB,
              status: 'completed',
              progress: 100,
              audioDurationMs: 6000,
              transcript: {
                text: 'What brings you in today? A sore throat.',
                language: 'en',
                durationMs: 6000,
                segments: [
                  { id: 'seg-1', startMs: 0, endMs: 2500, text: 'What brings you in today?' },
                  { id: 'seg-2', startMs: 2500, endMs: 6000, text: 'A sore throat.' },
                ],
              },
              error: null,
            })
      case 'POST /api/ai/soap':
        return json(202, { jobId: SOAP_JOB, kind: 'soap' })
      case `GET /api/ai/jobs/${SOAP_JOB}`:
        return json(200, {
          jobId: SOAP_JOB,
          kind: 'soap',
          status: 'completed',
          soapResult: { soap: SOAP, warnings: [] },
          reviewResult: null,
          error: null,
        })
      case 'POST /api/ai/clinical-review':
        return json(202, { jobId: REVIEW_JOB, kind: 'clinical-review' })
      case `GET /api/ai/jobs/${REVIEW_JOB}`:
        return json(200, {
          jobId: REVIEW_JOB,
          kind: 'clinical-review',
          status: 'completed',
          soapResult: null,
          reviewResult: {
            informationGaps: [],
            followUpQuestions: [
              {
                id: 'sug-1',
                category: 'follow_up_question',
                description: 'How long has the sore throat lasted?',
                rationale: 'Duration helps to narrow the cause.',
                uncertainty: 'medium',
                evidence: [{ source: 'transcript', quote: 'A sore throat.', segmentId: 'seg-2' }],
              },
            ],
            differentialConsiderations: [],
            documentationInconsistencies: [],
            omittedUnverified: 0,
          },
          error: null,
        })
      case `DELETE /api/ai/jobs/${SOAP_JOB}`:
      case `DELETE /api/ai/jobs/${REVIEW_JOB}`:
        return json(204, null)
      default:
        return json(404, { detail: 'Not Found' })
    }
  })
}

describe('AirSOAP workflow against the backend', () => {
  it('transcribes, then generates the note and runs the review only on request, with labels the clinician set', async () => {
    const user = userEvent.setup()
    const fetch = backendFetch()
    render(<App api={createHttpApi({ fetch: fetch as unknown as typeof globalThis.fetch, pollIntervalMs: 1 })} />)

    expect(screen.queryByRole('combobox', { name: /simulate a service failure/i })).not.toBeInTheDocument()
    await fillTestPatient(user)
    await user.click(screen.getByRole('button', { name: 'Start encounter' }))
    await screen.findByRole('heading', { level: 1, name: 'Ready to record' })
    expect(screen.getByText(/microphone on this device records the visit/i)).toBeInTheDocument()

    await user.click(screen.getByRole('checkbox', { name: /consented/i }))
    await user.click(screen.getByRole('button', { name: /start recording/i }))
    await screen.findByRole('heading', { level: 1, name: 'Recording in progress' })
    await user.click(screen.getByRole('button', { name: /stop and transcribe/i }))

    await screen.findByRole('heading', { level: 1, name: 'Review draft note' }, { timeout: 3000 })
    const transcript = screen.getByRole('list', { name: 'Transcript segments' })
    expect(within(transcript).getByDisplayValue('What brings you in today?')).toBeInTheDocument()
    const speakers = within(transcript).getAllByRole('combobox', { name: /speaker for segment/i })
    expect(speakers[0]).toHaveDisplayValue('Unknown')

    // No AI call has happened yet.
    const called = () => fetch.mock.calls.map(([url]) => String(url))
    expect(called().some((url) => url.startsWith('/api/ai/'))).toBe(false)
    expect(screen.getByRole('textbox', { name: 'Plan' })).toHaveValue('')

    await user.selectOptions(speakers[0], 'clinician')
    await user.selectOptions(speakers[1], 'patient')
    await user.click(screen.getByRole('button', { name: 'Generate SOAP note' }))
    await waitFor(() => expect(screen.getByRole('textbox', { name: 'Subjective' })).toHaveValue(SOAP.subjective))
    expect(screen.getByRole('textbox', { name: 'Objective' })).toHaveValue(SOAP.objective)

    await user.click(screen.getByRole('button', { name: 'Run Clinical Review' }))
    await screen.findByText('How long has the sore throat lasted?')
    expect(screen.getByText(/Transcript, turn 2/)).toBeInTheDocument()

    // What each AI task was sent: the clinician's labels, the edited note, and no identifiers.
    const sent = (path: string) =>
      JSON.parse(String(fetch.mock.calls.find(([url, init]) => String(url) === path && init?.method === 'POST')?.[1]?.body))
    expect(sent('/api/ai/soap').segments.map((s: { speaker: string }) => s.speaker)).toEqual(['clinician', 'patient'])
    expect(sent('/api/ai/clinical-review').soap.subjective).toBe(SOAP.subjective)
    const bodies = fetch.mock.calls.map(([, init]) => String(init?.body ?? '')).join(' ')
    expect(bodies).not.toMatch(/Jordan|Testpatient|1991|DEMO-000123/)
    expect(fetch.mock.calls.every(([url]) => String(url).startsWith('/api/'))).toBe(true)

    // Finished AI jobs are removed from backend memory.
    await waitFor(() => {
      const deletes = fetch.mock.calls.filter(([, init]) => init?.method === 'DELETE').map(([url]) => String(url))
      expect(deletes).toEqual(expect.arrayContaining([`/api/ai/jobs/${SOAP_JOB}`, `/api/ai/jobs/${REVIEW_JOB}`]))
    })
  })
})
