import json
from pathlib import Path
from typing import Any

import pytest

from vepbench.cli import main
from vepbench.questions.fetch import FetchedQuestions

ROOT = Path(__file__).resolve().parents[1]


def test_help_promotes_evaluation_and_question_fetch(
    capsys: pytest.CaptureFixture[str],
) -> None:
    assert main(["--help"]) == 0

    output = capsys.readouterr().out
    assert "Evaluate language models" in output
    assert "evaluate" in output
    assert "questions" in output


def test_fetch_command_reports_verified_cache(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
    capsys: pytest.CaptureFixture[str],
) -> None:
    cached = tmp_path / "main-abc.jsonl"
    cached.write_text("{}\n", encoding="utf-8")

    def fake_fetch(**kwargs: object) -> FetchedQuestions:
        assert kwargs == {"version": "main", "output": None, "cache_dir": tmp_path}
        return FetchedQuestions(cached, "main", 1, "a" * 64, True)

    monkeypatch.setattr("vepbench.cli.fetch_questions", fake_fetch)

    assert main(["questions", "fetch", "--cache-dir", str(tmp_path)]) == 0
    assert f"using cached 1 question(s) at {cached}" in capsys.readouterr().out


def test_build_command_writes_questions_and_manifest(
    tmp_path: Path, capsys: pytest.CaptureFixture[str]
) -> None:
    output = tmp_path / "questions.jsonl"
    task = tmp_path / "task.yaml"
    task.write_text(
        f"""\
schema_version: "1.0"
task_family: synthetic_effect
question:
  type: multiple_choice
  source: {ROOT / "tests/fixtures/synthetic-source.jsonl"}
  prompt: {ROOT / "tests/fixtures/synthetic-template.json"}
evaluation:
  generation:
    max_tokens: 4096
""",
        encoding="utf-8",
    )

    status = main(
        [
            "questions",
            "build",
            "--task",
            str(task),
            "--schema",
            str(ROOT / "src/vepbench/schemas/question.schema.json"),
            "--output",
            str(output),
        ]
    )

    assert status == 0
    assert "wrote 1 question(s)" in capsys.readouterr().out
    manifest = json.loads((tmp_path / "questions.manifest.json").read_text(encoding="utf-8"))
    assert manifest["records"] == 1
    assert manifest["path"] == "questions.jsonl"


def test_cli_reports_argument_errors_without_system_exit(
    capsys: pytest.CaptureFixture[str],
) -> None:
    status = main(["evaluate"])

    assert status == 2
    assert "--model" in capsys.readouterr().err


def test_direct_evaluation_command_runs_with_an_offline_transport(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    raw = {
        "id": "generation-test",
        "model": "example/model",
        "provider": "ExampleProvider",
        "choices": [{"finish_reason": "stop", "message": {"content": "FINAL: B"}}],
        "usage": {"prompt_tokens": 20, "completion_tokens": 2, "total_tokens": 22},
    }

    class OfflineTransport:
        def complete(self, request_body: dict[str, Any], api_key: str) -> dict[str, Any]:
            assert request_body["model"] == "example/model"
            assert api_key == "offline-key"
            return raw

    monkeypatch.setattr("vepbench.evaluation.core.OpenRouterTransport", lambda: OfflineTransport())
    monkeypatch.setenv("OPENROUTER_API_KEY", "offline-key")
    output = tmp_path / "results.jsonl"

    status = main(
        [
            "evaluate",
            "--direct",
            "--model",
            "example/model",
            "--questions",
            str(ROOT / "tests/fixtures/synthetic-questions.jsonl"),
            "--run-id",
            "offline-run",
            "--output",
            str(output),
            "--concurrency",
            "1",
        ]
    )

    assert status == 0
    result = json.loads(output.read_text(encoding="utf-8"))
    assert result["scoring"]["result_type"] == "correct"
