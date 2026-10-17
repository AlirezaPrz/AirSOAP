// Synthetic data only. The patient and the conversation are fictional.
import type {
  AiSegment,
  ClinicalReview,
  ClinicalReviewRequest,
  EncounterType,
  PatientIdentity,
  ReviewSuggestion,
  SoapGenerationContext,
  SoapNote,
  Speaker,
  Transcript,
} from '../api/types'

export const SAMPLE_PATIENT: PatientIdentity & { encounterType: EncounterType } = {
  fullName: 'Jordan Testpatient',
  dateOfBirth: '1991-04-12',
  mrn: 'DEMO-000123',
  encounterType: 'acute',
}

/** Who really spoke each line. The mock transcript hides this, like real Whisper output: speakers start `unknown`. */
const SCRIPT: ReadonlyArray<[Speaker, string]> = [
  ['clinician', 'Good morning. What brings you in today?'],
  ['patient', "I've had a sore throat for about three days now, and it keeps getting worse. It really hurts to swallow."],
  ['clinician', 'Have you had any fever or chills?'],
  ['patient', 'I felt feverish last night. I checked and it was around 101.'],
  ['clinician', 'Any cough, runny nose, or congestion?'],
  ['patient', 'No cough, not really. Just the throat, and my neck feels a bit swollen.'],
  ['clinician', 'Has anyone around you been sick recently?'],
  ['patient', 'A coworker had strep throat last week, actually.'],
  ['clinician', 'Any allergies to medications?'],
  ['patient', 'Not that I know of.'],
  [
    'clinician',
    'Okay, let me take a look. Your temperature today is 38.3. Your tonsils are swollen with some white exudate, and the lymph nodes at the front of your neck are tender. Your lungs sound clear.',
  ],
  ['clinician', "I'm going to do a rapid strep test. … The rapid strep test is positive."],
  [
    'clinician',
    "I'll prescribe amoxicillin, 500 milligrams twice a day for ten days. Take ibuprofen or acetaminophen for pain and fever, drink plenty of fluids, and stay home until you've been on the antibiotic for at least 12 hours.",
  ],
  ['patient', 'Okay. How soon should I start feeling better?'],
  [
    'clinician',
    "Most people feel better within two to three days. If you're not improving by then, or if you have trouble breathing or swallowing liquids, come back or go to urgent care.",
  ],
]

/** Roughly 15 characters per second of speech plus a short pause. */
export function createMockTranscript(): Transcript {
  let cursor = 0
  const segments = SCRIPT.map(([, text], index) => {
    const startMs = cursor
    const endMs = startMs + Math.round((text.length / 15) * 1000)
    cursor = endMs + 600
    return { id: `seg-${index + 1}`, speaker: 'unknown' as const, startMs, endMs, text }
  })
  return { segments, language: 'en', durationMs: cursor }
}

export function createMockSoap({ patientAge }: SoapGenerationContext): SoapNote {
  const who = patientAge === '90+' ? 'Patient aged 90 or older' : `${patientAge}-year-old patient`
  return {
    subjective: `${who} presenting with 3 days of progressively worsening sore throat and odynophagia. Reports subjective fever last night (~101 °F). Denies cough and rhinorrhea. Reports anterior neck swelling. Sick contact: coworker diagnosed with streptococcal pharyngitis last week. No known drug allergies.`,
    objective:
      'Temp 38.3 °C. Oropharynx: tonsillar swelling with white exudate. Neck: tender anterior cervical lymphadenopathy. Lungs: clear to auscultation bilaterally. Rapid strep antigen test: positive.',
    assessment:
      'Acute streptococcal pharyngitis, confirmed by positive rapid antigen test. Centor criteria 4/4 (fever, tonsillar exudate, tender anterior cervical nodes, absence of cough).',
    plan: [
      '1. Amoxicillin 500 mg PO twice daily × 10 days.',
      '2. Ibuprofen or acetaminophen as needed for pain and fever.',
      '3. Encourage oral fluids; remain home until ≥12 hours of antibiotic therapy.',
      '4. Return precautions: no improvement in 48–72 hours, difficulty breathing, or inability to swallow liquids.',
    ].join('\n'),
  }
}

/** Small deterministic hash, so mock suggestions keep the same ID when the review is rerun. */
function stableId(category: string, description: string): string {
  let hash = 5381
  for (const char of `${category}|${description.toLowerCase()}`) hash = ((hash << 5) + hash + char.charCodeAt(0)) >>> 0
  return `sug-${hash.toString(16).padStart(8, '0')}`
}

const firstWords = (text: string, count = 10) => text.trim().split(/\s+/).slice(0, count).join(' ')

/** Mock suggestions that quote the request's own text, as the real backend requires. */
export function createMockReview({ segments, soap }: ClinicalReviewRequest): ClinicalReview {
  const pick = (speaker: Speaker): AiSegment | undefined =>
    segments.find((segment) => segment.speaker === speaker) ?? segments[0]
  const patientLine = pick('patient')
  const clinicianLine = pick('clinician')
  const planQuote = firstWords(soap.plan)

  const make = (
    category: ReviewSuggestion['category'],
    description: string,
    rationale: string,
    segment: AiSegment | undefined,
    extra: Partial<ReviewSuggestion> = {},
  ): ReviewSuggestion => ({
    id: stableId(category, description),
    category,
    description,
    rationale: `${rationale} (Mock output; no AI model was used.)`,
    uncertainty: 'medium',
    evidence: segment ? [{ source: 'transcript', quote: firstWords(segment.text), segmentId: segment.id }] : [],
    ...extra,
  })

  return {
    informationGaps: [
      make(
        'information_gap',
        'The transcript does not say how the symptoms have changed since they began.',
        'Progression helps to judge urgency.',
        patientLine,
        { gapType: 'uncertain' },
      ),
    ],
    followUpQuestions: [
      make(
        'follow_up_question',
        'Has anything made the symptoms better or worse?',
        'The patient described the symptoms, but relieving and aggravating factors were not discussed.',
        patientLine,
      ),
    ],
    differentialConsiderations: [
      make(
        'differential_consideration',
        'A viral cause, alongside the condition in your assessment',
        'The documented symptoms are not specific to one cause.',
        clinicianLine,
        { missingInformation: 'Test results and exposure history are not documented.' },
      ),
    ],
    documentationInconsistencies: planQuote
      ? [
          {
            id: stableId('documentation_inconsistency', 'The plan could be checked against the conversation.'),
            category: 'documentation_inconsistency',
            description: 'The plan could be checked against the conversation.',
            rationale: 'Confirm that each item in the plan was discussed. (Mock output; no AI model was used.)',
            uncertainty: 'low',
            evidence: [{ source: 'note', quote: planQuote, field: 'plan' }],
          },
        ]
      : [],
    omittedUnverified: 0,
  }
}
