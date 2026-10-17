"""Run one AI task: ask the model, validate the answer, retry once if it was unusable."""

import logging
from collections.abc import Callable
from typing import Any, TypeVar

from .base import AiClient, AiCompletion, AiTask
from .errors import InvalidOutput, invalid_output_error

logger = logging.getLogger(__name__)

Req = TypeVar("Req")
Res = TypeVar("Res")


async def run_task(
    client: AiClient,
    task: AiTask[Req],
    request: Req,
    parse: Callable[[Req, dict[str, Any]], Res],
    *,
    max_attempts: int,
) -> tuple[Res, AiCompletion | None]:
    """Returns the validated result. Raises AiError if every attempt fails or the model is unavailable.

    Only a fixed reason code is logged for a rejected answer, never the answer itself.
    """
    for attempt in range(max_attempts):
        try:
            completion = await client.complete(task, request, attempt=attempt)
            return parse(request, completion.data), completion
        except InvalidOutput as exc:
            logger.warning("task=%s attempt=%d rejected reason=%s", task.name, attempt + 1, exc.reason)
    raise invalid_output_error()
