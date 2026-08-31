import json
from copy import deepcopy
from datetime import UTC, datetime
from pathlib import Path
from typing import Any

import pytest

from vepbench.batch import collect_batch_file, refresh_batch_state, submit_batch_file
from vepbench.builder import BuildError, canonical_json, read_jsonl

ROOT = Path(__file__).resolve().parents[1]
QUESTIONS = ROOT / "tests/fixtures/synthetic-questions.jsonl"
QUESTION_SCHEMA = ROOT / "schemas/question.schema.json"


class FakeBatchTransport:
    def __init__(self) -> None:
        self.requests: list[tuple[dict[str, Any], str]] = []

    def create(self, request_body: dict[str, Any], api_key: str) -> dict[str, Any]:
        self.requests.append((request_body, api_key))
        return {"id": "batch-test", "status": "validating", "request_counts": {"total": 1}}

    def retrieve(self, batch_id: str, api_key: str) -> dict[str, Any]:
        self.requests.append(({"batch_id": batch_id}, api_key))
        return {
            "id": batch_id,
            "status": "in_progress",
            "request_counts": {"total": 1, "completed": 0, "failed": 0},
            "results": None,
        }


class CompletedBatchTransport(FakeBatchTransport):
    def retrieve(self, batch_id: str, api_key: str) -> dict[str, Any]:
        item = {
            "custom_id": "mc-effect-v1:synthetic-001",
            "id": "mc-effect-v1:synthetic-001",
            "error": None,
            "response": {
                "status_code": 200,
                "request_id": None,
                "body": {
                    "id": "generation-test",
                    "model": "example/model",
                    "provider": "ExampleProvider",
                    "created": 1788000000,
                    "choices": [
                        {
                            "finish_reason": "stop",
                            "message": {"content": "FINAL: B", "reasoning": "Reasoning"},
                        }
                    ],
                    "usage": {"prompt_tokens": 20, "completion_tokens": 8},
                },
            },
        }
        return {
            "id": batch_id,
            "status": "completed",
            "request_counts": {"total": 1, "completed": 1, "failed": 0},
            "results": [item],
            "usage": {"prompt_tokens": 20, "completion_tokens": 8, "cost": 0.001},
        }


class StateObservingTransport(FakeBatchTransport):
    def __init__(self, state_path: Path) -> None:
        super().__init__()
        self.state_path = state_path

    def create(self, request_body: dict[str, Any], api_key: str) -> dict[str, Any]:
        state = json.loads(self.state_path.read_text(encoding="utf-8"))
        assert state["status"] == "submitting"
        assert state["batch_id"] is None
        return super().create(request_body, api_key)


class MixedCompletedBatchTransport(FakeBatchTransport):
    def retrieve(self, batch_id: str, api_key: str) -> dict[str, Any]:
        custom_ids = [request["custom_id"] for request in self.requests[0][0]["requests"]]
        valid = CompletedBatchTransport().retrieve(batch_id, api_key)["results"][0]
        valid["custom_id"] = custom_ids[0]
        valid["id"] = custom_ids[0]
        malformed = deepcopy(valid)
        malformed["custom_id"] = custom_ids[1]
        malformed["id"] = custom_ids[1]
        malformed["response"]["body"]["choices"] = []
        invalid_field = deepcopy(valid)
        invalid_field["custom_id"] = custom_ids[2]
        invalid_field["id"] = custom_ids[2]
        invalid_field["response"]["body"]["choices"][0]["finish_reason"] = 123
        return {
            "id": batch_id,
            "status": "completed",
            "request_counts": {"total": 3, "completed": 3, "failed": 0},
            "results": [valid, malformed, invalid_field],
        }


def _three_question_file(tmp_path: Path) -> Path:
    first = read_jsonl(QUESTIONS)[0]
    second = deepcopy(first)
    second["question_id"] = "mc-effect-v1:synthetic-002"
    second["provenance"]["source_record_id"] = "synthetic-002"
    third = deepcopy(first)
    third["question_id"] = "mc-effect-v1:synthetic-003"
    third["provenance"]["source_record_id"] = "synthetic-003"
    output = tmp_path / "three-questions.jsonl"
    output.write_text(
        "".join(f"{canonical_json(question)}\n" for question in [first, second, third]),
        encoding="utf-8",
        newline="\n",
    )
    return output


def test_submit_batch_persists_non_secret_resumable_state(tmp_path: Path) -> None:
    transport = FakeBatchTransport()
    state_path = tmp_path / "state.json"
    result_output = tmp_path / "result.jsonl"
    parameters = {
        "max_tokens": 16384,
        "reasoning": {"effort": "medium", "exclude": False},
        "seed": 20260829,
    }

    summary = submit_batch_file(
        questions_path=QUESTIONS,
        question_schema_path=QUESTION_SCHEMA,
        state_path=state_path,
        result_output=result_output,
        run_id="batch-run",
        model_id="example/model",
        api_key="test-secret",
        generation_parameters=parameters,
        transport=transport,
        now=datetime(2026, 8, 29, 12, 0, tzinfo=UTC),
    )

    request = transport.requests[0][0]
    assert request["endpoint"] == "/v1/chat/completions"
    assert request["model"] == "example/model"
    assert request["requests"][0]["custom_id"] == "mc-effect-v1:synthetic-001"
    assert request["requests"][0]["body"]["seed"] == 20260829
    assert "temperature" not in request["requests"][0]["body"]
    assert summary.batch_id == "batch-test"
    assert summary.requests == 1
    assert not result_output.exists()

    state = json.loads(state_path.read_text(encoding="utf-8"))
    assert state["batch_id"] == "batch-test"
    assert state["question_set_size"] == 1
    assert state["result_output"] == str(result_output)
    assert state["generation_parameters"] == parameters
    assert "test-secret" not in state_path.read_text(encoding="utf-8")

    stale_temporary = state_path.with_suffix(f"{state_path.suffix}.tmp")
    stale_temporary.write_text("stale interrupted update\n", encoding="utf-8")
    status = refresh_batch_state(
        state_path=state_path,
        api_key="test-secret",
        transport=transport,
    )
    assert status.status == "in_progress"
    assert (status.total, status.completed, status.failed) == (1, 0, 0)
    refreshed = json.loads(state_path.read_text(encoding="utf-8"))
    assert refreshed["raw_status"]["request_counts"]["total"] == 1
    assert "test-secret" not in state_path.read_text(encoding="utf-8")
    assert stale_temporary.read_text(encoding="utf-8") == "stale interrupted update\n"


def test_submit_batch_persists_pending_state_before_provider_call(tmp_path: Path) -> None:
    state_path = tmp_path / "state.json"
    transport = StateObservingTransport(state_path)

    submit_batch_file(
        questions_path=QUESTIONS,
        question_schema_path=QUESTION_SCHEMA,
        state_path=state_path,
        result_output=tmp_path / "result.jsonl",
        run_id="batch-run",
        model_id="example/model",
        api_key="test-secret",
        generation_parameters={"max_tokens": 128},
        transport=transport,
    )

    assert json.loads(state_path.read_text(encoding="utf-8"))["batch_id"] == "batch-test"


def test_submit_batch_rejects_invalid_run_id_before_provider_call(tmp_path: Path) -> None:
    transport = FakeBatchTransport()

    with pytest.raises(BuildError, match="invalid run_id"):
        submit_batch_file(
            questions_path=QUESTIONS,
            question_schema_path=QUESTION_SCHEMA,
            state_path=tmp_path / "state.json",
            result_output=tmp_path / "result.jsonl",
            run_id="invalid run id",
            model_id="example/model",
            api_key="test-secret",
            generation_parameters={"max_tokens": 128},
            transport=transport,
        )

    assert transport.requests == []


def test_collect_batch_writes_sorted_schema_valid_results(tmp_path: Path) -> None:
    transport = CompletedBatchTransport()
    state_path = tmp_path / "state.json"
    result_output = tmp_path / "result.jsonl"
    submit_batch_file(
        questions_path=QUESTIONS,
        question_schema_path=QUESTION_SCHEMA,
        state_path=state_path,
        result_output=result_output,
        run_id="batch-run",
        model_id="example/model",
        api_key="test-secret",
        generation_parameters={"max_tokens": 128},
        transport=transport,
        now=datetime(2026, 8, 29, 12, 0, tzinfo=UTC),
    )

    summary = collect_batch_file(
        state_path=state_path,
        questions_path=QUESTIONS,
        question_schema_path=QUESTION_SCHEMA,
        result_schema_path=ROOT / "schemas/result.schema.json",
        api_key="test-secret",
        transport=transport,
        now=datetime(2026, 8, 29, 13, 0, tzinfo=UTC),
    )

    assert summary.is_complete
    result = read_jsonl(result_output)[0]
    assert result["scoring"]["correct"] is True
    assert result["response"]["latency_seconds"] is None
    assert result["response"]["raw"]["custom_id"] == "mc-effect-v1:synthetic-001"
    assert result["response"]["raw"]["response"]["body"]["id"] == "generation-test"
    assert result["usage"] == {"prompt_tokens": 20, "completion_tokens": 8}


def test_collect_batch_records_malformed_success_as_api_error(tmp_path: Path) -> None:
    questions = _three_question_file(tmp_path)
    transport = MixedCompletedBatchTransport()
    state_path = tmp_path / "state.json"
    result_output = tmp_path / "result.jsonl"
    submit_batch_file(
        questions_path=questions,
        question_schema_path=QUESTION_SCHEMA,
        state_path=state_path,
        result_output=result_output,
        run_id="batch-run",
        model_id="example/model",
        api_key="test-secret",
        generation_parameters={"max_tokens": 128},
        transport=transport,
    )

    summary = collect_batch_file(
        state_path=state_path,
        questions_path=questions,
        question_schema_path=QUESTION_SCHEMA,
        result_schema_path=ROOT / "schemas/result.schema.json",
        api_key="test-secret",
        transport=transport,
        now=datetime(2026, 8, 29, 13, 0, tzinfo=UTC),
    )

    assert (summary.completed, summary.api_errors) == (1, 2)
    records = read_jsonl(result_output)
    assert [record["response"]["status"] for record in records] == [
        "completed",
        "api_error",
        "api_error",
    ]
    assert records[1]["error"]["status_code"] == 200
    assert "no completion choice" in records[1]["error"]["message"]
    assert records[1]["response"]["raw"]["custom_id"] == records[1]["question_id"]
    assert "finish_reason is not a string" in records[2]["error"]["message"]
    assert records[2]["response"]["raw"]["custom_id"] == records[2]["question_id"]
