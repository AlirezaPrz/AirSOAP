import type { EncounterType, ProcessingStage, SoapField, Speaker } from '../api/types'

export const ENCOUNTER_TYPE_LABELS: Record<EncounterType, string> = {
  acute: 'Acute visit',
  'follow-up': 'Follow-up',
  'new-patient': 'New patient',
  wellness: 'Wellness visit',
}

export const SPEAKER_LABELS: Record<Speaker, string> = {
  clinician: 'Clinician',
  patient: 'Patient',
  unassigned: 'Unassigned',
}

export const STAGE_LABELS: Record<ProcessingStage, string> = {
  transcription: 'Transcription',
  'soap-generation': 'SOAP note generation',
}

export const SOAP_FIELD_META: Record<SoapField, { label: string; hint: string }> = {
  subjective: { label: 'Subjective', hint: 'History, symptoms, and the patient’s own account.' },
  objective: { label: 'Objective', hint: 'Vitals, exam findings, and test results.' },
  assessment: { label: 'Assessment', hint: 'Diagnosis or differential with reasoning.' },
  plan: { label: 'Plan', hint: 'Treatment, follow-up, and patient instructions.' },
}

/** 83_000 → "01:23" */
export function formatDuration(ms: number): string {
  const totalSeconds = Math.max(0, Math.floor(ms / 1000))
  const minutes = Math.floor(totalSeconds / 60)
  const seconds = totalSeconds % 60
  return `${String(minutes).padStart(2, '0')}:${String(seconds).padStart(2, '0')}`
}

/** "1991-04-12" → "Apr 12, 1991" without timezone drift. */
export function formatDate(isoDate: string): string {
  const [year, month, day] = isoDate.split('-').map(Number)
  return new Date(Date.UTC(year, month - 1, day)).toLocaleDateString('en-US', {
    year: 'numeric',
    month: 'short',
    day: 'numeric',
    timeZone: 'UTC',
  })
}
