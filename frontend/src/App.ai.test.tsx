import { act, render, screen, waitFor, within } from '@testing-library/react'
import userEvent from '@testing-library/user-event'
import { afterEach, describe, expect, it, vi } from 'vitest'
import App from './App'
import { ApiRequestError } from './api/client'
import { createMockApi } from './api/mockClient'
import type {
  ClinicalReview,
  ClinicalReviewRequest,
  ClinicalReviewResponse,
  SoapGenerationRequest,
  SoapGenerationResponse,
} from './api/types'

type User = ReturnType<typeof userEvent.setup>

interface Deferred<T> {
  promise: Promise<T>
  resolve: (value: T) => void
  reject: (error: unknown) => void
}

function deferred<T>(): Deferred<T> {
  let resolve!: (value: T) => void
  let reject!: (error: unknown) => void
  const promise = new Promise<T>((res, rej) => {
    resolve = res
    reject = rej
  })
  return { promise, resolve, reject }
}

const AI_SOAP = {
  subjective: 'AI subjective text.',
  objective: 'AI objective text.',
  assessment: 'AI assessment text.',
  plan: 'AI plan text.',
}
const soapResponse = (overrides: Partial<SoapGenerationResponse> = {}): SoapGenerationResponse => ({
  soap: AI_SOAP,
  warnings: [],
  model: 'test',
  generatedAt: '2026-10-17T10:00:00Z',
  ...overrides,
})

const suggestion = (id: string, description: string, extra = {}) => ({
  id,
  category: 'follow_up_question' as const,
  description,
  rationale: `Why: ${description}`,
  uncertainty: 'medium' as const,
  evidence: [{ source: 'transcript' as const, quote: 'I felt feverish last night', segmentId: 'seg-4' }],
  ...extra,
})
const REVIEW: ClinicalReview = {
  informationGaps: [],
  followUpQuestions: [suggestion('sug-a', 'Has the fever persisted?'), suggestion('sug-b', 'Any travel recently?')],
  differentialConsiderations: [
    suggestion('sug-c', 'Viral pharyngitis', {
      category: 'differential_consideration',
      missingInformation: 'No test result is documented.',
    }),
  ],
  documentationInconsistencies: [
    suggestion('sug-d', 'Plan wording differs from the conversation', {
      category: 'documentation_inconsistency',
      evidence: [{ source: 'note', quote: 'AI plan text.', field: 'plan' }],
    }),
  ],
  omittedUnverified: 2,
}
const reviewResponse = (review: ClinicalReview = REVIEW): ClinicalReviewResponse => ({
  review,
  model: 'test',
  generatedAt: '2026-10-17T10:00:00Z',
})

/** The mock for recording and transcription, with the two AI tasks under the test's control. */
function setup() {
  const base = createMockApi({ latencyScale: 0 })
  const soapCalls: { request: SoapGenerationRequest; signal?: AbortSignal; result: Deferred<SoapGenerationResponse> }[] = []
  const reviewCalls: { request: ClinicalReviewRequest; signal?: AbortSignal; result: Deferred<ClinicalReviewResponse> }[] = []
  const api = {
    ...base,
    generateSoap: vi.fn((request: SoapGenerationRequest, options?: { signal?: AbortSignal }) => {
      const result = deferred<SoapGenerationResponse>()
      soapCalls.push({ request, signal: options?.signal, result })
      return result.promise
    }),
    runClinicalReview: vi.fn((request: ClinicalReviewRequest, options?: { signal?: AbortSignal }) => {
      const result = deferred<ClinicalReviewResponse>()
      reviewCalls.push({ request, signal: options?.signal, result })
      return result.promise
    }),
  }
  return { api, soapCalls, reviewCalls }
}

async function reachReview(user: User) {
  await user.click(screen.getByRole('button', { name: 'Use sample patient' }))
  await user.click(screen.getByRole('button', { name: 'Start encounter' }))
  await screen.findByRole('heading', { level: 1, name: 'Ready to record' })
  await user.click(screen.getByRole('checkbox', { name: /consented/i }))
  await user.click(screen.getByRole('button', { name: /start recording/i }))
  await screen.findByRole('heading', { level: 1, name: 'Recording in progress' })
  await user.click(screen.getByRole('button', { name: /stop and transcribe/i }))
  await screen.findByRole('heading', { level: 1, name: 'Review draft note' }, { timeout: 3000 })
}

const textbox = (name: string) => screen.getByRole('textbox', { name })

/** Generate a note and let the fake model answer, leaving a filled note on screen. */
async function withNote(user: User, calls: ReturnType<typeof setup>['soapCalls']) {
  await user.click(screen.getByRole('button', { name: 'Generate SOAP note' }))
  await waitFor(() => expect(calls).toHaveLength(1))
  await act(async () => calls[0].result.resolve(soapResponse()))
  await waitFor(() => expect(textbox('Plan')).toHaveValue('AI plan text.'))
}

afterEach(() => vi.restoreAllMocks())

describe('SOAP generation in the review screen', () => {
  it('starts only when asked, shows progress, and can be cancelled without touching the note', async () => {
    const user = userEvent.setup()
    const { api, soapCalls } = setup()
    render(<App api={api} />)
    await reachReview(user)
    expect(api.generateSoap).not.toHaveBeenCalled()

    await user.click(screen.getByRole('button', { name: 'Generate SOAP note' }))
    expect(await screen.findByText(/Generating the SOAP note on this device/)).toBeInTheDocument()
    expect(screen.queryByRole('button', { name: 'Generate SOAP note' })).not.toBeInTheDocument()
    expect(screen.getByRole('button', { name: 'Submit note (demo)' })).toBeDisabled()

    await user.click(screen.getByRole('button', { name: 'Cancel' }))
    expect(soapCalls[0].signal?.aborted).toBe(true)
    expect(screen.queryByText(/Generating the SOAP note/)).not.toBeInTheDocument()

    // The model answers anyway, too late: the answer is thrown away.
    await act(async () => soapCalls[0].result.resolve(soapResponse()))
    expect(textbox('Plan')).toHaveValue('')
    expect(screen.getByRole('button', { name: 'Generate SOAP note' })).toBeInTheDocument()
  })

  it('keeps the sections the clinician types while the model is still working', async () => {
    const user = userEvent.setup()
    const { api, soapCalls } = setup()
    render(<App api={api} />)
    await reachReview(user)

    await user.click(screen.getByRole('button', { name: 'Generate SOAP note' }))
    await waitFor(() => expect(soapCalls).toHaveLength(1))
    await user.type(textbox('Plan'), 'My own plan')
    await act(async () => soapCalls[0].result.resolve(soapResponse()))

    await waitFor(() => expect(textbox('Subjective')).toHaveValue('AI subjective text.'))
    expect(textbox('Plan')).toHaveValue('My own plan') // the late answer did not overwrite it
    expect(textbox('Assessment')).toHaveValue('AI assessment text.')
  })

  it('sends the labels the clinician set and nothing identifying', async () => {
    const user = userEvent.setup()
    const { api, soapCalls } = setup()
    render(<App api={api} />)
    await reachReview(user)
    const speakers = screen.getAllByRole('combobox', { name: /speaker for segment/i })
    await user.selectOptions(speakers[0], 'clinician')
    await user.selectOptions(speakers[1], 'patient')

    await user.click(screen.getByRole('button', { name: 'Generate SOAP note' }))
    await waitFor(() => expect(soapCalls).toHaveLength(1))
    const { request } = soapCalls[0]
    expect(request.segments.slice(0, 3).map((s) => s.speaker)).toEqual(['clinician', 'patient', 'unknown'])
    expect(Object.keys(request).sort()).toEqual(['context', 'segments'])
    expect(JSON.stringify(request)).not.toMatch(/Jordan|Testpatient|1991|DEMO-000123/)
  })

  it('shows an API error without losing the clinician’s work, and can try again', async () => {
    const user = userEvent.setup()
    const { api, soapCalls } = setup()
    render(<App api={api} />)
    await reachReview(user)
    await user.selectOptions(screen.getAllByRole('combobox', { name: /speaker for segment/i })[0], 'clinician')

    await user.click(screen.getByRole('button', { name: 'Generate SOAP note' }))
    await waitFor(() => expect(soapCalls).toHaveLength(1))
    await act(async () =>
      soapCalls[0].result.reject(
        new ApiRequestError({ code: 'AI_TIMEOUT', message: 'The AI model took too long to answer.', retryable: true }),
      ),
    )

    const alert = await screen.findByRole('alert')
    expect(alert).toHaveTextContent('The AI model took too long to answer.')
    expect(screen.getAllByRole('combobox', { name: /speaker for segment/i })[0]).toHaveDisplayValue('Clinician')
    await user.type(textbox('Subjective'), 'Typed after the failure') // still editable
    expect(textbox('Subjective')).toHaveValue('Typed after the failure')

    await user.click(screen.getByRole('button', { name: 'Try again' }))
    await waitFor(() => expect(soapCalls).toHaveLength(2))
    expect(screen.queryByRole('alert')).not.toBeInTheDocument()
  })

  it('does not offer a retry for an error that retrying cannot fix', async () => {
    const user = userEvent.setup()
    const { api, soapCalls } = setup()
    render(<App api={api} />)
    await reachReview(user)
    await user.click(screen.getByRole('button', { name: 'Generate SOAP note' }))
    await waitFor(() => expect(soapCalls).toHaveLength(1))
    await act(async () =>
      soapCalls[0].result.reject(
        new ApiRequestError({ code: 'AI_MODEL_MISSING', message: 'The configured AI model is not installed.', retryable: false }),
      ),
    )
    expect(await screen.findByRole('alert')).toHaveTextContent('not installed')
    expect(screen.queryByRole('button', { name: 'Try again' })).not.toBeInTheDocument()
  })

  it('lists values the transcript never mentioned, and drops the warning once the clinician edits that section', async () => {
    const user = userEvent.setup()
    const { api, soapCalls } = setup()
    render(<App api={api} />)
    await reachReview(user)
    await user.click(screen.getByRole('button', { name: 'Generate SOAP note' }))
    await waitFor(() => expect(soapCalls).toHaveLength(1))
    await act(async () =>
      soapCalls[0].result.resolve(
        soapResponse({ warnings: [{ field: 'plan', message: 'The value "14" in Plan was not found in the transcript. Please check it.' }] }),
      ),
    )
    expect(await screen.findByText(/The value "14" in Plan was not found/)).toBeInTheDocument()
    await user.type(textbox('Plan'), ' edited')
    expect(screen.queryByText(/The value "14" in Plan/)).not.toBeInTheDocument()
  })

  it('asks before replacing an existing note with a regenerated draft', async () => {
    const user = userEvent.setup()
    const { api, soapCalls } = setup()
    render(<App api={api} />)
    await reachReview(user)
    await withNote(user, soapCalls)

    await user.click(screen.getByRole('button', { name: 'Regenerate' }))
    await user.click(within(screen.getByRole('dialog')).getByRole('button', { name: 'Keep my note' }))
    expect(api.generateSoap).toHaveBeenCalledTimes(1)

    await user.click(screen.getByRole('button', { name: 'Regenerate' }))
    await user.click(within(screen.getByRole('dialog')).getByRole('button', { name: 'Regenerate' }))
    await waitFor(() => expect(soapCalls).toHaveLength(2))
  })
})

describe('Clinical Review', () => {
  it('cannot run before a note exists and never runs on its own', async () => {
    const user = userEvent.setup()
    const { api, soapCalls } = setup()
    render(<App api={api} />)
    await reachReview(user)
    expect(screen.getByRole('button', { name: 'Run Clinical Review' })).toBeDisabled()
    await withNote(user, soapCalls)
    expect(screen.getByRole('button', { name: 'Run Clinical Review' })).toBeEnabled()
    expect(api.runClinicalReview).not.toHaveBeenCalled()
  })

  it('sends the current speaker labels and the clinician’s edited note', async () => {
    const user = userEvent.setup()
    const { api, soapCalls, reviewCalls } = setup()
    render(<App api={api} />)
    await reachReview(user)
    await user.selectOptions(screen.getAllByRole('combobox', { name: /speaker for segment/i })[1], 'patient')
    await withNote(user, soapCalls)
    await user.clear(textbox('Assessment'))
    await user.type(textbox('Assessment'), 'Edited by the doctor')

    await user.click(screen.getByRole('button', { name: 'Run Clinical Review' }))
    await waitFor(() => expect(reviewCalls).toHaveLength(1))
    const { request } = reviewCalls[0]
    expect(request.segments[1].speaker).toBe('patient')
    expect(request.soap.assessment).toBe('Edited by the doctor')
    expect(request.soap.plan).toBe('AI plan text.')
    expect(JSON.stringify(request)).not.toMatch(/Jordan|Testpatient|1991|DEMO-000123/)
  })

  it('shows suggestions apart from the note, with evidence, and never changes the note', async () => {
    const user = userEvent.setup()
    const { api, soapCalls, reviewCalls } = setup()
    render(<App api={api} />)
    await reachReview(user)
    await withNote(user, soapCalls)
    const before = ['Subjective', 'Objective', 'Assessment', 'Plan'].map((name) => (textbox(name) as HTMLTextAreaElement).value)

    await user.click(screen.getByRole('button', { name: 'Run Clinical Review' }))
    expect(await screen.findByText(/Running the clinical review on this device/)).toBeInTheDocument()
    expect(screen.getByRole('button', { name: 'Run Clinical Review' })).toBeDisabled()
    await waitFor(() => expect(reviewCalls).toHaveLength(1))
    await act(async () => reviewCalls[0].result.resolve(reviewResponse()))

    expect(await screen.findByRole('heading', { level: 3, name: 'Follow-up questions' })).toBeInTheDocument()
    expect(screen.getByText('Has the fever persisted?')).toBeInTheDocument()
    expect(screen.getByText('Viral pharyngitis')).toBeInTheDocument()
    expect(screen.getByText(/No test result is documented/)).toBeInTheDocument()
    expect(screen.getAllByText(/Transcript, turn 4/).length).toBeGreaterThan(0) // linked to the real turn
    expect(screen.getByText(/Note, Plan/)).toBeInTheDocument()
    expect(screen.getByText('AI-generated suggestions')).toBeInTheDocument()
    expect(screen.getByText(/2 suggestions were left out/)).toBeInTheDocument()
    expect(screen.getByRole('heading', { level: 3, name: 'Information gaps' })).toBeInTheDocument()
    expect(screen.getByText('No information gaps were suggested.')).toBeInTheDocument()

    const after = ['Subjective', 'Objective', 'Assessment', 'Plan'].map((name) => (textbox(name) as HTMLTextAreaElement).value)
    expect(after).toEqual(before) // nothing was added to the note
    expect(screen.queryByText('Has the fever persisted?', { selector: 'textarea' })).not.toBeInTheDocument()
  })

  it('lets the clinician dismiss suggestions, and they stay dismissed after a rerun', async () => {
    const user = userEvent.setup()
    const { api, soapCalls, reviewCalls } = setup()
    render(<App api={api} />)
    await reachReview(user)
    await withNote(user, soapCalls)
    await user.click(screen.getByRole('button', { name: 'Run Clinical Review' }))
    await waitFor(() => expect(reviewCalls).toHaveLength(1))
    await act(async () => reviewCalls[0].result.resolve(reviewResponse()))
    await screen.findByText('Has the fever persisted?')

    await user.click(screen.getByRole('button', { name: 'Dismiss suggestion: Has the fever persisted?' }))
    expect(screen.queryByText('Has the fever persisted?')).not.toBeInTheDocument()
    expect(screen.getByText('Any travel recently?')).toBeInTheDocument()

    // Edit the note and rerun: the model repeats the suggestion (same stable ID).
    await user.type(textbox('Plan'), ' plus a review in two days')
    expect(screen.getByText(/based on an earlier version/i)).toBeInTheDocument()
    await user.click(screen.getByRole('button', { name: 'Run Clinical Review' }))
    await waitFor(() => expect(reviewCalls).toHaveLength(2))
    expect(reviewCalls[1].request.soap.plan).toBe('AI plan text. plus a review in two days')
    await act(async () => reviewCalls[1].result.resolve(reviewResponse()))

    await waitFor(() => expect(screen.queryByText(/based on an earlier version/i)).not.toBeInTheDocument())
    expect(screen.queryByText('Has the fever persisted?')).not.toBeInTheDocument() // still dismissed
    expect(screen.getByText('Any travel recently?')).toBeInTheDocument()
  })

  it('shows an error, keeps the note and the earlier suggestions, and can be rerun', async () => {
    const user = userEvent.setup()
    const { api, soapCalls, reviewCalls } = setup()
    render(<App api={api} />)
    await reachReview(user)
    await withNote(user, soapCalls)
    await user.click(screen.getByRole('button', { name: 'Run Clinical Review' }))
    await waitFor(() => expect(reviewCalls).toHaveLength(1))
    await act(async () => reviewCalls[0].result.resolve(reviewResponse()))
    await screen.findByText('Has the fever persisted?')

    await user.click(screen.getByRole('button', { name: 'Run Clinical Review' }))
    await waitFor(() => expect(reviewCalls).toHaveLength(2))
    await act(async () =>
      reviewCalls[1].result.reject(
        new ApiRequestError({ code: 'AI_UNAVAILABLE', message: 'The local AI model service is not running.', retryable: true }),
      ),
    )
    expect(await screen.findByRole('alert')).toHaveTextContent('The local AI model service is not running.')
    expect(textbox('Plan')).toHaveValue('AI plan text.')
    expect(screen.getByText('Has the fever persisted?')).toBeInTheDocument()
    expect(screen.getByRole('button', { name: 'Run Clinical Review' })).toBeEnabled()
  })

  it('can be cancelled, and a late answer is ignored', async () => {
    const user = userEvent.setup()
    const { api, soapCalls, reviewCalls } = setup()
    render(<App api={api} />)
    await reachReview(user)
    await withNote(user, soapCalls)
    await user.click(screen.getByRole('button', { name: 'Run Clinical Review' }))
    await waitFor(() => expect(reviewCalls).toHaveLength(1))

    await user.click(screen.getByRole('button', { name: 'Cancel review' }))
    expect(reviewCalls[0].signal?.aborted).toBe(true)
    await act(async () => reviewCalls[0].result.resolve(reviewResponse()))
    expect(screen.queryByText('Has the fever persisted?')).not.toBeInTheDocument()
    expect(screen.getByRole('button', { name: 'Run Clinical Review' })).toBeEnabled()
  })

  it('is abandoned when the note is submitted while it runs', async () => {
    const user = userEvent.setup()
    const { api, soapCalls, reviewCalls } = setup()
    render(<App api={api} />)
    await reachReview(user)
    await withNote(user, soapCalls)
    await user.click(screen.getByRole('button', { name: 'Run Clinical Review' }))
    await waitFor(() => expect(reviewCalls).toHaveLength(1))

    await user.click(screen.getByRole('button', { name: 'Submit note (demo)' }))
    expect(reviewCalls[0].signal?.aborted).toBe(true)
    await user.click(within(screen.getByRole('dialog')).getByRole('button', { name: 'Back to review' }))
    expect(screen.queryByText(/Running the clinical review/)).not.toBeInTheDocument()
    expect(screen.getByRole('button', { name: 'Run Clinical Review' })).toBeEnabled()
  })
})

describe('leaving the encounter while the AI is working', () => {
  it('aborts both tasks and clears their results when the encounter is discarded', async () => {
    const user = userEvent.setup()
    const { api, soapCalls, reviewCalls } = setup()
    render(<App api={api} />)
    await reachReview(user)
    await withNote(user, soapCalls)
    await user.click(screen.getByRole('button', { name: 'Run Clinical Review' }))
    await waitFor(() => expect(reviewCalls).toHaveLength(1))

    await user.click(screen.getByRole('button', { name: 'Discard' }))
    await user.click(within(screen.getByRole('dialog')).getByRole('button', { name: 'Discard encounter' }))
    await screen.findByRole('heading', { level: 1, name: 'New encounter' })
    expect(reviewCalls[0].signal?.aborted).toBe(true)

    await act(async () => reviewCalls[0].result.resolve(reviewResponse()))
    expect(screen.getByRole('heading', { level: 1, name: 'New encounter' })).toBeInTheDocument()
    expect(screen.queryByText('Has the fever persisted?')).not.toBeInTheDocument()
    expect(screen.queryByText('AI plan text.')).not.toBeInTheDocument()
  })

  it('starts the next encounter with no suggestions, note or labels left over', async () => {
    const user = userEvent.setup()
    const { api, soapCalls, reviewCalls } = setup()
    render(<App api={api} />)
    await reachReview(user)
    await user.selectOptions(screen.getAllByRole('combobox', { name: /speaker for segment/i })[0], 'clinician')
    await withNote(user, soapCalls)
    await user.click(screen.getByRole('button', { name: 'Run Clinical Review' }))
    await waitFor(() => expect(reviewCalls).toHaveLength(1))
    await act(async () => reviewCalls[0].result.resolve(reviewResponse()))
    await screen.findByText('Has the fever persisted?')
    await user.click(screen.getByRole('button', { name: 'Dismiss suggestion: Has the fever persisted?' }))

    await user.click(screen.getByRole('button', { name: 'Discard' }))
    await user.click(within(screen.getByRole('dialog')).getByRole('button', { name: 'Discard encounter' }))
    await screen.findByRole('heading', { level: 1, name: 'New encounter' })

    await reachReview(user)
    expect(textbox('Plan')).toHaveValue('')
    expect(screen.getAllByRole('combobox', { name: /speaker for segment/i })[0]).toHaveDisplayValue('Unknown')
    expect(screen.queryByRole('heading', { level: 3, name: 'Follow-up questions' })).not.toBeInTheDocument()
    expect(screen.getByRole('button', { name: 'Run Clinical Review' })).toBeDisabled()
  })
})
