import { render, screen, waitFor, within } from '@testing-library/react'
import userEvent from '@testing-library/user-event'
import { afterEach, beforeEach, describe, expect, it, vi } from 'vitest'
import App from './App'
import type { SoapGenerationRequest } from './api/types'
import { createMockApi, type MockAirSoapApi } from './api/mockClient'

let api: MockAirSoapApi

beforeEach(() => {
  api = createMockApi({ latencyScale: 0 })
})

afterEach(() => {
  vi.restoreAllMocks()
})

async function startEncounter(user: ReturnType<typeof userEvent.setup>) {
  await user.click(screen.getByRole('button', { name: 'Use sample patient' }))
  await user.click(screen.getByRole('button', { name: 'Start encounter' }))
  await screen.findByRole('heading', { level: 1, name: 'Ready to record' })
}

async function recordAndStop(user: ReturnType<typeof userEvent.setup>) {
  await user.click(screen.getByRole('checkbox', { name: /consented/i }))
  await user.click(screen.getByRole('button', { name: /start recording/i }))
  await screen.findByRole('heading', { level: 1, name: 'Recording in progress' })
  await user.click(screen.getByRole('button', { name: /stop and generate note/i }))
}

describe('AirSOAP workflow', () => {
  it('completes the full encounter and resets on return to the dashboard', async () => {
    const user = userEvent.setup()
    const generateSoap = vi.spyOn(api, 'generateSoap')
    render(<App api={api} />)

    await startEncounter(user)
    expect(screen.getByRole('region', { name: 'Current patient' })).toHaveTextContent('Jordan Testpatient')

    await recordAndStop(user)
    await screen.findByRole('heading', { level: 1, name: 'Processing the recording' })
    await screen.findByRole('heading', { level: 1, name: 'Review draft note' }, { timeout: 3000 })

    // The AI received age + encounter type, but no identifiers.
    const sent = generateSoap.mock.calls[0][0] as SoapGenerationRequest
    expect(sent.context).toMatchObject({ encounterType: 'acute' })
    expect(typeof sent.context.patientAge).toBe('number')
    expect(JSON.stringify(sent)).not.toMatch(/Jordan|Testpatient|1991-04-12|DEMO-000123/)

    // Speaker labels and editable transcript.
    expect(screen.getAllByRole('combobox', { name: /speaker for segment/i }).length).toBeGreaterThan(1)
    const firstSegment = screen.getByRole('textbox', { name: /segment 1 text \(clinician/i })
    await user.clear(firstSegment)
    await user.type(firstSegment, 'Edited greeting.')
    expect(firstSegment).toHaveValue('Edited greeting.')

    // Editing a SOAP field; emptying one blocks submission.
    const plan = screen.getByRole('textbox', { name: 'Plan' })
    await user.clear(plan)
    expect(screen.getByRole('button', { name: 'Submit note (demo)' })).toBeDisabled()
    expect(screen.getByText('Plan cannot be empty.')).toBeInTheDocument()
    await user.type(plan, 'Amoxicillin 500 mg BID x 10 days.')
    expect(screen.getByRole('button', { name: 'Submit note (demo)' })).toBeEnabled()

    // Confirmation dialog: back, then submit.
    await user.click(screen.getByRole('button', { name: 'Submit note (demo)' }))
    let dialog = screen.getByRole('dialog', { name: 'Submit demo note?' })
    expect(within(dialog).getByText(/No EMR is connected/)).toBeInTheDocument()
    await user.keyboard('{Escape}')
    expect(screen.queryByRole('dialog')).not.toBeInTheDocument()
    expect(screen.getByRole('textbox', { name: 'Plan' })).toHaveValue('Amoxicillin 500 mg BID x 10 days.')

    await user.click(screen.getByRole('button', { name: 'Submit note (demo)' }))
    dialog = screen.getByRole('dialog', { name: 'Submit demo note?' })
    await user.click(within(dialog).getByRole('button', { name: 'Submit demo note' }))

    await screen.findByRole('heading', { level: 1, name: 'Demo submission complete' })
    expect(
      screen.getByText('No external EMR is connected. No medical record has been created or modified.'),
    ).toBeInTheDocument()
    expect(screen.queryByText('Jordan Testpatient')).not.toBeInTheDocument()

    await user.click(screen.getByRole('button', { name: 'Return to dashboard' }))
    await screen.findByRole('heading', { level: 1, name: 'New encounter' })
    expect(screen.getByRole('textbox', { name: /full name/i })).toHaveValue('')
  })

  it('shows validation errors and does not start with an incomplete form', async () => {
    const user = userEvent.setup()
    render(<App api={api} />)

    await user.click(screen.getByRole('button', { name: 'Start encounter' }))
    expect(screen.getByText('Enter the patient’s full name.')).toBeInTheDocument()
    expect(screen.getByText('Enter the date of birth.')).toBeInTheDocument()
    expect(screen.getByRole('textbox', { name: /full name/i })).toHaveAttribute('aria-invalid', 'true')
    expect(screen.getByRole('textbox', { name: /full name/i })).toHaveFocus()
    expect(screen.getByRole('heading', { level: 1, name: 'New encounter' })).toBeInTheDocument()
  })

  it('requires recording consent before starting', async () => {
    const user = userEvent.setup()
    render(<App api={api} />)
    await startEncounter(user)
    expect(screen.getByRole('button', { name: /start recording/i })).toBeDisabled()
  })

  it('recovers from a processing failure via retry, resuming at SOAP generation', async () => {
    const user = userEvent.setup()
    const transcribe = vi.spyOn(api, 'transcribe')
    render(<App api={api} />)

    await user.click(screen.getByRole('button', { name: 'Use sample patient' }))
    await user.selectOptions(screen.getByRole('combobox', { name: /simulate a service failure/i }), 'soap-generation')
    await user.click(screen.getByRole('button', { name: 'Start encounter' }))
    await recordAndStop(user)

    await screen.findByRole('heading', { level: 1, name: 'Processing failed' }, { timeout: 3000 })
    expect(screen.getByRole('alert')).toHaveTextContent(/note generator timed out/i)

    await user.click(screen.getByRole('button', { name: 'Retry' }))
    await screen.findByRole('heading', { level: 1, name: 'Review draft note' }, { timeout: 3000 })
    expect(transcribe).toHaveBeenCalledTimes(1)
  })

  it('shows a submission error inside the dialog and allows retrying', async () => {
    const user = userEvent.setup()
    render(<App api={api} />)

    await user.click(screen.getByRole('button', { name: 'Use sample patient' }))
    await user.selectOptions(screen.getByRole('combobox', { name: /simulate a service failure/i }), 'submission')
    await user.click(screen.getByRole('button', { name: 'Start encounter' }))
    await recordAndStop(user)
    await screen.findByRole('heading', { level: 1, name: 'Review draft note' }, { timeout: 3000 })

    await user.click(screen.getByRole('button', { name: 'Submit note (demo)' }))
    await user.click(screen.getByRole('button', { name: 'Submit demo note' }))
    const dialog = screen.getByRole('dialog')
    await waitFor(() => expect(within(dialog).getByRole('alert')).toHaveTextContent(/Nothing was submitted/))

    await user.click(within(dialog).getByRole('button', { name: 'Submit demo note' }))
    await screen.findByRole('heading', { level: 1, name: 'Demo submission complete' })
  })

  it('cancels during processing, aborts the job, and discards the recording', async () => {
    const user = userEvent.setup()
    const slowApi = createMockApi({ latencyScale: 0.05 }) // ~150 ms jobs, long enough to cancel
    const discard = vi.spyOn(slowApi, 'discardRecording')
    const generateSoap = vi.spyOn(slowApi, 'generateSoap')
    render(<App api={slowApi} />)

    await startEncounter(user)
    await recordAndStop(user)
    await screen.findByRole('heading', { level: 1, name: 'Processing the recording' })

    await user.click(screen.getByRole('button', { name: 'Cancel' }))
    await user.click(within(screen.getByRole('dialog')).getByRole('button', { name: 'Discard encounter' }))

    await screen.findByRole('heading', { level: 1, name: 'New encounter' })
    expect(discard).toHaveBeenCalledTimes(1)
    await new Promise((resolve) => setTimeout(resolve, 300))
    expect(generateSoap).not.toHaveBeenCalled()
    expect(screen.getByRole('heading', { level: 1, name: 'New encounter' })).toBeInTheDocument()
  })

  it('cancels during recording and returns to an empty dashboard', async () => {
    const user = userEvent.setup()
    render(<App api={api} />)
    await startEncounter(user)
    await user.click(screen.getByRole('checkbox', { name: /consented/i }))
    await user.click(screen.getByRole('button', { name: /start recording/i }))
    await screen.findByRole('heading', { level: 1, name: 'Recording in progress' })

    await user.click(screen.getByRole('button', { name: 'Cancel encounter' }))
    await user.click(screen.getByRole('button', { name: 'Keep going' }))
    expect(screen.getByRole('heading', { level: 1, name: 'Recording in progress' })).toBeInTheDocument()

    await user.click(screen.getByRole('button', { name: 'Cancel encounter' }))
    await user.click(screen.getByRole('button', { name: 'Discard encounter' }))
    await screen.findByRole('heading', { level: 1, name: 'New encounter' })
    expect(screen.getByRole('textbox', { name: /full name/i })).toHaveValue('')
  })

  it('never touches browser storage or the console during a full encounter', async () => {
    const user = userEvent.setup()
    const storageCalls = [
      vi.spyOn(Storage.prototype, 'setItem'),
      vi.spyOn(Storage.prototype, 'getItem'),
      vi.spyOn(Storage.prototype, 'removeItem'),
    ]
    const consoleCalls = (['log', 'info', 'warn', 'error', 'debug'] as const).map((m) => vi.spyOn(console, m))
    render(<App api={api} />)

    await startEncounter(user)
    await recordAndStop(user)
    await screen.findByRole('heading', { level: 1, name: 'Review draft note' }, { timeout: 3000 })
    await user.click(screen.getByRole('button', { name: 'Submit note (demo)' }))
    await user.click(screen.getByRole('button', { name: 'Submit demo note' }))
    await screen.findByRole('heading', { level: 1, name: 'Demo submission complete' })

    for (const spy of [...storageCalls, ...consoleCalls]) expect(spy).not.toHaveBeenCalled()
  })
})
