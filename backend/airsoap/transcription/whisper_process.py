"""Starts whisper-server only while it is needed, so its CPU and RAM are free the rest of the time.

The model (about 650 MB for small.en on the Pi) is loaded when a transcription
begins and the process is stopped after ``idle_seconds`` without work, or at
once if the job is cancelled. Loading takes a few seconds, so the first
recording after a quiet period waits for it; the job simply stays
``processing`` meanwhile.

whisper-server is always started on a loopback address. Its stdout, where
whisper.cpp prints recognized text, is discarded; only a few stderr lines
(model loading, errors) are kept, and only logged if startup fails.
"""

import asyncio
import contextlib
import logging
import os
from collections import deque
from collections.abc import AsyncIterator, Awaitable, Callable
from contextlib import asynccontextmanager
from pathlib import Path

from .base import TranscriptionError

logger = logging.getLogger(__name__)

POLL_INTERVAL_SECONDS = 0.2
TERMINATE_TIMEOUT_SECONDS = 5.0


def _unavailable(message: str, code: str = "TRANSCRIBER_UNAVAILABLE") -> TranscriptionError:
    return TranscriptionError(code, message)


class WhisperProcessManager:
    def __init__(
        self,
        *,
        binary: Path,
        model: Path,
        host: str,
        port: int,
        threads: int,
        language: str,
        idle_seconds: float,
        startup_timeout: float,
        probe: Callable[[], Awaitable[bool]],
    ) -> None:
        self._binary = binary
        self._model = model
        self._host = host
        self._port = port
        self._threads = threads
        self._language = language
        self._idle_seconds = idle_seconds
        self._startup_timeout = startup_timeout
        self._probe = probe

        self._lock = asyncio.Lock()  # serializes start and stop
        self._process: asyncio.subprocess.Process | None = None
        self._stderr: deque[str] = deque(maxlen=5)
        self._reader: asyncio.Task[None] | None = None
        self._active = 0
        self._idle_task: asyncio.Task[None] | None = None
        self._idle_stopping = False

    # -- state -------------------------------------------------------------

    @property
    def is_running(self) -> bool:
        return self._process is not None and self._process.returncode is None

    @property
    def pid(self) -> int | None:
        return self._process.pid if self.is_running and self._process else None

    def prerequisites_ok(self) -> bool:
        return os.access(self._binary, os.X_OK) and self._model.is_file()

    def build_command(self) -> list[str]:
        """An argument list, never a shell string. Always bound to the configured loopback host."""
        return [
            str(self._binary),
            "--model", str(self._model),
            "--host", self._host,
            "--port", str(self._port),
            "--threads", str(self._threads),
            "--language", self._language,
        ]  # fmt: skip

    # -- use ---------------------------------------------------------------

    @asynccontextmanager
    async def lease(self) -> AsyncIterator[None]:
        """Keep whisper-server running for the duration of one request."""
        self._cancel_idle_timer()
        self._active += 1
        try:
            async with self._lock:
                if not self.is_running:
                    await self._start()
            yield
        finally:
            self._active -= 1
            if self._active == 0:
                self._schedule_idle_stop()

    async def stop(self) -> None:
        """Stop now. Also used at shutdown and when a job is cancelled."""
        self._cancel_idle_timer()
        async with self._lock:
            await self._terminate()

    # -- internals ---------------------------------------------------------

    async def _start(self) -> None:
        if not self.prerequisites_ok():
            raise _unavailable("The transcription service is not installed on this device.")
        if await self._probe():
            raise _unavailable(
                "Another process is already using the transcription port.", "TRANSCRIBER_PORT_IN_USE"
            )
        try:
            process = await asyncio.create_subprocess_exec(  # noqa: S603 - fixed argument list
                *self.build_command(),
                stdin=asyncio.subprocess.DEVNULL,
                stdout=asyncio.subprocess.DEVNULL,
                stderr=asyncio.subprocess.PIPE,
            )
        except (FileNotFoundError, PermissionError) as exc:
            raise _unavailable("The transcription service could not be started.") from exc

        self._process = process
        self._stderr.clear()
        self._reader = asyncio.create_task(self._read_stderr(process))
        try:
            await self._wait_until_ready(process)
        except BaseException:
            # Failed, timed out or the job was cancelled while the model was loading.
            await self._terminate()
            raise
        logger.info("whisper-server started pid=%d", process.pid)

    async def _wait_until_ready(self, process: asyncio.subprocess.Process) -> None:
        loop = asyncio.get_running_loop()
        deadline = loop.time() + self._startup_timeout
        while True:
            if process.returncode is not None:
                logger.warning(
                    "whisper-server exited during startup (code %s): %s",
                    process.returncode,
                    " | ".join(self._stderr) or "no output",
                )
                raise _unavailable("The transcription service could not be started.")
            if await self._probe():
                return
            if loop.time() >= deadline:
                logger.warning("whisper-server was not ready after %.0f s", self._startup_timeout)
                raise _unavailable("The transcription service took too long to start.")
            await asyncio.sleep(POLL_INTERVAL_SECONDS)

    async def _read_stderr(self, process: asyncio.subprocess.Process) -> None:
        stream = process.stderr
        if stream is None:
            return
        # Keep draining so a chatty process can never block on a full pipe.
        while line := await stream.readline():
            self._stderr.append(line.decode("utf-8", "replace").strip()[:200])

    async def _terminate(self) -> None:
        process, self._process = self._process, None
        reader, self._reader = self._reader, None
        if process is not None and process.returncode is None:
            process.terminate()
            try:
                await asyncio.wait_for(process.wait(), timeout=TERMINATE_TIMEOUT_SECONDS)
            except TimeoutError:
                process.kill()
                await process.wait()
            logger.info("whisper-server stopped pid=%d", process.pid)
        if reader is not None:
            reader.cancel()
            await asyncio.gather(reader, return_exceptions=True)

    def _schedule_idle_stop(self) -> None:
        if not self.is_running:
            return
        self._cancel_idle_timer()
        self._idle_task = asyncio.create_task(self._stop_when_idle(), name="airsoap-whisper-idle-stop")

    def _cancel_idle_timer(self) -> None:
        task = self._idle_task
        # Once the timer has fired and is stopping the process, let it finish.
        if task is not None and not task.done() and not self._idle_stopping:
            task.cancel()
        self._idle_task = None

    async def _stop_when_idle(self) -> None:
        with contextlib.suppress(asyncio.CancelledError):
            await asyncio.sleep(self._idle_seconds)
            self._idle_stopping = True
            try:
                async with self._lock:
                    if self._active == 0:
                        await self._terminate()
            finally:
                self._idle_stopping = False
