"""Deterministic multiple-choice question generation."""

import hashlib
import json
import string
from collections.abc import Iterable, Mapping
from pathlib import Path
from typing import Any

from jsonschema import Draft202012Validator

QUESTION_SCHEMA_VERSION = "1.0"
REQUIRED_SOURCE_FIELDS = {
    "source_dataset",
    "source_record_id",
    "variant",
    "question",
    "choices",
    "answer_choice_id",
    "task_family",
}
ALLOWED_SOURCE_FIELDS = REQUIRED_SOURCE_FIELDS | {"tags"}
REQUIRED_TEMPLATE_FIELDS = {"template_id", "template_version", "prompt"}
PROMPT_FIELDS = {"variant", "question", "choices"}


class BuildError(ValueError):
    """Raised when source data cannot produce a valid benchmark question."""


def canonical_json(value: Any) -> str:
    """Return the canonical JSON representation used for record fingerprints."""

    return json.dumps(
        value,
        allow_nan=False,
        ensure_ascii=False,
        separators=(",", ":"),
        sort_keys=True,
    )


def sha256_json(value: Any) -> str:
    return hashlib.sha256(canonical_json(value).encode("utf-8")).hexdigest()


def sha256_file(path: str | Path) -> str:
    return hashlib.sha256(Path(path).read_bytes()).hexdigest()


def read_jsonl(path: str | Path) -> list[dict[str, Any]]:
    source_path = Path(path)
    records: list[dict[str, Any]] = []
    for line_number, raw_line in enumerate(
        source_path.read_text(encoding="utf-8").splitlines(), start=1
    ):
        if not raw_line.strip():
            continue
        try:
            record = json.loads(raw_line)
        except json.JSONDecodeError as exc:
            raise BuildError(f"{source_path}:{line_number}: invalid JSON: {exc.msg}") from exc
        if not isinstance(record, dict):
            raise BuildError(f"{source_path}:{line_number}: each record must be an object")
        records.append(record)
    if not records:
        raise BuildError(f"{source_path}: no source records found")
    return records


def load_template(path: str | Path) -> dict[str, str]:
    template_path = Path(path)
    try:
        template = json.loads(template_path.read_text(encoding="utf-8"))
    except json.JSONDecodeError as exc:
        raise BuildError(f"{template_path}: invalid JSON: {exc.msg}") from exc
    if not isinstance(template, dict):
        raise BuildError(f"{template_path}: template must be an object")
    missing = REQUIRED_TEMPLATE_FIELDS - set(template)
    unknown = set(template) - REQUIRED_TEMPLATE_FIELDS
    if missing or unknown:
        raise BuildError(_field_error(template_path, missing, unknown))
    if any(not isinstance(template[field], str) or not template[field] for field in template):
        raise BuildError(f"{template_path}: template fields must be non-empty strings")

    found_fields = {
        field_name
        for _, field_name, _, _ in string.Formatter().parse(template["prompt"])
        if field_name is not None
    }
    if found_fields != PROMPT_FIELDS:
        raise BuildError(
            f"{template_path}: prompt placeholders must be exactly "
            f"{sorted(PROMPT_FIELDS)}, got {sorted(found_fields)}"
        )
    return template


def build_questions(
    source_records: Iterable[Mapping[str, Any]],
    template: Mapping[str, str],
    schema: Mapping[str, Any],
) -> list[dict[str, Any]]:
    Draft202012Validator.check_schema(schema)
    validator = Draft202012Validator(schema)
    questions: list[dict[str, Any]] = []

    for index, source in enumerate(source_records, start=1):
        source_record = dict(source)
        _validate_source_record(source_record, index)
        choices = [dict(choice) for choice in source_record["choices"]]
        rendered_choices = "\n".join(
            f"{choice['choice_id']}. {choice['text']}" for choice in choices
        )
        prompt = template["prompt"].format(
            variant=source_record["variant"],
            question=source_record["question"],
            choices=rendered_choices,
        )
        question = {
            "schema_version": QUESTION_SCHEMA_VERSION,
            "question_id": (
                f"{template['template_id']}:{source_record['source_record_id']}"
            ),
            "task_type": "multiple_choice",
            "prompt": prompt,
            "choices": choices,
            "answer_choice_id": source_record["answer_choice_id"],
            "provenance": {
                "source_dataset": source_record["source_dataset"],
                "source_record_id": source_record["source_record_id"],
                "source_record_sha256": sha256_json(source_record),
                "template_id": template["template_id"],
                "template_version": template["template_version"],
            },
            "metadata": {
                "task_family": source_record["task_family"],
                "tags": source_record.get("tags", []),
            },
        }
        validate_question(question, validator)
        questions.append(question)

    questions.sort(key=lambda question: question["question_id"])
    question_ids = [question["question_id"] for question in questions]
    if len(question_ids) != len(set(question_ids)):
        duplicates = sorted(
            question_id
            for question_id in set(question_ids)
            if question_ids.count(question_id) > 1
        )
        raise BuildError(f"duplicate generated question IDs: {duplicates}")
    return questions


def validate_question(
    question: Mapping[str, Any], validator: Draft202012Validator
) -> None:
    errors = sorted(validator.iter_errors(question), key=lambda error: list(error.path))
    if errors:
        details = "; ".join(
            f"{'.'.join(str(part) for part in error.path) or '<record>'}: {error.message}"
            for error in errors
        )
        raise BuildError(f"{question.get('question_id', '<unknown>')}: {details}")

    choice_ids = [choice["choice_id"] for choice in question["choices"]]
    if len(choice_ids) != len(set(choice_ids)):
        raise BuildError(f"{question['question_id']}: choice IDs must be unique")
    if choice_ids.count(question["answer_choice_id"]) != 1:
        raise BuildError(
            f"{question['question_id']}: answer_choice_id must identify exactly one choice"
        )
    for choice in question["choices"]:
        rendered = f"{choice['choice_id']}. {choice['text']}"
        if question["prompt"].count(rendered) != 1:
            raise BuildError(
                f"{question['question_id']}: prompt must contain choice {rendered!r} exactly once"
            )


def write_questions(questions: Iterable[Mapping[str, Any]], output: str | Path) -> None:
    output_path = Path(output)
    output_path.parent.mkdir(parents=True, exist_ok=True)
    payload = "".join(f"{canonical_json(question)}\n" for question in questions)
    output_path.write_text(payload, encoding="utf-8", newline="\n")


def build_file(
    source: str | Path,
    template_path: str | Path,
    schema_path: str | Path,
    output: str | Path,
) -> tuple[int, str]:
    source_records = read_jsonl(source)
    template = load_template(template_path)
    schema = json.loads(Path(schema_path).read_text(encoding="utf-8"))
    questions = build_questions(source_records, template, schema)
    write_questions(questions, output)
    return len(questions), sha256_file(output)


def _validate_source_record(record: dict[str, Any], index: int) -> None:
    missing = REQUIRED_SOURCE_FIELDS - set(record)
    unknown = set(record) - ALLOWED_SOURCE_FIELDS
    if missing or unknown:
        raise BuildError(_field_error(f"source record {index}", missing, unknown))

    for field in REQUIRED_SOURCE_FIELDS - {"choices"}:
        if not isinstance(record[field], str) or not record[field]:
            raise BuildError(f"source record {index}: {field} must be a non-empty string")
    tags = record.get("tags", [])
    if (
        not isinstance(tags, list)
        or any(not isinstance(tag, str) or not tag for tag in tags)
        or len(tags) != len(set(tags))
    ):
        raise BuildError(f"source record {index}: tags must be unique non-empty strings")
    choices = record["choices"]
    if not isinstance(choices, list) or len(choices) < 2:
        raise BuildError(f"source record {index}: choices must contain at least two items")
    for choice_index, choice in enumerate(choices, start=1):
        if not isinstance(choice, dict) or set(choice) != {"choice_id", "text"}:
            raise BuildError(
                f"source record {index}: choice {choice_index} must contain choice_id and text"
            )
        if any(not isinstance(value, str) or not value for value in choice.values()):
            raise BuildError(
                f"source record {index}: choice {choice_index} values must be non-empty strings"
            )


def _field_error(
    location: str | Path, missing: set[str], unknown: set[str]
) -> str:
    parts = []
    if missing:
        parts.append(f"missing fields {sorted(missing)}")
    if unknown:
        parts.append(f"unknown fields {sorted(unknown)}")
    return f"{location}: {', '.join(parts)}"
