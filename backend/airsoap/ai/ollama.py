"""Client for the local Ollama server (loopback only).

One shared client serves every AI task: the same model, one request at a time
(the caller serializes requests; see ``gate.py``). Answers are requested as JSON
that follows the task's schema. Failures become fixed, safe error messages.
"""

import json
import logging
from typing import Any

import httpx

from .base import AiCompletion, AiTask
from .errors import AiError, InvalidOutput

logger = logging.getLogger(__name__)

CONNECT_TIMEOUT_SECONDS = 5.0
LIGHT_TIMEOUT_SECONDS = 5.0
RETRY_TEMPERATURE = 0.3


def inline_schema(schema: dict[str, Any]) -> dict[str, Any]:
    """Resolve ``$ref`` and drop annotation keys, so the schema is plain and self-contained.

    Ollama turns the schema into a decoding grammar; flat schemas are the most reliable.
    """
    defs: dict[str, Any] = schema.get("$defs", {})

    def walk(node: Any) -> Any:
        if isinstance(node, dict):
            if "$ref" in node:
                return walk(defs[node["$ref"].rsplit("/", 1)[-1]])
            return {
                key: walk(value)
                for key, value in node.items()
                # Annotation keywords have scalar values; a *field* named "title" has a dict value.
                if key != "$defs"
                and not (key in {"title", "default", "description"} and not isinstance(value, dict))
            }
        if isinstance(node, list):
            return [walk(item) for item in node]
        return node

    return walk(schema)


class OllamaClient:
    name = "ollama"

    def __init__(
        self,
        *,
        base_url: str,
        model: str,
        timeout_seconds: float,
        keep_alive: str,
        num_ctx: int,
        transport: httpx.AsyncBaseTransport | None = None,
    ) -> None:
        self._base_url = base_url.rstrip("/")
        self._model = model
        self._timeout = httpx.Timeout(timeout_seconds, connect=CONNECT_TIMEOUT_SECONDS)
        self._keep_alive: str | int = int(keep_alive) if keep_alive.lstrip("-").isdigit() else keep_alive
        self._num_ctx = num_ctx
        self._transport = transport

    def _http(self, timeout: httpx.Timeout | float) -> httpx.AsyncClient:
        return httpx.AsyncClient(
            base_url=self._base_url,
            timeout=timeout,
            transport=self._transport,
            trust_env=False,  # clinical text stays on this device: never use an environment proxy
        )

    async def complete(self, task: AiTask[Any], request: Any, *, attempt: int) -> AiCompletion:
        payload = {
            "model": self._model,
            "messages": [{"role": m.role, "content": m.content} for m in task.build_messages(request)],
            "stream": False,
            "format": task.schema,
            "keep_alive": self._keep_alive,
            "options": {
                # The first attempt is deterministic; a retry varies slightly so it can differ.
                "temperature": 0 if attempt == 0 else RETRY_TEMPERATURE,
                "seed": attempt,
                "num_ctx": self._num_ctx,
                "num_predict": task.max_tokens,
            },
        }
        try:
            async with self._http(self._timeout) as client:
                response = await client.post("/api/chat", json=payload)
        except httpx.TimeoutException as exc:
            raise AiError("AI_TIMEOUT", "The AI model took too long to answer.", retryable=True) from exc
        except httpx.TransportError as exc:
            raise AiError(
                "AI_UNAVAILABLE", "The local AI model service is not running.", retryable=True
            ) from exc

        if response.status_code == 404:
            raise AiError("AI_MODEL_MISSING", "The configured AI model is not installed on this device.")
        if response.status_code != 200:
            raise AiError("AI_FAILED", "The local AI model service reported an error.", retryable=True)
        return self._parse(response)

    @staticmethod
    def _parse(response: httpx.Response) -> AiCompletion:
        try:
            body = response.json()
            content = body["message"]["content"]
        except (ValueError, KeyError, TypeError) as exc:
            raise InvalidOutput("bad_response") from exc
        if not isinstance(content, str):
            raise InvalidOutput("bad_response")
        if body.get("done_reason") == "length":
            raise InvalidOutput("truncated")  # hit the token limit mid-answer
        try:
            data = json.loads(content)
        except ValueError as exc:
            raise InvalidOutput("invalid_json") from exc
        if not isinstance(data, dict):
            raise InvalidOutput("not_an_object")
        prompt_tokens, completion_tokens = body.get("prompt_eval_count"), body.get("eval_count")
        return AiCompletion(
            data=data,
            prompt_tokens=prompt_tokens if isinstance(prompt_tokens, int) else None,
            completion_tokens=completion_tokens if isinstance(completion_tokens, int) else None,
        )

    async def is_ready(self) -> bool:
        try:
            async with self._http(LIGHT_TIMEOUT_SECONDS) as client:
                response = await client.get("/api/tags")
            names = {
                entry.get("name") for entry in response.json().get("models", []) if isinstance(entry, dict)
            }
        except (httpx.HTTPError, ValueError, AttributeError):
            return False
        wanted = self._model if ":" in self._model else f"{self._model}:latest"
        return response.status_code == 200 and wanted in names

    async def unload(self) -> None:
        """Ask Ollama to drop the model from memory (an empty chat with keep_alive 0)."""
        try:
            async with self._http(LIGHT_TIMEOUT_SECONDS) as client:
                await client.post("/api/chat", json={"model": self._model, "messages": [], "keep_alive": 0})
        except httpx.HTTPError:
            logger.debug("could not ask Ollama to unload the model")

    async def close(self) -> None:
        return None
