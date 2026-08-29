import json
from datetime import UTC, datetime
from pathlib import Path
from typing import Any

from vepbench.batch import collect_batch_file, refresh_batch_state, submit_batch_file
from vepbench.builder import read_jsonl

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
