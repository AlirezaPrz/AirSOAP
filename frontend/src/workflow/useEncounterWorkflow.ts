import { useEffect, useLayoutEffect, useReducer, useRef, type Dispatch } from 'react'
import { isCancellation, toApiError, type AirSoapApi } from '../api/client'
import type { EncounterType, PatientIdentity, SoapField, Speaker, TranscriptSegment } from '../api/types'
import { buildClinicalReviewRequest, buildSoapRequest, buildSpeakerRequest } from './privacy'
import {
  canCancel,
  getRecordingId,
  initialWorkflowState,
  missingSoapFields,
  workflowReducer,
  type StateOf,
  type WorkflowAction,
} from './reducer'

export interface StartEncounterInput {
  patient: PatientIdentity
  encounterType: EncounterType
}

async function runProcessing(
  api: AirSoapApi,
  state: StateOf<'processing'>,
  dispatch: Dispatch<WorkflowAction>,
  signal: AbortSignal,
) {
  try {
    const result = await api.transcribe(
      { recordingId: state.recordingId },
      {
        signal,
        onProgress: (progress) => {
          if (!signal.aborted) dispatch({ type: 'PROCESSING_PROGRESS', stage: 'transcription', progress })
        },
      },
    )
    if (!signal.aborted) dispatch({ type: 'TRANSCRIPT_READY', transcript: result.transcript })
  } catch (error) {
    if (signal.aborted || isCancellation(error)) return
    const apiError = toApiError(error, {
      code: 'UNKNOWN',
      message: 'Something went wrong while processing the recording.',
      retryable: true,
    })
    dispatch({
      type: 'PROCESSING_FAILED',
      error: { stage: 'transcription', message: apiError.message, retryable: apiError.retryable },
    })
  }
}

/**
 * Owns the encounter state machine and its side effects. All encounter data
 * lives in this reducer only — nothing is persisted or logged.
 */
export function useEncounterWorkflow(api: AirSoapApi) {
  const [state, dispatch] = useReducer(workflowReducer, initialWorkflowState)
  const stateRef = useRef(state)
  useLayoutEffect(() => {
    stateRef.current = state
  })

  // Start (or restart, on retry) processing; unmounting or leaving the state aborts it.
  const processingKey = state.status === 'processing' ? `${state.recordingId}#${state.attempt}` : null
  useEffect(() => {
    const current = stateRef.current
    if (processingKey === null || current.status !== 'processing') return
    const controller = new AbortController()
    void runProcessing(api, current, dispatch, controller.signal)
    return () => controller.abort()
  }, [api, processingKey])

  // AI tasks run on demand while the review screen is open. Each gets a numbered request, and its
  // controller is aborted when the clinician cancels, submits or leaves, so a late answer is dropped.
  const requestCounter = useRef(0)
  const aiControllers = useRef<{
    soap: AbortController | null
    review: AbortController | null
    speakers: AbortController | null
  }>({ soap: null, review: null, speakers: null })
  const abortAi = () => {
    aiControllers.current.soap?.abort()
    aiControllers.current.review?.abort()
    aiControllers.current.speakers?.abort()
    aiControllers.current = { soap: null, review: null, speakers: null }
  }
  useEffect(() => {
    if (state.status !== 'review') abortAi()
  }, [state.status])
  useEffect(() => abortAi, [])

  const discard = (recordingId: string) => {
    api.discardRecording({ recordingId }).catch(() => {
      // Best effort: the mock backend holds no audio.
    })
  }

  return {
    state,

    startEncounter({ patient, encounterType }: StartEncounterInput) {
      dispatch({ type: 'START_ENCOUNTER', encounter: { id: crypto.randomUUID(), patient, encounterType } })
    },

    /** Rejects with an ApiRequestError if the recorder fails to start. */
    async startRecording() {
      const current = stateRef.current
      if (current.status !== 'ready') return
      const { recordingId, live, liveSimulated } = await api.startRecording({ encounterId: current.encounter.id })
      const latest = stateRef.current
      if (latest.status !== 'ready' || latest.encounter.id !== current.encounter.id) {
        // Cancelled while the recorder was starting.
        discard(recordingId)
        return
      }
      dispatch({
        type: 'RECORDING_STARTED',
        encounterId: current.encounter.id,
        recordingId,
        startedAt: Date.now(),
        live: live === true && typeof api.followLive === 'function',
        liveSimulated: liveSimulated === true,
      })
    },

    /** Rejects with an ApiRequestError if the recorder fails to stop. */
    async stopRecording() {
      const current = stateRef.current
      if (current.status !== 'recording') return
      const { recordingId } = await api.stopRecording({ recordingId: current.recordingId })
      dispatch({ type: 'RECORDING_STOPPED', recordingId })
    },

    cancelEncounter() {
      const current = stateRef.current
      if (!canCancel(current)) return
      abortAi()
      const recordingId = getRecordingId(current)
      dispatch({ type: 'CANCEL_ENCOUNTER' })
      if (recordingId) discard(recordingId)
    },

    retryProcessing() {
      dispatch({ type: 'RETRY_PROCESSING' })
    },

    editTranscriptSegment(segmentId: string, changes: Partial<Pick<TranscriptSegment, 'text' | 'speaker'>>) {
      dispatch({ type: 'EDIT_TRANSCRIPT_SEGMENT', segmentId, changes })
    },

    alternateSpeakers(first: Exclude<Speaker, 'unknown'>) {
      dispatch({ type: 'ALTERNATE_SPEAKERS', first })
    },

    editSoapField(field: SoapField, value: string) {
      dispatch({ type: 'EDIT_SOAP_FIELD', field, value })
    },

    /** Generates the note from the labelled transcript. Sections the clinician edits meanwhile are kept. */
    async generateSoap() {
      const current = stateRef.current
      if (current.status !== 'review' || current.draft.soapGeneration.status === 'running') return
      const requestId = ++requestCounter.current
      const controller = new AbortController()
      aiControllers.current.soap = controller
      dispatch({ type: 'SOAP_REQUESTED', requestId })
      try {
        const { soap, warnings } = await api.generateSoap(buildSoapRequest(current.encounter, current.draft.transcript), {
          signal: controller.signal,
        })
        if (!controller.signal.aborted) dispatch({ type: 'SOAP_SUCCEEDED', requestId, soap, warnings })
      } catch (error) {
        if (controller.signal.aborted || isCancellation(error)) return
        const apiError = toApiError(error, {
          code: 'AI_FAILED',
          message: 'The note could not be generated. Please try again.',
          retryable: true,
        })
        dispatch({ type: 'SOAP_FAILED', requestId, message: apiError.message, retryable: apiError.retryable })
      }
    },

    cancelSoap() {
      aiControllers.current.soap?.abort()
      aiControllers.current.soap = null
      dispatch({ type: 'SOAP_CANCELLED' })
    },

    /**
     * Runs only when the clinician asks. Sends each turn's words (identifiers removed) and fills in
     * labels the clinician has not set; turns edited meanwhile keep their label.
     */
    async suggestSpeakers() {
      const current = stateRef.current
      if (current.status !== 'review' || current.draft.speakers.status === 'running') return
      const requestId = ++requestCounter.current
      const controller = new AbortController()
      aiControllers.current.speakers = controller
      const request = buildSpeakerRequest(current.encounter, current.draft.transcript)
      const sentText = Object.fromEntries(current.draft.transcript.segments.map((s) => [s.id, s.text]))
      dispatch({ type: 'SPEAKERS_REQUESTED', requestId })
      try {
        const { suggestions } = await api.suggestSpeakers(request, { signal: controller.signal })
        if (!controller.signal.aborted) dispatch({ type: 'SPEAKERS_SUCCEEDED', requestId, suggestions, sentText })
      } catch (error) {
        if (controller.signal.aborted || isCancellation(error)) return
        const { message } = toApiError(error, {
          code: 'AI_FAILED',
          message: 'Speaker suggestions could not be made. Your labels were not changed.',
          retryable: true,
        })
        dispatch({ type: 'SPEAKERS_FAILED', requestId, message })
      }
    },

    cancelSpeakers() {
      aiControllers.current.speakers?.abort()
      aiControllers.current.speakers = null
      dispatch({ type: 'SPEAKERS_CANCELLED' })
    },

    /** Runs only when the clinician asks. Sends the current labels and note; never changes the note. */
    async runClinicalReview() {
      const current = stateRef.current
      if (current.status !== 'review' || current.draft.review.status === 'running') return
      const requestId = ++requestCounter.current
      const basedOnVersion = current.draft.version
      const controller = new AbortController()
      aiControllers.current.review = controller
      dispatch({ type: 'REVIEW_REQUESTED', requestId })
      try {
        const { review } = await api.runClinicalReview(
          buildClinicalReviewRequest(current.encounter, current.draft.transcript, current.draft.soap),
          { signal: controller.signal },
        )
        if (!controller.signal.aborted) dispatch({ type: 'REVIEW_SUCCEEDED', requestId, review, basedOnVersion })
      } catch (error) {
        if (controller.signal.aborted || isCancellation(error)) return
        const { message } = toApiError(error, {
          code: 'AI_FAILED',
          message: 'The clinical review could not be completed. Your note was not changed.',
          retryable: true,
        })
        dispatch({ type: 'REVIEW_FAILED', requestId, message })
      }
    },

    cancelClinicalReview() {
      aiControllers.current.review?.abort()
      aiControllers.current.review = null
      dispatch({ type: 'REVIEW_CANCELLED' })
    },

    dismissSuggestion(suggestionId: string) {
      dispatch({ type: 'REVIEW_DISMISSED', suggestionId })
    },

    requestSubmit() {
      const current = stateRef.current
      const accepted =
        current.status === 'review' &&
        current.draft.soapGeneration.status !== 'running' &&
        missingSoapFields(current.draft.soap).length === 0
      // A review still running when the note is submitted would only answer after the fact.
      if (accepted) aiControllers.current.review?.abort()
      dispatch({ type: 'REQUEST_SUBMIT' })
    },

    cancelSubmit() {
      dispatch({ type: 'CANCEL_SUBMIT' })
    },

    async confirmSubmit() {
      const current = stateRef.current
      if (current.status !== 'confirming') return
      dispatch({ type: 'CONFIRM_SUBMIT' })
      try {
        const submission = await api.submitDemo({
          encounterId: current.encounter.id,
          patient: current.encounter.patient,
          encounterType: current.encounter.encounterType,
          transcript: current.draft.transcript,
          soap: current.draft.soap,
        })
        dispatch({ type: 'SUBMIT_SUCCEEDED', submission })
      } catch (error) {
        const { message } = toApiError(error, {
          code: 'UNKNOWN',
          message: 'The demo submission failed. Nothing was submitted.',
          retryable: true,
        })
        dispatch({ type: 'SUBMIT_FAILED', message })
      }
    },

    returnToDashboard() {
      dispatch({ type: 'RETURN_TO_DASHBOARD' })
    },
  }
}

export type EncounterWorkflow = ReturnType<typeof useEncounterWorkflow>
