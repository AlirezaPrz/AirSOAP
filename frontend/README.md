# AirSOAP — Frontend prototype

Phase 1–2 prototype of AirSOAP, a local-first AI SOAP documentation assistant for a Raspberry Pi 5.
By default it runs the whole clinical workflow on **mock data**. With `VITE_AIRSOAP_API=http` it uses the Phase 3 backend (`../backend`) for real recording and Whisper transcription. SOAP generation (Ollama) and EMR integration are not connected yet.

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

## Using the backend

```bash
VITE_AIRSOAP_API=http npm run dev   # Vite proxies /api to 127.0.0.1:8000 (override with AIRSOAP_BACKEND_URL)
```

`src/api/httpClient.ts` implements `AirSoapApi` against the FastAPI backend:
- **Recording:** start and stop map to `/api/recording/*`. Only the backend's own recording ID is sent; the encounter ID and patient details stay in the browser.
- **Transcription:** polls `GET /api/transcriptions/{jobId}` and reports `null` progress while whisper-server is working, which shows an indeterminate bar. Cancelling calls the job's cancel endpoint.
- **Speakers:** Whisper does not detect them, so every transcript segment arrives as `unassigned` ("Unassigned" in the review screen) for the clinician to set.
- **SOAP note:** every field is filled with "Placeholder, SOAP generation is not connected yet (Phase 4)".
- **Demo submission:** stays local.

See `../backend/README.md` for running the backend on the Mac (mock mode) or on the Raspberry Pi.
