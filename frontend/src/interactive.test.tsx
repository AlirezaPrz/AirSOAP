import { render, screen, waitFor, within } from '@testing-library/react'
import userEvent from '@testing-library/user-event'
import { describe, expect, it } from 'vitest'
import App from './App'
import { Root } from './Root'
import type { Services } from './api'
import { createMockAuthApi } from './api/auth'
import { createMockApi } from './api/mockClient'

const services = (): Services => ({
  api: createMockApi({ latencyScale: 0 }),
  auth: createMockAuthApi(),
  onSessionEnded: () => () => {},
})

describe('sign-in showcase', () => {
  it('can be paused and stepped through by hand', async () => {
    const user = userEvent.setup()
    render(<Root services={services()} />)
    const carousel = await screen.findByRole('region', { name: 'How AirSOAP works' })
    expect(within(carousel).getByText('Look at the patient, not the keyboard.')).toBeInTheDocument()

    await user.click(within(carousel).getByRole('button', { name: 'Pause slides' }))
    expect(within(carousel).getByRole('button', { name: 'Play slides' })).toBeInTheDocument()

    await user.click(within(carousel).getByRole('button', { name: /slide 2 of 3/i }))
    expect(within(carousel).getByText('Everything stays in the room.')).toBeInTheDocument()
    expect(within(carousel).getByRole('img')).toHaveAccessibleName(/raspberry pi/i)
  })
})

describe('what the AI sees', () => {
  it('shows each typed detail on the side it goes to', async () => {
    const user = userEvent.setup()
    render(<App api={createMockApi({ latencyScale: 0 })} />)
    const preview = screen.getByRole('region', { name: 'What the AI sees' })
    expect(within(preview).getAllByText('not entered')).toHaveLength(3)

    await user.click(screen.getByRole('button', { name: 'Use sample patient' }))
    expect(within(preview).getByText('Jordan Testpatient')).toHaveClass('line-through')
    expect(within(preview).getByText(/^\d+ years$/)).toBeInTheDocument()
    expect(within(preview).getByText('Acute visit')).toBeInTheDocument()
  })
})

describe('labelling speakers', () => {
  it('labels alternating turns in one click and tracks progress in the steps bar', async () => {
    const user = userEvent.setup()
    render(<App api={createMockApi({ latencyScale: 0 })} />)
    await user.click(screen.getByRole('button', { name: 'Use sample patient' }))
    await user.click(screen.getByRole('button', { name: 'Start encounter' }))
    await user.click(await screen.findByRole('checkbox', { name: /consented/i }))
    await user.click(screen.getByRole('button', { name: /start recording/i }))
    await user.click(await screen.findByRole('button', { name: /stop and transcribe/i }))
    await screen.findByRole('heading', { level: 1, name: 'Review draft note' }, { timeout: 3000 })

    const steps = screen.getByRole('navigation', { name: 'Review steps' })
    expect(within(steps).getByRole('button', { name: /label speakers/i })).toHaveTextContent(/0 of \d+ turns/)

    await user.click(screen.getByRole('button', { name: 'Alternate, clinician first' }))
    const speakers = screen.getAllByRole('combobox', { name: /speaker for segment/i })
    expect(speakers[0]).toHaveDisplayValue('Clinician')
    expect(speakers[1]).toHaveDisplayValue('Patient')
    await waitFor(() =>
      expect(within(steps).getByRole('button', { name: /label speakers/i })).toHaveTextContent('Every turn labelled'),
    )
    expect(screen.queryByRole('button', { name: 'Alternate, clinician first' })).not.toBeInTheDocument()
  })

  it('fills the remaining unknown turns', async () => {
    const user = userEvent.setup()
    render(<App api={createMockApi({ latencyScale: 0 })} />)
    await user.click(screen.getByRole('button', { name: 'Use sample patient' }))
    await user.click(screen.getByRole('button', { name: 'Start encounter' }))
    await user.click(await screen.findByRole('checkbox', { name: /consented/i }))
    await user.click(screen.getByRole('button', { name: /start recording/i }))
    await user.click(await screen.findByRole('button', { name: /stop and transcribe/i }))
    await screen.findByRole('heading', { level: 1, name: 'Review draft note' }, { timeout: 3000 })

    const speakers = () => screen.getAllByRole('combobox', { name: /speaker for segment/i })
    await user.selectOptions(speakers()[0], 'clinician')
    await user.click(screen.getByRole('button', { name: 'Rest are patient' }))
    expect(speakers()[0]).toHaveDisplayValue('Clinician')
    expect(speakers().slice(1).every((s) => (s as HTMLSelectElement).value === 'patient')).toBe(true)
  })
})

describe('toasts', () => {
  it('confirms sign-in without a dialog', async () => {
    const user = userEvent.setup()
    render(<Root services={services()} />)
    await user.type(await screen.findByLabelText('Email'), 'doctor@example.test')
    await user.type(screen.getByLabelText('Password'), 'anything-at-all')
    await user.click(screen.getByRole('button', { name: 'Sign in' }))
    const notifications = await screen.findByRole('list', { name: 'Notifications' })
    expect(await within(notifications).findByText('Signed in as Demo Clinician.')).toBeInTheDocument()
    await user.click(within(notifications).getByRole('button', { name: 'Dismiss' }))
    expect(within(notifications).queryByText('Signed in as Demo Clinician.')).not.toBeInTheDocument()
  })
})
