#!/usr/bin/env python3
"""End-to-end check of a running AirSOAP backend: record, stop, poll the transcription.

Uses only the standard library, so it runs on the Pi without the virtualenv.
By default it prints timings and counts only; pass --show-transcript to print
the text to this terminal (it is never written anywhere else).

    python3 scripts/smoke_test.py --seconds 8
"""

import argparse
import json
import sys
import time
import urllib.error
import urllib.request


def call(base: str, method: str, path: str, body: dict | None = None) -> dict:
    data = json.dumps(body).encode() if body is not None else None
    request = urllib.request.Request(  # noqa: S310 - URL is the local backend given on the command line
        f"{base}{path}", data=data, method=method, headers={"Content-Type": "application/json"}
    )
    try:
        with urllib.request.urlopen(request, timeout=30) as response:  # noqa: S310
            return json.loads(response.read())
    except urllib.error.HTTPError as exc:
        payload = json.loads(exc.read() or b"{}")
        error = payload.get("error", payload)
        sys.exit(f"{method} {path} failed with HTTP {exc.code}: {error}")
    except urllib.error.URLError as exc:
        sys.exit(f"Cannot reach the backend at {base}: {exc.reason}")


def main() -> None:
    parser = argparse.ArgumentParser(
        description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter
    )
    parser.add_argument("--base-url", default="http://127.0.0.1:8000")
    parser.add_argument("--seconds", type=float, default=5.0, help="seconds to record; speak meanwhile")
    parser.add_argument("--timeout", type=float, default=900.0, help="seconds to wait for the transcript")
    parser.add_argument("--show-transcript", action="store_true", help="print the transcript text")
    args = parser.parse_args()
    base = args.base_url.rstrip("/")

    health = call(base, "GET", "/api/health")
    print(
        f"health: recorder={health['recorder']} (available={health['recorderAvailable']}) "
        f"transcriber={health['transcriber']} (ready={health['transcriberReachable']}, "
        f"model loaded={health['transcriberLoaded']}) "
        f"state={health['recording']}"
    )
    if not health["transcriberReachable"]:
        sys.exit("The transcriber is not ready: check the whisper-server path and model in the env file.")

    started = call(base, "POST", "/api/recording/start")
    print(f"recording for {args.seconds:.1f} s; speak now...")
    time.sleep(args.seconds)
    stopped = call(base, "POST", "/api/recording/stop", {"recordingId": started["recordingId"]})
    print(f"recorded {stopped['durationMs'] / 1000:.1f} s of audio; job {stopped['jobId']}")

    began = time.monotonic()
    last_status = None
    while True:
        status = call(base, "GET", f"/api/transcriptions/{stopped['jobId']}")
        if status["status"] != last_status:
            print(f"  {time.monotonic() - began:6.1f} s  {status['status']}")
            last_status = status["status"]
        if status["status"] in {"completed", "error", "cancelled"}:
            break
        if time.monotonic() - began > args.timeout:
            call(base, "POST", f"/api/transcriptions/{stopped['jobId']}/cancel")
            sys.exit("Timed out waiting for the transcript; job cancelled.")
        time.sleep(0.5)

    elapsed = time.monotonic() - began
    if status["status"] != "completed":
        sys.exit(f"Transcription {status['status']}: {status['error']}")

    transcript = status["transcript"]
    words = len(transcript["text"].split())
    audio_s = stopped["durationMs"] / 1000
    print(
        f"completed in {elapsed:.1f} s for {audio_s:.1f} s of audio "
        f"(realtime factor {elapsed / audio_s:.2f}); {len(transcript['segments'])} segments, {words} words"
    )
    if args.show_transcript:
        for segment in transcript["segments"]:
            print(f"  [{segment['startMs'] / 1000:6.1f} s] {segment['text']}")


if __name__ == "__main__":
    main()
