"""Microphone capture on the Raspberry Pi with ALSA's ``arecord``."""

import asyncio
import logging
import shutil
import signal
from collections import deque
from pathlib import Path

from ..audio import CHANNELS, SAMPLE_RATE
from .base import RecorderError

logger = logging.getLogger(__name__)

STARTUP_GRACE_SECONDS = 0.4
STOP_TIMEOUT_SECONDS = 5.0
KILL_TIMEOUT_SECONDS = 2.0


class ArecordRecorder:
    name = "arecord"

    def __init__(
        self,
        *,
        binary: str,
        device: str,
        max_seconds: int,
        startup_grace: float = STARTUP_GRACE_SECONDS,
        stop_timeout: float = STOP_TIMEOUT_SECONDS,
    ) -> None:
        self._binary = binary
        self._device = device
        self._max_seconds = max_seconds
        self._startup_grace = startup_grace
        self._stop_timeout = stop_timeout

    def is_available(self) -> bool:
        return shutil.which(self._binary) is not None

    def build_command(self, path: Path) -> list[str]:
        """An argument list (never a shell string). ``-d`` caps the recording length."""
        return [
            self._binary,
            "-q",
            "-D", self._device,
            "-f", "S16_LE",
            "-r", str(SAMPLE_RATE),
            "-c", str(CHANNELS),
            "-t", "wav",
            "-d", str(self._max_seconds),
            str(path),
        ]  # fmt: skip

    async def start(self, path: Path) -> "ArecordProcess":
        try:
            process = await asyncio.create_subprocess_exec(  # noqa: S603 - fixed argument list
                *self.build_command(path),
                stdin=asyncio.subprocess.DEVNULL,
                stdout=asyncio.subprocess.DEVNULL,
                stderr=asyncio.subprocess.PIPE,
            )
        except (FileNotFoundError, PermissionError) as exc:
            raise RecorderError("RECORDER_UNAVAILABLE", "The recording tool is not available.") from exc

        recording = ArecordProcess(process, stop_timeout=self._stop_timeout)
        # arecord exits almost immediately when the device is missing or busy.
        try:
            await asyncio.wait_for(asyncio.shield(process.wait()), timeout=self._startup_grace)
        except TimeoutError:
            return recording
        await recording.drain_stderr()
        logger.warning(
            "arecord exited during startup (code %s): %s", process.returncode, recording.stderr_tail()
        )
        raise RecorderError("RECORDING_FAILED", "The microphone could not be opened.")


class ArecordProcess:
    def __init__(self, process: asyncio.subprocess.Process, *, stop_timeout: float) -> None:
        self._process = process
        self._stop_timeout = stop_timeout
        self._stderr: deque[str] = deque(maxlen=5)
        # Keep reading stderr so a chatty device can never fill the pipe and stall arecord.
        self._reader = asyncio.create_task(self._read_stderr())

    async def _read_stderr(self) -> None:
        stream = self._process.stderr
        if stream is None:
            return
        while line := await stream.readline():
            self._stderr.append(line.decode("utf-8", "replace").strip()[:200])

    async def drain_stderr(self) -> None:
        await asyncio.wait({self._reader}, timeout=1.0)

    def stderr_tail(self) -> str:
        return " | ".join(self._stderr) or "no output"

    async def stop(self) -> None:
        """SIGINT makes arecord write the final WAV sizes before it exits.

        arecord exits non-zero after SIGINT, so the exit code is not treated as a
        failure; the caller validates the WAV file instead.
        """
        if self._process.returncode is None:
            self._process.send_signal(signal.SIGINT)
            try:
                await asyncio.wait_for(self._process.wait(), timeout=self._stop_timeout)
            except TimeoutError:
                logger.warning("arecord did not stop after SIGINT; terminating")
                await self._terminate()
        await self.drain_stderr()

    async def abort(self) -> None:
        if self._process.returncode is None:
            await self._terminate()
        await self.drain_stderr()

    async def _terminate(self) -> None:
        self._process.terminate()
        try:
            await asyncio.wait_for(self._process.wait(), timeout=KILL_TIMEOUT_SECONDS)
        except TimeoutError:
            self._process.kill()
            await self._process.wait()
