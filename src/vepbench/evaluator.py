"""Local OpenRouter evaluation and deterministic multiple-choice scoring."""

from __future__ import annotations

import json
import re
import time
import urllib.error
import urllib.request
from collections.abc import Callable, Mapping
from dataclasses import dataclass
from datetime import UTC, datetime
from pathlib import Path
from typing import Any, Protocol

from jsonschema import Draft202012Validator

from .builder import (
    BuildError,
    canonical_json,
    read_jsonl,
    sha256_file,
    sha256_json,
    validate_question,
)

OPENROUTER_ENDPOINT = "https://openrouter.ai/api/v1/chat/completions"
FINAL_ANSWER = re.compile(r"^FINAL:\s*([A-Za-z0-9_-]+)\s*$", re.MULTILINE)
RECORD_ID = re.compile(r"^[A-Za-z0-9][A-Za-z0-9._:-]*$")


@dataclass
class ProviderError(RuntimeError):
    """A provider request failed or returned an unusable response."""

    message: str
    status_code: int | None = None
    raw_response: dict[str, Any] | None = None

    def __str__(self) -> str:
        return self.message


class CompletionTransport(Protocol):
    def complete(self, request_body: Mapping[str, Any], api_key: str) -> dict[str, Any]: ...


class OpenRouterTransport:
    """Minimal non-streaming OpenRouter Chat Completions transport."""

    def __init__(self, *, endpoint: str = OPENROUTER_ENDPOINT, timeout: int = 900):
        self.endpoint = endpoint
        self.timeout = timeout

    def complete(self, request_body: Mapping[str, Any], api_key: str) -> dict[str, Any]:
        request = urllib.request.Request(
            self.endpoint,
            data=canonical_json(request_body).encode("utf-8"),
            headers={
                "Authorization": f"Bearer {api_key}",
                "Content-Type": "application/json",
                "User-Agent": "VEPBench/0.1",
                "X-OpenRouter-Metadata": "enabled",
            },
            method="POST",
        )
        try:
            with urllib.request.urlopen(request, timeout=self.timeout) as response:
                payload = response.read()
        except urllib.error.HTTPError as exc:
            payload = exc.read()
            raw = _decode_json_object(payload)
            message = _provider_error_message(raw) or f"OpenRouter returned HTTP {exc.code}"
            raise ProviderError(message, status_code=exc.code, raw_response=raw) from exc
        except urllib.error.URLError as exc:
            raise ProviderError(f"OpenRouter request failed: {exc.reason}") from exc

        raw = _decode_json_object(payload)
        if raw is None:
            raise ProviderError("OpenRouter returned a non-object JSON response")
        if error := _provider_error_message(raw):
            raise ProviderError(error, raw_response=raw)
        return raw


@dataclass(frozen=True)
class Score:
    parsed_answer: str | None
    value: int
    correct: bool
    parse_error: str | None


@dataclass(frozen=True)
class EvaluationSummary:
    run_id: str
    output: Path
    completed: int
    api_errors: int

    @property
    def is_complete(self) -> bool:
        return self.api_errors == 0


def score_multiple_choice(
    content: str | None, valid_choice_ids: set[str], answer_choice_id: str
) -> Score:
    matches = FINAL_ANSWER.findall(content or "")
    if not matches:
        return Score(None, 0, False, "missing FINAL: <choice-id> line")
    selected = matches[-1]
    if selected not in valid_choice_ids:
        return Score(selected, 0, False, f"unknown choice ID {selected!r}")
    correct = selected == answer_choice_id
    return Score(selected, int(correct), correct, None)


def evaluate_file(
    *,
    questions_path: str | Path,
    question_schema_path: str | Path,
    result_schema_path: str | Path,
    output: str | Path,
    run_id: str,
    model_id: str,
    api_key: str,
    temperature: float = 0.0,
    max_tokens: int = 4096,
    reasoning_effort: str | None = None,
    transport: CompletionTransport | None = None,
    now: Callable[[], datetime] | None = None,
    monotonic: Callable[[], float] | None = None,
) -> EvaluationSummary:
    if not RECORD_ID.fullmatch(run_id) or len(run_id) > 200:
        raise BuildError(f"invalid run_id {run_id!r}")
    if not model_id:
        raise BuildError("model_id must be non-empty")
    if max_tokens < 1:
        raise BuildError("max_tokens must be positive")

    questions_file = Path(questions_path)
    questions = read_jsonl(questions_file)
    schema = json.loads(Path(question_schema_path).read_text(encoding="utf-8"))
    Draft202012Validator.check_schema(schema)
    question_validator = Draft202012Validator(schema)
    for question in questions:
        validate_question(question, question_validator)
    question_ids = [question["question_id"] for question in questions]
    if question_ids != sorted(question_ids):
        raise BuildError(f"{questions_file}: questions must be sorted by question_id")
    if len(question_ids) != len(set(question_ids)):
        raise BuildError(f"{questions_file}: duplicate question IDs")
    result_schema = json.loads(Path(result_schema_path).read_text(encoding="utf-8"))
    Draft202012Validator.check_schema(result_schema)
    result_validator = Draft202012Validator(result_schema)

    output_path = Path(output)
    output_path.parent.mkdir(parents=True, exist_ok=True)
    if output_path.exists():
        raise BuildError(f"refusing to overwrite existing run file {output_path}")

    client = transport or OpenRouterTransport()
    clock = now or (lambda: datetime.now(UTC))
    timer = monotonic or time.monotonic
    question_set_sha256 = sha256_file(questions_file)
    generation_parameters: dict[str, Any] = {
        "temperature": temperature,
        "max_tokens": max_tokens,
    }
    if reasoning_effort is not None:
        generation_parameters["reasoning"] = {
            "effort": reasoning_effort,
            "exclude": False,
        }

    completed = 0
    api_errors = 0
    with output_path.open("x", encoding="utf-8", newline="\n") as output_file:
        for question in questions:
            request_body: dict[str, Any] = {
                "model": model_id,
                "messages": [{"role": "user", "content": question["prompt"]}],
                **generation_parameters,
            }
            started = timer()
            try:
                raw = client.complete(request_body, api_key)
                latency = timer() - started
                result = _completed_result(
                    raw=raw,
                    question=question,
                    question_set_sha256=question_set_sha256,
                    run_id=run_id,
                    model_id=model_id,
                    generation_parameters=generation_parameters,
                    evaluated_at=clock(),
                    latency_seconds=latency,
                )
                completed += 1
            except ProviderError as exc:
                latency = timer() - started
                result = _error_result(
                    error=exc,
                    question=question,
                    question_set_sha256=question_set_sha256,
                    run_id=run_id,
                    model_id=model_id,
                    generation_parameters=generation_parameters,
                    evaluated_at=clock(),
                    latency_seconds=latency,
                )
                api_errors += 1
            validate_result(result, result_validator)
            output_file.write(f"{canonical_json(result)}\n")
            output_file.flush()

    return EvaluationSummary(run_id, output_path, completed, api_errors)


def validate_result(
    result: Mapping[str, Any], validator: Draft202012Validator
) -> None:
    """Validate a result record, including invariants JSON Schema cannot express."""

    errors = sorted(validator.iter_errors(result), key=lambda error: list(error.path))
    if errors:
        details = "; ".join(
            f"{'.'.join(str(part) for part in error.path) or '<record>'}: {error.message}"
            for error in errors
        )
        raise BuildError(f"{result.get('question_id', '<unknown>')}: {details}")

    question = result["question"]
    choice_ids = [choice["choice_id"] for choice in question["choices"]]
    if len(choice_ids) != len(set(choice_ids)):
        raise BuildError(f"{result['question_id']}: result choice IDs must be unique")
    if choice_ids.count(question["answer_choice_id"]) != 1:
        raise BuildError(
            f"{result['question_id']}: result answer_choice_id must identify exactly one choice"
        )


def _completed_result(
    *,
    raw: dict[str, Any],
    question: Mapping[str, Any],
    question_set_sha256: str,
    run_id: str,
    model_id: str,
    generation_parameters: Mapping[str, Any],
    evaluated_at: datetime,
    latency_seconds: float,
) -> dict[str, Any]:
    choice, message = _first_choice(raw)
    content = message.get("content")
    if content is not None and not isinstance(content, str):
        raise ProviderError("OpenRouter response content is not a string", raw_response=raw)
    score = score_multiple_choice(
        content,
        {choice["choice_id"] for choice in question["choices"]},
        question["answer_choice_id"],
    )
    provider = raw.get("provider")
    if provider is None:
        metadata = raw.get("openrouter_metadata")
        if isinstance(metadata, dict):
            provider = metadata.get("provider")
    if provider is not None and not isinstance(provider, str):
        provider = canonical_json(provider)

    usage = raw.get("usage")
    if not isinstance(usage, dict):
        usage = {}
    return _result_base(
        question=question,
        question_set_sha256=question_set_sha256,
        run_id=run_id,
        model_id=model_id,
        upstream_provider=provider,
        generation_parameters=generation_parameters,
        evaluated_at=evaluated_at,
        response={
            "status": "completed",
            "content": content,
            "reasoning": _extract_reasoning(message),
            "finish_reason": choice.get("finish_reason"),
            "latency_seconds": latency_seconds,
            "raw": raw,
        },
        scoring={
            "metric": "exact_match",
            "parsed_answer": score.parsed_answer,
            "value": score.value,
            "correct": score.correct,
            "parse_error": score.parse_error,
        },
        usage=usage,
        error=None,
    )


def _error_result(
    *,
    error: ProviderError,
    question: Mapping[str, Any],
    question_set_sha256: str,
    run_id: str,
    model_id: str,
    generation_parameters: Mapping[str, Any],
    evaluated_at: datetime,
    latency_seconds: float,
) -> dict[str, Any]:
    return _result_base(
        question=question,
        question_set_sha256=question_set_sha256,
        run_id=run_id,
        model_id=model_id,
        upstream_provider=None,
        generation_parameters=generation_parameters,
        evaluated_at=evaluated_at,
        response={
            "status": "api_error",
            "content": None,
            "reasoning": None,
            "finish_reason": None,
            "latency_seconds": latency_seconds,
            "raw": error.raw_response,
        },
        scoring={
            "metric": "exact_match",
            "parsed_answer": None,
            "value": None,
            "correct": None,
            "parse_error": None,
        },
        usage={},
        error={
            "type": "provider_error",
            "message": str(error),
            "status_code": error.status_code,
        },
    )


def _result_base(
    *,
    question: Mapping[str, Any],
    question_set_sha256: str,
    run_id: str,
    model_id: str,
    upstream_provider: str | None,
    generation_parameters: Mapping[str, Any],
    evaluated_at: datetime,
    response: Mapping[str, Any],
    scoring: Mapping[str, Any],
    usage: Mapping[str, Any],
    error: Mapping[str, Any] | None,
) -> dict[str, Any]:
    return {
        "schema_version": "1.0",
        "run_id": run_id,
        "question_id": question["question_id"],
        "question_sha256": sha256_json(question),
        "question_set_sha256": question_set_sha256,
        "completion_index": 0,
        "evaluated_at": evaluated_at.astimezone(UTC).isoformat().replace("+00:00", "Z"),
        "question": {
            "task_type": question["task_type"],
            "prompt": question["prompt"],
            "choices": question["choices"],
            "answer_choice_id": question["answer_choice_id"],
            "task_family": question["metadata"]["task_family"],
            "tags": question["metadata"].get("tags", []),
        },
        "model": {
            "gateway": "openrouter",
            "model_id": model_id,
            "model_revision": None,
            "upstream_provider": upstream_provider,
        },
        "generation_parameters": dict(generation_parameters),
        "response": dict(response),
        "scoring": dict(scoring),
        "usage": dict(usage),
        "error": None if error is None else dict(error),
    }


def _first_choice(raw: Mapping[str, Any]) -> tuple[dict[str, Any], dict[str, Any]]:
    choices = raw.get("choices")
    if not isinstance(choices, list) or not choices or not isinstance(choices[0], dict):
        raise ProviderError("OpenRouter response has no completion choice", raw_response=dict(raw))
    choice = choices[0]
    message = choice.get("message")
    if not isinstance(message, dict):
        raise ProviderError("OpenRouter response choice has no message", raw_response=dict(raw))
    return choice, message


def _extract_reasoning(message: Mapping[str, Any]) -> str | None:
    for key in ("reasoning", "reasoning_content"):
        value = message.get(key)
        if isinstance(value, str) and value:
            return value
    details = message.get("reasoning_details")
    if not isinstance(details, list):
        return None
    summaries = [
        detail.get("summary")
        for detail in details
        if isinstance(detail, dict)
        and detail.get("type") == "reasoning.summary"
        and isinstance(detail.get("summary"), str)
        and detail.get("summary")
    ]
    return "\n\n".join(summaries) or None


def _decode_json_object(payload: bytes) -> dict[str, Any] | None:
    try:
        value = json.loads(payload.decode("utf-8"))
    except (UnicodeDecodeError, json.JSONDecodeError):
        return None
    return value if isinstance(value, dict) else None


def _provider_error_message(raw: Mapping[str, Any] | None) -> str | None:
    if not raw or "error" not in raw:
        return None
    error = raw["error"]
    if isinstance(error, str):
        return error
    if isinstance(error, dict) and isinstance(error.get("message"), str):
        return error["message"]
    return "OpenRouter returned an API error"
