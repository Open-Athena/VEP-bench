import hashlib
import json
from copy import deepcopy
from pathlib import Path

import pytest

from vepbench.builder import BuildError, canonical_json, read_jsonl, sha256_json
from vepbench.site import build_site

ROOT = Path(__file__).resolve().parents[1]
QUESTIONS = ROOT / "tests/fixtures/synthetic-questions.jsonl"
QUESTION_SCHEMA = ROOT / "schemas/question.schema.json"
RESULT_SCHEMA = ROOT / "schemas/result.schema.json"
RESULTS = ROOT / "tests/fixtures/results"
PUBLIC_QUESTIONS = ROOT / "benchmark/questions.jsonl"
PUBLIC_RESULTS = ROOT / "results"
ASSETS = ROOT / "web"


def test_site_build_validates_and_copies_raw_artifacts(tmp_path: Path) -> None:
    output = tmp_path / "site"
    manifest = build_site(
        questions_path=QUESTIONS,
        question_schema_path=QUESTION_SCHEMA,
        results_dir=RESULTS,
        result_schema_path=RESULT_SCHEMA,
        assets_dir=ASSETS,
        output=output,
    )

    assert manifest["questions"]["records"] == 1
    assert manifest["results"][0]["run_id"] == "synthetic-demo"
    assert manifest["results"][0]["complete"] is True
    assert manifest["results"][0]["current_question_set"] is True
    assert manifest["results"][0]["questions_covered"] == 1
    assert manifest["results"][0]["questions_expected"] == 1
    assert manifest["results"][0]["api_errors"] == 0
    assert (output / "data/questions.jsonl").read_bytes() == QUESTIONS.read_bytes()
    assert (output / "data/results/synthetic-demo.jsonl").read_bytes() == (
        RESULTS / "synthetic-demo.jsonl"
    ).read_bytes()
    assert (output / "index.md").is_file()
    assert (output / "tasks.md").is_file()
    assert (output / "questions.md").is_file()
    assert (output / "tasks/consequence-classification.md").is_file()
    assert (output / "tasks/consequences.svg").read_bytes() == (
        ASSETS / "tasks/consequences.svg"
    ).read_bytes()
    explorer = json.loads((output / "data/explorer.json").read_text())
    assert explorer["schema_version"] == "1.1"
    assert explorer["questions"] == read_jsonl(QUESTIONS)
    assert "runs" not in explorer
    assert explorer["task_runs"][0]["records_data"] == read_jsonl(
        RESULTS / "synthetic-demo.jsonl"
    )
    assert explorer["task_runs"][0]["task_family"] == (
        explorer["questions"][0]["metadata"]["task_family"]
    )
    assert explorer["task_runs"][0]["complete"] is True
    assert explorer["task_runs"][0]["current_task_version"] is True
    assert explorer["task_runs"][0]["questions_expected"] == 1


def test_site_groups_mixed_result_file_by_structured_task_family(
    tmp_path: Path,
) -> None:
    first_question = read_jsonl(QUESTIONS)[0]
    second_question = deepcopy(first_question)
    second_question["question_id"] = "mc-effect-v1:synthetic-002"
    second_question["metadata"]["task_family"] = "second_synthetic_task"
    second_question["provenance"]["source_record_id"] = "synthetic-002"
    questions = [first_question, second_question]
    questions_path = tmp_path / "questions.jsonl"
    question_set_payload = "".join(
        f"{canonical_json(question)}\n" for question in questions
    )
    questions_path.write_text(
        question_set_payload,
        encoding="utf-8",
        newline="\n",
    )
    question_set_sha256 = hashlib.sha256(question_set_payload.encode()).hexdigest()

    first_result = read_jsonl(RESULTS / "synthetic-demo.jsonl")[0]
    second_result = deepcopy(first_result)
    results = [first_result, second_result]
    for result, question in zip(results, questions, strict=True):
        result["run_id"] = "mixed-task-run"
        result["question_id"] = question["question_id"]
        result["question"] = question
        result["question_sha256"] = sha256_json(question)
        result["question_set_sha256"] = question_set_sha256
        result["question_set_size"] = 2
    results_dir = tmp_path / "results"
    results_dir.mkdir()
    (results_dir / "mixed-task-run.jsonl").write_text(
        "".join(f"{canonical_json(result)}\n" for result in results),
        encoding="utf-8",
        newline="\n",
    )

    build_site(
        questions_path=questions_path,
        question_schema_path=QUESTION_SCHEMA,
        results_dir=results_dir,
        result_schema_path=RESULT_SCHEMA,
        assets_dir=ASSETS,
        output=tmp_path / "site",
    )

    explorer = json.loads((tmp_path / "site/data/explorer.json").read_text())
    task_runs = {run["task_family"]: run for run in explorer["task_runs"]}
    assert set(task_runs) == {
        first_question["metadata"]["task_family"],
        "second_synthetic_task",
    }
    assert all(run["records"] == 1 for run in task_runs.values())
    assert all(run["questions_expected"] == 1 for run in task_runs.values())
    assert all(run["complete"] is True for run in task_runs.values())
    assert all(run["current_task_version"] is True for run in task_runs.values())

    historical_results = deepcopy(results)
    historical_second_question = deepcopy(second_question)
    historical_second_question["prompt"] += "\n\nHistorical wording."
    historical_results[1]["question"] = historical_second_question
    historical_results[1]["question_sha256"] = sha256_json(
        historical_second_question
    )
    historical_payload = "".join(
        f"{canonical_json(question)}\n"
        for question in [first_question, historical_second_question]
    )
    historical_sha256 = hashlib.sha256(historical_payload.encode()).hexdigest()
    for result in historical_results:
        result["question_set_sha256"] = historical_sha256
    historical_dir = tmp_path / "historical-results"
    historical_dir.mkdir()
    (historical_dir / "historical-mixed-task-run.jsonl").write_text(
        "".join(f"{canonical_json(result)}\n" for result in historical_results),
        encoding="utf-8",
        newline="\n",
    )

    historical_manifest = build_site(
        questions_path=questions_path,
        question_schema_path=QUESTION_SCHEMA,
        results_dir=historical_dir,
        result_schema_path=RESULT_SCHEMA,
        assets_dir=ASSETS,
        output=tmp_path / "historical-site",
    )
    assert historical_manifest["results"][0]["current_question_set"] is False

    historical_explorer = json.loads(
        (tmp_path / "historical-site/data/explorer.json").read_text()
    )
    historical_task_runs = {
        run["task_family"]: run for run in historical_explorer["task_runs"]
    }
    first_task_family = first_question["metadata"]["task_family"]
    assert historical_task_runs[first_task_family]["current_task_version"] is True
    assert historical_task_runs[first_task_family]["complete"] is True
    assert historical_task_runs["second_synthetic_task"]["current_task_version"] is False


def test_public_site_builds_with_committed_results(tmp_path: Path) -> None:
    manifest = build_site(
        questions_path=PUBLIC_QUESTIONS,
        question_schema_path=QUESTION_SCHEMA,
        results_dir=PUBLIC_RESULTS,
        result_schema_path=RESULT_SCHEMA,
        assets_dir=ASSETS,
        output=tmp_path / "site",
    )

    assert manifest["questions"]["records"] == 190
    assert len(manifest["results"]) == 4
    by_run_id = {result["run_id"]: result for result in manifest["results"]}
    historical_result = by_run_id["gpt-5.6-luna-medium-parallel-20260829"]
    assert historical_result["records"] == 190
    assert historical_result["complete"] is True
    assert historical_result["current_question_set"] is False
    assert historical_result["questions_covered"] == 190
    assert historical_result["questions_expected"] == 190
    assert historical_result["api_errors"] == 0
    current_result = by_run_id["gpt-5.6-luna-medium-prompt-v1.1-20260830"]
    assert current_result["records"] == 190
    assert current_result["complete"] is True
    assert current_result["current_question_set"] is True
    assert current_result["questions_covered"] == 190
    assert current_result["questions_expected"] == 190
    assert current_result["api_errors"] == 0
    historical = read_jsonl(
        PUBLIC_RESULTS / "gpt-5.6-luna-medium-parallel-20260829.jsonl"
    )[0]
    assert historical["question"]["provenance"]["template_version"] == "1.0"
    current = read_jsonl(
        PUBLIC_RESULTS / "gpt-5.6-luna-medium-prompt-v1.1-20260830.jsonl"
    )[0]
    assert current["question"]["provenance"]["template_version"] == "1.1"
    for effort in ("low", "high"):
        run_id = f"gpt-5.6-luna-{effort}-prompt-v1.1-20260830"
        comparison = by_run_id[run_id]
        assert comparison["records"] == 190
        assert comparison["complete"] is True
        assert comparison["current_question_set"] is True
        assert comparison["questions_covered"] == 190
        assert comparison["questions_expected"] == 190
        assert comparison["api_errors"] == 0


def test_site_build_rejects_result_with_wrong_question_digest(tmp_path: Path) -> None:
    results = tmp_path / "results"
    results.mkdir()
    result = read_jsonl(RESULTS / "synthetic-demo.jsonl")[0]
    result["question_sha256"] = "0" * 64
    (results / "tampered.jsonl").write_text(
        f"{canonical_json(result)}\n", encoding="utf-8", newline="\n"
    )

    with pytest.raises(BuildError, match="question digest does not match"):
        build_site(
            questions_path=QUESTIONS,
            question_schema_path=QUESTION_SCHEMA,
            results_dir=results,
            result_schema_path=RESULT_SCHEMA,
            assets_dir=ASSETS,
            output=tmp_path / "site",
        )


def test_site_rejects_complete_run_with_wrong_question_set_digest(tmp_path: Path) -> None:
    results = tmp_path / "results"
    results.mkdir()
    result = read_jsonl(RESULTS / "synthetic-demo.jsonl")[0]
    result["question_set_sha256"] = "0" * 64
    (results / "tampered.jsonl").write_text(
        f"{canonical_json(result)}\n", encoding="utf-8", newline="\n"
    )

    with pytest.raises(BuildError, match="question-set digest does not match snapshots"):
        build_site(
            questions_path=QUESTIONS,
            question_schema_path=QUESTION_SCHEMA,
            results_dir=results,
            result_schema_path=RESULT_SCHEMA,
            assets_dir=ASSETS,
            output=tmp_path / "site",
        )


def test_site_accepts_self_validating_historical_question_set(tmp_path: Path) -> None:
    results = tmp_path / "results"
    results.mkdir()
    result = read_jsonl(RESULTS / "synthetic-demo.jsonl")[0]
    result["question"]["prompt"] += "\n\nHistorical wording."
    result["question_sha256"] = sha256_json(result["question"])
    question_set_payload = f"{canonical_json(result['question'])}\n".encode()
    result["question_set_sha256"] = hashlib.sha256(question_set_payload).hexdigest()
    (results / "historical.jsonl").write_text(
        f"{canonical_json(result)}\n", encoding="utf-8", newline="\n"
    )

    manifest = build_site(
        questions_path=QUESTIONS,
        question_schema_path=QUESTION_SCHEMA,
        results_dir=results,
        result_schema_path=RESULT_SCHEMA,
        assets_dir=ASSETS,
        output=tmp_path / "site",
    )

    assert manifest["results"][0]["complete"] is True
    assert manifest["results"][0]["current_question_set"] is False
    assert manifest["results"][0]["questions_expected"] == 1


def test_manifest_marks_api_error_run_incomplete(tmp_path: Path) -> None:
    results = tmp_path / "results"
    results.mkdir()
    result = read_jsonl(RESULTS / "synthetic-demo.jsonl")[0]
    result["response"].update(
        {
            "status": "api_error",
            "content": None,
            "reasoning": None,
            "finish_reason": None,
        }
    )
    result["scoring"].update(
        {
            "parsed_answer": None,
            "value": None,
            "correct": None,
            "parse_error": None,
        }
    )
    result["error"] = {
        "type": "provider_error",
        "message": "synthetic failure",
        "status_code": 503,
    }
    (results / "error.jsonl").write_text(
        f"{canonical_json(result)}\n", encoding="utf-8", newline="\n"
    )

    manifest = build_site(
        questions_path=QUESTIONS,
        question_schema_path=QUESTION_SCHEMA,
        results_dir=results,
        result_schema_path=RESULT_SCHEMA,
        assets_dir=ASSETS,
        output=tmp_path / "site",
    )

    assert manifest["results"][0]["complete"] is False
    assert manifest["results"][0]["api_errors"] == 1


def test_site_build_refuses_nonempty_output(tmp_path: Path) -> None:
    output = tmp_path / "site"
    output.mkdir()
    (output / "keep.txt").write_text("user data", encoding="utf-8")

    with pytest.raises(BuildError, match="refusing to overwrite"):
        build_site(
            questions_path=QUESTIONS,
            question_schema_path=QUESTION_SCHEMA,
            results_dir=RESULTS,
            result_schema_path=RESULT_SCHEMA,
            assets_dir=ASSETS,
            output=output,
        )
