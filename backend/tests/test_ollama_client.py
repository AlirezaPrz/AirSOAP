"""OllamaClient against an in-process fake HTTP transport. No real Ollama is ever contacted."""

import json
import socket

import httpx
import pytest
from helpers import soap_payload

from airsoap.ai.errors import AiError, InvalidOutput
from airsoap.ai.ollama import OllamaClient, inline_schema
from airsoap.ai.schemas import SoapRequest
from airsoap.ai.tasks import soap

pytestmark = pytest.mark.anyio

REQUEST = SoapRequest.model_validate(soap_payload())


def client_for(handler, **overrides) -> OllamaClient:
    options = {
        "base_url": "http://127.0.0.1:11434",
        "model": "qwen2.5:3b-instruct",
        "timeout_seconds": 30,
        "keep_alive": "5m",
        "num_ctx": 8192,
        "transport": httpx.MockTransport(handler),
    }
    return OllamaClient(**{**options, **overrides})


def chat_reply(content: str, **extra) -> httpx.Response:
    body = {
        "message": {"role": "assistant", "content": content},
        "done": True,
        "done_reason": "stop",
        **extra,
    }
    return httpx.Response(200, json=body)


async def test_sends_one_local_chat_request_with_schema_and_options():
    seen: list[httpx.Request] = []

    def handler(request: httpx.Request) -> httpx.Response:
        seen.append(request)
        return chat_reply(
            json.dumps(soap.SoapDraft.model_construct(**{k: "x" for k in soap.SOAP_KEYS}).model_dump()),
            prompt_eval_count=321,
            eval_count=45,
        )

    completion = await client_for(handler).complete(soap.TASK, REQUEST, attempt=0)

    assert len(seen) == 1
    assert (seen[0].method, str(seen[0].url)) == ("POST", "http://127.0.0.1:11434/api/chat")
    payload = json.loads(seen[0].read())
    assert payload["model"] == "qwen2.5:3b-instruct"
    assert payload["stream"] is False
    assert payload["keep_alive"] == "5m"
    assert payload["format"] == soap.TASK.schema
    assert payload["options"] == {"temperature": 0, "seed": 0, "num_ctx": 8192, "num_predict": 900}
    assert [m["role"] for m in payload["messages"]] == ["system", "user"]
    assert completion.prompt_tokens == 321
    assert completion.completion_tokens == 45


async def test_a_retry_varies_the_sampling():
    payloads = []

    def handler(request: httpx.Request) -> httpx.Response:
        payloads.append(json.loads(request.read()))
        return chat_reply("{}")

    await client_for(handler).complete(soap.TASK, REQUEST, attempt=1)
    assert payloads[0]["options"]["temperature"] > 0
    assert payloads[0]["options"]["seed"] == 1


async def test_numeric_keep_alive_is_sent_as_a_number():
    payloads = []

    def handler(request: httpx.Request) -> httpx.Response:
        payloads.append(json.loads(request.read()))
        return chat_reply("{}")

    await client_for(handler, keep_alive="0").complete(soap.TASK, REQUEST, attempt=0)
    assert payloads[0]["keep_alive"] == 0


@pytest.mark.parametrize(
    ("error", "code"),
    [
        (httpx.ConnectError("refused"), "AI_UNAVAILABLE"),
        (httpx.ReadTimeout("slow"), "AI_TIMEOUT"),
    ],
)
async def test_connection_failure_and_timeout(error, code):
    def handler(request: httpx.Request) -> httpx.Response:
        raise error

    with pytest.raises(AiError) as excinfo:
        await client_for(handler).complete(soap.TASK, REQUEST, attempt=0)
    assert excinfo.value.code == code
    assert excinfo.value.retryable is True
    assert "refused" not in excinfo.value.message


async def test_missing_model_and_server_errors_use_safe_messages():
    def missing(request: httpx.Request) -> httpx.Response:
        return httpx.Response(404, json={"error": "model 'secret-name' not found"})

    def broken(request: httpx.Request) -> httpx.Response:
        return httpx.Response(500, text="Traceback: prompt was: Patient age: 35")

    with pytest.raises(AiError) as not_installed:
        await client_for(missing).complete(soap.TASK, REQUEST, attempt=0)
    assert not_installed.value.code == "AI_MODEL_MISSING"
    assert "secret-name" not in not_installed.value.message

    with pytest.raises(AiError) as failed:
        await client_for(broken).complete(soap.TASK, REQUEST, attempt=0)
    assert failed.value.code == "AI_FAILED"
    assert "Traceback" not in failed.value.message
    assert "Patient age" not in failed.value.message


@pytest.mark.parametrize(
    ("response", "reason"),
    [
        (httpx.Response(200, text="not json at all"), "bad_response"),
        (httpx.Response(200, json={"unexpected": True}), "bad_response"),
        (httpx.Response(200, json={"message": {"content": 5}}), "bad_response"),
        (chat_reply("this is {not valid json"), "invalid_json"),
        (chat_reply('["a", "list"]'), "not_an_object"),
        (chat_reply('{"subjective": "cut off', done_reason="length"), "truncated"),
    ],
)
async def test_malformed_model_output_is_rejected(response, reason):
    with pytest.raises(InvalidOutput) as excinfo:
        await client_for(lambda request: response).complete(soap.TASK, REQUEST, attempt=0)
    assert excinfo.value.reason == reason


async def test_is_ready_requires_the_configured_model():
    tags = {"models": [{"name": "qwen2.5:3b-instruct"}, {"name": "qwen2.5:1.5b-instruct"}]}
    assert await client_for(lambda r: httpx.Response(200, json=tags)).is_ready() is True
    other = {"models": [{"name": "qwen2.5:1.5b-instruct"}]}
    assert await client_for(lambda r: httpx.Response(200, json=other)).is_ready() is False
    assert await client_for(lambda r: httpx.Response(500)).is_ready() is False

    def refuse(request: httpx.Request) -> httpx.Response:
        raise httpx.ConnectError("refused")

    assert await client_for(refuse).is_ready() is False


async def test_unload_asks_for_keep_alive_zero_and_never_raises():
    payloads = []

    def ok(request: httpx.Request) -> httpx.Response:
        payloads.append(json.loads(request.read()))
        return httpx.Response(200, json={})

    await client_for(ok).unload()
    assert payloads == [{"model": "qwen2.5:3b-instruct", "messages": [], "keep_alive": 0}]

    def refuse(request: httpx.Request) -> httpx.Response:
        raise httpx.ConnectError("refused")

    await client_for(refuse).unload()  # must not raise


def test_environment_proxies_are_ignored():
    assert client_for(lambda r: httpx.Response(200))._http(1).trust_env is False


def test_schema_is_flat_and_keeps_fields_named_like_annotations():
    nested = {
        "$defs": {"Item": {"title": "Item", "type": "object", "properties": {"title": {"type": "string"}}}},
        "title": "Root",
        "type": "object",
        "properties": {"items": {"type": "array", "items": {"$ref": "#/$defs/Item"}, "default": []}},
    }
    flat = inline_schema(nested)
    assert "$ref" not in json.dumps(flat)
    assert "$defs" not in flat
    assert "title" not in flat  # the annotation is gone ...
    assert flat["properties"]["items"]["items"]["properties"] == {
        "title": {"type": "string"}
    }  # ... the field stays
    assert "default" not in flat["properties"]["items"]


def test_the_guard_blocks_ollamas_real_port():
    with socket.socket() as sock, pytest.raises(AssertionError, match="11434"):
        sock.connect(("127.0.0.1", 11434))
    with socket.socket() as sock, pytest.raises(AssertionError, match="192.168.4.60"):
        sock.connect(("192.168.4.60", 80))
