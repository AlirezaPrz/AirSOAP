import { useEffect, useLayoutEffect, useReducer, useRef, type Dispatch } from 'react'
import { isCancellation, toApiError, type AirSoapApi } from '../api/client'
import type { EncounterType, PatientIdentity, ProcessingStage, SoapField, TranscriptSegment } from '../api/types'
import { buildSoapRequest } from './privacy'
import {
  canCancel,
  getRecordingId,
  initialWorkflowState,
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
  const reportProgress = (stage: ProcessingStage) => (progress: number | null) => {
    if (!signal.aborted) dispatch({ type: 'PROCESSING_PROGRESS', stage, progress })
  }

  let stage: ProcessingStage = state.stage
  try {
    let transcript = state.transcript
    if (!transcript) {
      const result = await api.transcribe(
        { recordingId: state.recordingId },
        { signal, onProgress: reportProgress('transcription') },
      )
      if (signal.aborted) return
      transcript = result.transcript
      dispatch({ type: 'TRANSCRIPT_READY', transcript })
      stage = 'soap-generation'
    }

    const { soap } = await api.generateSoap(buildSoapRequest(state.encounter, transcript), {
      signal,
      onProgress: reportProgress('soap-generation'),
    })
    if (!signal.aborted) dispatch({ type: 'SOAP_READY', soap })
  } catch (error) {
    if (signal.aborted || isCancellation(error)) return
    const apiError = toApiError(error, {
      code: 'UNKNOWN',
      message: 'Something went wrong while processing the recording.',
      retryable: true,
    })
    dispatch({ type: 'PROCESSING_FAILED', error: { stage, message: apiError.message, retryable: apiError.retryable } })
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
      const { recordingId } = await api.startRecording({ encounterId: current.encounter.id })
      const latest = stateRef.current
      if (latest.status !== 'ready' || latest.encounter.id !== current.encounter.id) {
        // Cancelled while the recorder was starting.
        discard(recordingId)
        return
      }
      dispatch({ type: 'RECORDING_STARTED', encounterId: current.encounter.id, recordingId, startedAt: Date.now() })
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

    editSoapField(field: SoapField, value: string) {
      dispatch({ type: 'EDIT_SOAP_FIELD', field, value })
    },

    requestSubmit() {
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
