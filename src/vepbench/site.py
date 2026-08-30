"""Validate committed benchmark data and assemble Observable source data."""

import hashlib
import json
import shutil
from collections import Counter
from collections.abc import Mapping
from pathlib import Path
from typing import Any

from jsonschema import Draft202012Validator, FormatChecker

from .builder import (
    BuildError,
    canonical_json,
    read_jsonl,
    sha256_file,
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
    """Stage validated data and Observable source files for a static build."""

    questions_file = Path(questions_path)
    questions = read_jsonl(questions_file)
    question_schema = _load_validator(question_schema_path)
    result_validator = _load_validator(result_schema_path)
    question_by_id = {question["question_id"]: question for question in questions}
    if len(question_by_id) != len(questions):
        raise BuildError(f"{questions_file}: duplicate question IDs")
    for question in questions:
        validate_question(question, question_schema)
    current_tasks: dict[str, dict[str, Mapping[str, Any]]] = {}
    for question in questions:
        current_tasks.setdefault(question["metadata"]["task_family"], {})[
            question["question_id"]
        ] = question
    current_task_sizes = Counter(
        question["metadata"]["task_family"] for question in questions
    )

    question_set_sha256 = sha256_file(questions_file)
    result_files: list[dict[str, Any]] = []
    explorer_task_runs: list[dict[str, Any]] = []
    validated_result_files: list[Path] = []
    seen_run_ids: set[str] = set()
    source_results_dir = Path(results_dir)
    if source_results_dir.exists():
        for result_file in sorted(source_results_dir.glob("*.jsonl")):
            records = read_jsonl(result_file)
            validation = _validate_result_file(
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
            result_summary = {
                "path": f"data/results/{result_file.name}",
                "sha256": sha256_file(result_file),
                "records": len(records),
                "run_id": run_id,
                "complete": validation["source_set_complete"] and api_errors == 0,
                "current_question_set": validation["current_question_set"],
                "questions_covered": len(question_ids_in_run),
                "questions_expected": validation["questions_expected"],
                "api_errors": api_errors,
            }
            result_files.append(result_summary)
            task_families = sorted(
                {record["question"]["metadata"]["task_family"] for record in records}
            )
            for task_family in task_families:
                task_records = [
                    record
                    for record in records
                    if record["question"]["metadata"]["task_family"] == task_family
                ]
                task_question_ids = {
                    record["question_id"] for record in task_records
                }
                task_questions = {
                    record["question_id"]: record["question"]
                    for record in task_records
                }
                current_task_version = task_questions == current_tasks.get(
                    task_family, {}
                )
                task_api_errors = sum(
                    record["response"]["status"] == "api_error"
                    for record in task_records
                )
                if current_task_version:
                    task_questions_expected: int | None = current_task_sizes.get(
                        task_family, 0
                    )
                elif validation["source_set_complete"]:
                    task_questions_expected = len(task_question_ids)
                else:
                    task_questions_expected = None
                task_complete = (
                    task_questions_expected is not None
                    and len(task_question_ids) == task_questions_expected
                    and task_api_errors == 0
                )
                explorer_task_runs.append(
                    {
                        "path": result_summary["path"],
                        "sha256": result_summary["sha256"],
                        "run_id": run_id,
                        "task_family": task_family,
                        "complete": task_complete,
                        "current_task_version": current_task_version,
                        "questions_covered": len(task_question_ids),
                        "questions_expected": task_questions_expected,
                        "api_errors": task_api_errors,
                        "records": len(task_records),
                        "records_data": task_records,
                    }
                )

    output_dir = Path(output)
    if output_dir.exists() and any(output_dir.iterdir()):
        raise BuildError(f"refusing to overwrite non-empty site directory {output_dir}")
    output_dir.mkdir(parents=True, exist_ok=True)

    assets = Path(assets_dir)
    for source in sorted(assets.rglob("*")):
        relative = source.relative_to(assets)
        if any(part.startswith(".") for part in relative.parts):
            continue
        if source.is_file():
            destination = output_dir / relative
            destination.parent.mkdir(parents=True, exist_ok=True)
            shutil.copy2(source, destination)

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
    explorer = {
        "schema_version": "1.1",
        "manifest": manifest,
        "questions": questions,
        "task_runs": explorer_task_runs,
    }
    (data_dir / "explorer.json").write_text(
        f"{canonical_json(explorer)}\n", encoding="utf-8", newline="\n"
    )
    return manifest


def _load_validator(path: str | Path) -> Draft202012Validator:
    schema = json.loads(Path(path).read_text(encoding="utf-8"))
    Draft202012Validator.check_schema(schema)
    return Draft202012Validator(schema, format_checker=FormatChecker())


def _validate_result_file(
    records: list[dict[str, Any]],
    *,
    result_file: Path,
    validator: Draft202012Validator,
    question_by_id: Mapping[str, Mapping[str, Any]],
    question_set_sha256: str,
) -> dict[str, Any]:
    for record in records:
        validate_result(record, validator)
    run_ids = {record.get("run_id") for record in records}
    if len(run_ids) != 1:
        raise BuildError(f"{result_file}: must contain exactly one run_id")
    models = {canonical_json(record.get("model")) for record in records}
    parameters = {
        canonical_json(record.get("generation_parameters")) for record in records
    }
    if len(models) != 1 or len(parameters) != 1:
        raise BuildError(f"{result_file}: model and generation parameters must be constant")
    question_set_digests = {record.get("question_set_sha256") for record in records}
    if len(question_set_digests) != 1:
        raise BuildError(f"{result_file}: question-set digest must be constant")
    claimed_question_set_sha256 = next(iter(question_set_digests))
    question_set_sizes = {record.get("question_set_size") for record in records}
    if len(question_set_sizes) != 1:
        raise BuildError(f"{result_file}: question-set size must be constant")
    claimed_question_set_size = next(iter(question_set_sizes))
    seen_keys: set[tuple[str, int]] = set()
    embedded_questions: dict[str, Mapping[str, Any]] = {}
    for record in records:
        question_id = record["question_id"]
        result_key = (question_id, record["completion_index"])
        if result_key in seen_keys:
            raise BuildError(f"{result_file}: duplicate result {result_key!r}")
        seen_keys.add(result_key)
        previous = embedded_questions.setdefault(question_id, record["question"])
        if previous != record["question"]:
            raise BuildError(f"{result_file}: multiple snapshots for {question_id}")

        if claimed_question_set_sha256 == question_set_sha256:
            current = question_by_id.get(question_id)
            if current is None or record["question"] != current:
                raise BuildError(
                    f"{result_file}: snapshot does not match current question set for {question_id}"
                )

    ordered_keys = sorted(seen_keys)
    if [
        (record["question_id"], record["completion_index"]) for record in records
    ] != ordered_keys:
        raise BuildError(f"{result_file}: results must be sorted by question and completion")

    reconstructed = "".join(
        f"{canonical_json(embedded_questions[question_id])}\n"
        for question_id in sorted(embedded_questions)
    ).encode("utf-8")
    embedded_count = len(embedded_questions)
    if embedded_count > claimed_question_set_size:
        raise BuildError(f"{result_file}: more questions than declared question-set size")
    reconstructed_sha256 = hashlib.sha256(reconstructed).hexdigest()
    source_set_complete = embedded_count == claimed_question_set_size
    if source_set_complete and reconstructed_sha256 != claimed_question_set_sha256:
        raise BuildError(f"{result_file}: question-set digest does not match snapshots")
    current_question_set = claimed_question_set_sha256 == question_set_sha256
    if current_question_set and claimed_question_set_size != len(question_by_id):
        raise BuildError(f"{result_file}: current question-set size does not match")
    return {
        "source_set_complete": source_set_complete,
        "current_question_set": current_question_set,
        "questions_expected": claimed_question_set_size,
    }
