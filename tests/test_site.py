import hashlib
import json
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
    assert (output / "questions.md").is_file()
    explorer = json.loads((output / "data/explorer.json").read_text())
    assert explorer["questions"] == read_jsonl(QUESTIONS)
    assert explorer["runs"][0]["records_data"] == read_jsonl(
        RESULTS / "synthetic-demo.jsonl"
    )


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
    assert len(manifest["results"]) == 2
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
