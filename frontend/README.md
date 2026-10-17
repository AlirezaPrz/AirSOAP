# AirSOAP — Frontend prototype

Phase 1–2 prototype of AirSOAP, a local-first AI SOAP documentation assistant for a Raspberry Pi 5.
It runs the whole clinical workflow on **mock data**. There's no microphone access, no Whisper or Ollama, and no EMR.

```bash
npm install
npm run dev        # http://localhost:5173
npm test           # Vitest + Testing Library
npm run typecheck
npm run lint       # oxlint (bans browser storage and console use)
npm run build
```

## Workflow

Dashboard (patient ID) → Record → Processing (transcription → SOAP generation) → Review and edit → Confirm demo submission → Completion → back to the dashboard (encounter reset).

The **Demo options** on the dashboard can make transcription, SOAP generation, or submission fail once, so you can see the error and retry states.

## Structure

| Path | Purpose |
| --- | --- |
| `src/api/types.ts` | Request/response contracts for the planned backend |
| `src/api/client.ts` | `AirSoapApi` interface (the only backend surface the UI uses), plus error helpers |
| `src/api/mockClient.ts` | Mock implementation with simulated latency, progress, abort, and failure injection |
| `src/workflow/reducer.ts` | Pure encounter state machine (`idle → ready → recording → processing → review → confirming → submitting → completed`) |
| `src/workflow/useEncounterWorkflow.ts` | Hook that wires the reducer to the API (processing, cancellation, submission) |
| `src/workflow/privacy.ts` | Age calculation, identifier redaction, `buildSoapRequest()` |
| `src/mocks/encounter.ts` | Synthetic patient, transcript, and SOAP note |
| `src/screens/*` | One component per workflow step |

## Privacy guarantees in this prototype

- **The AI never receives the patient's name, date of birth, or MRN.** `SoapGenerationRequest` has no fields for them. `buildSoapRequest()` sends only the transcript (with identifiers redacted in case they were spoken), the age in whole years (`'90+'` for 90 and over), and the encounter type.
- **Encounter data lives only in React state.** Nothing goes to localStorage, sessionStorage, IndexedDB, cookies, analytics, or the console. This is enforced by lint rules, a source scan test (`src/noPersistence.test.ts`), and a runtime test that spies on `Storage` and `console`.
- When a submission completes, all encounter data is dropped from state and only the demo receipt is kept. Cancelling also clears everything.

## Integrating the real backend

Write an HTTP client that implements `AirSoapApi` against the contracts in `src/api/types.ts`. Make sure it honours `AbortSignal` and reports `onProgress`, for example from SSE. Then pass it in with `<App api={httpClient} />`. The screens and the state machine don't need to change.
