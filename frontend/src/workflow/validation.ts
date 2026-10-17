import type { EncounterType } from '../api/types'
import { calculateAge, parseIsoDate } from './privacy'

export interface PatientFormValues {
  fullName: string
  dateOfBirth: string
  mrn: string
  encounterType: EncounterType | ''
}

export type PatientFormErrors = Partial<Record<keyof PatientFormValues, string>>

export const EMPTY_PATIENT_FORM: PatientFormValues = { fullName: '', dateOfBirth: '', mrn: '', encounterType: '' }

const MAX_AGE = 120

export function validatePatientForm(values: PatientFormValues, today: Date = new Date()): PatientFormErrors {
  const errors: PatientFormErrors = {}

  const name = values.fullName.trim()
  if (name === '') errors.fullName = 'Enter the patient’s full name.'
  else if (name.length < 2) errors.fullName = 'Name must be at least 2 characters.'
  else if (name.length > 100) errors.fullName = 'Name must be 100 characters or fewer.'

  if (values.dateOfBirth === '') {
    errors.dateOfBirth = 'Enter the date of birth.'
  } else if (!parseIsoDate(values.dateOfBirth)) {
    errors.dateOfBirth = 'Enter a valid date.'
  } else {
    const age = calculateAge(values.dateOfBirth, today)
    if (age === null) errors.dateOfBirth = 'Date of birth cannot be in the future.'
    else if (age > MAX_AGE) errors.dateOfBirth = `Age cannot exceed ${MAX_AGE} years.`
  }

  const mrn = values.mrn.trim()
  if (mrn !== '' && !/^[A-Za-z0-9-]{1,32}$/.test(mrn)) {
    errors.mrn = 'Use up to 32 letters, numbers, or hyphens.'
  }

  if (values.encounterType === '') errors.encounterType = 'Select an encounter type.'

  return errors
}
