import type { PatientAge, PatientIdentity, SoapGenerationRequest, Transcript } from '../api/types'
import type { Encounter } from './reducer'

const ISO_DATE = /^(\d{4})-(\d{2})-(\d{2})$/

const MONTHS = [
  'January',
  'February',
  'March',
  'April',
  'May',
  'June',
  'July',
  'August',
  'September',
  'October',
  'November',
  'December',
]

export const REDACTED = '[REDACTED]'

/** Parses a strict YYYY-MM-DD date; returns null for impossible dates. */
export function parseIsoDate(value: string): { year: number; month: number; day: number } | null {
  const match = ISO_DATE.exec(value)
  if (!match) return null
  const [year, month, day] = match.slice(1).map(Number)
  const date = new Date(Date.UTC(year, month - 1, day))
  if (date.getUTCFullYear() !== year || date.getUTCMonth() !== month - 1 || date.getUTCDate() !== day) return null
  return { year, month, day }
}

/** Whole years between the date of birth and `today`; null if invalid or in the future. */
export function calculateAge(dateOfBirth: string, today: Date = new Date()): number | null {
  const dob = parseIsoDate(dateOfBirth)
  if (!dob) return null
  const month = today.getMonth() + 1
  const day = today.getDate()
  let age = today.getFullYear() - dob.year
  if (month < dob.month || (month === dob.month && day < dob.day)) age -= 1
  return age < 0 ? null : age
}

/** Ages of 90+ are aggregated so the value cannot narrow down a birth date. */
export function toModelAge(age: number): PatientAge {
  return age >= 90 ? '90+' : age
}

const escapeRegExp = (value: string) => value.replace(/[.*+?^${}()|[\]\\]/g, '\\$&')

/** Matches `term` only as a whole word, case-insensitively, including non-ASCII letters. */
const wholeWord = (term: string) => new RegExp(`(?<![\\p{L}\\p{N}])${escapeRegExp(term)}(?![\\p{L}\\p{N}])`, 'giu')

function dobVariants(dateOfBirth: string): string[] {
  const dob = parseIsoDate(dateOfBirth)
  if (!dob) return []
  const { year, month, day } = dob
  const mm = String(month).padStart(2, '0')
  const dd = String(day).padStart(2, '0')
  const monthName = MONTHS[month - 1]
  return [
    dateOfBirth,
    `${mm}/${dd}/${year}`,
    `${month}/${day}/${year}`,
    `${dd}/${mm}/${year}`,
    `${day}/${month}/${year}`,
    `${monthName} ${day}, ${year}`,
    `${monthName} ${day} ${year}`,
    `${day} ${monthName} ${year}`,
  ]
}

/**
 * Removes the patient's name (full and each part), date of birth and MRN
 * from free text, in case they were spoken during the visit.
 */
export function redactIdentifiers(text: string, patient: PatientIdentity): string {
  const nameParts = patient.fullName.trim().split(/\s+/).filter((part) => part.length >= 2)
  const terms = [patient.fullName.trim(), ...nameParts, ...dobVariants(patient.dateOfBirth), patient.mrn?.trim() ?? '']
    .filter((term) => term.length > 0)
    // Longest first so the full name is replaced before its parts.
    .sort((a, b) => b.length - a.length)

  return terms.reduce((result, term) => result.replace(wholeWord(term), REDACTED), text)
}

/**
 * The only way to build what the language model receives. Name, date of
 * birth and MRN are never copied in; the age is derived locally.
 */
export function buildSoapRequest(
  encounter: Encounter,
  transcript: Transcript,
  today: Date = new Date(),
): SoapGenerationRequest {
  const age = calculateAge(encounter.patient.dateOfBirth, today)
  if (age === null) throw new Error('Cannot derive patient age from date of birth.')

  return {
    transcript: {
      language: transcript.language,
      durationMs: transcript.durationMs,
      segments: transcript.segments.map((segment) => ({
        ...segment,
        text: redactIdentifiers(segment.text, encounter.patient),
      })),
    },
    context: { patientAge: toModelAge(age), encounterType: encounter.encounterType },
  }
}
