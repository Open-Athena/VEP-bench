import gzip
import json
from pathlib import Path

import pytest

from vepbench.browser_qa import DEFAULT_PREDICTION, DEFAULT_QUESTION_ID, prepare_fixture
from vepbench.builder import build_questions, canonical_json, load_template, read_jsonl
from vepbench.publication import validate_version

ROOT = Path(__file__).resolve().parents[1]
QUESTIONS = ROOT / "tests/fixtures/synthetic-questions.jsonl"
PRODUCTION_SOURCE = ROOT / "data/sources/chr17-vep-consequences.jsonl"
PRODUCTION_TEMPLATE = ROOT / "templates/vep_most_severe_consequence.json"
QUESTION_SCHEMA = ROOT / "schemas/question.schema.json"


def test_browser_qa_defaults_match_production_question_set() -> None:
    questions = build_questions(
        read_jsonl(PRODUCTION_SOURCE),
        load_template(PRODUCTION_TEMPLATE),
        json.loads(QUESTION_SCHEMA.read_text(encoding="utf-8")),
    )
    selected = next(
        question for question in questions if question["question_id"] == DEFAULT_QUESTION_ID
    )

    assert selected["answer_choice_id"] == "C13"
    assert DEFAULT_PREDICTION == "C17"
    assert DEFAULT_PREDICTION in {choice["choice_id"] for choice in selected["choices"]}


@pytest.mark.parametrize("config_relative", ["data/config.json", "_file/data/config.abc123.json"])
def test_browser_qa_fixture_is_complete_and_offline(tmp_path: Path, config_relative: str) -> None:
    site = tmp_path / "site"
    config_path = site / config_relative
    config_path.parent.mkdir(parents=True)
    config_path.write_text("{}\n", encoding="utf-8")
    output = tmp_path / "publication"
    base_url = "http://127.0.0.1:4173/publication/versions/main"

    manifest = prepare_fixture(
        questions_path=QUESTIONS,
        output=output,
        selected_question_id="mc-effect-v1:synthetic-001",
        prediction="A",
        site_root=site,
        data_base_url=base_url,
    )

    assert manifest == validate_version(output, version_name="main")
    config = json.loads(config_path.read_text(encoding="utf-8"))
    assert config["data_base_url"] == base_url
    runs = json.loads((output / "versions/main/runs.json").read_text(encoding="utf-8"))
    run = runs["runs"][0]
    assert run["coverage"]["complete"] is True
    assert run["model"]["family"] == "Synthetic browser QA"
    assert run["model"]["release_date"] == "2026-08-01"
    assert run["metrics"]["total_tokens"] == 112
    assert run["metrics"]["total_cost_usd"] == 0
    outcome_index = json.loads(
        gzip.decompress((output / "versions/main" / run["outcome_index_path"]).read_bytes())
    )
    assert outcome_index["outcomes"] == [
        {
            "question_id": "mc-effect-v1:synthetic-001",
            "correct": False,
            "result_type": "incorrect",
        }
    ]
    answer_path = next((output / "versions/main/answers/browser-qa").glob("*.json.gz"))
    answer = json.loads(gzip.decompress(answer_path.read_bytes()))
    assert answer["scoring"] == {
        "correct": False,
        "metric": "exact_match",
        "parse_error": None,
        "parsed_answer": "A",
        "result_type": "incorrect",
        "value": 0,
    }


def test_browser_qa_fixture_supports_multiple_task_runs(tmp_path: Path) -> None:
    secondary_questions = tmp_path / "secondary-questions.jsonl"
    secondary = dict(read_jsonl(QUESTIONS)[0])
    secondary["question_id"] = "secondary-task-v1:synthetic-001"
    secondary["metadata"] = {
        **secondary["metadata"],
        "task_family": "secondary_task",
    }
    secondary_questions.write_text(
        f"{canonical_json(secondary)}\n",
        encoding="utf-8",
        newline="\n",
    )

    output = tmp_path / "publication"
    prepare_fixture(
        questions_path=[QUESTIONS, secondary_questions],
        output=output,
        selected_question_id="mc-effect-v1:synthetic-001",
        prediction="A",
        include_alternate_model=True,
    )

    runs_document = json.loads((output / "versions/main/runs.json").read_text(encoding="utf-8"))
    assert len(runs_document["runs"]) == 4
    assert {
        profile["task_family"] for profile in runs_document["leaderboard"]["evaluation_profiles"]
    } == {"synthetic_effect", "secondary_task"}
    assert {run["model"]["model_id"] for run in runs_document["runs"]} == {
        "synthetic/browser-qa",
        "synthetic/browser-qa-alternate",
    }
