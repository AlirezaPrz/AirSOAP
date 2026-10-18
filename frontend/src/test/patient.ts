import { screen } from '@testing-library/react'
import type userEvent from '@testing-library/user-event'

/** Types the fictional patient the old "Use sample patient" button filled in. */
export async function fillTestPatient(user: ReturnType<typeof userEvent.setup>) {
  await user.type(await screen.findByLabelText(/full name/i), 'Jordan Testpatient')
  await user.type(screen.getByLabelText(/date of birth/i), '1991-04-12')
  await user.type(screen.getByLabelText(/mrn/i), 'DEMO-000123')
  await user.click(screen.getByRole('radio', { name: 'Acute visit' }))
}
