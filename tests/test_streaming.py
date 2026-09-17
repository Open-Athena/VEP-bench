import io
import json
import urllib.error
import urllib.request
from typing import ClassVar

import pytest

from vepbench.evaluation.core import ProviderError, provider_response_snapshot
from vepbench.evaluation.streaming import OpenRouterStreamingTransport


class Response(io.BytesIO):
    headers: ClassVar = {"Content-Type": "text/event-stream", "X-Generation-Id": "gen-test"}


def event(delta=None, *, finish=None, **extra):
    return {
        "id": "gen-test",
        "provider": "Meta",
        "choices": [{"index": 0, "delta": delta or {}, "finish_reason": finish}],
        **extra,
    }


def wire(chunks, *, done=True):
    body = b": OPENROUTER PROCESSING\r\n\r\n"
    for chunk in chunks:
        body += ("data: " + json.dumps(chunk) + "\r\n\r\n").encode()
    return body + (b"data: [DONE]\n\n" if done else b"")


def complete(monkeypatch, payload):
    def urlopen(request, timeout):
        body = json.loads(request.data)
        assert body["stream"] is True
        assert body["max_tokens"] == 128000
        assert body["reasoning"] == {"effort": "max", "exclude": False}
        assert body["messages"] == [{"role": "user", "content": "offline fixture"}]
        assert timeout == 900
        return Response(payload)

    monkeypatch.setattr(urllib.request, "urlopen", urlopen)
    return OpenRouterStreamingTransport().complete(
        {
            "model": "offline/model",
            "messages": [{"role": "user", "content": "offline fixture"}],
            "max_tokens": 128000,
            "reasoning": {"effort": "max", "exclude": False},
        },
        "offline-key",
    )


@pytest.mark.parametrize("empty_choices", [True, False])
def test_stream_preserves_text_reasoning_usage_and_original_events(monkeypatch, empty_choices):
    chunks = [
        event({"role": "assistant", "reasoning": "Think "}),
        event({"reasoning": "carefully.", "content": "FINAL: "}),
        event({"content": "B"}, finish="stop"),
        event(finish="stop", usage={"completion_tokens": 12, "cost": 0.001}),
    ]
    if empty_choices:
        chunks[-1]["choices"] = []
    raw = complete(monkeypatch, wire(chunks))
    snapshot = provider_response_snapshot(raw)
    assert snapshot["content"] == "FINAL: B"
    assert snapshot["reasoning"] == "Think carefully."
    assert snapshot["finish_reason"] == "stop"
    assert snapshot["upstream_provider"] == "Meta"
    assert raw["usage"] == {"completion_tokens": 12, "cost": 0.001}
    assert raw["vepbench_stream"]["events"] == chunks
    assert raw["vepbench_stream"]["generation_id"] == "gen-test"


def test_stream_reassembles_reasoning_details(monkeypatch):
    chunks = [
        event({"reasoning_details": [{"index": 0, "type": "reasoning.text", "text": "First "}]}),
        event({"reasoning_details": [{"index": 0, "type": "reasoning.text", "text": "second"}]}),
        event({"content": "FINAL: B"}, finish="stop"),
    ]
    assert (
        provider_response_snapshot(complete(monkeypatch, wire(chunks)))["reasoning"]
        == "First second"
    )


@pytest.mark.parametrize("placement", ["top", "choice", "finish_only"])
def test_stream_errors_never_turn_partial_answers_into_completed_responses(monkeypatch, placement):
    failure = event(finish="error", usage={"cost": 0})
    if placement == "top":
        failure["error"] = {"code": 502, "message": "Lost connection"}
    elif placement == "choice":
        failure["choices"][0]["error"] = {"code": 502, "message": "Lost connection"}
    chunks = [event({"content": "FINAL: B"}), failure]
    with pytest.raises(ProviderError) as raised:
        complete(monkeypatch, wire(chunks))
    raw = raised.value.raw_response
    assert raw["vepbench_stream"]["events"] == chunks
    assert raw["usage"]["cost"] == 0
    with pytest.raises(ProviderError):
        provider_response_snapshot(raw)


@pytest.mark.parametrize(
    ("payload", "message"),
    [
        (wire([event({"content": "FINAL: B"}, finish="stop")], done=False), "before \\[DONE\\]"),
        (wire([event({"content": "FINAL: B"})]), "omitted finish reason"),
        (b"data: broken\n\n", "Invalid JSON"),
        (wire([event({"tool_calls": [{"index": 0}]})]), "tool calls"),
    ],
)
def test_unusable_streams_are_api_errors(monkeypatch, payload, message):
    with pytest.raises(ProviderError, match=message):
        complete(monkeypatch, payload)


def test_multiline_sse_event(monkeypatch):
    payload = (
        b'data: {"choices": [{"index": 0,\n'
        b'data: "delta": {"content": "FINAL: B"}, "finish_reason": "stop"}]}\n\n'
        b"data: [DONE]\n\n"
    )
    assert provider_response_snapshot(complete(monkeypatch, payload))["content"] == "FINAL: B"


def test_http_error_retains_provider_payload(monkeypatch):
    payload = {"error": {"code": 503, "message": "Unavailable"}}

    def fail(*args, **kwargs):
        raise urllib.error.HTTPError(
            "https://offline.invalid",
            503,
            "Unavailable",
            {},
            io.BytesIO(json.dumps(payload).encode()),
        )

    monkeypatch.setattr(urllib.request, "urlopen", fail)
    with pytest.raises(ProviderError) as raised:
        OpenRouterStreamingTransport().complete({}, "offline-key")
    assert raised.value.status_code == 503
    assert raised.value.raw_response == payload
