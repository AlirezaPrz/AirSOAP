"""What every AI task and every model client has in common."""

from collections.abc import Callable
from dataclasses import dataclass
from typing import Any, Generic, Protocol, TypeVar

Req = TypeVar("Req")


@dataclass(frozen=True)
class Message:
    role: str  # "system" or "user"
    content: str


@dataclass(frozen=True)
class AiTask(Generic[Req]):  # noqa: UP046 - kept simple for readers
    """A prompt template, the JSON schema the answer must follow, and a mock for development."""

    name: str
    build_messages: Callable[[Req], list[Message]]
    schema: dict[str, Any]
    max_tokens: int
    mock: Callable[[Req], dict[str, Any]]


@dataclass(frozen=True)
class AiCompletion:
    data: dict[str, Any]
    prompt_tokens: int | None = None
    completion_tokens: int | None = None


class AiClient(Protocol):
    name: str

    async def complete(self, task: AiTask[Any], request: Any, *, attempt: int) -> AiCompletion:
        """Run one task and return the model's JSON answer. May raise AiError or InvalidOutput."""
        ...

    async def is_ready(self) -> bool:
        """The service is reachable and the configured model is installed."""
        ...

    async def unload(self) -> None:
        """Free the model's memory now. Best effort; never raises."""
        ...

    async def close(self) -> None: ...
