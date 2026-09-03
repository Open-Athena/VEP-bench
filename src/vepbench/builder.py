"""Deterministic benchmark question generation."""

import hashlib
import json
import math
import string
from collections.abc import Iterable, Mapping
from pathlib import Path
from typing import Any

from jsonschema import Draft202012Validator

QUESTION_SCHEMA_VERSION = "1.0"
RANKING_QUESTION_SCHEMA_VERSION = "2.0"
RANKING_REFERENCE_CONTIG = "element"
MULTIPLE_CHOICE_SOURCE_FIELDS = {
    "source_dataset",
    "source_record_id",
    "variant",
    "question",
    "choices",
    "answer_choice_id",
    "task_family",
}
RANKING_SOURCE_FIELDS = {
    "source_dataset",
    "source_record_id",
    "assay_context",
    "reference_sequence",
    "candidates",
    "task_family",
}
OPTIONAL_SOURCE_FIELDS = {"source_metadata", "tags"}
REQUIRED_TEMPLATE_FIELDS = {"template_id", "template_version", "prompt"}
MULTIPLE_CHOICE_PROMPT_FIELDS = {"variant", "question", "choices"}
RANKING_PROMPT_FIELDS = {"assay_context", "reference_sequence", "candidate_table"}


class BuildError(ValueError):
    """Raised when source data cannot produce a valid benchmark question."""


def is_finite_number(value: Any) -> bool:
    """Return whether a JSON number has a finite binary-float representation."""

    if isinstance(value, bool) or not isinstance(value, int | float):
        return False
    try:
        return math.isfinite(float(value))
    except OverflowError:
        return False


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
    digest = hashlib.sha256()
    with Path(path).open("rb") as source:
        for chunk in iter(lambda: source.read(1024 * 1024), b""):
            digest.update(chunk)
    return digest.hexdigest()


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
    task_type = template.get("task_type", "multiple_choice")
    allowed_fields = (
        REQUIRED_TEMPLATE_FIELDS
        if task_type == "multiple_choice"
        else REQUIRED_TEMPLATE_FIELDS | {"task_type"}
    )
    missing = REQUIRED_TEMPLATE_FIELDS - set(template)
    unknown = set(template) - allowed_fields
    if missing or unknown:
        raise BuildError(_field_error(template_path, missing, unknown))
    if task_type not in {"multiple_choice", "ranking"}:
        raise BuildError(f"{template_path}: unsupported task_type {task_type!r}")
    if any(not isinstance(value, str) or not value for value in template.values()):
        raise BuildError(f"{template_path}: template fields must be non-empty strings")

    found_fields = {
        field_name
        for _, field_name, _, _ in string.Formatter().parse(template["prompt"])
        if field_name is not None
    }
    expected_fields = (
        MULTIPLE_CHOICE_PROMPT_FIELDS if task_type == "multiple_choice" else RANKING_PROMPT_FIELDS
    )
    if found_fields != expected_fields:
        raise BuildError(
            f"{template_path}: prompt placeholders must be exactly "
            f"{sorted(expected_fields)}, got {sorted(found_fields)}"
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
    task_type = template.get("task_type", "multiple_choice")

    for index, source in enumerate(source_records, start=1):
        source_record = dict(source)
        if task_type == "multiple_choice":
            _validate_multiple_choice_source_record(source_record, index)
            choices = [dict(choice) for choice in source_record["choices"]]
            rendered_choices = "\n".join(
                f"{choice['choice_id']}. {choice['text']}" for choice in choices
            )
            prompt = template["prompt"].format(
                variant=source_record["variant"],
                question=source_record["question"],
                choices=rendered_choices,
            )
            task_fields: dict[str, Any] = {
                "schema_version": QUESTION_SCHEMA_VERSION,
                "task_type": "multiple_choice",
                "choices": choices,
                "answer_choice_id": source_record["answer_choice_id"],
            }
        else:
            _validate_ranking_source_record(source_record, index)
            candidates = sorted(
                (dict(candidate) for candidate in source_record["candidates"]),
                key=_candidate_vcf_key,
            )
            candidate_table = "\n".join(
                [
                    f"{candidate['chrom']}\t{candidate['pos']}\t{candidate['candidate_id']}\t"
                    f"{candidate['ref']}\t{candidate['alt']}"
                    for candidate in candidates
                ]
            )
            prompt = template["prompt"].format(
                assay_context=source_record["assay_context"],
                reference_sequence=_format_sequence(source_record["reference_sequence"]),
                candidate_table=candidate_table,
            )
            task_fields = {
                "schema_version": RANKING_QUESTION_SCHEMA_VERSION,
                "task_type": "ranking",
                "candidates": candidates,
            }
        question = {
            **task_fields,
            "question_id": (f"{template['template_id']}:{source_record['source_record_id']}"),
            "prompt": prompt,
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
            question_id for question_id in set(question_ids) if question_ids.count(question_id) > 1
        )
        raise BuildError(f"duplicate generated question IDs: {duplicates}")
    return questions


def validate_question(question: Mapping[str, Any], validator: Draft202012Validator) -> None:
    errors = sorted(validator.iter_errors(question), key=lambda error: list(error.path))
    if errors:
        details = "; ".join(
            f"{'.'.join(str(part) for part in error.path) or '<record>'}: {error.message}"
            for error in errors
        )
        raise BuildError(f"{question.get('question_id', '<unknown>')}: {details}")

    if question["task_type"] == "multiple_choice":
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
                    f"{question['question_id']}: prompt must contain choice "
                    f"{rendered!r} exactly once"
                )
        return

    candidates = question["candidates"]
    candidate_ids = [candidate["candidate_id"] for candidate in candidates]
    if len(candidate_ids) != len(set(candidate_ids)):
        raise BuildError(f"{question['question_id']}: candidate IDs must be unique")
    variant_keys = [
        (candidate["chrom"], candidate["pos"], candidate["ref"], candidate["alt"])
        for candidate in candidates
    ]
    if len(variant_keys) != len(set(variant_keys)):
        raise BuildError(f"{question['question_id']}: candidate VCF keys must be unique")
    if candidates != sorted(candidates, key=_candidate_vcf_key):
        raise BuildError(
            f"{question['question_id']}: candidates must be sorted by CHROM, POS, REF, ALT"
        )
    for candidate in candidates:
        if not is_finite_number(candidate["reference_score"]):
            raise BuildError(f"{question['question_id']}: reference scores must be finite")
        rendered = (
            f"{candidate['chrom']}\t{candidate['pos']}\t{candidate['candidate_id']}\t"
            f"{candidate['ref']}\t{candidate['alt']}"
        )
        if question["prompt"].count(rendered) != 1:
            raise BuildError(
                f"{question['question_id']}: prompt must contain candidate row "
                f"{rendered!r} exactly once"
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
    manifest_output: str | Path | None = None,
) -> tuple[int, str]:
    source_records = read_jsonl(source)
    template = load_template(template_path)
    schema = json.loads(Path(schema_path).read_text(encoding="utf-8"))
    questions = build_questions(source_records, template, schema)
    write_questions(questions, output)
    output_path = Path(output)
    digest = sha256_file(output_path)
    manifest_path = (
        Path(manifest_output)
        if manifest_output is not None
        else output_path.with_name(f"{output_path.stem}.manifest.json")
    )
    manifest = {
        "schema_version": "1.0",
        "path": output_path.name,
        "sha256": digest,
        "bytes": output_path.stat().st_size,
        "records": len(questions),
    }
    manifest_path.parent.mkdir(parents=True, exist_ok=True)
    manifest_path.write_text(f"{canonical_json(manifest)}\n", encoding="utf-8", newline="\n")
    return len(questions), digest


def _validate_multiple_choice_source_record(record: dict[str, Any], index: int) -> None:
    missing = MULTIPLE_CHOICE_SOURCE_FIELDS - set(record)
    unknown = set(record) - (MULTIPLE_CHOICE_SOURCE_FIELDS | OPTIONAL_SOURCE_FIELDS)
    if missing or unknown:
        raise BuildError(_field_error(f"source record {index}", missing, unknown))

    for field in MULTIPLE_CHOICE_SOURCE_FIELDS - {"choices"}:
        if not isinstance(record[field], str) or not record[field]:
            raise BuildError(f"source record {index}: {field} must be a non-empty string")
    tags = record.get("tags", [])
    if (
        not isinstance(tags, list)
        or any(not isinstance(tag, str) or not tag for tag in tags)
        or len(tags) != len(set(tags))
    ):
        raise BuildError(f"source record {index}: tags must be unique non-empty strings")
    source_metadata = record.get("source_metadata", {})
    if not isinstance(source_metadata, dict):
        raise BuildError(f"source record {index}: source_metadata must be an object")
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


def _validate_ranking_source_record(record: dict[str, Any], index: int) -> None:
    missing = RANKING_SOURCE_FIELDS - set(record)
    unknown = set(record) - (RANKING_SOURCE_FIELDS | OPTIONAL_SOURCE_FIELDS)
    if missing or unknown:
        raise BuildError(_field_error(f"source record {index}", missing, unknown))
    for field in RANKING_SOURCE_FIELDS - {"candidates"}:
        if not isinstance(record[field], str) or not record[field]:
            raise BuildError(f"source record {index}: {field} must be a non-empty string")
    sequence = record["reference_sequence"]
    if sequence != sequence.upper() or any(base not in "ACGT" for base in sequence):
        raise BuildError(f"source record {index}: reference_sequence must contain only A/C/G/T")
    _validate_common_source_metadata(record, index)

    candidates = record["candidates"]
    expected_fields = {"candidate_id", "chrom", "pos", "ref", "alt", "reference_score"}
    if not isinstance(candidates, list) or len(candidates) < 2:
        raise BuildError(f"source record {index}: candidates must contain at least two items")
    for candidate_index, candidate in enumerate(candidates, start=1):
        if not isinstance(candidate, dict) or set(candidate) != expected_fields:
            raise BuildError(
                f"source record {index}: candidate {candidate_index} has invalid fields"
            )
        for field in ("candidate_id", "chrom", "ref", "alt"):
            if not isinstance(candidate[field], str) or not candidate[field]:
                raise BuildError(
                    f"source record {index}: candidate {candidate_index} {field} "
                    "must be a non-empty string"
                )
        if candidate["chrom"] != RANKING_REFERENCE_CONTIG:
            raise BuildError(
                f"source record {index}: candidate {candidate_index} chrom must be "
                f"{RANKING_REFERENCE_CONTIG!r}"
            )
        if (
            isinstance(candidate["pos"], bool)
            or not isinstance(candidate["pos"], int)
            or candidate["pos"] < 1
        ):
            raise BuildError(
                f"source record {index}: candidate {candidate_index} pos must be positive"
            )
        reference_start = candidate["pos"] - 1
        reference_end = reference_start + len(candidate["ref"])
        if sequence[reference_start:reference_end] != candidate["ref"]:
            raise BuildError(
                f"source record {index}: candidate {candidate_index} REF must agree "
                "with the local reference sequence"
            )
        score = candidate["reference_score"]
        if not is_finite_number(score):
            raise BuildError(
                f"source record {index}: candidate {candidate_index} reference_score must be finite"
            )


def _validate_common_source_metadata(record: dict[str, Any], index: int) -> None:
    tags = record.get("tags", [])
    if (
        not isinstance(tags, list)
        or any(not isinstance(tag, str) or not tag for tag in tags)
        or len(tags) != len(set(tags))
    ):
        raise BuildError(f"source record {index}: tags must be unique non-empty strings")
    source_metadata = record.get("source_metadata", {})
    if not isinstance(source_metadata, dict):
        raise BuildError(f"source record {index}: source_metadata must be an object")


def _candidate_vcf_key(candidate: Mapping[str, Any]) -> tuple[str, int, str, str]:
    return (candidate["chrom"], candidate["pos"], candidate["ref"], candidate["alt"])


def _format_sequence(sequence: str, width: int = 80) -> str:
    return "\n".join(sequence[index : index + width] for index in range(0, len(sequence), width))


def _field_error(location: str | Path, missing: set[str], unknown: set[str]) -> str:
    parts = []
    if missing:
        parts.append(f"missing fields {sorted(missing)}")
    if unknown:
        parts.append(f"unknown fields {sorted(unknown)}")
    return f"{location}: {', '.join(parts)}"
