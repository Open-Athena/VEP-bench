from __future__ import annotations

from pathlib import Path

import pytest

from vepbench.builder import BuildError, canonical_json, read_jsonl
from vepbench.demo import build_demo_result
from vepbench.site import build_site

ROOT = Path(__file__).resolve().parents[1]
QUESTIONS = ROOT / "benchmark/questions.jsonl"
QUESTION_SCHEMA = ROOT / "schemas/question.schema.json"
RESULT_SCHEMA = ROOT / "schemas/result.schema.json"
RESULTS = ROOT / "results"
ASSETS = ROOT / "web"


def test_committed_demo_result_is_reproducible(tmp_path: Path) -> None:
    rebuilt = tmp_path / "synthetic-demo.jsonl"
    summary = build_demo_result(
        questions_path=QUESTIONS,
        question_schema_path=QUESTION_SCHEMA,
        result_schema_path=RESULT_SCHEMA,
        response_path=ROOT / "data/fixtures/synthetic-openrouter-response.json",
        output=rebuilt,
    )

    assert summary.is_complete
    assert rebuilt.read_bytes() == (RESULTS / "synthetic-demo.jsonl").read_bytes()


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
    assert manifest["results"][0]["api_errors"] == 0
    assert (output / "data/questions.jsonl").read_bytes() == QUESTIONS.read_bytes()
    assert (output / "data/results/synthetic-demo.jsonl").read_bytes() == (
        RESULTS / "synthetic-demo.jsonl"
    ).read_bytes()
    assert (output / "index.html").is_file()
    assert (output / "app.js").is_file()


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
