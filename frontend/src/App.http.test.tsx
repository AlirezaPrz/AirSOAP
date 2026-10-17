import { render, screen, within } from '@testing-library/react'
import userEvent from '@testing-library/user-event'
import { describe, expect, it, vi } from 'vitest'
import App from './App'
import { SOAP_PLACEHOLDER, createHttpApi } from './api/httpClient'

const REC = 'a'.repeat(32)
const JOB = 'b'.repeat(32)

function json(status: number, payload: unknown) {
  return new Response(JSON.stringify(payload), { status, headers: { 'Content-Type': 'application/json' } })
}

/** Just enough of the FastAPI backend for one encounter. */
function backendFetch() {
  let polls = 0
  return vi.fn(async (input: RequestInfo | URL, init?: RequestInit) => {
    const route = `${init?.method ?? 'GET'} ${String(input)}`
    switch (route) {
      case 'POST /api/recording/start':
        return json(200, { recordingId: REC, startedAt: '2026-10-17T10:00:00Z' })
      case 'POST /api/recording/stop':
        return json(200, { recordingId: REC, durationMs: 6000, jobId: JOB })
      case `GET /api/transcriptions/${JOB}`:
        polls += 1
        return polls < 3
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
      default:
        return json(404, { detail: 'Not Found' })
    }
  })
}

describe('AirSOAP workflow against the backend', () => {
  it('shows the real transcript with unassigned speakers and placeholder SOAP fields', async () => {
    const user = userEvent.setup()
    const fetch = backendFetch()
    render(<App api={createHttpApi({ fetch: fetch as unknown as typeof globalThis.fetch, pollIntervalMs: 1 })} />)

    expect(screen.queryByRole('combobox', { name: /simulate a service failure/i })).not.toBeInTheDocument()
    await user.click(screen.getByRole('button', { name: 'Use sample patient' }))
    await user.click(screen.getByRole('button', { name: 'Start encounter' }))
    await screen.findByRole('heading', { level: 1, name: 'Ready to record' })
    expect(screen.getByText(/recorded by the microphone on this device/i)).toBeInTheDocument()

    await user.click(screen.getByRole('checkbox', { name: /consented/i }))
    await user.click(screen.getByRole('button', { name: /start recording/i }))
    await screen.findByRole('heading', { level: 1, name: 'Recording in progress' })
    await user.click(screen.getByRole('button', { name: /stop and generate note/i }))

    await screen.findByRole('heading', { level: 1, name: 'Review draft note' }, { timeout: 3000 })
    const transcript = screen.getByRole('list', { name: 'Transcript segments' })
    expect(within(transcript).getByDisplayValue('What brings you in today?')).toBeInTheDocument()
    expect(within(transcript).getAllByRole('combobox', { name: /speaker for segment/i })[0]).toHaveDisplayValue('Unassigned')
    expect(screen.getByRole('textbox', { name: 'Plan' })).toHaveValue(SOAP_PLACEHOLDER)

    // Only recording and transcription requests reach the backend, never patient details.
    const bodies = fetch.mock.calls.map(([, init]) => String(init?.body ?? ''))
    expect(bodies.join(' ')).not.toMatch(/Jordan|Testpatient|1991|DEMO-000123/)
    expect(fetch.mock.calls.every(([url]) => String(url).startsWith('/api/'))).toBe(true)
  })
})
