import { describe, expect, it } from 'vitest'
import type { Transcript } from '../api/types'
import { createMockTranscript } from '../mocks/encounter'
import { REDACTED, buildSoapRequest, calculateAge, redactIdentifiers, toModelAge } from './privacy'
import type { Encounter } from './reducer'
import { validatePatientForm } from './validation'

const today = new Date(2026, 9, 17) // Oct 17, 2026 (local time)

describe('calculateAge', () => {
  it('counts whole years, accounting for birthdays later in the year', () => {
    expect(calculateAge('1991-04-12', today)).toBe(35)
    expect(calculateAge('1991-10-17', today)).toBe(35)
    expect(calculateAge('1991-10-18', today)).toBe(34)
  })

  it('returns null for invalid or future dates', () => {
    expect(calculateAge('1991-02-30', today)).toBeNull()
    expect(calculateAge('12/04/1991', today)).toBeNull()
    expect(calculateAge('2027-01-01', today)).toBeNull()
  })
})

describe('toModelAge', () => {
  it('aggregates ages of 90 and over', () => {
    expect(toModelAge(89)).toBe(89)
    expect(toModelAge(90)).toBe('90+')
    expect(toModelAge(104)).toBe('90+')
  })
})

describe('redactIdentifiers', () => {
  const patient = { fullName: 'Jordan Testpatient', dateOfBirth: '1991-04-12', mrn: 'DEMO-000123' }

  it('removes full name, name parts, DOB formats, and MRN', () => {
    const text =
      'Hi Jordan Testpatient. Is it Ms. testpatient? Born April 12, 1991, or 04/12/1991, record DEMO-000123. Jordan agreed.'
    const result = redactIdentifiers(text, patient)
    expect(result).not.toMatch(/jordan|testpatient|1991|DEMO-000123/i)
    expect(result).toContain(REDACTED)
  })

  it('does not redact name fragments inside other words', () => {
    const result = redactIdentifiers('Jordanian food', { fullName: 'Jordan Lee', dateOfBirth: '1980-01-01' })
    expect(result).toBe('Jordanian food')
  })
})

describe('buildSoapRequest', () => {
  const encounter: Encounter = {
    id: 'enc-1',
    patient: { fullName: 'Jordan Testpatient', dateOfBirth: '1991-04-12', mrn: 'DEMO-000123' },
    encounterType: 'acute',
  }

  it('includes age and encounter type but never name, DOB, or MRN', () => {
    const base = createMockTranscript()
    const transcript: Transcript = {
      ...base,
      segments: [
        ...base.segments,
        { id: 'x', speaker: 'patient', startMs: 0, endMs: 1, text: 'I’m Jordan Testpatient, born 04/12/1991.' },
      ],
    }

    const request = buildSoapRequest(encounter, transcript, today)
    const serialized = JSON.stringify(request)

    expect(request.context).toEqual({ patientAge: 35, encounterType: 'acute' })
    expect(Object.keys(request).sort()).toEqual(['context', 'transcript'])
    expect(serialized).not.toMatch(/jordan|testpatient/i)
    expect(serialized).not.toContain('1991')
    expect(serialized).not.toContain('DEMO-000123')
  })

  it('keeps speaker labels and timing', () => {
    const transcript = createMockTranscript()
    const request = buildSoapRequest(encounter, transcript, today)
    expect(request.transcript.segments.map((s) => s.speaker)).toEqual(transcript.segments.map((s) => s.speaker))
    expect(request.transcript.durationMs).toBe(transcript.durationMs)
  })

  it('sends 90+ for elderly patients', () => {
    const elderly = { ...encounter, patient: { ...encounter.patient, dateOfBirth: '1930-01-01' } }
    expect(buildSoapRequest(elderly, createMockTranscript(), today).context.patientAge).toBe('90+')
  })
})

describe('validatePatientForm', () => {
  const valid = { fullName: 'Jordan Testpatient', dateOfBirth: '1991-04-12', mrn: '', encounterType: 'acute' as const }

  it('accepts a complete form', () => {
    expect(validatePatientForm(valid, today)).toEqual({})
  })

  it('requires name, DOB, and encounter type', () => {
    const errors = validatePatientForm({ fullName: ' ', dateOfBirth: '', mrn: '', encounterType: '' }, today)
    expect(Object.keys(errors).sort()).toEqual(['dateOfBirth', 'encounterType', 'fullName'])
  })

  it('rejects future and implausible dates of birth and malformed MRNs', () => {
    expect(validatePatientForm({ ...valid, dateOfBirth: '2030-01-01' }, today).dateOfBirth).toMatch(/future/)
    expect(validatePatientForm({ ...valid, dateOfBirth: '1880-01-01' }, today).dateOfBirth).toMatch(/exceed/)
    expect(validatePatientForm({ ...valid, mrn: 'bad mrn!' }, today).mrn).toBeDefined()
  })
})
