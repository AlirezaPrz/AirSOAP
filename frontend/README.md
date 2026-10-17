# AirSOAP — Frontend prototype

Phase 1–2 prototype of AirSOAP, a local-first AI SOAP documentation assistant for a Raspberry Pi 5.
By default it runs the whole clinical workflow on **mock data**. With `VITE_AIRSOAP_API=http` it uses the backend (`../backend`) for real recording, Whisper transcription, and SOAP generation and Clinical Review with the local Qwen model. EMR integration is not connected.

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
- **Speakers:** Whisper does not detect them, so every transcript segment arrives as `unknown` ("Unknown" in the review screen). The clinician sets each turn to Clinician or Patient by hand; nothing guesses the labels.
- **SOAP note:** generated only when the clinician clicks **Generate SOAP note**, from the transcript with the labels they set. The request carries age and encounter type, never the name, date of birth or MRN, and identifiers are removed from the transcript text. If the clinician types in a section while the model is working, that section keeps their text. Values in the draft that the transcript never mentions are listed as warnings.
- **Clinical Review:** an optional panel, run only when the clinician clicks **Run Clinical Review**. It sends the current labels and the clinician's edited note (identifiers removed) and shows suggestions apart from the note: information gaps, follow-up questions, differential considerations and documentation consistency checks, each with quoted evidence. Suggestions can be dismissed (they stay dismissed when the review is rerun) and are marked out of date if the note or labels change afterwards. Nothing is ever added to the note.
- **Leaving:** discarding, cancelling or submitting aborts any AI task in flight, a late answer is ignored, and the backend job is deleted so no result lingers in its memory.
- **Demo submission:** stays local.

The mock API (`VITE_AIRSOAP_API` unset) provides deterministic stand-ins for all of this, including simulated failures, so the whole flow runs without a backend.

See `../backend/README.md` for running the backend on the Mac (mock mode) or on the Raspberry Pi.
