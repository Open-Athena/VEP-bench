from __future__ import annotations

import json
from datetime import UTC, datetime
from pathlib import Path
from typing import Any

import pytest
from jsonschema import Draft202012Validator

from vepbench.builder import read_jsonl
from vepbench.evaluator import ProviderError, evaluate_file, score_multiple_choice

ROOT = Path(__file__).resolve().parents[1]
QUESTIONS = ROOT / "benchmark/questions.jsonl"
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
    Draft202012Validator(schema).validate(result)


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
    }
    assert result["response"]["raw"] == raw
    assert result["response"]["reasoning"] == "Provider-exposed trace"
    assert result["model"]["upstream_provider"] == "ExampleProvider"
    assert result["evaluated_at"] == "2026-08-28T12:00:00Z"
    assert "test-secret" not in output.read_text(encoding="utf-8")
    assert transport.requests[0][0]["messages"][0]["content"] == result["question"]["prompt"]


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

    assert _load_one(output)["response"]["reasoning"] == "Summary exposed by the provider"


@pytest.mark.parametrize(
    ("run_id", "max_tokens", "message"),
    [
        ("invalid run id", 4096, "invalid run_id"),
        ("valid-run", 0, "max_tokens must be positive"),
    ],
)
def test_invalid_run_settings_fail_before_a_provider_call(
    tmp_path: Path, run_id: str, max_tokens: int, message: str
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
            max_tokens=max_tokens,
            transport=transport,
            now=lambda: FIXED_TIME,
        )

    assert transport.requests == []
