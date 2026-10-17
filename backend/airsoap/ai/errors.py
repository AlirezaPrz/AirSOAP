"""Errors from the local language model. Messages are fixed strings: they never contain
prompts, transcript or note text, or raw model output."""


class AiError(Exception):
    def __init__(self, code: str, message: str, *, retryable: bool = False) -> None:
        super().__init__(message)
        self.code = code
        self.message = message
        self.retryable = retryable


class InvalidOutput(Exception):
    """The model's answer was unusable (bad JSON, wrong shape, failed a check).

    ``reason`` is a short fixed code that is safe to log; it never carries model text.
    """

    def __init__(self, reason: str) -> None:
        super().__init__(reason)
        self.reason = reason


def invalid_output_error() -> AiError:
    return AiError(
        "AI_INVALID_OUTPUT",
        "The AI model returned an answer that could not be used. Please try again.",
        retryable=True,
    )
