from __future__ import annotations

import copy
import json
from pathlib import Path

import pytest
from jsonschema import Draft202012Validator

from vepbench.builder import (
    BuildError,
    build_file,
    build_questions,
    load_template,
    read_jsonl,
    validate_question,
)

ROOT = Path(__file__).resolve().parents[1]
SOURCE = ROOT / "data/sources/synthetic.jsonl"
TEMPLATE = ROOT / "templates/multiple_choice.json"
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


def test_committed_questions_match_builder(tmp_path: Path) -> None:
    rebuilt = tmp_path / "questions.jsonl"
    build_file(SOURCE, TEMPLATE, SCHEMA, rebuilt)

    assert rebuilt.read_bytes() == (ROOT / "benchmark/questions.jsonl").read_bytes()


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
