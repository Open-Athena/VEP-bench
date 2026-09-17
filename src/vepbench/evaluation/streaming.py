"""Text-only streaming transport with complete provider event provenance."""

import http.client
import json
import urllib.error
import urllib.request
from collections.abc import Iterable, Mapping
from typing import Any

from ..artifacts import canonical_json
from .core import OPENROUTER_ENDPOINT, ProviderError, _provider_error


class _Stream:
    def __init__(self) -> None:
        self.raw: dict[str, Any] = {
            "choices": [{"index": 0, "message": {}, "finish_reason": None}],
            "vepbench_stream": {"request": {"stream": True}, "events": [], "done": False},
        }
        self.details: dict[tuple[Any, Any], dict[str, Any]] = {}

    def event(self, data: str) -> bool:
        if data == "[DONE]":
            self.raw["vepbench_stream"]["done"] = True
            return True
        try:
            chunk = json.loads(data)
        except ValueError as exc:
            self.raw["vepbench_stream"]["invalid_event"] = data
            raise ProviderError("Invalid JSON in response stream", raw_response=self.raw) from exc
        self.raw["vepbench_stream"]["events"].append(chunk)
        if not isinstance(chunk, dict):
            raise ProviderError("Non-object response stream event", raw_response=self.raw)
        for key, value in chunk.items():
            if key not in ("choices", "object"):
                self.raw[key] = value
        self.raw["object"] = "chat.completion"
        if error := _provider_error(chunk):
            # Keep any usage/error metadata and the entire event, including partial text.
            self.raw["error"] = {"message": error.message, "code": error.status_code}
            raise ProviderError(error.message, error.status_code, self.raw)
        choices = chunk.get("choices", [])
        if not isinstance(choices, list):
            raise ProviderError("Invalid stream choices", raw_response=self.raw)
        choice = self.raw["choices"][0]
        message = choice["message"]
        for update in choices:
            if not isinstance(update, dict) or update.get("index", 0) != 0:
                raise ProviderError("Unexpected stream choice", raw_response=self.raw)
            delta = update.get("delta", {})
            if not isinstance(delta, dict):
                raise ProviderError("Invalid stream delta", raw_response=self.raw)
            for field, value in delta.items():
                if value is None:
                    continue
                if field in ("content", "reasoning", "reasoning_content", "refusal"):
                    if not isinstance(value, str):
                        raise ProviderError("Non-text stream delta", raw_response=self.raw)
                    message[field] = message.get(field, "") + value
                elif field == "role":
                    message[field] = value
                elif field == "reasoning_details":
                    if not isinstance(value, list):
                        raise ProviderError("Invalid reasoning details", raw_response=self.raw)
                    for detail in value:
                        if not isinstance(detail, dict):
                            raise ProviderError("Invalid reasoning detail", raw_response=self.raw)
                        key = (detail.get("index", 0), detail.get("type"))
                        assembled = self.details.setdefault(key, {})
                        for name, part in detail.items():
                            if name in ("text", "summary", "data", "signature") and isinstance(
                                part, str
                            ):
                                assembled[name] = assembled.get(name, "") + part
                            elif part is not None:
                                assembled[name] = part
                    message[field] = list(self.details.values())
                elif field in ("tool_calls", "function_call"):
                    raise ProviderError(
                        "Text-only transport received tool calls", raw_response=self.raw
                    )
            for field in ("finish_reason", "native_finish_reason"):
                if update.get(field) is not None:
                    choice[field] = update[field]
        return False

    def read(self, lines: Iterable[bytes]) -> dict[str, Any]:
        data: list[str] = []
        try:
            for encoded in lines:
                line = encoded.decode("utf-8").rstrip("\r\n")
                if line == "":
                    if data and self.event("\n".join(data)):
                        break
                    data = []
                elif line.startswith("data:"):
                    value = line[5:]
                    data.append(value[1:] if value.startswith(" ") else value)
                # SSE comments (OpenRouter keepalives) and event/id fields carry no completion.
        except (UnicodeError, TimeoutError, http.client.HTTPException, OSError) as exc:
            raise ProviderError(
                f"Response stream interrupted: {exc}", raw_response=self.raw
            ) from exc
        if not self.raw["vepbench_stream"]["done"]:
            raise ProviderError("Response stream ended before [DONE]", raw_response=self.raw)
        if self.raw["choices"][0]["finish_reason"] is None:
            raise ProviderError("Response stream omitted finish reason", raw_response=self.raw)
        return self.raw


class OpenRouterStreamingTransport:
    """Assemble text deltas while retaining original events in raw_response.

    Streaming changes the wire format only. OpenRouter sends SSE keepalives to
    avoid idle timeouts: https://openrouter.ai/docs/api_reference/streaming
    No retries are made here, including when a partial stream disconnects.
    """

    def __init__(self, *, endpoint: str = OPENROUTER_ENDPOINT, timeout: int = 900):
        self.endpoint = endpoint
        self.timeout = timeout

    def complete(self, request_body: Mapping[str, Any], api_key: str) -> dict[str, Any]:
        request = urllib.request.Request(
            self.endpoint,
            data=canonical_json({**request_body, "stream": True}).encode("utf-8"),
            headers={
                "Authorization": f"Bearer {api_key}",
                "Content-Type": "application/json",
                "Accept": "text/event-stream",
                "User-Agent": "VEP-bench/0.1",
                "X-OpenRouter-Metadata": "enabled",
            },
            method="POST",
        )
        stream = _Stream()
        try:
            with urllib.request.urlopen(request, timeout=self.timeout) as response:
                stream.raw["vepbench_stream"]["generation_id"] = response.headers.get(
                    "X-Generation-Id"
                )
                if "text/event-stream" not in response.headers.get("Content-Type", ""):
                    body = response.read().decode("utf-8")
                    try:
                        raw = json.loads(body)
                    except ValueError:
                        raw = {"body": body}
                    if isinstance(raw, dict) and (error := _provider_error(raw)):
                        raise error
                    raise ProviderError("Expected SSE response", raw_response={"body": raw})
                return stream.read(response)
        except urllib.error.HTTPError as exc:
            try:
                body = exc.read().decode("utf-8")
                raw = json.loads(body)
            except ValueError, TimeoutError, http.client.HTTPException, OSError:
                raw = None
            raw = raw if isinstance(raw, dict) else None
            error = _provider_error(raw)
            raise ProviderError(
                str(error) if error else f"OpenRouter returned HTTP {exc.code}", exc.code, raw
            ) from exc
        except (urllib.error.URLError, TimeoutError, http.client.HTTPException, OSError) as exc:
            raise ProviderError(
                f"OpenRouter stream failed: {exc}", raw_response=stream.raw
            ) from exc
