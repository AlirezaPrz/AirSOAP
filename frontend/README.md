# AirSOAP — Frontend

The frontend of AirSOAP, a local-first AI SOAP documentation assistant for a Raspberry Pi 5.
By default it runs the whole clinical workflow on **mock data**, including a simulated sign-in where any email and password work (an email starting with `admin` gets both roles, so the role choice and the account screens can be tried). With `VITE_AIRSOAP_API=http` it uses the backend (`../backend`) for real recording, Whisper transcription, and SOAP generation and Clinical Review with the local Qwen model. EMR integration is not connected.

```bash
npm install
npm run dev        # http://localhost:5173
npm test           # Vitest + Testing Library
npm run typecheck
npm run lint       # oxlint (bans browser storage and console use)
npm run build
```

## Workflow

Sign in → (choose a new password, if it is a temporary one) → (choose clinical workspace or administration, if the account has both roles) → the workspace or the account screen.

In the workspace: Dashboard (patient ID) → Record → Processing (transcription → SOAP generation) → Review and edit → Confirm demo submission → Completion → back to the dashboard (encounter reset).

The **Demo options** on the dashboard can make transcription, SOAP generation, or submission fail once, so you can see the error and retry states.

## Structure

| Path | Purpose |
| --- | --- |
| `src/Root.tsx` | Follows the server's view of the session and picks the screen: sign-in, password change, role choice, workspace (`App`) or accounts |
| `src/auth/*` | Sign-in, password change and role choice screens; `useSessionKeepAlive` |
| `src/admin/*` | The administrator's account list, create, edit, disable and reset dialogs |
| `src/api/request.ts` | The one way to call the backend: app header, same-origin cookie, "session ended" signal |
| `src/api/auth.ts` | `AuthApi` over HTTP, and the in-memory mock |
| `src/api/types.ts` | Request/response contracts for the backend |
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

## Signing in

- **The session is an HttpOnly cookie** set by the backend. The frontend never sees it, and stores nothing about the session or the account in localStorage, sessionStorage, IndexedDB or a URL (the source scan test checks this too). After a reload, `GET /api/auth/me` tells the app who is signed in.
- **Every request** carries `X-AirSOAP-Request: 1`, which the backend requires as proof it came from this app (`src/api/request.ts`).
- **When the session ends** (sign-out, 30 idle minutes, 12 hours, password change, the account disabled), the app returns to the sign-in screen and unmounts the workspace, which discards the encounter. The backend discards the recording and jobs too. Signing out during an encounter asks for confirmation first.
- **Activity keeps the session alive:** a click or key press refreshes it at most every two minutes, so a long edit does not time out. Without activity the screen clears when the session expires.
- **Temporary passwords** created by an administrator are shown once in a dialog and dropped from memory when it closes.

## Using the backend

```bash
VITE_AIRSOAP_API=http npm run dev   # Vite proxies /api to 127.0.0.1:8000 (override with AIRSOAP_BACKEND_URL)
```

`src/api/httpClient.ts` implements `AirSoapApi` against the FastAPI backend:
- **Recording:** start and stop map to `/api/recording/*`. Only the backend's own recording ID is sent; the encounter ID and patient details stay in the browser.
- **Transcription:** polls `GET /api/transcriptions/{jobId}` and reports `null` progress while whisper-server is working, which shows an indeterminate bar. Cancelling calls the job's cancel endpoint.
- **Speakers:** Whisper does not detect them, so every transcript segment arrives as `unknown` ("Unknown" in the review screen). The clinician sets each turn to Clinician or Patient by hand; nothing guesses the labels.
- **SOAP note:** generated only when the clinician clicks **Generate SOAP note**, from the transcript with the labels they set. The request carries age and encounter type, never the name, date of birth or MRN, and identifiers are removed from the transcript text. If the clinician types in a section while the model is working, that section keeps their text. Values in the draft that the transcript never mentions are listed as warnings.
- **Clinical Review:** an optional panel, run only when the clinician clicks **Run Clinical Review**. It sends the current labels and the clinician's edited note (identifiers removed) and shows suggestions apart from the note in two lists: **information gaps** (details that seem missing or unclear, each with a question the clinician could ask) and **documentation inconsistencies** (where the note differs from what was said, shown as "Said" and "Written" side by side), each with quoted evidence. Suggestions can be dismissed (they stay dismissed when the review is rerun) and are marked out of date if the note or labels change afterwards. Nothing is ever added to the note.
- **Leaving:** discarding, cancelling or submitting aborts any AI task in flight, a late answer is ignored, and the backend job is deleted so no result lingers in its memory.
- **Live transcript:** when the backend reports `live: true`, the recording screen follows `GET /api/recording/{id}/live` (server-sent events via `EventSource`) and shows each line as it is transcribed, read only, with the status in words (listening, behind, failed, reconnecting). New lines never move the view while the clinician has scrolled up. That transcript is the one reviewed afterwards.
- **Suggest speakers:** in review, asks the backend's 3B model to label turns from the words. Suggested labels are marked **AI** until the clinician accepts or changes them; the clinician's own labels, and turns edited while it ran, are never replaced.
- **Demo submission:** stays local.

The mock API (`VITE_AIRSOAP_API` unset) provides deterministic stand-ins for all of this, including simulated failures, so the whole flow runs without a backend.

See `../backend/README.md` for running the backend on the Mac (mock mode) or on the Raspberry Pi.
