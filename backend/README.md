# AirSOAP backend (Phases 3 and 4)

A FastAPI service that records from the Raspberry Pi's USB microphone with `arecord` and transcribes the whole recording in one request to a loopback-only whisper.cpp `whisper-server` running the existing `ggml-small.en.bin` model. By default the backend starts whisper-server only while it is transcribing and stops it afterwards, so the model's CPU and RAM are free for whatever runs next (Phase 4's language model).

Phase 4 adds two tasks that run on the local Qwen 2.5 3B model through Ollama: **SOAP note generation** and an optional **Clinical Review**. Both share one Ollama client and run one at a time. There is no AI speaker labelling (the clinician labels speakers by hand), no audio diarization and no EMR integration.

On macOS it runs with a mock recorder, mock transcriber and mock AI, so you can develop and test without a microphone, Whisper or Ollama.

> This is a prototype. It makes no claim of regulatory compliance or production readiness.

## How it works

```
browser ──HTTP──▶ FastAPI 127.0.0.1:8000 ──arecord──▶ $XDG_RUNTIME_DIR/airsoap/rec-<uuid>.wav
                         │
                         └─ on stop: starts whisper-server (if not running), then one
                            POST /inference ──▶ whisper-server 127.0.0.1:8178
                            and stops it again after the idle timeout
```

1. `POST /api/recording/start` runs `arecord -D <device> -f S16_LE -r 16000 -c 1 -t wav` into a server-named file.
2. `POST /api/recording/stop` sends SIGINT, so arecord finalizes the WAV header. The backend checks the file (16 kHz, mono, 16-bit) and queues a transcription job.
3. A single worker sends each recording to `whisper-server` as one request, in order. In the default on-demand mode it first starts whisper-server and waits until the model is loaded (a few seconds on the Pi); the job shows `processing` meanwhile.
4. The frontend polls `GET /api/transcriptions/{jobId}` until the job is `completed`, `error` or `cancelled`.

After the last job, whisper-server stays loaded for `AIRSOAP_WHISPER_IDLE_SECONDS` (20 by default, so back-to-back recordings don't reload the model) and is then stopped. Cancelling a job stops it immediately. `GET /api/health` reports `transcriberLoaded` so you can see whether the model is in memory right now.

whisper-server reports no progress during a request. So `progress` is `0` while queued, `null` while processing, and `100` when complete. The frontend shows an indeterminate bar while it is `null`.

## API

| Method and path | Purpose |
| --- | --- |
| `GET /api/health` | Recorder and transcriber in use, whether whisper-server is reachable, and the recording state (`idle` or `recording`) |
| `POST /api/recording/start` | Start the one allowed recording. Returns `recordingId`. 409 if one is already running |
| `POST /api/recording/stop` | Body `{ "recordingId" }`. Finalizes the WAV and queues it. Returns `recordingId`, `durationMs`, `jobId` |
| `POST /api/recording/cancel` | Body `{ "recordingId" }`. Stops and deletes the audio without transcribing |
| `GET /api/transcriptions/{jobId}` | `jobId`, `status` (`queued`, `processing`, `completed`, `cancelled`, `error`), `progress`, `audioDurationMs`, `transcript` (when complete), `error` (when failed) |
| `POST /api/transcriptions/{jobId}/cancel` | Cancel a queued or running job. Calling it again is harmless |

Errors use one shape: `{"error": {"code", "message", "retryable"}}`. Messages are fixed strings, with no paths, transcript text or patient details. IDs are random UUIDs. Interactive API docs are at `http://127.0.0.1:8000/docs` while the server runs.

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
| `AIRSOAP_HOST` / `AIRSOAP_PORT` | `127.0.0.1` / `8000` | API bind address |
| `AIRSOAP_FRONTEND_DIST` | unset | Optional: serve a built frontend from this directory |
| `AIRSOAP_MOCK_TRANSCRIBE_SECONDS` | `2` | Simulated transcription time in mock mode |

The optional always-on unit (`deploy/whisper-server.service`) reads `WHISPER_MODEL`, `WHISPER_PORT` and `WHISPER_THREADS` from the same file; the default on-demand mode doesn't use it.

## Develop on the Mac

```bash
cd backend
python3 -m venv .venv
.venv/bin/pip install -r requirements-dev.txt

.venv/bin/python -m pytest            # no microphone, Whisper or Ollama needed
.venv/bin/ruff check . && .venv/bin/ruff format --check .
.venv/bin/python -m airsoap           # mock mode on 127.0.0.1:8000
```

Then run the frontend against it from another terminal:

```bash
cd frontend
VITE_AIRSOAP_API=http npm run dev     # Vite proxies /api to 127.0.0.1:8000
```

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

The example is already filled in for your setup: the microphone (`arecord -l` shows card 2 named `MICROPHONE`), the whisper-server and model paths, and the Ollama model. Open it once and check the paths match your Pi. Rules for this file: one `KEY=value` per line, **comments on their own lines** (a `# note` after a value becomes part of the value), and no quotes needed.

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

**5. Install and start the backend** (a systemd user unit, no sudo).

```bash
mkdir -p ~/.config/systemd/user
cp deploy/airsoap-backend.service ~/.config/systemd/user/
systemctl --user daemon-reload
systemctl --user enable --now airsoap-backend.service
```

There is no separate whisper-server service: the backend starts whisper-server with the first transcription and stops it again after `AIRSOAP_WHISPER_IDLE_SECONDS`. Prefer to keep it always loaded? Use the optional `deploy/whisper-server.service` and set `AIRSOAP_WHISPER_LIFECYCLE=external`.

**6. Check the configuration was loaded.**

```bash
bash scripts/check_config.sh
```

It compares the env file with what the running backend actually has and prints `[FIX]` lines for anything wrong: a setting that needs a restart, a missing path, Windows line endings, a comment on a value line, whisper-server in the wrong mode, or Ollama not ready. Fix what it says and run it again until it ends with `All good.`

**7. Check it end to end.**

```bash
curl -s http://127.0.0.1:8000/api/health
```

Look for these three things in the answer:
- `"transcriber":"whisper-server (on demand)"` and `"transcriberReachable":true`. If it says just `"whisper-server"`, the backend is **not** in on-demand mode: the env file lacks `AIRSOAP_WHISPER_LIFECYCLE=on-demand`, or you did not restart after adding it.
- `"transcriberLoaded":false`. This is normal: the model loads when needed.
- `"ai":"ollama"` and `"aiReady":true`.

Then run the two smoke tests:

```bash
python3 scripts/smoke_test.py --seconds 8 --show-transcript   # speak while it records
python3 scripts/smoke_ai.py --show                            # SOAP then review on a fictional visit; prints timings
```

The first transcription after a quiet period includes the model load (a few seconds). Check that whisper-server comes and goes: `ps -C whisper-server -o pid,rss,etime` shows nothing before the test, a process (about 650 MB RSS) during it, and nothing again after the idle time. `ps`'s `%CPU` is a lifetime average, so read `top` for current usage.

### After you change anything

| You changed | Do this |
| --- | --- |
| `~/.config/airsoap/airsoap.env` | `systemctl --user daemon-reload && systemctl --user restart airsoap-backend.service`, then `bash scripts/check_config.sh` |
| The code (`git pull`) | `.venv/bin/pip install -r requirements.txt`, then restart the service as above |
| `airsoap-backend.service` | Copy it to `~/.config/systemd/user/`, then `daemon-reload` and restart |
| Ollama's settings | `sudo systemctl restart ollama` |

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

The backend logs job IDs, durations and timings, never transcript or note text.

### Run in the foreground (for debugging)

Stop the service first (`systemctl --user stop airsoap-backend`) so two backends do not fight over port 8000:

```bash
set -a; . ~/.config/airsoap/airsoap.env; set +a
.venv/bin/python -m airsoap
```

### Use the UI with the Pi's microphone

The backend only listens on `127.0.0.1` on the Pi. Use one of these:

**A. From your Mac, through an SSH tunnel.** No Node is needed on the Pi.

```bash
# Mac, terminal 1: forward the Mac's port 8000 to the Pi's loopback API
ssh -N -L 8000:127.0.0.1:8000 ai-innovate@192.168.4.60
# Mac, terminal 2
cd frontend && VITE_AIRSOAP_API=http npm run dev
```

Open http://localhost:5173. Audio is recorded and transcribed on the Pi; only the API traffic goes through the encrypted tunnel. If port 8000 is busy on the Mac, use `-L 8001:127.0.0.1:8000` and start Vite with `AIRSOAP_BACKEND_URL=http://127.0.0.1:8001`.

**B. In a browser on the Pi.** Build the frontend in HTTP mode, then let the backend serve it:

```bash
# On the Mac (dist/ is not committed):
cd frontend && VITE_AIRSOAP_API=http npm run build
scp -r dist ai-innovate@192.168.4.60:~/AirSOAP/frontend/
# On the Pi: uncomment AIRSOAP_FRONTEND_DIST in ~/.config/airsoap/airsoap.env, then
systemctl --user restart airsoap-backend.service
```

Then open http://127.0.0.1:8000 on the Pi.

## Privacy and cleanup

- **Everything is local.** Audio goes from arecord to a RAM-backed directory (`/run/user/<uid>/airsoap`) to whisper-server on `127.0.0.1`. There are no cloud APIs, analytics or telemetry. The backend refuses a non-loopback `AIRSOAP_WHISPER_URL` unless you explicitly allow it.
- **whisper-server is never exposed.** In on-demand mode the backend starts it with `--host 127.0.0.1` from an argument list, refuses to start if the port is already taken, and discards its stdout (where whisper.cpp prints recognized text). The client ignores proxy environment variables.
- **Whisper receives only audio.** Each request carries the WAV file plus `response_format`, `temperature` and `language`. There is no prompt, and no patient name or date of birth (the backend never receives them).
- **Audio is deleted** as soon as its job completes, fails or is cancelled. It is also deleted when a recording is cancelled, when a recording fails or is empty, and on shutdown. Any leftovers from a crash are swept on startup. Failed recordings are not kept, so after a failure you record again.
- **Only `rec-<uuid>.wav` files are touched.** Paths are generated by the server, checked to sit directly inside the work dir, and never taken from requests. arecord runs from an argument list, never a shell string. The device name is validated.
- **Transcripts stay in memory** for `AIRSOAP_JOB_TTL_SECONDS` (10 minutes by default), then are dropped. They are never written to disk.
- **Logs** contain IDs, durations and timings only, never transcript text or audio. whisper.cpp prints recognized text to stdout, so the whisper-server unit sends stdout to `/dev/null` (`StandardOutput=null`). Check with `journalctl --user -u airsoap-backend` after a test that no transcript lines appear.

## Limitations

- With on-demand whisper, cancelling a job stops whisper-server at once. With an external always-on whisper-server, it finishes the request it is already processing and the result is discarded.
- With on-demand whisper, the first transcription after a quiet period includes loading the model (about 6 s on the Pi 5).
- Speaker labels are not detected. Every segment arrives as "Unassigned", and the clinician can set the speaker during review.
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
