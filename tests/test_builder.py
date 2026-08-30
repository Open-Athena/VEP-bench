import copy
import json
import math
from pathlib import Path

import pytest
from jsonschema import Draft202012Validator

from vepbench.builder import (
    BuildError,
    build_file,
    build_questions,
    canonical_json,
    load_template,
    read_jsonl,
    validate_question,
)

ROOT = Path(__file__).resolve().parents[1]
SOURCE = ROOT / "tests/fixtures/synthetic-source.jsonl"
TEMPLATE = ROOT / "tests/fixtures/synthetic-template.json"
PRODUCTION_SOURCE = ROOT / "data/sources/chr17-vep-consequences.jsonl"
PRODUCTION_TEMPLATE = ROOT / "templates/vep_most_severe_consequence.json"
EXAMPLE_PROMPT = ROOT / "EXAMPLE_PROMPT.md"
SCHEMA = ROOT / "schemas/question.schema.json"


@pytest.fixture
def schema() -> dict:
    value = json.loads(SCHEMA.read_text(encoding="utf-8"))
    Draft202012Validator.check_schema(value)
    return value


@pytest.fixture
def generated_question(schema: dict) -> dict:
    questions = build_questions(read_jsonl(SOURCE), load_template(TEMPLATE), schema)
    assert len(questions) == 1
    return questions[0]


def test_build_is_byte_identical(tmp_path: Path) -> None:
    first = tmp_path / "first.jsonl"
    second = tmp_path / "second.jsonl"

    first_count, first_digest = build_file(SOURCE, TEMPLATE, SCHEMA, first)
    second_count, second_digest = build_file(SOURCE, TEMPLATE, SCHEMA, second)

    assert first_count == second_count == 1
    assert first_digest == second_digest
    assert first.read_bytes() == second.read_bytes()
    assert first.read_bytes().endswith(b"\n")


def test_canonical_json_rejects_non_finite_numbers() -> None:
    with pytest.raises(ValueError, match="Out of range float values"):
        canonical_json({"temperature": math.nan})


def test_committed_questions_match_builder(tmp_path: Path) -> None:
    rebuilt = tmp_path / "questions.jsonl"
    build_file(PRODUCTION_SOURCE, PRODUCTION_TEMPLATE, SCHEMA, rebuilt)

    assert rebuilt.read_bytes() == (ROOT / "benchmark/questions.jsonl").read_bytes()


def test_example_prompt_matches_first_committed_question() -> None:
    first_question = read_jsonl(ROOT / "benchmark/questions.jsonl")[0]
    example = EXAMPLE_PROMPT.read_text(encoding="utf-8")

    assert f"\n\n{first_question['prompt']}\n" in example


def test_production_prompt_has_unambiguous_final_line_instructions(
    schema: dict,
) -> None:
    questions = build_questions(
        read_jsonl(PRODUCTION_SOURCE), load_template(PRODUCTION_TEMPLATE), schema
    )
    prompt = questions[0]["prompt"]

    assert questions[0]["provenance"]["template_version"] == "1.1"
    assert "Example: `FINAL: C07`" in prompt
    assert (
        "Do not include the consequence name, a period, or any other text "
        "on that line." in prompt
    )


def test_generated_question_satisfies_schema(
    generated_question: dict, schema: dict
) -> None:
    Draft202012Validator(schema).validate(generated_question)
    assert generated_question["question_id"] == "mc-effect-v1:synthetic-001"
    assert generated_question["answer_choice_id"] == "B"


def test_duplicate_choice_ids_fail(generated_question: dict, schema: dict) -> None:
    question = copy.deepcopy(generated_question)
    question["choices"][1]["choice_id"] = "A"

    with pytest.raises(BuildError, match="choice IDs must be unique"):
        validate_question(question, Draft202012Validator(schema))


def test_missing_answer_choice_fails(generated_question: dict, schema: dict) -> None:
    question = copy.deepcopy(generated_question)
    question["answer_choice_id"] = "Z"

    with pytest.raises(BuildError, match="must identify exactly one choice"):
        validate_question(question, Draft202012Validator(schema))


def test_prompt_choice_disagreement_fails(
    generated_question: dict, schema: dict
) -> None:
    question = copy.deepcopy(generated_question)
    question["prompt"] = question["prompt"].replace("C. No effect", "C. Other")

    with pytest.raises(BuildError, match="prompt must contain choice"):
        validate_question(question, Draft202012Validator(schema))


def test_source_records_generate_in_question_id_order(schema: dict) -> None:
    records = read_jsonl(SOURCE)
    later = copy.deepcopy(records[0])
    later["source_record_id"] = "synthetic-002"
    questions = build_questions([later, records[0]], load_template(TEMPLATE), schema)

    assert [question["question_id"] for question in questions] == [
        "mc-effect-v1:synthetic-001",
        "mc-effect-v1:synthetic-002",
    ]
