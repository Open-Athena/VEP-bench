"""Asynchronous OpenRouter Batch API submission and local state."""

from __future__ import annotations

import http.client
import json
import os
import tempfile
import urllib.error
import urllib.parse
import urllib.request
from collections.abc import Mapping
from dataclasses import dataclass
from datetime import UTC, datetime
from pathlib import Path
from typing import Any, Protocol

from jsonschema import Draft202012Validator, FormatChecker

from .builder import (
    BuildError,
    canonical_json,
    read_jsonl,
    sha256_file,
    validate_question,
)
from .evaluator import (
    EvaluationSummary,
    ProviderError,
    completed_result,
    error_result,
    validate_generation_parameters,
    validate_result,
    validate_run_id,
)

OPENROUTER_BATCHES_ENDPOINT = "https://openrouter.ai/api/beta/batches"


class BatchTransport(Protocol):
    def create(self, request_body: Mapping[str, Any], api_key: str) -> dict[str, Any]: ...

    def retrieve(self, batch_id: str, api_key: str) -> dict[str, Any]: ...


class OpenRouterBatchTransport:
    """Minimal OpenRouter Batch API transport."""

    def __init__(self, *, endpoint: str = OPENROUTER_BATCHES_ENDPOINT, timeout: int = 900):
        self.endpoint = endpoint
        self.timeout = timeout

    def create(self, request_body: Mapping[str, Any], api_key: str) -> dict[str, Any]:
        return self._request(
            self.endpoint,
            method="POST",
            api_key=api_key,
            request_body=request_body,
        )

    def retrieve(self, batch_id: str, api_key: str) -> dict[str, Any]:
        encoded_id = urllib.parse.quote(batch_id, safe="")
        return self._request(f"{self.endpoint}/{encoded_id}", method="GET", api_key=api_key)

    def _request(
        self,
        url: str,
        *,
        method: str,
        api_key: str,
        request_body: Mapping[str, Any] | None = None,
    ) -> dict[str, Any]:
        data = None
        if request_body is not None:
            data = canonical_json(request_body).encode("utf-8")
        request = urllib.request.Request(
            url,
            data=data,
            headers={
                "Authorization": f"Bearer {api_key}",
                "Content-Type": "application/json",
                "User-Agent": "VEPBench/0.1",
                "X-OpenRouter-Metadata": "enabled",
            },
            method=method,
        )
        try:
            with urllib.request.urlopen(request, timeout=self.timeout) as response:
                payload = response.read()
        except urllib.error.HTTPError as exc:
            try:
                payload = exc.read()
            except TimeoutError, http.client.HTTPException, OSError:
                payload = b""
            raw = _decode_json_object(payload)
            message = _batch_error_message(raw) or f"OpenRouter returned HTTP {exc.code}"
            raise ProviderError(message, status_code=exc.code, raw_response=raw) from exc
        except urllib.error.URLError as exc:
            raise ProviderError(f"OpenRouter batch request failed: {exc.reason}") from exc
        except (TimeoutError, http.client.HTTPException, OSError) as exc:
            raise ProviderError(f"OpenRouter batch request failed: {exc}") from exc

        raw = _decode_json_object(payload)
        if raw is None:
            raise ProviderError("OpenRouter returned a non-object batch response")
        if error := _batch_error_message(raw):
            raise ProviderError(error, raw_response=raw)
        return raw


@dataclass(frozen=True)
class BatchSubmissionSummary:
    run_id: str
    batch_id: str
    status: str
    requests: int
    state_path: Path
    result_output: Path


@dataclass(frozen=True)
class BatchStatusSummary:
    batch_id: str
    status: str
    total: int | None
    completed: int | None
    failed: int | None
    state_path: Path


def submit_batch_file(
    *,
    questions_path: str | Path,
    question_schema_path: str | Path,
    state_path: str | Path,
    result_output: str | Path,
    run_id: str,
    model_id: str,
    api_key: str,
    generation_parameters: Mapping[str, Any],
    transport: BatchTransport | None = None,
    now: datetime | None = None,
) -> BatchSubmissionSummary:
    """Validate a question set, submit it as one batch, and persist resumable state."""

    validate_run_id(run_id)
    if not model_id:
        raise BuildError("model_id must be non-empty")
    validate_generation_parameters(generation_parameters)
    resolved_parameters = json.loads(canonical_json(dict(generation_parameters)))

    questions_file = Path(questions_path)
    questions = read_jsonl(questions_file)
    schema = json.loads(Path(question_schema_path).read_text(encoding="utf-8"))
    Draft202012Validator.check_schema(schema)
    validator = Draft202012Validator(schema)
    for question in questions:
        validate_question(question, validator)
    question_ids = [question["question_id"] for question in questions]
    if question_ids != sorted(question_ids):
        raise BuildError(f"{questions_file}: questions must be sorted by question_id")
    if len(question_ids) != len(set(question_ids)):
        raise BuildError(f"{questions_file}: duplicate question IDs")

    state_file = Path(state_path)
    if state_file.exists():
        raise BuildError(f"refusing to overwrite existing batch state {state_file}")
    output_file = Path(result_output)
    if output_file.exists():
        raise BuildError(f"refusing to overwrite existing run file {output_file}")

    requests = [
        {
            "custom_id": question["question_id"],
            "body": {
                "model": model_id,
                "messages": [{"role": "user", "content": question["prompt"]}],
                **resolved_parameters,
            },
        }
        for question in questions
    ]
    request_body = {
        "endpoint": "/v1/chat/completions",
        "model": model_id,
        "requests": requests,
    }
    submitted_at = now or datetime.now(UTC)
    state = {
        "schema_version": "1.0",
        "run_id": run_id,
        "batch_id": None,
        "status": "submitting",
        "submitted_at": submitted_at.astimezone(UTC).isoformat().replace("+00:00", "Z"),
        "endpoint": "/v1/chat/completions",
        "model_id": model_id,
        "generation_parameters": resolved_parameters,
        "question_set_sha256": sha256_file(questions_file),
        "question_set_size": len(questions),
        "question_ids": question_ids,
        "result_output": str(output_file),
        "raw_submission": None,
    }
    state_file.parent.mkdir(parents=True, exist_ok=True)
    _write_new_json(state_file, state)

    try:
        raw = (transport or OpenRouterBatchTransport()).create(request_body, api_key)
    except ProviderError as exc:
        state["status"] = "submission_error"
        state["submission_error"] = {
            "message": str(exc),
            "status_code": exc.status_code,
            "raw_response": exc.raw_response,
        }
        _replace_json(state_file, state)
        raise
    state["raw_submission"] = raw
    batch_id = raw.get("id")
    if not isinstance(batch_id, str) or not batch_id:
        state["status"] = "submission_response_invalid"
        _replace_json(state_file, state)
        raise ProviderError("OpenRouter batch response has no batch ID", raw_response=raw)
    status = raw.get("status")
    if not isinstance(status, str) or not status:
        state["batch_id"] = batch_id
        state["status"] = "submission_response_invalid"
        _replace_json(state_file, state)
        raise ProviderError("OpenRouter batch response has no status", raw_response=raw)

    state["batch_id"] = batch_id
    state["status"] = status
    _replace_json(state_file, state)
    return BatchSubmissionSummary(
        run_id=run_id,
        batch_id=batch_id,
        status=status,
        requests=len(requests),
        state_path=state_file,
        result_output=output_file,
    )


def refresh_batch_state(
    *,
    state_path: str | Path,
    api_key: str,
    transport: BatchTransport | None = None,
) -> BatchStatusSummary:
    """Retrieve a batch and atomically refresh its non-secret local state."""

    state_file = Path(state_path)
    try:
        state = json.loads(state_file.read_text(encoding="utf-8"))
    except json.JSONDecodeError as exc:
        raise BuildError(f"{state_file}: invalid JSON: {exc}") from exc
    if not isinstance(state, dict) or state.get("schema_version") != "1.0":
        raise BuildError(f"{state_file}: unsupported batch state")
    batch_id = state.get("batch_id")
    if not isinstance(batch_id, str) or not batch_id:
        raise BuildError(
            f"{state_file}: batch submission has no batch_id; "
            "inspect the preserved submission state before retrying"
        )

    raw = (transport or OpenRouterBatchTransport()).retrieve(batch_id, api_key)
    if raw.get("id") != batch_id:
        raise ProviderError("OpenRouter returned the wrong batch ID", raw_response=raw)
    status = raw.get("status")
    if not isinstance(status, str) or not status:
        raise ProviderError("OpenRouter batch response has no status", raw_response=raw)
    counts = raw.get("request_counts")
    if not isinstance(counts, Mapping):
        counts = {}

    state["status"] = status
    state["raw_status"] = raw
    _replace_json(state_file, state)
    return BatchStatusSummary(
        batch_id=batch_id,
        status=status,
        total=_optional_int(counts.get("total")),
        completed=_optional_int(counts.get("completed")),
        failed=_optional_int(counts.get("failed")),
        state_path=state_file,
    )


def collect_batch_file(
    *,
    state_path: str | Path,
    questions_path: str | Path,
    question_schema_path: str | Path,
    result_schema_path: str | Path,
    api_key: str,
    transport: BatchTransport | None = None,
    now: datetime | None = None,
) -> EvaluationSummary:
    """Refresh a completed batch and materialize canonical scored result JSONL."""

    status = refresh_batch_state(
        state_path=state_path,
        api_key=api_key,
        transport=transport,
    )
    if status.status != "completed":
        raise BuildError(f"batch {status.batch_id} is {status.status}, not completed")

    state_file = Path(state_path)
    state = json.loads(state_file.read_text(encoding="utf-8"))
    raw_batch = state.get("raw_status")
    if not isinstance(raw_batch, dict) or not isinstance(raw_batch.get("results"), list):
        raise BuildError(f"{state_file}: completed batch has no results")

    questions_file = Path(questions_path)
    questions = read_jsonl(questions_file)
    question_schema = json.loads(Path(question_schema_path).read_text(encoding="utf-8"))
    Draft202012Validator.check_schema(question_schema)
    question_validator = Draft202012Validator(question_schema)
    for question in questions:
        validate_question(question, question_validator)
    question_ids = [question["question_id"] for question in questions]
    if question_ids != sorted(question_ids):
        raise BuildError(f"{questions_file}: questions must be sorted by question_id")
    if question_ids != state.get("question_ids"):
        raise BuildError(f"{state_file}: question IDs do not match {questions_file}")
    if sha256_file(questions_file) != state.get("question_set_sha256"):
        raise BuildError(f"{state_file}: question-set digest does not match {questions_file}")
    if len(questions) != state.get("question_set_size"):
        raise BuildError(f"{state_file}: question-set size does not match {questions_file}")

    result_schema = json.loads(Path(result_schema_path).read_text(encoding="utf-8"))
    Draft202012Validator.check_schema(result_schema)
    result_validator = Draft202012Validator(result_schema, format_checker=FormatChecker())
    batch_items: dict[str, dict[str, Any]] = {}
    for item in raw_batch["results"]:
        if not isinstance(item, dict):
            raise BuildError(f"{state_file}: batch result is not an object")
        custom_id = item.get("custom_id")
        if not isinstance(custom_id, str) or not custom_id:
            raise BuildError(f"{state_file}: batch result has no custom_id")
        if custom_id in batch_items:
            raise BuildError(f"{state_file}: duplicate batch result {custom_id!r}")
        batch_items[custom_id] = item
    if set(batch_items) != set(question_ids):
        raise BuildError(f"{state_file}: batch results do not match submitted question IDs")

    run_id = state.get("run_id")
    model_id = state.get("model_id")
    parameters = state.get("generation_parameters")
    if not isinstance(run_id, str) or not isinstance(model_id, str):
        raise BuildError(f"{state_file}: missing run metadata")
    if not isinstance(parameters, dict):
        raise BuildError(f"{state_file}: missing generation parameters")
    output_path = Path(state["result_output"])
    output_path.parent.mkdir(parents=True, exist_ok=True)
    if output_path.exists():
        raise BuildError(f"refusing to overwrite existing run file {output_path}")

    collected_at = now or datetime.now(UTC)
    completed = 0
    api_errors = 0
    temporary_path: Path | None = None
    try:
        with tempfile.NamedTemporaryFile(
            mode="w",
            encoding="utf-8",
            newline="\n",
            dir=output_path.parent,
            prefix=f".{output_path.name}.",
            suffix=".tmp",
            delete=False,
        ) as output_file:
            temporary_path = Path(output_file.name)
            for question in questions:
                item = batch_items[question["question_id"]]
                response = item.get("response")
                body = response.get("body") if isinstance(response, Mapping) else None
                status_code = response.get("status_code") if isinstance(response, Mapping) else None
                result: dict[str, Any]
                if status_code == 200 and isinstance(body, dict) and item.get("error") is None:
                    try:
                        result = completed_result(
                            raw=body,
                            provider_response=item,
                            question=question,
                            question_set_sha256=state["question_set_sha256"],
                            question_set_size=state["question_set_size"],
                            run_id=run_id,
                            model_id=model_id,
                            generation_parameters=parameters,
                            evaluated_at=_response_time(body, collected_at),
                            latency_seconds=None,
                        )
                    except ProviderError as exc:
                        error = ProviderError(
                            f"Unusable successful batch response: {exc}",
                            status_code=200,
                            raw_response=item,
                        )
                        result = error_result(
                            error=error,
                            question=question,
                            question_set_sha256=state["question_set_sha256"],
                            question_set_size=state["question_set_size"],
                            run_id=run_id,
                            model_id=model_id,
                            generation_parameters=parameters,
                            evaluated_at=collected_at,
                            latency_seconds=None,
                        )
                        api_errors += 1
                    else:
                        completed += 1
                else:
                    error = ProviderError(
                        _batch_item_error(item),
                        status_code=status_code if isinstance(status_code, int) else None,
                        raw_response=item,
                    )
                    result = error_result(
                        error=error,
                        question=question,
                        question_set_sha256=state["question_set_sha256"],
                        question_set_size=state["question_set_size"],
                        run_id=run_id,
                        model_id=model_id,
                        generation_parameters=parameters,
                        evaluated_at=collected_at,
                        latency_seconds=None,
                    )
                    api_errors += 1
                validate_result(result, result_validator)
                output_file.write(f"{canonical_json(result)}\n")
            output_file.flush()
            os.fsync(output_file.fileno())
        try:
            os.link(temporary_path, output_path)
        except FileExistsError as exc:
            raise BuildError(f"refusing to overwrite existing run file {output_path}") from exc
        _fsync_directory(output_path.parent)
    finally:
        if temporary_path is not None:
            temporary_path.unlink(missing_ok=True)
    return EvaluationSummary(run_id, output_path, completed, api_errors)


def _write_new_json(path: Path, value: Mapping[str, Any]) -> None:
    with path.open("x", encoding="utf-8", newline="\n") as output:
        output.write(f"{canonical_json(value)}\n")
        output.flush()
        os.fsync(output.fileno())
    _fsync_directory(path.parent)


def _replace_json(path: Path, value: Mapping[str, Any]) -> None:
    temporary_path: Path | None = None
    try:
        with tempfile.NamedTemporaryFile(
            mode="w",
            encoding="utf-8",
            newline="\n",
            dir=path.parent,
            prefix=f".{path.name}.",
            suffix=".tmp",
            delete=False,
        ) as output:
            temporary_path = Path(output.name)
            output.write(f"{canonical_json(value)}\n")
            output.flush()
            os.fsync(output.fileno())
        temporary_path.replace(path)
        _fsync_directory(path.parent)
    finally:
        if temporary_path is not None:
            temporary_path.unlink(missing_ok=True)


def _fsync_directory(path: Path) -> None:
    descriptor = os.open(path, os.O_RDONLY)
    try:
        os.fsync(descriptor)
    finally:
        os.close(descriptor)


def _decode_json_object(payload: bytes) -> dict[str, Any] | None:
    try:
        decoded = json.loads(payload)
    except UnicodeDecodeError, json.JSONDecodeError:
        return None
    return decoded if isinstance(decoded, dict) else None


def _batch_error_message(raw: Mapping[str, Any] | None) -> str | None:
    if raw is None or "error" not in raw or raw["error"] in (None, {}):
        return None
    error = raw["error"]
    if isinstance(error, str):
        return error
    if isinstance(error, Mapping):
        message = error.get("message")
        if isinstance(message, str):
            return message
    return "OpenRouter returned a batch error"


def _optional_int(value: Any) -> int | None:
    return value if isinstance(value, int) and not isinstance(value, bool) else None


def _response_time(body: Mapping[str, Any], fallback: datetime) -> datetime:
    created = body.get("created")
    if isinstance(created, int) and not isinstance(created, bool):
        try:
            return datetime.fromtimestamp(created, UTC)
        except OverflowError, OSError, ValueError:
            pass
    return fallback


def _batch_item_error(item: Mapping[str, Any]) -> str:
    error = item.get("error")
    if isinstance(error, str) and error:
        return error
    if isinstance(error, Mapping):
        message = error.get("message")
        if isinstance(message, str) and message:
            return message
    response = item.get("response")
    if isinstance(response, Mapping):
        status_code = response.get("status_code")
        return f"OpenRouter batch item returned HTTP {status_code}"
    return "OpenRouter batch item failed"
