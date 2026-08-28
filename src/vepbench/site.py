"""Validate committed benchmark data and assemble the static Pages artifact."""

from __future__ import annotations

import json
import shutil
from collections.abc import Mapping
from pathlib import Path
from typing import Any

from jsonschema import Draft202012Validator

from .builder import (
    BuildError,
    canonical_json,
    read_jsonl,
    sha256_file,
    sha256_json,
    validate_question,
)
from .evaluator import validate_result


def build_site(
    *,
    questions_path: str | Path,
    question_schema_path: str | Path,
    results_dir: str | Path,
    result_schema_path: str | Path,
    assets_dir: str | Path,
    output: str | Path,
) -> dict[str, Any]:
    """Build a self-contained static artifact while validating all public data."""

    questions_file = Path(questions_path)
    questions = read_jsonl(questions_file)
    question_schema = _load_validator(question_schema_path)
    result_validator = _load_validator(result_schema_path)
    question_by_id = {question["question_id"]: question for question in questions}
    if len(question_by_id) != len(questions):
        raise BuildError(f"{questions_file}: duplicate question IDs")
    for question in questions:
        validate_question(question, question_schema)

    question_set_sha256 = sha256_file(questions_file)
    result_files: list[dict[str, Any]] = []
    validated_result_files: list[Path] = []
    seen_run_ids: set[str] = set()
    source_results_dir = Path(results_dir)
    if source_results_dir.exists():
        for result_file in sorted(source_results_dir.glob("*.jsonl")):
            records = read_jsonl(result_file)
            _validate_result_file(
                records,
                result_file=result_file,
                validator=result_validator,
                question_by_id=question_by_id,
                question_set_sha256=question_set_sha256,
            )
            run_id = records[0]["run_id"]
            if run_id in seen_run_ids:
                raise BuildError(f"{result_file}: duplicate run_id {run_id!r}")
            seen_run_ids.add(run_id)
            validated_result_files.append(result_file)
            question_ids_in_run = {record["question_id"] for record in records}
            api_errors = sum(
                record["response"]["status"] == "api_error" for record in records
            )
            result_files.append(
                {
                    "path": f"data/results/{result_file.name}",
                    "sha256": sha256_file(result_file),
                    "records": len(records),
                    "run_id": run_id,
                    "complete": question_ids_in_run == set(question_by_id),
                    "api_errors": api_errors,
                }
            )

    output_dir = Path(output)
    if output_dir.exists() and any(output_dir.iterdir()):
        raise BuildError(f"refusing to overwrite non-empty site directory {output_dir}")
    output_dir.mkdir(parents=True, exist_ok=True)

    assets = Path(assets_dir)
    for source in sorted(assets.iterdir()):
        if source.is_file():
            shutil.copy2(source, output_dir / source.name)

    data_dir = output_dir / "data"
    result_output_dir = data_dir / "results"
    result_output_dir.mkdir(parents=True)
    shutil.copy2(questions_file, data_dir / "questions.jsonl")
    for result_file in validated_result_files:
        shutil.copy2(result_file, result_output_dir / result_file.name)

    manifest = {
        "schema_version": "1.0",
        "questions": {
            "path": "data/questions.jsonl",
            "sha256": question_set_sha256,
            "records": len(questions),
        },
        "results": result_files,
    }
    (data_dir / "manifest.json").write_text(
        f"{canonical_json(manifest)}\n", encoding="utf-8", newline="\n"
    )
    return manifest


def _load_validator(path: str | Path) -> Draft202012Validator:
    schema = json.loads(Path(path).read_text(encoding="utf-8"))
    Draft202012Validator.check_schema(schema)
    return Draft202012Validator(schema)


def _validate_result_file(
    records: list[dict[str, Any]],
    *,
    result_file: Path,
    validator: Draft202012Validator,
    question_by_id: Mapping[str, Mapping[str, Any]],
    question_set_sha256: str,
) -> None:
    run_ids = {record.get("run_id") for record in records}
    if len(run_ids) != 1:
        raise BuildError(f"{result_file}: must contain exactly one run_id")
    models = {canonical_json(record.get("model")) for record in records}
    parameters = {
        canonical_json(record.get("generation_parameters")) for record in records
    }
    if len(models) != 1 or len(parameters) != 1:
        raise BuildError(f"{result_file}: model and generation parameters must be constant")
    seen_keys: set[tuple[str, int]] = set()
    for record in records:
        validate_result(record, validator)
        question_id = record["question_id"]
        question = question_by_id.get(question_id)
        if question is None:
            raise BuildError(f"{result_file}: unknown question_id {question_id!r}")
        result_key = (question_id, record["completion_index"])
        if result_key in seen_keys:
            raise BuildError(f"{result_file}: duplicate result {result_key!r}")
        seen_keys.add(result_key)
        if record["question_set_sha256"] != question_set_sha256:
            raise BuildError(f"{result_file}: question-set digest does not match")
        if record["question_sha256"] != sha256_json(question):
            raise BuildError(f"{result_file}: question digest does not match {question_id}")
        expected_snapshot = {
            "task_type": question["task_type"],
            "prompt": question["prompt"],
            "choices": question["choices"],
            "answer_choice_id": question["answer_choice_id"],
            "task_family": question["metadata"]["task_family"],
            "tags": question["metadata"].get("tags", []),
        }
        if record["question"] != expected_snapshot:
            raise BuildError(f"{result_file}: question snapshot does not match {question_id}")
