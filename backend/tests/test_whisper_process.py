"""On-demand whisper-server lifecycle, against a stand-in server script (real subprocess and HTTP)."""

import asyncio
import socket
import socketserver
import threading
from collections.abc import Iterator
from contextlib import closing, contextmanager
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from pathlib import Path

import pytest
from helpers import make_binary

from airsoap.audio import synth_tone, write_wav
from airsoap.transcription import (
    ManagedWhisperTranscriber,
    TranscriptionError,
    WhisperProcessManager,
    WhisperServerTranscriber,
)

pytestmark = pytest.mark.anyio

# Behaves like whisper-server: answers GET / and POST /inference over HTTP on --host/--port.
FAKE_SERVER = """\
import argparse, json, socketserver, time
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer

parser = argparse.ArgumentParser()
for flag in ("--model", "--host", "--port", "--threads", "--language"):
    parser.add_argument(flag)
args = parser.parse_args()
INFERENCE_DELAY = __DELAY__

class Handler(BaseHTTPRequestHandler):
    def log_message(self, *_):
        pass

    def reply(self, body):
        self.send_response(200)
        self.send_header("Content-Type", "application/json")
        self.send_header("Content-Length", str(len(body)))
        self.end_headers()
        self.wfile.write(body)

    def do_GET(self):
        self.reply(b"ok")

    def do_POST(self):
        self.rfile.read(int(self.headers.get("Content-Length", 0)))
        time.sleep(INFERENCE_DELAY)
        payload = {"language": "en", "duration": 1.0,
                   "segments": [{"start": 0.0, "end": 1.0, "text": " Hello there."}]}
        self.reply(json.dumps(payload).encode())

class Server(ThreadingHTTPServer):
    def server_bind(self):
        # The stock implementation does a reverse-DNS lookup that can stall for seconds.
        socketserver.TCPServer.server_bind(self)
        self.server_name, self.server_port = "localhost", self.server_address[1]

Server((args.host, int(args.port)), Handler).serve_forever()
"""
EXITS_AT_ONCE = "import sys\nsys.stderr.write('model load failed\\n')\nsys.exit(1)\n"
NEVER_READY = "import time\ntime.sleep(60)\n"


def free_port() -> int:
    with closing(socket.socket()) as sock:
        sock.bind(("127.0.0.1", 0))
        return sock.getsockname()[1]


class Env:
    def __init__(self, tmp_path: Path, source: str = FAKE_SERVER, *, delay: float = 0.0) -> None:
        self.port = free_port()
        self.binary = Path(make_binary(tmp_path, "whisper-server", source.replace("__DELAY__", str(delay))))
        self.model = tmp_path / "ggml-small.en.bin"
        self.model.write_bytes(b"model")
        self.wav = tmp_path / "rec.wav"
        write_wav(self.wav, synth_tone(500))

    def build(
        self,
        *,
        idle: float = 0.3,
        startup: float = 10.0,
        model: Path | None = None,
        binary: Path | None = None,
    ):
        client = WhisperServerTranscriber(
            base_url=f"http://127.0.0.1:{self.port}", timeout_seconds=10, language="en"
        )
        manager = WhisperProcessManager(
            binary=binary or self.binary,
            model=model or self.model,
            host="127.0.0.1",
            port=self.port,
            threads=2,
            language="en",
            idle_seconds=idle,
            startup_timeout=startup,
            probe=client.is_reachable,
        )
        return ManagedWhisperTranscriber(client=client, manager=manager), manager


async def until(predicate, timeout: float = 5.0) -> None:
    async with asyncio.timeout(timeout):
        while not predicate():
            await asyncio.sleep(0.02)


async def test_starts_on_first_request_and_stops_after_idle(tmp_path: Path):
    env = Env(tmp_path)
    transcriber, manager = env.build(idle=0.3)
    try:
        assert manager.is_running is False
        assert transcriber.is_loaded() is False
        assert await transcriber.is_reachable() is True  # installed and able to start

        result = await transcriber.transcribe(env.wav, 500)
        assert result.text == "Hello there."
        assert manager.is_running is True
        first_pid = manager.pid

        await until(lambda: not manager.is_running)  # idle timeout releases the model
        assert transcriber.is_loaded() is False

        await transcriber.transcribe(env.wav, 500)  # starts again on demand
        assert manager.is_running is True
        assert manager.pid != first_pid
    finally:
        await transcriber.close()
    assert manager.is_running is False


async def test_idle_timer_restarts_after_each_request(tmp_path: Path):
    env = Env(tmp_path)
    transcriber, manager = env.build(idle=1.0)
    try:
        await transcriber.transcribe(env.wav, 500)
        await asyncio.sleep(0.6)
        await transcriber.transcribe(env.wav, 500)  # resets the countdown
        pid = manager.pid
        await asyncio.sleep(0.6)  # 1.2 s since the first request, but only 0.6 s since the last
        assert manager.is_running and manager.pid == pid
        await until(lambda: not manager.is_running)
    finally:
        await transcriber.close()


async def test_cancelling_a_request_stops_whisper_server_immediately(tmp_path: Path):
    env = Env(tmp_path, delay=30)  # an inference that would take 30 s
    transcriber, manager = env.build(idle=60)
    task = asyncio.create_task(transcriber.transcribe(env.wav, 500))
    try:
        await until(lambda: manager.is_running)
        await asyncio.sleep(0.5)  # the request is now in flight
        task.cancel()
        with pytest.raises(asyncio.CancelledError):
            await task
        assert manager.is_running is False  # not waiting for the idle timer
    finally:
        task.cancel()
        await transcriber.close()


async def test_cancelling_during_model_load_stops_the_process(tmp_path: Path):
    env = Env(tmp_path, NEVER_READY)
    transcriber, manager = env.build(startup=30)
    task = asyncio.create_task(transcriber.transcribe(env.wav, 500))
    try:
        await until(lambda: manager.is_running)
        task.cancel()
        with pytest.raises(asyncio.CancelledError):
            await task
        assert manager.is_running is False
    finally:
        await transcriber.close()


async def test_process_that_exits_during_startup_is_reported(tmp_path: Path):
    env = Env(tmp_path, EXITS_AT_ONCE)
    transcriber, manager = env.build()
    with pytest.raises(TranscriptionError) as excinfo:
        await transcriber.transcribe(env.wav, 500)
    assert excinfo.value.code == "TRANSCRIBER_UNAVAILABLE"
    assert "model load failed" not in excinfo.value.message  # raw process output stays out of API errors
    assert manager.is_running is False


async def test_startup_timeout(tmp_path: Path):
    env = Env(tmp_path, NEVER_READY)
    transcriber, manager = env.build(startup=0.6)
    with pytest.raises(TranscriptionError) as excinfo:
        await transcriber.transcribe(env.wav, 500)
    assert excinfo.value.code == "TRANSCRIBER_UNAVAILABLE"
    assert "too long" in excinfo.value.message
    assert manager.is_running is False


async def test_missing_binary_or_model(tmp_path: Path):
    env = Env(tmp_path)
    for kwargs in ({"binary": tmp_path / "nope"}, {"model": tmp_path / "missing.bin"}):
        transcriber, manager = env.build(**kwargs)
        assert await transcriber.is_reachable() is False
        with pytest.raises(TranscriptionError) as excinfo:
            await transcriber.transcribe(env.wav, 500)
        assert excinfo.value.code == "TRANSCRIBER_UNAVAILABLE"
        assert manager.is_running is False


@contextmanager
def occupied_port(port: int) -> Iterator[None]:
    class Handler(BaseHTTPRequestHandler):
        def log_message(self, *_):
            pass

        def do_GET(self):
            self.send_response(200)
            self.send_header("Content-Length", "0")
            self.end_headers()

    class Server(ThreadingHTTPServer):
        def server_bind(self):
            socketserver.TCPServer.server_bind(self)
            self.server_name, self.server_port = "localhost", self.server_address[1]

    server = Server(("127.0.0.1", port), Handler)
    thread = threading.Thread(target=server.serve_forever, daemon=True)
    thread.start()
    try:
        yield
    finally:
        server.shutdown()
        server.server_close()
        thread.join()


async def test_refuses_to_start_when_something_already_serves_the_port(tmp_path: Path):
    env = Env(tmp_path)
    transcriber, manager = env.build()
    with occupied_port(env.port), pytest.raises(TranscriptionError) as excinfo:
        await transcriber.transcribe(env.wav, 500)
    assert excinfo.value.code == "TRANSCRIBER_PORT_IN_USE"
    assert manager.is_running is False


async def test_command_is_a_loopback_argument_list(tmp_path: Path):
    env = Env(tmp_path)
    _, manager = env.build()
    assert manager.build_command() == [
        str(env.binary),
        "--model",
        str(env.model),
        "--host",
        "127.0.0.1",
        "--port",
        str(env.port),
        "--threads",
        "2",
        "--language",
        "en",
    ]
