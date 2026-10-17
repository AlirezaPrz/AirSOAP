"""An AI client for development on macOS and for tests. Never contacts any service."""

import asyncio
from typing import Any

from .base import AiCompletion, AiTask


class MockAiClient:
    name = "mock"

    def __init__(self, *, delay_seconds: float = 0.0) -> None:
        self._delay = delay_seconds

    async def complete(self, task: AiTask[Any], request: Any, *, attempt: int) -> AiCompletion:
        await asyncio.sleep(self._delay)
        return AiCompletion(data=task.mock(request))

    async def is_ready(self) -> bool:
        return True

    async def unload(self) -> None:
        return None

    async def close(self) -> None:
        return None
