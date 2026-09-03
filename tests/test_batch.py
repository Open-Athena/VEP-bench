import json
import math
from copy import deepcopy
from datetime import UTC, datetime
from pathlib import Path
from typing import Any

import pytest

from vepbench.artifacts import canonical_json, read_jsonl
from vepbench.errors import BuildError
from vepbench.evaluation.batch import (
    _allocate_batch_usage,
    collect_batch_file,
    merge_batch_result_files,
    refresh_batch_state,
    submit_batch_file,
)
from vepbench.evaluation.core import validate_batch_usage_allocations

ROOT = Path(__file__).resolve().parents[1]
QUESTIONS = ROOT / "tests/fixtures/synthetic-questions.jsonl"
QUESTION_SCHEMA = ROOT / "src/vepbench/schemas/question.schema.json"


class FakeBatchTransport:
    def __init__(self, batch_id: str = "batch-test") -> None:
        self.requests: list[tuple[dict[str, Any], str]] = []
        self.batch_id = batch_id

    def create(self, request_body: dict[str, Any], api_key: str) -> dict[str, Any]:
        self.requests.append((request_body, api_key))
        return {"id": self.batch_id, "status": "validating", "request_counts": {"total": 1}}

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
            "custom_id": "request_000000",
            "id": "request_000000",
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


class RequestedCompletedBatchTransport(FakeBatchTransport):
    def retrieve(self, batch_id: str, api_key: str) -> dict[str, Any]:
        custom_ids = [request["custom_id"] for request in self.requests[0][0]["requests"]]
        items = []
        for custom_id in custom_ids:
            item = deepcopy(CompletedBatchTransport().retrieve(batch_id, api_key)["results"][0])
            item["custom_id"] = custom_id
            item["id"] = custom_id
            items.append(item)
        return {
            "id": batch_id,
            "status": "completed",
            "request_counts": {
                "total": len(items),
                "completed": len(items),
                "failed": 0,
            },
            "results": items,
            "usage": {"cost": 0.001 * len(items)},
        }


class LegacyCompletedBatchTransport(CompletedBatchTransport):
    def retrieve(self, batch_id: str, api_key: str) -> dict[str, Any]:
        response = super().retrieve(batch_id, api_key)
        question_id = "mc-effect-v1:synthetic-001"
        response["results"][0]["custom_id"] = question_id
        response["results"][0]["id"] = question_id
        return response


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
            "usage": {"prompt_tokens": 60, "completion_tokens": 24, "cost": 0.003},
        }


class FailedCompletedBatchTransport(FakeBatchTransport):
    def retrieve(self, batch_id: str, api_key: str) -> dict[str, Any]:
        return {
            "id": batch_id,
            "status": "completed",
            "request_counts": {"total": 1, "completed": 0, "failed": 1},
            "results": [
                {
                    "custom_id": "request_000000",
                    "id": "request_000000",
                    "error": {"message": "provider failed"},
                    "response": {"status_code": 500, "body": None},
                }
            ],
            "usage": {"cost": 0.002},
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
    assert request["requests"][0]["custom_id"] == "request_000000"
    assert request["requests"][0]["body"]["seed"] == 20260829
    assert "temperature" not in request["requests"][0]["body"]
    assert summary.batch_id == "batch-test"
    assert summary.requests == 1
    assert not result_output.exists()

    state = json.loads(state_path.read_text(encoding="utf-8"))
    assert state["batch_id"] == "batch-test"
    assert state["question_set_size"] == 1
    assert state["submitted_custom_ids"] == ["request_000000"]
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


def test_submit_and_collect_batch_chunk_retains_full_question_set_identity(
    tmp_path: Path,
) -> None:
    questions = _three_question_file(tmp_path)
    transport = RequestedCompletedBatchTransport()
    state_path = tmp_path / "state.json"
    result_output = tmp_path / "chunk.jsonl"

    summary = submit_batch_file(
        questions_path=questions,
        question_schema_path=QUESTION_SCHEMA,
        state_path=state_path,
        result_output=result_output,
        run_id="batch-run",
        model_id="example/model",
        api_key="test-secret",
        generation_parameters={"max_tokens": 128},
        batch_offset=1,
        batch_size=1,
        transport=transport,
    )

    assert summary.requests == 1
    state = json.loads(state_path.read_text(encoding="utf-8"))
    assert state["question_set_size"] == 3
    assert state["submitted_question_ids"] == ["mc-effect-v1:synthetic-002"]
    assert state["submitted_custom_ids"] == ["request_000001"]
    assert [request["custom_id"] for request in transport.requests[0][0]["requests"]] == [
        "request_000001"
    ]

    collected = collect_batch_file(
        state_path=state_path,
        questions_path=questions,
        question_schema_path=QUESTION_SCHEMA,
        result_schema_path=ROOT / "src/vepbench/schemas/result.schema.json",
        api_key="test-secret",
        transport=transport,
    )

    assert collected.is_complete
    result = read_jsonl(result_output)[0]
    assert result["question_id"] == "mc-effect-v1:synthetic-002"
    assert result["question_set_size"] == 3


def test_collect_rejects_reordered_persisted_custom_ids(tmp_path: Path) -> None:
    questions = _three_question_file(tmp_path)
    transport = RequestedCompletedBatchTransport()
    state_path = tmp_path / "state.json"
    submit_batch_file(
        questions_path=questions,
        question_schema_path=QUESTION_SCHEMA,
        state_path=state_path,
        result_output=tmp_path / "result.jsonl",
        run_id="batch-run",
        model_id="example/model",
        api_key="test-secret",
        generation_parameters={"max_tokens": 128},
        batch_size=2,
        transport=transport,
    )
    refresh_batch_state(state_path=state_path, api_key="test-secret", transport=transport)
    state = json.loads(state_path.read_text(encoding="utf-8"))
    state["submitted_custom_ids"].reverse()
    state_path.write_text(f"{canonical_json(state)}\n", encoding="utf-8", newline="\n")

    with pytest.raises(BuildError, match="custom IDs do not match their question IDs"):
        collect_batch_file(
            state_path=state_path,
            questions_path=questions,
            question_schema_path=QUESTION_SCHEMA,
            result_schema_path=ROOT / "src/vepbench/schemas/result.schema.json",
            api_key="test-secret",
            transport=transport,
        )


def test_collect_retains_legacy_question_id_custom_id_fallback(tmp_path: Path) -> None:
    transport = LegacyCompletedBatchTransport()
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
    )
    refresh_batch_state(state_path=state_path, api_key="test-secret", transport=transport)
    state = json.loads(state_path.read_text(encoding="utf-8"))
    del state["submitted_custom_ids"]
    question_id = state["submitted_question_ids"][0]
    state_path.write_text(f"{canonical_json(state)}\n", encoding="utf-8", newline="\n")

    summary = collect_batch_file(
        state_path=state_path,
        questions_path=QUESTIONS,
        question_schema_path=QUESTION_SCHEMA,
        result_schema_path=ROOT / "src/vepbench/schemas/result.schema.json",
        api_key="test-secret",
        transport=transport,
    )

    assert summary.is_complete
    assert read_jsonl(result_output)[0]["question_id"] == question_id


def test_merge_batch_chunks_writes_one_ordered_full_run(tmp_path: Path) -> None:
    questions = _three_question_file(tmp_path)
    chunk_outputs = []
    for index, (offset, size) in enumerate(((0, 2), (2, 1)), start=1):
        transport = RequestedCompletedBatchTransport(batch_id=f"batch-test-{index}")
        state_path = tmp_path / f"state-{index}.json"
        chunk_output = tmp_path / f"chunk-{index}.jsonl"
        submit_batch_file(
            questions_path=questions,
            question_schema_path=QUESTION_SCHEMA,
            state_path=state_path,
            result_output=chunk_output,
            run_id="batch-run",
            model_id="example/model",
            api_key="test-secret",
            generation_parameters={"max_tokens": 128},
            batch_offset=offset,
            batch_size=size,
            transport=transport,
        )
        collect_batch_file(
            state_path=state_path,
            questions_path=questions,
            question_schema_path=QUESTION_SCHEMA,
            result_schema_path=ROOT / "src/vepbench/schemas/result.schema.json",
            api_key="test-secret",
            transport=transport,
        )
        chunk_outputs.append(chunk_output)

    merged_output = tmp_path / "merged.jsonl"
    summary = merge_batch_result_files(
        result_paths=chunk_outputs,
        questions_path=questions,
        question_schema_path=QUESTION_SCHEMA,
        result_schema_path=ROOT / "src/vepbench/schemas/result.schema.json",
        output=merged_output,
    )

    assert summary.is_complete
    records = read_jsonl(merged_output)
    assert [record["question_id"] for record in records] == [
        "mc-effect-v1:synthetic-001",
        "mc-effect-v1:synthetic-002",
        "mc-effect-v1:synthetic-003",
    ]
    assert {record["question_set_size"] for record in records} == {3}
    assert sum(record["usage"]["cost"] for record in records) == pytest.approx(0.003)

    first_chunk = read_jsonl(chunk_outputs[0])
    cost_tampered = deepcopy(first_chunk)
    cost_tampered[0]["usage"]["cost"] += 0.0001
    cost_tampered_path = tmp_path / "cost-tampered.jsonl"
    cost_tampered_path.write_text(
        "".join(f"{canonical_json(record)}\n" for record in cost_tampered),
        encoding="utf-8",
        newline="\n",
    )
    with pytest.raises(BuildError, match="allocations do not sum to its receipt"):
        merge_batch_result_files(
            result_paths=[cost_tampered_path, chunk_outputs[1]],
            questions_path=questions,
            question_schema_path=QUESTION_SCHEMA,
            result_schema_path=ROOT / "src/vepbench/schemas/result.schema.json",
            output=tmp_path / "cost-tampered-merged.jsonl",
        )

    provenance_tampered = deepcopy(first_chunk)
    provenance_tampered[0]["usage"]["vepbench"]["batch_usage"]["cost"] += 0.0001
    provenance_tampered_path = tmp_path / "provenance-tampered.jsonl"
    provenance_tampered_path.write_text(
        "".join(f"{canonical_json(record)}\n" for record in provenance_tampered),
        encoding="utf-8",
        newline="\n",
    )
    with pytest.raises(BuildError, match="inconsistent allocation provenance"):
        merge_batch_result_files(
            result_paths=[provenance_tampered_path, chunk_outputs[1]],
            questions_path=questions,
            question_schema_path=QUESTION_SCHEMA,
            result_schema_path=ROOT / "src/vepbench/schemas/result.schema.json",
            output=tmp_path / "provenance-tampered-merged.jsonl",
        )


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
        result_schema_path=ROOT / "src/vepbench/schemas/result.schema.json",
        api_key="test-secret",
        transport=transport,
        now=datetime(2026, 8, 29, 13, 0, tzinfo=UTC),
    )

    assert summary.is_complete
    result = read_jsonl(result_output)[0]
    assert result["scoring"]["correct"] is True
    assert result["response"]["latency_seconds"] is None
    assert result["response"]["raw"]["custom_id"] == "request_000000"
    assert result["response"]["raw"]["response"]["body"]["id"] == "generation-test"
    assert result["usage"]["prompt_tokens"] == 20
    assert result["usage"]["completion_tokens"] == 8
    assert result["usage"]["cost"] == 0.001
    assert result["usage"]["vepbench"] == {
        "batch_id": "batch-test",
        "batch_question_ids": ["mc-effect-v1:synthetic-001"],
        "batch_usage": {"prompt_tokens": 20, "completion_tokens": 8, "cost": 0.001},
        "cost_allocation": "proportional_total_tokens",
        "cost_source": "allocated_batch_total",
    }


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
        result_schema_path=ROOT / "src/vepbench/schemas/result.schema.json",
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
    assert records[1]["response"]["raw"]["custom_id"] == "request_000001"
    assert "finish_reason is not a string" in records[2]["error"]["message"]
    assert records[2]["response"]["raw"]["custom_id"] == "request_000002"
    assert sum(record["usage"]["cost"] for record in records) == pytest.approx(0.003)
    assert all(record["usage"]["vepbench"]["batch_usage"]["cost"] == 0.003 for record in records)


def test_collect_batch_records_cost_when_every_request_failed(tmp_path: Path) -> None:
    transport = FailedCompletedBatchTransport()
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
    )

    summary = collect_batch_file(
        state_path=state_path,
        questions_path=QUESTIONS,
        question_schema_path=QUESTION_SCHEMA,
        result_schema_path=ROOT / "src/vepbench/schemas/result.schema.json",
        api_key="test-secret",
        transport=transport,
    )

    assert (summary.completed, summary.api_errors) == (0, 1)
    result = read_jsonl(result_output)[0]
    assert result["response"]["status"] == "api_error"
    assert result["usage"]["cost"] == 0.002
    assert result["usage"]["vepbench"]["cost_allocation"] == "equal"


def test_batch_allocation_accepts_one_ulp_rounding_difference() -> None:
    question_ids = ["question-a", "question-b"]
    items = {
        question_id: {"response": {"body": {"usage": {"total_tokens": total_tokens}}}}
        for question_id, total_tokens in zip(question_ids, (5612, 6426), strict=True)
    }
    receipt_cost = 0.984714
    allocations = _allocate_batch_usage(
        {"usage": {"cost": receipt_cost}},
        "batch-rounding",
        question_ids,
        items,
    )
    records = [
        {
            "run_id": "rounding-run",
            "question_id": question_id,
            "usage": allocation,
        }
        for question_id, allocation in allocations.items()
    ]

    assert math.fsum(record["usage"]["cost"] for record in records) != receipt_cost
    validate_batch_usage_allocations(records, context="rounding regression")
