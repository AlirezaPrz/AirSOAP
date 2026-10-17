#!/usr/bin/env python3
"""End-to-end check of a running AirSOAP backend: record, stop, poll the transcription.

Uses only the standard library, so it runs on the Pi without the virtualenv.
It signs in as a clinician (you are asked for the email and password; the password
is hidden) and signs out at the end, which also discards the recording and transcript.
By default it prints timings and counts only; pass --show-transcript to print
the text to this terminal (it is never written anywhere else).

    python3 scripts/smoke_test.py --seconds 8
    python3 scripts/smoke_test.py --status      # only the backend's detailed status
"""

import argparse
import sys
import time

from _airsoap_client import Client, add_connection_arguments, connect


def main() -> None:
    parser = argparse.ArgumentParser(
        description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter
    )
    add_connection_arguments(parser)
    parser.add_argument("--status", action="store_true", help="print the detailed status and stop")
    parser.add_argument("--seconds", type=float, default=5.0, help="seconds to record; speak meanwhile")
    parser.add_argument("--timeout", type=float, default=900.0, help="seconds to wait for the transcript")
    parser.add_argument("--show-transcript", action="store_true", help="print the transcript text")
    args = parser.parse_args()
    client = connect(args)
    client.sign_in(args.email, "clinician")
    try:
        check(client, args)
    finally:
        client.sign_out()


def check(client: Client, args: argparse.Namespace) -> None:
    health = client.call("GET", "/api/system/status")
    print(
        f"health: recorder={health['recorder']} (available={health['recorderAvailable']}) "
        f"transcriber={health['transcriber']} (ready={health['transcriberReachable']}, "
        f"model loaded={health['transcriberLoaded']}) "
        f"state={health['recording']}"
    )
    if args.status:
        print(f"ai={health['ai']} ready={health['aiReady']}")
        return
    if not health["transcriberReachable"]:
        sys.exit("The transcriber is not ready: check the whisper-server path and model in the env file.")

    started = client.call("POST", "/api/recording/start")
    print(f"recording for {args.seconds:.1f} s; speak now...")
    time.sleep(args.seconds)
    stopped = client.call("POST", "/api/recording/stop", {"recordingId": started["recordingId"]})
    print(f"recorded {stopped['durationMs'] / 1000:.1f} s of audio; job {stopped['jobId']}")

    began = time.monotonic()
    last_status = None
    while True:
        status = client.call("GET", f"/api/transcriptions/{stopped['jobId']}")
        if status["status"] != last_status:
            print(f"  {time.monotonic() - began:6.1f} s  {status['status']}")
            last_status = status["status"]
        if status["status"] in {"completed", "error", "cancelled"}:
            break
        if time.monotonic() - began > args.timeout:
            client.call("POST", f"/api/transcriptions/{stopped['jobId']}/cancel")
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
