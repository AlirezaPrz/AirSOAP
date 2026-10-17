import { useState } from 'react'
import type { AirSoapApi } from './api/client'
import { createMockApi, isMockApi } from './api/mockClient'
import { AppShell } from './components/AppShell'
import { CompletionScreen } from './screens/CompletionScreen'
import { DashboardScreen, type SimulatedFailure } from './screens/DashboardScreen'
import { ProcessingScreen } from './screens/ProcessingScreen'
import { RecordingScreen } from './screens/RecordingScreen'
import { ReviewScreen } from './screens/ReviewScreen'
import { useEncounterWorkflow, type StartEncounterInput } from './workflow/useEncounterWorkflow'

interface AppProps {
  /** Backend implementation; defaults to the in-memory mock. */
  api?: AirSoapApi
}

export default function App({ api: providedApi }: AppProps) {
  const [defaultApi] = useState(() => createMockApi())
  const api = providedApi ?? defaultApi
  const workflow = useEncounterWorkflow(api)
  const { state } = workflow

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
    <AppShell status={state.status} encounter={'encounter' in state ? state.encounter : null}>
      {screen}
    </AppShell>
  )
}
