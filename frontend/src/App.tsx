import { useState } from 'react'
import type { AirSoapApi } from './api/client'
import { createDefaultApi } from './api'
import { isMockApi } from './api/mockClient'
import type { AccountInfo } from './components/AccountSummary'
import { AppShell } from './components/AppShell'
import { ConfirmDialog } from './components/ConfirmDialog'
import { CompletionScreen } from './screens/CompletionScreen'
import { DashboardScreen, type SimulatedFailure } from './screens/DashboardScreen'
import { ProcessingScreen } from './screens/ProcessingScreen'
import { RecordingScreen } from './screens/RecordingScreen'
import { ReviewScreen } from './screens/ReviewScreen'
import type { WorkflowStatus } from './workflow/reducer'
import { useEncounterWorkflow, type StartEncounterInput } from './workflow/useEncounterWorkflow'

const STEP_GROUP: Record<WorkflowStatus, string> = {
  idle: 'patient',
  ready: 'record',
  recording: 'record',
  processing: 'process',
  'processing-error': 'process',
  review: 'review',
  confirming: 'review',
  submitting: 'review',
  completed: 'done',
}

interface AppProps {
  /** Backend implementation; defaults to the mock, or the local backend with VITE_AIRSOAP_API=http. */
  api?: AirSoapApi
  /** The signed-in clinician. Signing out unmounts this component, which discards the encounter. */
  account?: AccountInfo
}

export default function App({ api: providedApi, account }: AppProps) {
  const [defaultApi] = useState(() => createDefaultApi())
  const api = providedApi ?? defaultApi
  const workflow = useEncounterWorkflow(api)
  const { state } = workflow
  const [confirmingSignOut, setConfirmingSignOut] = useState(false)
  const encounterOpen = state.status !== 'idle' && state.status !== 'completed'

  const shellAccount: AccountInfo | undefined = account && {
    ...account,
    onSignOut: () => (encounterOpen ? setConfirmingSignOut(true) : account.onSignOut()),
  }

  const handleStart = (input: StartEncounterInput, simulatedFailure: SimulatedFailure) => {
    if (isMockApi(api)) {
      api.clearFailures()
      if (simulatedFailure !== 'none') api.failNext(simulatedFailure)
    }
    workflow.startEncounter(input)
  }

  const screen = (() => {
    switch (state.status) {
      case 'idle':
        return <DashboardScreen onStart={handleStart} showDemoControls={isMockApi(api)} />
      case 'ready':
      case 'recording':
        return (
          <RecordingScreen
            key={state.status}
            state={state}
            simulated={isMockApi(api)}
            onStartRecording={workflow.startRecording}
            onStopRecording={workflow.stopRecording}
            onCancel={workflow.cancelEncounter}
          />
        )
      case 'processing':
      case 'processing-error':
        return (
          <ProcessingScreen
            key={`${state.status}-${state.attempt}`}
            state={state}
            onRetry={workflow.retryProcessing}
            onCancel={workflow.cancelEncounter}
          />
        )
      case 'review':
      case 'confirming':
      case 'submitting':
        return (
          <ReviewScreen
            state={state}
            onEditSegment={workflow.editTranscriptSegment}
            onEditSoap={workflow.editSoapField}
            onGenerateSoap={workflow.generateSoap}
            onCancelSoap={workflow.cancelSoap}
            onRunReview={workflow.runClinicalReview}
            onCancelReview={workflow.cancelClinicalReview}
            onDismissSuggestion={workflow.dismissSuggestion}
            onRequestSubmit={workflow.requestSubmit}
            onCancelSubmit={workflow.cancelSubmit}
            onConfirmSubmit={workflow.confirmSubmit}
            onDiscard={workflow.cancelEncounter}
          />
        )
      case 'completed':
        return <CompletionScreen submission={state.submission} onReturn={workflow.returnToDashboard} />
    }
  })()

  return (
    <AppShell status={state.status} encounter={'encounter' in state ? state.encounter : null} account={shellAccount}>
      {/* Each step of the encounter arrives; the review states share one screen, so they share one key. */}
      <div key={STEP_GROUP[state.status]} className="arrive">
        {screen}
      </div>
      {confirmingSignOut && account && (
        <ConfirmDialog
          title="Sign out and discard this encounter?"
          confirmLabel="Sign out and discard"
          cancelLabel="Keep working"
          tone="danger"
          onConfirm={account.onSignOut}
          onCancel={() => setConfirmingSignOut(false)}
        >
          <p>
            The recording, transcript and note for this encounter exist only in this session. Signing out discards them
            from this browser and the device.
          </p>
        </ConfirmDialog>
      )}
    </AppShell>
  )
}
