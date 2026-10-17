# AirSOAP backend (Phases 3 to 5)

A FastAPI service that records from the Raspberry Pi's USB microphone with `arecord` and transcribes the whole recording in one request to a loopback-only whisper.cpp `whisper-server` running the existing `ggml-small.en.bin` model. By default the backend starts whisper-server only while it is transcribing and stops it afterwards, so the model's CPU and RAM are free for whatever runs next (Phase 4's language model).

Phase 4 adds two tasks that run on the local Qwen 2.5 3B model through Ollama: **SOAP note generation** and an optional **Clinical Review**. Both share one Ollama client and run one at a time. There is no AI speaker labelling (the clinician labels speakers by hand), no audio diarization and no EMR integration.

Phase 5 adds **individual accounts**: everyone signs in with their email and password, an administrator creates and disables accounts (there is no self-registration), and every sensitive endpoint checks the session and the role on the server. Accounts live in a small SQLite database on the Pi's SD card; patient and clinical content is still never stored. On the Pi, Caddy serves the app and the API from one HTTPS address, `https://raspberrypi.local`, while the backend, whisper-server and Ollama stay on loopback.

On macOS it runs with a mock recorder, mock transcriber and mock AI, so you can develop and test without a microphone, Whisper or Ollama.

> This is a prototype. It makes no claim of regulatory compliance or production readiness.

## How it works

```
browser ──HTTPS──▶ Caddy :443 (raspberrypi.local, local CA)
                      │
                      └──▶ FastAPI 127.0.0.1:8000 ── built frontend + /api, session checks
                                 │                      account database (SQLite, SD card)
                                 ├─ arecord ──▶ $XDG_RUNTIME_DIR/airsoap/rec-<uuid>.wav
                                 ├─ on stop: starts whisper-server (if not running), then one
                                 │  POST /inference ──▶ whisper-server 127.0.0.1:8178,
                                 │  and stops it again after the idle timeout
                                 └─ AI tasks ──▶ Ollama 127.0.0.1:11434
```

1. `POST /api/recording/start` runs `arecord -D <device> -f S16_LE -r 16000 -c 1 -t wav` into a server-named file.
2. `POST /api/recording/stop` sends SIGINT, so arecord finalizes the WAV header. The backend checks the file (16 kHz, mono, 16-bit) and queues a transcription job.
3. A single worker sends each recording to `whisper-server` as one request, in order. In the default on-demand mode it first starts whisper-server and waits until the model is loaded (a few seconds on the Pi); the job shows `processing` meanwhile.
4. The frontend polls `GET /api/transcriptions/{jobId}` until the job is `completed`, `error` or `cancelled`.

After the last job, whisper-server stays loaded for `AIRSOAP_WHISPER_IDLE_SECONDS` (20 by default, so back-to-back recordings don't reload the model) and is then stopped. Cancelling a job stops it immediately. `GET /api/system/status` (signed in) reports `transcriberLoaded` so you can see whether the model is in memory right now.

whisper-server reports no progress during a request. So `progress` is `0` while queued, `null` while processing, and `100` when complete. The frontend shows an indeterminate bar while it is `null`.

## API

Every endpoint needs a signed-in session except `GET /api/health`, `POST /api/auth/login` and `POST /api/auth/logout`. The **Access** column says which role the session must have chosen.

| Method and path | Access | Purpose |
| --- | --- | --- |
| `GET /api/health` | public | `{"status":"ok"}` and nothing else, for liveness checks |
| `GET /api/system/status` | either role | Recorder and transcriber in use, whether whisper-server is reachable, whether a model is loaded, and the recording state |
| `POST /api/recording/start` | clinician | Start the one allowed recording. Returns `recordingId`. 409 if one is already running |
| `POST /api/recording/stop` | clinician | Body `{ "recordingId" }`. Finalizes the WAV and queues it. Returns `recordingId`, `durationMs`, `jobId` |
| `POST /api/recording/cancel` | clinician | Body `{ "recordingId" }`. Stops and deletes the audio without transcribing |
| `GET /api/transcriptions/{jobId}` | clinician | `jobId`, `status` (`queued`, `processing`, `completed`, `cancelled`, `error`), `progress`, `audioDurationMs`, `transcript` (when complete), `error` (when failed) |
| `POST /api/transcriptions/{jobId}/cancel` | clinician | Cancel a queued or running job. Calling it again is harmless |

The AI endpoints (next section) are clinician-only too, and the sign-in and account endpoints are listed under [Accounts and sign-in](#accounts-and-sign-in-phase-5). A recording or job belongs to the clinician who started it: another account gets the same answer as for an ID that does not exist.

Errors use one shape: `{"error": {"code", "message", "retryable"}}`. Messages are fixed strings, with no paths, transcript text or patient details. IDs are random UUIDs. The interactive API docs are off; set `AIRSOAP_API_DOCS=true` on the Mac to get them at `http://127.0.0.1:8000/docs`.

## AI tasks (Phase 4)

Two separate tasks, each with its own prompt template, validated output schema and checks, sharing one Ollama client and the same model:

| Task | Endpoint | Input | Output |
| --- | --- | --- | --- |
| SOAP generation | `POST /api/ai/soap` | Transcript segments with the speaker labels the clinician set, plus age and encounter type | `subjective`, `objective`, `assessment`, `plan`, plus warnings |
| Clinical Review | `POST /api/ai/clinical-review` | The same labelled transcript **and the clinician's edited SOAP note**, plus age and encounter type | `informationGaps`, `followUpQuestions`, `differentialConsiderations`, `documentationInconsistencies` |

Both return `202 {jobId, kind}` at once, because a 3B model on the Pi can take minutes. Poll `GET /api/ai/jobs/{jobId}` for `status` (`queued`, `processing`, `completed`, `error`, `cancelled`) and the result in `soapResult` or `reviewResult`. `POST /api/ai/jobs/{jobId}/cancel` stops a job (and aborts its request to Ollama). `DELETE /api/ai/jobs/{jobId}` cancels if needed and removes the result from backend memory; the frontend calls it as soon as it has the result.

**Speaker labels.** Segments carry `clinician`, `patient` or `unknown`. Whisper does not identify speakers, so every segment starts as `unknown` and the clinician sets the labels in the review screen. Nothing guesses them. The labels the clinician chose are passed to both tasks, and `unknown` lines are never attributed to a person.

**What the checks do**
- **Output validation:** every answer must match a strict JSON schema (also given to Ollama as its output format). An unusable answer is retried once, then reported as `AI_INVALID_OUTPUT`. It is never shown as a result.
- **SOAP: no invented diagnosis or plan.** The model documents what was said; it is told never to diagnose. The Assessment may contain only a diagnosis or impression the clinician said out loud, and the Plan only treatments, tests and advice the clinician said out loud. A test result the clinician states (such as "the rapid test is positive") is recorded as an Objective finding, not turned into a diagnosis. The model must first answer two yes/no fields, `assessmentStated` and `planStated`, before it writes those sections. If it answers no, the server replaces the section with "Not documented." whatever the model wrote. If the Assessment still reads like a diagnosis (wording such as "consistent with", "likely", "suspected") and nothing resembling a diagnosis or impression appears in what the clinician said, the clinician gets a warning to check it. Only the clinician's words count, not the patient's.
- **SOAP: no invented numbers.** The prompt also forbids inventing findings or doses, treats a clinician's question as a question and not as a symptom, never assumes a negative, and asks for "Not documented" where the transcript is silent. Any number in the note that does not appear in the transcript (digits or spelled-out) comes back as a warning, so a made-up dose or vital is flagged.
- **These checks reduce the risk; they do not remove it.** A model can still word something misleadingly. The clinician reviews and edits every section before anything is submitted.
- **Review evidence:** every suggestion must quote text that really appears in the transcript or the note. The server checks each quote, attaches the segment ID or note field, and drops any suggestion whose quote it cannot find (counted in `omittedUnverified`). A differential with no verified evidence is dropped, so an empty list is a normal result. Suggestions that tell the clinician to start, stop or change a treatment are dropped as well.
- **Stable IDs:** a suggestion's ID comes from its category and wording, so rerunning the review after an edit keeps the same ID and a dismissed suggestion stays dismissed.
- **The note is never changed by the backend.** Clinical Review only returns suggestions.

**Limits.** Requests over 12,000 characters of transcript (16,000 with the note) or 128 KB are refused with a clear error, so the prompt always fits the model's context window.

### One model in memory at a time

A 4 GB Pi cannot hold whisper-server (about 650 MB) and Qwen (about 2 GB) together. A shared gate makes every transcription and every AI task take turns: before an AI task the backend stops whisper-server, and before a transcription it asks Ollama to unload Qwen. Ollama itself should also be limited to one model and one request (`deploy/ollama-override.conf`). `AIRSOAP_OLLAMA_KEEP_ALIVE` (default `5m`) lets a note and its review run back to back without reloading the model.

### Setting up Ollama

Covered in [Deploy on the Raspberry Pi](#deploy-on-the-raspberry-pi), step 4. The default model is the stock `qwen2.5:3b-instruct`. `airsoap-3b:latest` is also installed; look at it with `ollama show airsoap-3b --modelfile` before switching, since it may carry its own system prompt. Expect each task to take from tens of seconds to a few minutes on the Pi; `scripts/smoke_ai.py` prints the timings, and `AIRSOAP_OLLAMA_TIMEOUT_SECONDS` raises the limit.

### Privacy for the AI tasks

- Requests contain no patient name, date of birth or MRN: the frontend removes them from the transcript and note before sending, and sends only age (`90+` for 90 and over) and encounter type as context. Job IDs are random.
- Everything goes to `127.0.0.1:11434`; the backend refuses any other Ollama address and ignores proxy environment variables.
- Logs hold only job IDs, task kinds, timings and token counts. A test checks that transcript text, note text and model output never appear in them. Error messages are fixed strings. Validation errors (HTTP 422) never echo the rejected input.
- Task input is dropped from memory as soon as a job finishes. Results are kept for `AIRSOAP_JOB_TTL_SECONDS` (10 minutes) or until the frontend deletes the job, and they are never written to disk.
- If a task fails, nothing is kept: the job holds only a safe error. When the backend shuts down, running jobs are cancelled, all jobs are discarded and Ollama is asked to unload the model.
- Ollama's own logs are not touched by this app. Leave `OLLAMA_DEBUG` unset so it does not log request content.
- Local processing alone does not make this system compliant or production ready.

## Accounts and sign-in (Phase 5)

**Roles.** An account has one or both roles. A **clinician** records encounters and drafts notes. An **administrator** manages accounts and cannot see or start encounters. An account with both roles chooses one at each sign-in (clinical workspace or administration); a single-role account goes straight in. To switch, sign out and sign in again.

**No default account.** Nothing is created automatically, and there is no built-in username or password anywhere. The first administrator is created on the Pi with a command that asks for the password at a hidden prompt (step 7 of the deployment). After that, administrators create accounts in the browser.

**New accounts and resets.** The administrator enters an email, a name and the roles. AirSOAP generates a random temporary password and shows it **once**; the administrator hands it over in person. At first sign-in the person must choose their own password (at least 12 characters, not their email) before they can do anything else. Resetting a password works the same way and signs the person out everywhere.

**Sessions.**
- Signing in sets one cookie, `airsoap_session`: an opaque random value (256 bits), `HttpOnly` (page scripts cannot read it), `Secure` (HTTPS only), `SameSite=Strict` and limited to `/api`. Nothing about the session is stored in the browser's storage, and nothing secret appears in a URL.
- The database keeps only a SHA-256 hash of each session value, so a copy of the database cannot be used to sign in.
- A session ends after **30 minutes without activity** or **12 hours** in any case (`AIRSOAP_SESSION_IDLE_MINUTES`, `AIRSOAP_SESSION_ABSOLUTE_HOURS`). Clicks and key presses in the app count as activity, so editing a long note does not time out.
- When a session ends, by expiry, sign-out, password change or the account being disabled, the screen returns to sign-in and **the open encounter is discarded**: the browser drops it, and the backend cancels that clinician's recording and jobs and deletes the audio. There is no "sign in again to continue".
- Every request re-checks the session and the account. Disabling an account, changing its roles or resetting its password ends its sessions at once.

**Sign-in protection.**
- Passwords are hashed with **Argon2id** (RFC 9106 low-memory profile: 64 MiB, 3 passes, 4 lanes; a fraction of a second per sign-in). Hashes are upgraded automatically if the parameters change.
- A wrong password, an unknown email and a disabled account all get the same answer, `Incorrect email or password.`, and take about the same time.
- After **5 failed attempts for one account** or **20 from one network address** within 15 minutes, sign-in pauses for 15 minutes (`AIRSOAP_LOGIN_*`). The counts are kept in the database, so restarting does not reset them.
- **Cross-site request protection:** besides the `SameSite=Strict` cookie, every request that changes something must come from an allowed origin (`AIRSOAP_ALLOWED_ORIGINS`, by default `https://raspberrypi.local` on the Pi) and carry the header `X-AirSOAP-Request: 1`, which other websites cannot add. The server never allows cross-origin requests.
- Responses carry a strict Content-Security-Policy, `X-Frame-Options: DENY`, `nosniff`, and `Cache-Control: no-store` on every API answer.

**What the database holds.** Account emails, names, roles, Argon2id hashes, session hashes and expiry times, and an audit trail of account events (sign-in succeeded or failed, sign-out, role chosen, account created, changed, disabled or enabled, password changed or reset, requests refused). Audit entries record which account and network address, never a password, session value, typed email (failed sign-ins store a keyed hash of it) or any clinical content. They are deleted after `AIRSOAP_AUDIT_RETENTION_DAYS` (365). Patients, transcripts, notes and reviews are **not** in the database.

| Method and path | Access | Purpose |
| --- | --- | --- |
| `POST /api/auth/login` | public | Body `{email, password}`. Sets the session cookie; returns the account, `activeRole` (or `null` if a role must be chosen) and `mustChangePassword` |
| `POST /api/auth/logout` | public | Ends the session and discards this clinician's recording and jobs |
| `GET /api/auth/me` | any session | The current account and session expiry |
| `POST /api/auth/select-role` | any session | Body `{role}`, for accounts with both roles. Issues a new session value |
| `POST /api/auth/password` | any session | Body `{currentPassword, newPassword}`. Ends the account's other sessions |
| `GET /api/admin/users` | administrator | List accounts (no password data) |
| `POST /api/admin/users` | administrator | Body `{email, displayName, roles}`. Returns the account and its one-time temporary password |
| `PATCH /api/admin/users/{id}` | administrator | Body with any of `displayName`, `roles`, `isActive` |
| `POST /api/admin/users/{id}/reset-password` | administrator | Returns a new one-time temporary password |

Safeguards: administrators cannot disable their own account, and the last active administrator cannot be disabled or lose the role.

## Configuration

Everything is set with environment variables. On the Pi they live in `~/.config/airsoap/airsoap.env`; see [`deploy/airsoap.env.example`](deploy/airsoap.env.example).

| Variable | Default | Meaning |
| --- | --- | --- |
| `AIRSOAP_RECORDER` | `auto` | `auto` (mock on macOS, arecord on Linux), `mock` or `arecord` |
| `AIRSOAP_TRANSCRIBER` | `auto` | `auto` (mock on macOS, whisper-server on Linux), `mock` or `whisper-server` |
| `AIRSOAP_ARECORD_DEVICE` | `default` | ALSA device, e.g. `plughw:CARD=MICROPHONE,DEV=0` |
| `AIRSOAP_ARECORD_PATH` | `arecord` | Path to the arecord binary |
| `AIRSOAP_WORK_DIR` | `$XDG_RUNTIME_DIR/airsoap` on Linux, otherwise the system temp dir | Temporary audio. Created with 0700 permissions |
| `AIRSOAP_MAX_RECORDING_SECONDS` | `1800` | Hard cap, passed to arecord as `-d` |
| `AIRSOAP_WHISPER_LIFECYCLE` | `external` | `on-demand`: the backend starts and stops whisper-server (the Pi env example uses this). `external`: you run whisper-server yourself |
| `AIRSOAP_WHISPER_SERVER_PATH` | unset | Path to the `whisper-server` binary (required for `on-demand`) |
| `AIRSOAP_WHISPER_MODEL_PATH` | unset | Path to `ggml-small.en.bin` (required for `on-demand`) |
| `AIRSOAP_WHISPER_THREADS` | `4` | Threads passed to whisper-server (`on-demand`) |
| `AIRSOAP_WHISPER_IDLE_SECONDS` | `20` | Keep the model loaded this long after the last job; `0` stops it at once (`on-demand`) |
| `AIRSOAP_WHISPER_STARTUP_TIMEOUT_SECONDS` | `90` | Give up if the model is not ready by then (`on-demand`) |
| `AIRSOAP_WHISPER_URL` | `http://127.0.0.1:8178` | Must be on this device. With `on-demand` it must be loopback with an explicit port |
| `AIRSOAP_WHISPER_TIMEOUT_SECONDS` | `900` | How long one transcription may take |
| `AIRSOAP_WHISPER_LANGUAGE` | `en` | Sent as `language` to whisper-server |
| `AIRSOAP_AI` | `auto` | `auto` (mock on macOS, ollama on Linux), `mock` or `ollama` |
| `AIRSOAP_OLLAMA_URL` | `http://127.0.0.1:11434` | Must be on this device |
| `AIRSOAP_OLLAMA_MODEL` | `qwen2.5:3b-instruct` | The installed model tag |
| `AIRSOAP_OLLAMA_TIMEOUT_SECONDS` | `600` | Per request to Ollama |
| `AIRSOAP_OLLAMA_KEEP_ALIVE` | `5m` | How long Ollama keeps the model loaded after a task (`0` unloads at once) |
| `AIRSOAP_OLLAMA_NUM_CTX` | `8192` | Prompt plus answer window, in tokens |
| `AIRSOAP_AI_MAX_ATTEMPTS` | `2` | Tries per task when the answer is unusable |
| `AIRSOAP_MAX_PENDING_AI_JOBS` | `2` | AI queue limit |
| `AIRSOAP_MOCK_AI_SECONDS` | `1` | Simulated task time in mock mode |
| `AIRSOAP_JOB_TTL_SECONDS` | `600` | How long a finished transcript stays in memory |
| `AIRSOAP_MAX_PENDING_JOBS` | `4` | Queue limit |
| `AIRSOAP_HOST` / `AIRSOAP_PORT` | `127.0.0.1` / `8000` | API bind address. Keep it on loopback; Caddy is the way in |
| `AIRSOAP_FRONTEND_DIST` | unset | Serve the built frontend from this directory (on the Pi: `~/AirSOAP/frontend/dist`) |
| `AIRSOAP_DATABASE_PATH` | `~/.local/state/airsoap/airsoap.db` on Linux, `~/Library/Application Support/AirSOAP/airsoap.db` on macOS | The account database. Must be absolute and on persistent storage; temporary directories and the audio work dir are refused. Created with 0600 permissions in a 0700 folder |
| `AIRSOAP_ALLOWED_ORIGINS` | `https://raspberrypi.local` on Linux; `localhost`/`127.0.0.1` on ports 5173 and `AIRSOAP_PORT` on macOS | Comma-separated origins allowed to send requests that change anything |
| `AIRSOAP_COOKIE_SECURE` | `true` on Linux, `false` on macOS | Send the session cookie over HTTPS only |
| `AIRSOAP_SESSION_IDLE_MINUTES` | `30` | Sign-out after this long without activity |
| `AIRSOAP_SESSION_ABSOLUTE_HOURS` | `12` | Sign-out after this long in any case |
| `AIRSOAP_ROLE_SELECTION_MINUTES` | `5` | How long a sign-in may wait at the role choice or password change |
| `AIRSOAP_LOGIN_MAX_FAILURES_PER_ACCOUNT` | `5` | Failed sign-ins for one account before the pause |
| `AIRSOAP_LOGIN_MAX_FAILURES_PER_IP` | `20` | Failed sign-ins from one address before the pause |
| `AIRSOAP_LOGIN_FAILURE_WINDOW_MINUTES` | `15` | Window for both counts, and the length of the pause |
| `AIRSOAP_AUDIT_RETENTION_DAYS` | `365` | Days to keep audit events |
| `AIRSOAP_TRUSTED_PROXIES` | `127.0.0.1` | Proxies whose `X-Forwarded-For` is believed (Caddy on the same Pi), so rate limits see the real client address |
| `AIRSOAP_API_DOCS` | `false` | Serve `/docs` and `/openapi.json` (development only) |
| `AIRSOAP_MOCK_TRANSCRIBE_SECONDS` | `2` | Simulated transcription time in mock mode |

The optional always-on unit (`deploy/whisper-server.service`) reads `WHISPER_MODEL`, `WHISPER_PORT` and `WHISPER_THREADS` from the same file; the default on-demand mode doesn't use it.

## Develop on the Mac

```bash
cd backend
python3 -m venv .venv
.venv/bin/pip install -r requirements-dev.txt

.venv/bin/python -m pytest            # no microphone, Whisper or Ollama needed
.venv/bin/ruff check . && .venv/bin/ruff format --check .
```

The tests never touch a real account database: each test gets its own in a temporary folder, and a guard makes any accidental use of the default path point there too.

To run the backend with sign-in, create a development administrator once (it goes into `~/Library/Application Support/AirSOAP/airsoap.db`; use a made-up address and a password you only use here):

```bash
.venv/bin/python -m airsoap.admin --no-env-file create-admin --email dev@example.test --name "Dev Admin" --also-clinician
.venv/bin/python -m airsoap           # mock mode on 127.0.0.1:8000
```

Then run the frontend against it from another terminal, open http://localhost:5173 and sign in:

```bash
cd frontend
VITE_AIRSOAP_API=http npm run dev     # Vite proxies /api to 127.0.0.1:8000
```

Without `VITE_AIRSOAP_API=http` the frontend runs its built-in demo, where any email and password sign in (an email starting with `admin` gets both roles).

## Deploy on the Raspberry Pi

> **The one rule that causes most problems.** The backend reads `~/.config/airsoap/airsoap.env` **only when it starts**. If you edit that file and do not restart the service, the backend keeps running with the old settings and nothing tells you. After **every** edit, run:
>
> ```bash
> systemctl --user daemon-reload && systemctl --user restart airsoap-backend.service
> bash scripts/check_config.sh     # confirms the running backend now has your settings
> ```

These steps assume the layout you already have: the repo at `~/AirSOAP`, whisper.cpp at `~/src/whisper.cpp` with `build/bin/whisper-server`, the model at `~/src/whisper.cpp/models/ggml-small.en.bin`, and Ollama with `qwen2.5:3b-instruct`. Lingering is already enabled with `loginctl enable-linger ai-innovate`. Run every command from `~/AirSOAP/backend` unless it says otherwise.

### Fresh install

**1. Get the code.**

```bash
cd ~/AirSOAP
git fetch origin
git switch <branch>        # the branch you are deploying, e.g. dev, or feature/qwen-clinical-ai
git pull
cd backend
```

**2. Create the virtualenv.**

```bash
python3 -m venv .venv
.venv/bin/pip install -r requirements.txt
.venv/bin/python -m pytest      # optional: the test suite needs no microphone, Whisper or Ollama
```

**3. Create the env file.**

```bash
mkdir -p ~/.config/airsoap
cp deploy/airsoap.env.example ~/.config/airsoap/airsoap.env
chmod 600 ~/.config/airsoap/airsoap.env
```

The example is already filled in for your setup: the microphone (`arecord -l` shows card 2 named `MICROPHONE`), the whisper-server and model paths, the Ollama model, the account database at `~/.local/state/airsoap/airsoap.db`, and `https://raspberrypi.local` as the only allowed address. Open it once and check the paths match your Pi. Rules for this file: one `KEY=value` per line, **comments on their own lines** (a `# note` after a value becomes part of the value), and no quotes needed. It holds no passwords, but keep it `chmod 600`.

Check the microphone records:

```bash
arecord -D plughw:CARD=MICROPHONE,DEV=0 -f S16_LE -r 16000 -c 1 -d 3 /tmp/mic-check.wav && rm /tmp/mic-check.wav
```

**4. Set up Ollama** (needs sudo, once). This limits Ollama to one model and one request at a time, which a 4 GB Pi needs.

```bash
sudo systemctl edit ollama       # paste the lines from deploy/ollama-override.conf, save
sudo systemctl restart ollama
ss -ltn | grep 11434             # must show 127.0.0.1:11434, never 0.0.0.0
ollama list                      # must show the model named in AIRSOAP_OLLAMA_MODEL
```

**5. Build the frontend** (on the Pi, with its Node 20).

```bash
cd ~/AirSOAP/frontend
npm ci                                   # a warning that vitest wants Node 22 is fine: tests run on the Mac
VITE_AIRSOAP_API=http npm run build      # writes frontend/dist, which the backend serves
cd ~/AirSOAP/backend
```

**6. Install and start the backend** (a systemd user unit, no sudo).

```bash
mkdir -p ~/.config/systemd/user
cp deploy/airsoap-backend.service ~/.config/systemd/user/
systemctl --user daemon-reload
systemctl --user enable --now airsoap-backend.service
```

At its first start the backend creates the account database (folder `0700`, file `0600`). There is no separate whisper-server service: the backend starts whisper-server with the first transcription and stops it again after `AIRSOAP_WHISPER_IDLE_SECONDS`. Prefer to keep it always loaded? Use the optional `deploy/whisper-server.service` and set `AIRSOAP_WHISPER_LIFECYCLE=external`.

**7. Create the first administrator.** Run this as your normal user (not with sudo), in a terminal on the Pi or over SSH. You are asked for the password twice; it is not shown and not kept in your shell history.

```bash
.venv/bin/python -m airsoap.admin create-admin --email you@your-clinic.example --name "Dr Your Name" --also-clinician
```

`--also-clinician` lets the same account record encounters too; leave it out for an account that only manages others. The command refuses to create a second administrator by accident (add `--additional` if you mean it). From now on, create everyone else's account in the browser.

**8. Put Caddy in front** (needs sudo, once). Caddy is already installed.

```bash
sudo cp deploy/Caddyfile.example /etc/caddy/Caddyfile
sudo caddy validate --config /etc/caddy/Caddyfile
sudo systemctl reload caddy
```

Caddy now answers on `https://raspberrypi.local` with a certificate from its own local certificate authority, redirects plain HTTP to HTTPS, and forwards to the backend on `127.0.0.1:8000`. Browsers warn about the certificate until you trust Caddy's root certificate on each device, below.

**9. Trust the certificate on each laptop or tablet.** Copy Caddy's root certificate off the Pi (it is public, not a secret):

```bash
# On the Pi
sudo cat /var/lib/caddy/.local/share/caddy/pki/authorities/local/root.crt > ~/airsoap-local-ca.crt
# On the Mac
scp ai-innovate@raspberrypi.local:airsoap-local-ca.crt ~/Downloads/
sudo security add-trusted-cert -d -r trustRoot -k /Library/Keychains/System.keychain ~/Downloads/airsoap-local-ca.crt
```

On Windows, double-click the file, choose **Install Certificate**, **Local Machine**, and the **Trusted Root Certification Authorities** store. On an iPad, AirDrop or mail it to the device, install the profile, then enable it under Settings, General, About, Certificate Trust Settings. On the Pi itself, `sudo caddy trust` adds it to the system store; if Chromium still warns, import the same file under Settings, Privacy and security, Security, Manage certificates. Only trust a certificate you copied from your own Pi. Restart the browser, then open **https://raspberrypi.local**: there should be no warning.

`raspberrypi.local` is found through mDNS (Bonjour), which macOS, iOS, Windows 10 and later, and Raspberry Pi OS support. If a device cannot find it, check the Pi's hostname (`hostname`) and that `avahi-daemon` is running.

**10. Check the configuration was loaded.**

```bash
bash scripts/check_config.sh
```

It compares the env file with what the running backend actually has and prints `[FIX]` lines for anything wrong: a setting that needs a restart, a missing path, Windows line endings, a comment on a value line, whisper-server in the wrong mode, the database permissions, a cookie or origin setting that would weaken sign-in, a port open beyond loopback, Ollama not answering, or Caddy not forwarding. Fix what it says and run it again until it ends with `All good.`

**11. Check it end to end.** Sign in at https://raspberrypi.local and go through an encounter. Then run the two smoke tests on the Pi; each asks for an account's email and password (use a clinician account, or one with both roles), signs in, and signs out at the end:

```bash
python3 scripts/smoke_test.py --status                        # recorder, whisper-server, AI: all ready?
python3 scripts/smoke_test.py --seconds 8 --show-transcript   # speak while it records
python3 scripts/smoke_ai.py --show                            # SOAP then review on a fictional visit; prints timings
```

In the status, look for `transcriber=whisper-server (on demand) (ready=True, model loaded=False)` and `ai=ollama ready=True`. If it says just `whisper-server`, the backend is **not** in on-demand mode: the env file lacks `AIRSOAP_WHISPER_LIFECYCLE=on-demand`, or you did not restart after adding it. `model loaded=False` is normal: the model loads when needed.

The first transcription after a quiet period includes the model load (a few seconds). Check that whisper-server comes and goes: `ps -C whisper-server -o pid,rss,etime` shows nothing before the test, a process (about 650 MB RSS) during it, and nothing again after the idle time. `ps`'s `%CPU` is a lifetime average, so read `top` for current usage.

**Optional: a firewall.** Nothing but Caddy listens beyond loopback, so a firewall is extra protection rather than a requirement. With `ufw` (`sudo apt install ufw`), allow SSH and HTTPS from your local network only, for example:

```bash
sudo ufw default deny incoming
sudo ufw allow from 192.168.4.0/24 to any port 22 proto tcp
sudo ufw allow from 192.168.4.0/24 to any port 443 proto tcp
sudo ufw allow from 192.168.4.0/24 to any port 80 proto tcp     # only for Caddy's redirect to HTTPS
sudo ufw enable
```

Use your network's range (shown by `ip -4 addr`), and keep an SSH session open while you test, so a mistake cannot lock you out. Never forward ports 80 or 443 on your router: AirSOAP is meant for the local network only.

### After you change anything

| You changed | Do this |
| --- | --- |
| `~/.config/airsoap/airsoap.env` | `systemctl --user daemon-reload && systemctl --user restart airsoap-backend.service`, then `bash scripts/check_config.sh` |
| The code (`git pull`) | `.venv/bin/pip install -r requirements.txt`; `cd ../frontend && npm ci && VITE_AIRSOAP_API=http npm run build`; then restart the service as above. Database migrations run by themselves at start; back up first (below) |
| `airsoap-backend.service` | Copy it to `~/.config/systemd/user/`, then `daemon-reload` and restart |
| Ollama's settings | `sudo systemctl restart ollama` |
| `/etc/caddy/Caddyfile` | `sudo caddy validate --config /etc/caddy/Caddyfile && sudo systemctl reload caddy` |

### Already ran the always-on version?

Switch to on-demand like this:

```bash
systemctl --user disable --now whisper-server.service    # the backend now manages whisper-server itself
rm ~/.config/systemd/user/whisper-server.service
# Make sure ~/.config/airsoap/airsoap.env has the lines under "Whisper: started only while transcribing"
# (compare with deploy/airsoap.env.example), then:
cp deploy/airsoap-backend.service ~/.config/systemd/user/
systemctl --user daemon-reload && systemctl --user restart airsoap-backend.service
bash scripts/check_config.sh
ps -C whisper-server    # nothing: it starts with the first transcription
```

### Reading the logs

The Pi does not keep a per-user journal, so `journalctl --user -u airsoap-backend` may print "No journal files were found". Use this instead:

```bash
sudo journalctl _SYSTEMD_USER_UNIT=airsoap-backend -n 50 --no-pager
```

The backend logs job IDs, durations and timings, never transcript or note text, passwords or session values. uvicorn's access lines show method, path (with random IDs only) and status.

### Run in the foreground (for debugging)

Stop the service first (`systemctl --user stop airsoap-backend`) so two backends do not fight over port 8000:

```bash
set -a; . ~/.config/airsoap/airsoap.env; set +a
.venv/bin/python -m airsoap
```

### Managing accounts

Day to day, an administrator does everything in the browser: sign in, choose **Administration**, and use **New account**, **Edit** (name, roles, or **Can sign in** to disable) and **Reset password**.

On the Pi, `python -m airsoap.admin` handles first setup and recovery. Run it from `~/AirSOAP/backend` as your normal user; it reads the same env file as the service, refuses to run as root, and never takes a password on the command line:

| Command | What it does |
| --- | --- |
| `.venv/bin/python -m airsoap.admin list-users` | Every account, its roles, whether it is active and its last sign-in |
| `.venv/bin/python -m airsoap.admin create-admin --email E --name N [--also-clinician] [--additional]` | Create an administrator (asks for the password) |
| `.venv/bin/python -m airsoap.admin reset-password E` | Set a new password for an account (asks you to type the email again, then the password); ends its sessions |
| `.venv/bin/python -m airsoap.admin enable E` | Re-enable a disabled account |
| `.venv/bin/python -m airsoap.admin grant-role E admin` | Give an account a role back (`admin` or `clinician`) |
| `.venv/bin/python -m airsoap.admin backup DEST` | Write a consistent copy of the database, safe while the service runs |
| `.venv/bin/python -m airsoap.admin migrate` | Apply pending database migrations and show the schema version |

### Back up the account database

The database is small and changes only when accounts or sessions do. Back it up before every update and every so often:

```bash
mkdir -p -m 700 ~/airsoap-backups
.venv/bin/python -m airsoap.admin backup ~/airsoap-backups/airsoap-$(date +%F).db
```

The copy is owner-only (`0600`). It contains password hashes and the audit trail, so keep copies as carefully as the Pi itself: on a USB stick or computer that only you use, not in cloud sync, email or Git (`.gitignore` excludes `*.db`). A backup of a lost or retired Pi should be destroyed with it.

**Restore** (for example after a failed SD card):

```bash
systemctl --user stop airsoap-backend.service
install -m 600 ~/airsoap-backups/airsoap-2026-10-17.db ~/.local/state/airsoap/airsoap.db
rm -f ~/.local/state/airsoap/airsoap.db-wal ~/.local/state/airsoap/airsoap.db-shm
systemctl --user start airsoap-backend.service
```

Everyone signed in before the backup was taken must sign in again; accounts created after it are missing.

### Recovery

| Situation | Do this on the Pi |
| --- | --- |
| Someone forgot their password | An administrator uses **Reset password** in the browser and hands over the temporary password |
| The administrator forgot their password | `.venv/bin/python -m airsoap.admin reset-password you@…` |
| The only administrator is disabled | `.venv/bin/python -m airsoap.admin enable you@…` |
| No account has the administrator role any more | `.venv/bin/python -m airsoap.admin grant-role someone@… admin` |
| "Too many sign-in attempts" | Wait 15 minutes. Failures are counted per account and per network address, and a successful sign-in resets the account's count |
| Not sure which email an account uses | `.venv/bin/python -m airsoap.admin list-users` |
| The database is lost or damaged | Restore the latest backup (above). Without one, stop the service, move the damaged file aside, start the service (it creates an empty database) and run `create-admin` again |
| The service refuses to start with "database schema … is newer than this code" | You went back to older code. Switch to the newer branch again, or restore a backup taken with the older version |

### Use the UI with the Pi's microphone

Open **https://raspberrypi.local** in a browser on any computer or tablet on the same network once its certificate is trusted (step 9), or in Chromium on the Pi itself. Audio is always recorded by the Pi's USB microphone and transcribed on the Pi.

For **frontend development against the Pi**, run Vite on the Mac through an SSH tunnel. The Pi accepts only its own HTTPS address, so allow the dev server's origin while you do this, and remove it again afterwards:

```bash
# Pi: add http://localhost:5173 to AIRSOAP_ALLOWED_ORIGINS in ~/.config/airsoap/airsoap.env, then restart the service
# Mac, terminal 1: forward the Mac's port 8000 to the Pi's loopback API
ssh -N -L 8000:127.0.0.1:8000 ai-innovate@raspberrypi.local
# Mac, terminal 2
cd frontend && VITE_AIRSOAP_API=http npm run dev
```

Open http://localhost:5173 in Chrome or Firefox (they accept the HTTPS-only cookie on `localhost`; Safari does not). If port 8000 is busy on the Mac, use `-L 8001:127.0.0.1:8000` and start Vite with `AIRSOAP_BACKEND_URL=http://127.0.0.1:8001`.


## Privacy and cleanup

- **Accounts, not patients.** The SQLite database holds accounts, password and session hashes and the audit trail. Introducing it did not change what happens to clinical content: none of it is written there or anywhere else.
- **Everything is local.** Audio goes from arecord to a RAM-backed directory (`/run/user/<uid>/airsoap`) to whisper-server on `127.0.0.1`. There are no cloud APIs, analytics or telemetry. The backend refuses a non-loopback `AIRSOAP_WHISPER_URL` unless you explicitly allow it.
- **whisper-server is never exposed.** In on-demand mode the backend starts it with `--host 127.0.0.1` from an argument list, refuses to start if the port is already taken, and discards its stdout (where whisper.cpp prints recognized text). The client ignores proxy environment variables.
- **Whisper receives only audio.** Each request carries the WAV file plus `response_format`, `temperature` and `language`. There is no prompt, and no patient name or date of birth (the backend never receives them).
- **Audio is deleted** as soon as its job completes, fails or is cancelled. It is also deleted when a recording is cancelled, when a recording fails or is empty, and on shutdown. Any leftovers from a crash are swept on startup. Failed recordings are not kept, so after a failure you record again.
- **Only `rec-<uuid>.wav` files are touched.** Paths are generated by the server, checked to sit directly inside the work dir, and never taken from requests. arecord runs from an argument list, never a shell string. The device name is validated.
- **Transcripts stay in memory** for `AIRSOAP_JOB_TTL_SECONDS` (10 minutes by default), then are dropped. They are never written to disk. Signing out, or a session ending, drops that clinician's recording, transcripts and AI results at once.
- **Logs** contain IDs, durations and timings only, never transcript text or audio. whisper.cpp prints recognized text to stdout, so the whisper-server unit sends stdout to `/dev/null` (`StandardOutput=null`). Check with `journalctl --user -u airsoap-backend` after a test that no transcript lines appear.

## Limitations

These are the known limits of this prototype's sign-in. It is not production ready, and none of this makes it compliant with any regulation.

- **No second factor.** Sign-in is email and password only.
- **No self-service password reset** and no email: an administrator resets passwords, and the operator CLI is the last resort.
- **A local certificate authority.** Each device must trust Caddy's root certificate by hand. Anyone with root on the Pi can read that CA's key and the database.
- **The SD card is not encrypted.** The database (emails, names, password hashes, audit trail) and the env file are protected by file permissions only. Someone who takes the SD card can read them, though not the passwords themselves. Keep the Pi physically secure.
- **An unattended, signed-in screen** stays usable until 30 minutes of inactivity. Sign out when you step away.
- **Rate limits are per account and per network address.** Many addresses at once (unlikely on a local network) are only slowed down by the per-account limit.
- **Sessions are per browser.** Signing in on a second device does not end the first; disabling the account or changing its password does.
- **Audit events** are kept for 365 days, on the same SD card. They are a record for the operator, not tamper-proof.

- With on-demand whisper, cancelling a job stops whisper-server at once. With an external always-on whisper-server, it finishes the request it is already processing and the result is discarded.
- With on-demand whisper, the first transcription after a quiet period includes loading the model (about 6 s on the Pi 5).
- Speaker labels are not detected. Every segment arrives as "Unknown", and the clinician sets the speaker during review.
- Total time scales with recording length. A long recording on the Pi 5 can take minutes; raise `AIRSOAP_WHISPER_TIMEOUT_SECONDS` if needed.

## Troubleshooting

Start with `bash scripts/check_config.sh`. It finds the most common problems and says what to do.

| Symptom | Check |
| --- | --- |
| I edited the env file but nothing changed | The backend reads it only at start: `systemctl --user daemon-reload && systemctl --user restart airsoap-backend.service`, then `bash scripts/check_config.sh` |
| Health shows `"transcriber":"whisper-server"` with no `(on demand)`, and `transcriberReachable: false` | The backend is in always-on mode. The env file lacks `AIRSOAP_WHISPER_LIFECYCLE=on-demand`, or the service was not restarted after you added it |
| `transcriberReachable: false` in on-demand mode | `AIRSOAP_WHISPER_SERVER_PATH` must be an executable file and `AIRSOAP_WHISPER_MODEL_PATH` must exist (`check_config.sh` tests both). In external mode: `ss -ltn \| grep 8178` |
| `smoke_test.py` says "The transcriber is not ready" | Same as the two rows above |
| Two backends: the old one still answers | `ss -ltnp \| grep 8000` shows what holds the port. Stop a foreground `python -m airsoap` with Ctrl+C, then restart the service |
| `TRANSCRIBER_PORT_IN_USE` | An old always-on whisper-server is still running: `systemctl --user disable --now whisper-server.service` |
| "The transcription service could not be started." | The backend log has whisper-server's last error lines (see "Reading the logs"). Check the model file and that the binary runs: `~/src/whisper.cpp/build/bin/whisper-server --help` |
| `journalctl --user` says "No journal files were found" | Use `sudo journalctl _SYSTEMD_USER_UNIT=airsoap-backend -n 50 --no-pager` |
| Start fails with "The microphone could not be opened." | Another program holds the device; check `arecord -l` and the device name in the env file. The backend log has arecord's error line |
| "The recording contains no audio." | The recording was under 0.3 s, or the microphone produced no data |
| `TRANSCRIPTION_TIMEOUT` | Increase `AIRSOAP_WHISPER_TIMEOUT_SECONDS`, or check the Pi's CPU load and temperature |
| `aiReady: false` | `systemctl status ollama`; `ollama list` must show the model named in `AIRSOAP_OLLAMA_MODEL` |
| `AI_TIMEOUT` | Increase `AIRSOAP_OLLAMA_TIMEOUT_SECONDS`; check CPU load, temperature and free RAM (`free -h`) |
| `AI_MODEL_MISSING` | The tag in `AIRSOAP_OLLAMA_MODEL` is not installed; use one from `ollama list` |
| `AI_INVALID_OUTPUT` | The model's answer failed validation twice. Try again; if it keeps happening, shorten the transcript |
| The note has "Not documented." where you expected an Assessment or Plan | Nobody said a diagnosis or plan aloud in the transcript. That is intended: write it yourself |
| Services stop when you log out | `loginctl show-user ai-innovate \| grep Linger` should say `yes` |
| The browser warns "Your connection is not private" | Caddy's root certificate is not trusted on this device yet (deployment step 9). Restart the browser after installing it |
| `raspberrypi.local` cannot be found | `hostname` on the Pi must say `raspberrypi`; `systemctl status avahi-daemon` must be active; the device must be on the same network |
| Sign-in says "This request was blocked because it did not come from the AirSOAP app" | The address in the browser bar is not in `AIRSOAP_ALLOWED_ORIGINS`. Use https://raspberrypi.local, or add the address you use and restart the service |
| Sign-in seems to work, then you are back at the sign-in screen | The session cookie is HTTPS-only. Open the app through https://raspberrypi.local, never over plain `http://` |
| The backend will not start: "database_path must be on persistent storage" | `AIRSOAP_DATABASE_PATH` points into `/tmp`, `/run` or the audio folder. Use the path from the env example |
| The backend will not start: "owned by another user" or "symlink" | The database or its folder was created by another user (often `sudo`). Fix the owner: `sudo chown -R ai-innovate: ~/.local/state/airsoap` |
| `admin` CLI says "Run this as the AirSOAP service account, not as root" | Run it without `sudo` |
| "Too many sign-in attempts. Wait 15 minutes and try again." | Wait. If an attack is suspected, `sudo journalctl _SYSTEMD_USER_UNIT=airsoap-backend` shows the addresses of the requests |
