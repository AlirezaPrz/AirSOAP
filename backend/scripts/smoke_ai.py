#!/usr/bin/env python3
"""End-to-end check of the AI tasks on a running AirSOAP backend, using a built-in fictional visit.

Runs the SOAP task and then the Clinical Review task, one after the other, and prints how long each
took. Standard library only. Nothing real is sent: the transcript below is invented.
By default only timings and counts are printed; --show prints the results to this terminal.

    python3 scripts/smoke_ai.py
"""

import argparse
import json
import sys
import time
import urllib.error
import urllib.request

VISIT = [
    ("clinician", "What brings you in today?"),
    ("patient", "I have had a sore throat for three days and it hurts to swallow."),
    ("clinician", "Any fever or cough?"),
    ("patient", "I felt feverish last night, around 101. No cough."),
    ("clinician", "Your temperature today is 38.3 and your tonsils are swollen with white patches."),
    ("clinician", "Do you have any drug allergies?"),
]
CONTEXT = {"patientAge": 35, "encounterType": "acute"}


def call(base: str, method: str, path: str, body: dict | None = None) -> dict:
    data = json.dumps(body).encode() if body is not None else None
    request = urllib.request.Request(  # noqa: S310 - URL is the local backend given on the command line
        f"{base}{path}", data=data, method=method, headers={"Content-Type": "application/json"}
    )
    try:
        with urllib.request.urlopen(request, timeout=30) as response:  # noqa: S310
            raw = response.read()
            return json.loads(raw) if raw else {}
    except urllib.error.HTTPError as exc:
        error = json.loads(exc.read() or b"{}").get("error", {})
        sys.exit(f"{method} {path} failed with HTTP {exc.code}: {error}")
    except urllib.error.URLError as exc:
        sys.exit(f"Cannot reach the backend at {base}: {exc.reason}")


def run(base: str, path: str, payload: dict, timeout: float) -> tuple[dict, float]:
    job = call(base, "POST", path, payload)
    began = time.monotonic()
    last = None
    while True:
        status = call(base, "GET", f"/api/ai/jobs/{job['jobId']}")
        if status["status"] != last:
            print(f"  {time.monotonic() - began:6.1f} s  {status['status']}")
            last = status["status"]
        if status["status"] in {"completed", "error", "cancelled"}:
            elapsed = time.monotonic() - began
            call(base, "DELETE", f"/api/ai/jobs/{job['jobId']}")  # drop the result from backend memory
            return status, elapsed
        if time.monotonic() - began > timeout:
            call(base, "POST", f"/api/ai/jobs/{job['jobId']}/cancel")
            sys.exit("Timed out; job cancelled.")
        time.sleep(1)


def main() -> None:
    parser = argparse.ArgumentParser(
        description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter
    )
    parser.add_argument("--base-url", default="http://127.0.0.1:8000")
    parser.add_argument("--timeout", type=float, default=900.0)
    parser.add_argument("--show", action="store_true", help="print the generated note and suggestions")
    args = parser.parse_args()
    base = args.base_url.rstrip("/")

    health = call(base, "GET", "/api/health")
    print(f"health: ai={health['ai']} ready={health['aiReady']} whisper loaded={health['transcriberLoaded']}")
    if not health["aiReady"]:
        sys.exit("The AI service is not ready: is Ollama running and the model installed? (ollama list)")

    segments = [{"id": f"seg-{i}", "speaker": s, "text": t} for i, (s, t) in enumerate(VISIT, 1)]

    print("SOAP generation:")
    soap_status, soap_s = run(base, "/api/ai/soap", {"segments": segments, "context": CONTEXT}, args.timeout)
    if soap_status["status"] != "completed":
        sys.exit(f"SOAP generation {soap_status['status']}: {soap_status['error']}")
    result = soap_status["soapResult"]
    print(f"  done in {soap_s:.1f} s; {len(result['warnings'])} value(s) flagged as not in the transcript")
    if args.show:
        for name, text in result["soap"].items():
            print(f"  {name}: {text}")
        for warning in result["warnings"]:
            print(f"  warning: {warning['message']}")

    print("Clinical review (using the generated note):")
    review_status, review_s = run(
        base,
        "/api/ai/clinical-review",
        {"segments": segments, "soap": result["soap"], "context": CONTEXT},
        args.timeout,
    )
    if review_status["status"] != "completed":
        sys.exit(f"Clinical review {review_status['status']}: {review_status['error']}")
    review = review_status["reviewResult"]
    counts = {k: len(v) for k, v in review.items() if isinstance(v, list)}
    print(
        f"  done in {review_s:.1f} s; suggestions {counts}; "
        f"{review['omittedUnverified']} omitted as unverified"
    )
    if args.show:
        for group, items in review.items():
            if isinstance(items, list):
                for suggestion in items:
                    print(f"  [{group}] {suggestion['description']}")

    after = call(base, "GET", "/api/health")
    print(f"Total {soap_s + review_s:.1f} s. whisper-server loaded now: {after['transcriberLoaded']}")


if __name__ == "__main__":
    main()
