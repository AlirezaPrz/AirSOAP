# AirSOAP backend (Phase 3)

A FastAPI service that records from the Raspberry Pi's USB microphone with `arecord` and transcribes the whole recording in one request to a persistent, loopback-only whisper.cpp `whisper-server` running the existing `ggml-small.en.bin` model.

On macOS it runs with a mock recorder and a mock transcriber, so you can develop and test without a microphone or Whisper. There is no SOAP generation, Ollama, speaker diarization or EMR integration in this phase.

> This is a prototype. It makes no claim of regulatory compliance or production readiness.

## How it works

```
browser ──HTTP──▶ FastAPI 127.0.0.1:8000 ──arecord──▶ $XDG_RUNTIME_DIR/airsoap/rec-<uuid>.wav
                         │
                         └─ on stop: one POST /inference ──▶ whisper-server 127.0.0.1:8178 (model loaded once)
```

1. `POST /api/recording/start` runs `arecord -D <device> -f S16_LE -r 16000 -c 1 -t wav` into a server-named file.
2. `POST /api/recording/stop` sends SIGINT, so arecord finalizes the WAV header. The backend checks the file (16 kHz, mono, 16-bit) and queues a transcription job.
3. A single worker sends each recording to `whisper-server` as one request, in order.
4. The frontend polls `GET /api/transcriptions/{jobId}` until the job is `completed`, `error` or `cancelled`.

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
| `AIRSOAP_WHISPER_URL` | `http://127.0.0.1:8178` | Must be on this device unless `AIRSOAP_ALLOW_REMOTE_WHISPER=true` |
| `AIRSOAP_WHISPER_TIMEOUT_SECONDS` | `900` | How long one transcription may take |
| `AIRSOAP_WHISPER_LANGUAGE` | `en` | Sent as `language` to whisper-server |
| `AIRSOAP_JOB_TTL_SECONDS` | `600` | How long a finished transcript stays in memory |
| `AIRSOAP_MAX_PENDING_JOBS` | `4` | Queue limit |
| `AIRSOAP_HOST` / `AIRSOAP_PORT` | `127.0.0.1` / `8000` | API bind address |
| `AIRSOAP_FRONTEND_DIST` | unset | Optional: serve a built frontend from this directory |
| `AIRSOAP_MOCK_TRANSCRIBE_SECONDS` | `2` | Simulated transcription time in mock mode |

The model path and whisper-server port are set in the same env file (`WHISPER_MODEL`, `WHISPER_PORT`, `WHISPER_THREADS`) and read by the whisper-server unit.

## Develop on the Mac

```bash
cd backend
python3 -m venv .venv
.venv/bin/pip install -r requirements-dev.txt

.venv/bin/python -m pytest            # 72 tests; no microphone or Whisper needed
.venv/bin/ruff check . && .venv/bin/ruff format --check .
.venv/bin/python -m airsoap           # mock mode on 127.0.0.1:8000
```

Then run the frontend against it from another terminal:

```bash
cd frontend
VITE_AIRSOAP_API=http npm run dev     # Vite proxies /api to 127.0.0.1:8000
```

## Deploy on the Raspberry Pi

These steps assume the layout you already have: the repo at `~/AirSOAP`, whisper.cpp at `~/src/whisper.cpp` with `build/bin/whisper-server`, and the model at `~/src/whisper.cpp/models/ggml-small.en.bin`. Lingering is already enabled with `loginctl enable-linger ai-innovate`.

**1. Get the branch.**

```bash
cd ~/AirSOAP
git fetch origin
git switch feature/backend-whisper-transcription
```

**2. Create the virtualenv.**

```bash
cd ~/AirSOAP/backend
python3 -m venv .venv
.venv/bin/pip install -r requirements.txt
# Optional: run the test suite on the Pi too
.venv/bin/pip install -r requirements-dev.txt && .venv/bin/python -m pytest
```

**3. Configure.**

```bash
mkdir -p ~/.config/airsoap
cp deploy/airsoap.env.example ~/.config/airsoap/airsoap.env
chmod 600 ~/.config/airsoap/airsoap.env
```

The example is already filled in for your microphone: `arecord -l` shows card 2 named `MICROPHONE`, so `AIRSOAP_ARECORD_DEVICE=plughw:CARD=MICROPHONE,DEV=0`. Check that this device records:

```bash
arecord -D plughw:CARD=MICROPHONE,DEV=0 -f S16_LE -r 16000 -c 1 -d 3 /tmp/mic-check.wav && rm /tmp/mic-check.wav
```

**4. Install and start both services** (systemd user units, no sudo).

```bash
mkdir -p ~/.config/systemd/user
cp deploy/whisper-server.service deploy/airsoap-backend.service ~/.config/systemd/user/
systemctl --user daemon-reload
systemctl --user enable --now whisper-server.service airsoap-backend.service
systemctl --user status whisper-server.service airsoap-backend.service
```

whisper-server takes a few seconds to load the model. Follow it with `journalctl --user -u whisper-server -f`.

**5. Check it end to end.**

```bash
curl -s http://127.0.0.1:8000/api/health
# expect "recorder":"arecord","transcriber":"whisper-server","transcriberReachable":true

python3 scripts/smoke_test.py --seconds 8                    # speak while it records
python3 scripts/smoke_test.py --seconds 8 --show-transcript  # also print the text to this terminal
```

The smoke test prints how long transcription took and the realtime factor (processing time ÷ audio length). The backend logs the same numbers per job, without any text: `journalctl --user -u airsoap-backend`.

**To run in the foreground instead of systemd** (useful while debugging):

```bash
cd ~/AirSOAP/backend
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
- **Whisper receives only audio.** Each request carries the WAV file plus `response_format`, `temperature` and `language`. There is no prompt, and no patient name or date of birth (the backend never receives them).
- **Audio is deleted** as soon as its job completes, fails or is cancelled. It is also deleted when a recording is cancelled, when a recording fails or is empty, and on shutdown. Any leftovers from a crash are swept on startup. Failed recordings are not kept, so after a failure you record again.
- **Only `rec-<uuid>.wav` files are touched.** Paths are generated by the server, checked to sit directly inside the work dir, and never taken from requests. arecord runs from an argument list, never a shell string. The device name is validated.
- **Transcripts stay in memory** for `AIRSOAP_JOB_TTL_SECONDS` (10 minutes by default), then are dropped. They are never written to disk.
- **Logs** contain IDs, durations and timings only, never transcript text or audio. whisper.cpp prints recognized text to stdout, so the whisper-server unit sends stdout to `/dev/null` (`StandardOutput=null`). Check with `journalctl --user -u whisper-server` after a test that no transcript lines appear.

## Limitations

- Cancelling a job stops the backend from waiting, but whisper-server finishes the request it is already processing; the result is discarded. A new job waits until it is done.
- Speaker labels are not detected. Every segment arrives as "Unassigned", and the clinician can set the speaker during review.
- Total time scales with recording length. A long recording on the Pi 5 can take minutes; raise `AIRSOAP_WHISPER_TIMEOUT_SECONDS` if needed.

## Troubleshooting

| Symptom | Check |
| --- | --- |
| `transcriberReachable: false` | `systemctl --user status whisper-server`; the model may still be loading; `ss -ltn \| grep 8178` |
| Start fails with "The microphone could not be opened." | Another program holds the device; check `arecord -l` and the device name in the env file. The backend log has arecord's error line |
| "The recording contains no audio." | The recording was under 0.3 s, or the microphone produced no data |
| `TRANSCRIPTION_TIMEOUT` | Increase `AIRSOAP_WHISPER_TIMEOUT_SECONDS`, or check the Pi's CPU load and temperature |
| Services stop when you log out | `loginctl show-user ai-innovate \| grep Linger` should say `yes` |
