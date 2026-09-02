import copy
import http.client
import json
import math
import urllib.request
from datetime import UTC, datetime
from pathlib import Path
from typing import Any

import pytest
from jsonschema import Draft202012Validator, FormatChecker

from vepbench.builder import BuildError, canonical_json, read_jsonl
from vepbench.evaluator import (
    OpenRouterTransport,
    ProviderError,
    classify_result_type,
    evaluate_file,
    score_multiple_choice,
    validate_result,
)

ROOT = Path(__file__).resolve().parents[1]
QUESTIONS = ROOT / "tests/fixtures/synthetic-questions.jsonl"
QUESTION_SCHEMA = ROOT / "schemas/question.schema.json"
RESULT_SCHEMA = ROOT / "schemas/result.schema.json"
FIXED_TIME = datetime(2026, 8, 28, 12, 0, tzinfo=UTC)


class FakeTransport:
    def __init__(self, response: dict[str, Any] | ProviderError):
        self.response = response
        self.requests: list[tuple[dict[str, Any], str]] = []

    def complete(self, request_body: dict[str, Any], api_key: str) -> dict[str, Any]:
        self.requests.append((dict(request_body), api_key))
        if isinstance(self.response, ProviderError):
            raise self.response
        return self.response


def _load_one(path: Path) -> dict[str, Any]:
    rows = read_jsonl(path)
    assert len(rows) == 1
    return rows[0]


def _validate_result(result: dict[str, Any]) -> None:
    schema = json.loads(RESULT_SCHEMA.read_text(encoding="utf-8"))
    Draft202012Validator.check_schema(schema)
    validate_result(
        result,
        Draft202012Validator(schema, format_checker=FormatChecker()),
    )


def test_score_uses_last_well_formed_final_line() -> None:
    score = score_multiple_choice(
        "Possible answer:\nFINAL: A\nCorrection:\nFINAL: B\n",
        {"A", "B", "C"},
        "B",
    )

    assert score.parsed_answer == "B"
    assert score.value == 1
    assert score.correct is True
    assert score.parse_error is None


def test_score_rejects_missing_and_unknown_answers() -> None:
    missing = score_multiple_choice("I choose B.", {"A", "B"}, "B")
    unknown = score_multiple_choice("FINAL: Z", {"A", "B"}, "B")

    assert (missing.value, missing.correct, missing.parsed_answer) == (0, False, None)
    assert missing.parse_error == "missing FINAL: <choice-id> line"
    assert (unknown.value, unknown.correct, unknown.parsed_answer) == (0, False, "Z")
    assert unknown.parse_error == "unknown choice ID 'Z'"


@pytest.mark.parametrize(
    ("correct", "parse_error", "finish_reason", "refusal", "expected"),
    [
        (True, None, "stop", None, "correct"),
        (False, None, "stop", None, "incorrect"),
        (False, "missing final", "content_filter", None, "refusal"),
        (False, "missing final", "stop", "I cannot answer.", "refusal"),
        (False, "missing final", "length", None, "token_limit"),
        (False, "missing final", "stop", None, "format_error"),
        (True, None, "length", None, "correct"),
    ],
)
def test_result_type_classification_is_flat_and_deterministic(
    correct: bool,
    parse_error: str | None,
    finish_reason: str,
    refusal: str | None,
    expected: str,
) -> None:
    assert (
        classify_result_type(
            correct=correct,
            parse_error=parse_error,
            finish_reason=finish_reason,
            refusal=refusal,
        )
        == expected
    )


@pytest.mark.parametrize("content", ["FINAL:\nB", "FINAL: \nB", "FINAL:\r\nB"])
def test_score_does_not_accept_answer_on_the_next_line(content: str) -> None:
    score = score_multiple_choice(content, {"A", "B"}, "B")

    assert score.parsed_answer is None
    assert score.parse_error == "missing FINAL: <choice-id> line"


def test_completed_evaluation_is_valid_and_preserves_response(tmp_path: Path) -> None:
    raw = {
        "id": "generation-test",
        "model": "example/model",
        "provider": "ExampleProvider",
        "choices": [
            {
                "finish_reason": "stop",
                "message": {
                    "role": "assistant",
                    "content": "Synthetic reasoning.\nFINAL: B",
                    "reasoning": "Provider-exposed trace",
                },
            }
        ],
        "usage": {"prompt_tokens": 20, "completion_tokens": 8, "total_tokens": 28},
    }
    transport = FakeTransport(raw)
    output = tmp_path / "run.jsonl"

    summary = evaluate_file(
        questions_path=QUESTIONS,
        question_schema_path=QUESTION_SCHEMA,
        result_schema_path=RESULT_SCHEMA,
        output=output,
        run_id="synthetic-run",
        model_id="example/model",
        api_key="test-secret",
        transport=transport,
        now=lambda: FIXED_TIME,
    )
    result = _load_one(output)
    _validate_result(result)

    assert summary.is_complete
    assert result["scoring"] == {
        "metric": "exact_match",
        "parsed_answer": "B",
        "value": 1,
        "correct": True,
        "parse_error": None,
        "result_type": "correct",
    }
    assert result["response"]["raw"] == raw
    assert result["response"]["reasoning"] == "Provider-exposed trace"
    assert result["model"]["upstream_provider"] == "ExampleProvider"
    assert result["question_set_size"] == 1
    assert result["evaluated_at"] == "2026-08-28T12:00:00Z"
    assert result["generation_parameters"] == {"max_tokens": 4096, "temperature": 0.0}
    assert "test-secret" not in output.read_text(encoding="utf-8")
    assert transport.requests[0][0]["messages"][0]["content"] == result["question"]["prompt"]


def test_direct_evaluation_resumes_a_validated_ordered_prefix(tmp_path: Path) -> None:
    first = read_jsonl(QUESTIONS)[0]
    second = copy.deepcopy(first)
    second["question_id"] = "mc-effect-v1:synthetic-002"
    second["provenance"]["source_record_id"] = "synthetic-002"
    questions = tmp_path / "questions.jsonl"
    questions.write_text(
        "".join(f"{canonical_json(question)}\n" for question in (first, second)),
        encoding="utf-8",
        newline="\n",
    )
    raw = {
        "model": "example/model",
        "provider": "ExampleProvider",
        "choices": [{"finish_reason": "stop", "message": {"content": "FINAL: B"}}],
    }

    class InterruptAfterOne:
        def __init__(self) -> None:
            self.calls = 0

        def complete(self, request_body: dict[str, Any], api_key: str) -> dict[str, Any]:
            self.calls += 1
            if self.calls == 2:
                raise KeyboardInterrupt
            return raw

    output = tmp_path / "interrupted.jsonl"
    with pytest.raises(KeyboardInterrupt):
        evaluate_file(
            questions_path=questions,
            question_schema_path=QUESTION_SCHEMA,
            result_schema_path=RESULT_SCHEMA,
            output=output,
            run_id="resumable-run",
            model_id="example/model",
            api_key="test-secret",
            transport=InterruptAfterOne(),
            now=lambda: FIXED_TIME,
        )
    assert [row["question_id"] for row in read_jsonl(output)] == ["mc-effect-v1:synthetic-001"]

    resumed_transport = FakeTransport(raw)
    progress: list[tuple[int, int, int]] = []
    summary = evaluate_file(
        questions_path=questions,
        question_schema_path=QUESTION_SCHEMA,
        result_schema_path=RESULT_SCHEMA,
        output=output,
        run_id="resumable-run",
        model_id="example/model",
        api_key="test-secret",
        transport=resumed_transport,
        now=lambda: FIXED_TIME,
        resume=True,
        progress=lambda done, total, errors: progress.append((done, total, errors)),
    )

    assert summary.completed == 2
    assert summary.api_errors == 0
    assert len(resumed_transport.requests) == 1
    assert [row["question_id"] for row in read_jsonl(output)] == [
        "mc-effect-v1:synthetic-001",
        "mc-effect-v1:synthetic-002",
    ]
    assert progress == [(1, 2, 0), (2, 2, 0)]


def test_api_error_is_valid_and_marks_run_incomplete(tmp_path: Path) -> None:
    transport = FakeTransport(
        ProviderError(
            "temporary provider failure",
            status_code=503,
            raw_response={"error": {"message": "temporary provider failure"}},
        )
    )
    output = tmp_path / "run.jsonl"

    summary = evaluate_file(
        questions_path=QUESTIONS,
        question_schema_path=QUESTION_SCHEMA,
        result_schema_path=RESULT_SCHEMA,
        output=output,
        run_id="error-run",
        model_id="example/model",
        api_key="test-secret",
        transport=transport,
        now=lambda: FIXED_TIME,
    )
    result = _load_one(output)
    _validate_result(result)

    assert not summary.is_complete
    assert summary.api_errors == 1
    assert result["response"]["status"] == "api_error"
    assert result["scoring"]["value"] is None
    assert result["scoring"]["correct"] is None
    assert result["scoring"]["result_type"] is None
    assert result["error"]["status_code"] == 503


def test_reasoning_summary_is_extracted(tmp_path: Path) -> None:
    raw = {
        "choices": [
            {
                "finish_reason": "stop",
                "message": {
                    "content": "FINAL: A",
                    "reasoning_details": [
                        {
                            "type": "reasoning.summary",
                            "summary": "Summary exposed by the provider",
                        },
                        {
                            "type": "reasoning.text",
                            "text": "Text exposed by the provider",
                        },
                        {"type": "reasoning.encrypted", "data": "opaque"},
                    ],
                },
            }
        ]
    }
    output = tmp_path / "run.jsonl"
    evaluate_file(
        questions_path=QUESTIONS,
        question_schema_path=QUESTION_SCHEMA,
        result_schema_path=RESULT_SCHEMA,
        output=output,
        run_id="summary-run",
        model_id="example/model",
        api_key="test-secret",
        transport=FakeTransport(raw),
        now=lambda: FIXED_TIME,
    )

    assert _load_one(output)["response"]["reasoning"] == (
        "Summary exposed by the provider\n\nText exposed by the provider"
    )


def test_generation_parameters_can_omit_temperature(tmp_path: Path) -> None:
    transport = FakeTransport(
        {"choices": [{"finish_reason": "stop", "message": {"content": "FINAL: B"}}]}
    )
    output = tmp_path / "run.jsonl"
    parameters = {
        "max_tokens": 1024,
        "seed": 20260829,
        "reasoning": {"effort": "medium", "exclude": False},
    }

    evaluate_file(
        questions_path=QUESTIONS,
        question_schema_path=QUESTION_SCHEMA,
        result_schema_path=RESULT_SCHEMA,
        output=output,
        run_id="profile-run",
        model_id="example/model",
        api_key="test-secret",
        generation_parameters=parameters,
        transport=transport,
        now=lambda: FIXED_TIME,
    )

    assert "temperature" not in transport.requests[0][0]
    assert _load_one(output)["generation_parameters"] == parameters


@pytest.mark.parametrize(
    ("metadata", "expected"),
    [
        (
            {
                "endpoints": {
                    "available": [
                        {"provider": "First", "selected": False},
                        {"provider": "SelectedProvider", "selected": True},
                    ]
                },
                "attempts": [{"provider": "SelectedProvider", "status": 200}],
            },
            "SelectedProvider",
        ),
        (
            {
                "attempts": [
                    {"provider": "FailedProvider", "status": 503},
                    {"provider": "SuccessfulAttempt", "status": 200},
                ]
            },
            "SuccessfulAttempt",
        ),
    ],
)
def test_router_metadata_identifies_provider(
    tmp_path: Path, metadata: dict[str, Any], expected: str
) -> None:
    raw = {
        "choices": [{"finish_reason": "stop", "message": {"content": "FINAL: B"}}],
        "openrouter_metadata": metadata,
    }
    output = tmp_path / "run.jsonl"
    evaluate_file(
        questions_path=QUESTIONS,
        question_schema_path=QUESTION_SCHEMA,
        result_schema_path=RESULT_SCHEMA,
        output=output,
        run_id="metadata-run",
        model_id="example/model",
        api_key="test-secret",
        transport=FakeTransport(raw),
        now=lambda: FIXED_TIME,
    )

    assert _load_one(output)["model"]["upstream_provider"] == expected


def test_result_validation_recomputes_score(tmp_path: Path) -> None:
    output = tmp_path / "run.jsonl"
    evaluate_file(
        questions_path=QUESTIONS,
        question_schema_path=QUESTION_SCHEMA,
        result_schema_path=RESULT_SCHEMA,
        output=output,
        run_id="score-validation-run",
        model_id="example/model",
        api_key="test-secret",
        transport=FakeTransport(
            {"choices": [{"finish_reason": "stop", "message": {"content": "FINAL: B"}}]}
        ),
        now=lambda: FIXED_TIME,
    )
    result = _load_one(output)
    result["scoring"]["value"] = 0
    result["scoring"]["correct"] = False

    with pytest.raises(BuildError, match="stored score does not match response"):
        _validate_result(result)


def test_result_validation_recomputes_result_type(tmp_path: Path) -> None:
    output = tmp_path / "run.jsonl"
    evaluate_file(
        questions_path=QUESTIONS,
        question_schema_path=QUESTION_SCHEMA,
        result_schema_path=RESULT_SCHEMA,
        output=output,
        run_id="result-type-validation-run",
        model_id="example/model",
        api_key="test-secret",
        transport=FakeTransport(
            {"choices": [{"finish_reason": "stop", "message": {"content": "FINAL: B"}}]}
        ),
        now=lambda: FIXED_TIME,
    )
    result = _load_one(output)
    result["scoring"]["result_type"] = "incorrect"

    with pytest.raises(BuildError, match="stored result type does not match response"):
        _validate_result(result)


def test_result_validation_rejects_invalid_timestamp(tmp_path: Path) -> None:
    output = tmp_path / "run.jsonl"
    evaluate_file(
        questions_path=QUESTIONS,
        question_schema_path=QUESTION_SCHEMA,
        result_schema_path=RESULT_SCHEMA,
        output=output,
        run_id="timestamp-validation-run",
        model_id="example/model",
        api_key="test-secret",
        transport=FakeTransport(
            {"choices": [{"finish_reason": "stop", "message": {"content": "FINAL: B"}}]}
        ),
        now=lambda: FIXED_TIME,
    )
    result = copy.deepcopy(_load_one(output))
    result["evaluated_at"] = "not-a-date"

    with pytest.raises(BuildError, match="not a 'date-time'"):
        _validate_result(result)


@pytest.mark.parametrize(
    "failure", [TimeoutError("timed out"), http.client.IncompleteRead(b"partial")]
)
def test_transport_normalizes_read_failures(
    monkeypatch: pytest.MonkeyPatch, failure: BaseException
) -> None:
    class BrokenResponse:
        def __enter__(self) -> BrokenResponse:
            return self

        def __exit__(self, *args: object) -> None:
            return None

        def read(self) -> bytes:
            raise failure

    monkeypatch.setattr(urllib.request, "urlopen", lambda *args, **kwargs: BrokenResponse())

    with pytest.raises(ProviderError, match="OpenRouter request failed"):
        OpenRouterTransport().complete({"model": "example/model"}, "test-secret")


@pytest.mark.parametrize(
    ("run_id", "parameters", "message"),
    [
        ("invalid run id", {"max_tokens": 4096}, "invalid run_id"),
        ("valid-run", {"max_tokens": 0}, "max_tokens must be a positive integer"),
        ("valid-run", {"temperature": math.nan}, "temperature must be finite"),
        ("valid-run", {"temperature": math.inf}, "temperature must be finite"),
        ("valid-run", {"model": "other/model"}, "reserved field"),
    ],
)
def test_invalid_run_settings_fail_before_a_provider_call(
    tmp_path: Path,
    run_id: str,
    parameters: dict[str, Any],
    message: str,
) -> None:
    transport = FakeTransport({})

    with pytest.raises(ValueError, match=message):
        evaluate_file(
            questions_path=QUESTIONS,
            question_schema_path=QUESTION_SCHEMA,
            result_schema_path=RESULT_SCHEMA,
            output=tmp_path / "run.jsonl",
            run_id=run_id,
            model_id="example/model",
            api_key="test-secret",
            generation_parameters=parameters,
            transport=transport,
            now=lambda: FIXED_TIME,
        )

    assert transport.requests == []


def test_invalid_concurrency_fails_before_a_provider_call(tmp_path: Path) -> None:
    transport = FakeTransport({})

    with pytest.raises(BuildError, match="concurrency must be positive"):
        evaluate_file(
            questions_path=QUESTIONS,
            question_schema_path=QUESTION_SCHEMA,
            result_schema_path=RESULT_SCHEMA,
            output=tmp_path / "run.jsonl",
            run_id="valid-run",
            model_id="example/model",
            api_key="test-secret",
            concurrency=0,
            transport=transport,
            now=lambda: FIXED_TIME,
        )

    assert transport.requests == []
