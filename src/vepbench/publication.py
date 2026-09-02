"""Build and validate deterministic, browser-friendly benchmark versions."""

import gzip
import hashlib
import io
import json
import math
import re
import shutil
from collections.abc import Iterator, Mapping, Sequence
from datetime import date
from itertools import chain
from pathlib import Path
from typing import Any

import zstandard
from jsonschema import Draft202012Validator, FormatChecker

from .builder import (
    BuildError,
    canonical_json,
    read_jsonl,
    sha256_file,
    sha256_json,
    validate_question,
)
from .evaluator import (
    score_multiple_choice,
    validate_batch_usage_allocations,
    validate_result,
)

VERSION_NAME = re.compile(r"^[a-z0-9](?:[a-z0-9-]{0,62})?$")
SCHEMA_FILES = (
    "question.schema.json",
    "run.schema.json",
    "answer.schema.json",
    "raw-response.schema.json",
    "manifest.schema.json",
)
ZSTD_LEVEL = 3
GZIP_LEVEL = 9
BUCKET_README = """# VEPBench published data

This public bucket is the canonical store for generated VEPBench questions and
evaluation artifacts. `versions/main/manifest.json` is the official readiness
marker. Other named versions are experimental and may be removed without notice.

Schemas are shared from `schemas/`. See the source repository for generation,
validation, and publication code: https://github.com/Open-Athena/VEPBench
"""


def validate_version_name(version_name: str) -> None:
    """Reject names that cannot safely identify one bucket version prefix."""

    if not VERSION_NAME.fullmatch(version_name) or version_name in {".", ".."}:
        raise BuildError("version name must be a lowercase URL-safe slug of at most 63 characters")


def _load_model_catalog(path: Path) -> dict[str, dict[str, str]]:
    """Load display-only model metadata used by published leaderboard rows."""

    try:
        document = json.loads(path.read_text(encoding="utf-8"))
    except OSError as exc:
        raise BuildError(f"cannot read model catalog {path}: {exc}") from exc
    except json.JSONDecodeError as exc:
        raise BuildError(f"{path}: invalid JSON: {exc}") from exc
    if not isinstance(document, dict) or set(document) != {"schema_version", "models"}:
        raise BuildError(f"{path}: model catalog must contain schema_version and models")
    if document["schema_version"] != "1.0" or not isinstance(document["models"], dict):
        raise BuildError(f"{path}: unsupported model catalog")

    models: dict[str, dict[str, str]] = {}
    for model_id, metadata in document["models"].items():
        if not isinstance(model_id, str) or not model_id or not isinstance(metadata, dict):
            raise BuildError(f"{path}: invalid model catalog entry {model_id!r}")
        if set(metadata) != {"family", "release_date"}:
            raise BuildError(f"{path}: model {model_id!r} must contain family and release_date")
        family = metadata["family"]
        release_date = metadata["release_date"]
        if not isinstance(family, str) or not family.strip():
            raise BuildError(f"{path}: model {model_id!r} has an invalid family")
        if not isinstance(release_date, str) or not re.fullmatch(
            r"\d{4}-\d{2}-\d{2}", release_date
        ):
            raise BuildError(f"{path}: model {model_id!r} has an invalid release_date")
        try:
            date.fromisoformat(release_date)
        except ValueError as exc:
            raise BuildError(f"{path}: model {model_id!r} has an invalid release_date") from exc
        models[model_id] = {"family": family.strip(), "release_date": release_date}
    return models


def _as_paths(value: str | Path | Sequence[str | Path]) -> list[Path]:
    values = [value] if isinstance(value, (str, Path)) else list(value)
    if not values:
        raise BuildError("at least one path is required")
    return [Path(path) for path in values]


def _evaluation_profile(questions: Sequence[Mapping[str, Any]]) -> str:
    return ",".join(
        sorted(
            {
                (
                    f"{question['metadata']['task_family']}:"
                    f"{question['provenance']['template_id']}@"
                    f"{question['provenance']['template_version']}"
                )
                for question in questions
            }
        )
    )


def _task_set(questions: list[dict[str, Any]], content: bytes) -> dict[str, Any]:
    return {
        "questions": questions,
        "question_by_id": {question["question_id"]: question for question in questions},
        "question_digest": {
            question["question_id"]: sha256_json(question) for question in questions
        },
        "question_set_sha256": hashlib.sha256(content).hexdigest(),
        "question_set_size": len(questions),
        "evaluation_profile": _evaluation_profile(questions),
    }


def _task_sets_from_questions(
    questions: Sequence[dict[str, Any]],
) -> dict[str, dict[str, Any]]:
    grouped: dict[str, list[dict[str, Any]]] = {}
    for question in questions:
        grouped.setdefault(question["metadata"]["task_family"], []).append(question)
    return {
        task_family: _task_set(
            task_questions,
            b"".join(f"{canonical_json(question)}\n".encode() for question in task_questions),
        )
        for task_family, task_questions in grouped.items()
    }


def build_version(
    *,
    questions_path: str | Path | Sequence[str | Path],
    results_dir: str | Path | Sequence[str | Path],
    result_schema_path: str | Path,
    schemas_dir: str | Path,
    model_catalog_path: str | Path | None = None,
    output: str | Path,
    version_name: str,
) -> dict[str, Any]:
    """Convert local evaluation files into one complete bucket-shaped tree."""

    validate_version_name(version_name)
    output_dir = Path(output)
    if output_dir.exists() and any(output_dir.iterdir()):
        raise BuildError(f"refusing to overwrite non-empty publication directory {output_dir}")

    schema_source = Path(schemas_dir)
    schema_validators = {name: _load_validator(schema_source / name) for name in SCHEMA_FILES}
    result_validator = _load_validator(result_schema_path)
    model_catalog = (
        _load_model_catalog(Path(model_catalog_path)) if model_catalog_path is not None else None
    )

    question_validator = schema_validators["question.schema.json"]
    question_files = _as_paths(questions_path)
    questions: list[dict[str, Any]] = []
    task_sets: dict[str, dict[str, Any]] = {}
    for questions_file in question_files:
        file_questions = read_jsonl(questions_file)
        question_ids = [question["question_id"] for question in file_questions]
        if question_ids != sorted(question_ids):
            raise BuildError(f"{questions_file}: questions must be sorted by question_id")
        if len(question_ids) != len(set(question_ids)):
            raise BuildError(f"{questions_file}: duplicate question IDs")
        for question in file_questions:
            validate_question(question, question_validator)
        file_bytes = b"".join(
            f"{canonical_json(question)}\n".encode() for question in file_questions
        )
        if file_bytes != questions_file.read_bytes():
            raise BuildError(
                f"{questions_file}: questions must use canonical JSON, UTF-8, and LF endings"
            )
        task_families = {question["metadata"]["task_family"] for question in file_questions}
        if len(task_families) != 1:
            raise BuildError(f"{questions_file}: each question file must contain one task family")
        task_family = next(iter(task_families))
        if task_family in task_sets:
            raise BuildError(f"multiple question files define task family {task_family!r}")
        task_sets[task_family] = _task_set(file_questions, file_bytes)
        questions.extend(file_questions)

    questions.sort(key=lambda question: question["question_id"])
    question_ids = [question["question_id"] for question in questions]
    if len(question_ids) != len(set(question_ids)):
        raise BuildError("question files contain duplicate question IDs")
    question_set_bytes = b"".join(
        f"{canonical_json(question)}\n".encode() for question in questions
    )
    question_set_sha256 = hashlib.sha256(question_set_bytes).hexdigest()
    question_by_id = {question["question_id"]: question for question in questions}
    question_digest = {
        question_id: sha256_json(question) for question_id, question in question_by_id.items()
    }

    output_dir.mkdir(parents=True, exist_ok=True)
    (output_dir / "README.md").write_text(BUCKET_README, encoding="utf-8", newline="\n")
    published_schemas = output_dir / "schemas"
    published_schemas.mkdir()
    schema_manifest: dict[str, dict[str, Any]] = {}
    for name in SCHEMA_FILES:
        source = schema_source / name
        destination = published_schemas / name
        shutil.copyfile(source, destination)
        schema_manifest[name] = {
            "path": f"schemas/{name}",
            "sha256": sha256_file(destination),
            "bytes": destination.stat().st_size,
        }

    version_dir = output_dir / "versions" / version_name
    answers_dir = version_dir / "answers"
    outcomes_dir = version_dir / "outcomes"
    raw_dir = version_dir / "raw"
    answers_dir.mkdir(parents=True)
    outcomes_dir.mkdir()
    raw_dir.mkdir()

    question_artifact = _write_zstd(version_dir / "questions.jsonl.zst", question_set_bytes)

    question_index = {
        "schema_version": "1.0",
        "question_set_sha256": question_set_sha256,
        "question_set_size": len(questions),
        "questions": [
            {"question_sha256": question_digest[question["question_id"]], **question}
            for question in questions
        ],
    }
    question_index_bytes = _write_json(version_dir / "question-index.json", question_index)

    run_records: list[dict[str, Any]] = []
    answer_artifacts: list[dict[str, Any]] = []
    outcome_artifacts: list[dict[str, Any]] = []
    raw_artifacts: list[dict[str, Any]] = []
    configuration_keys: set[str] = set()
    seen_run_ids: set[str] = set()
    result_files = sorted(
        chain.from_iterable(
            source_results.glob("*.jsonl")
            for source_results in _as_paths(results_dir)
            if source_results.exists()
        )
    )
    for result_file in result_files:
        run = _convert_run(
            result_file=result_file,
            result_validator=result_validator,
            answer_validator=schema_validators["answer.schema.json"],
            raw_validator=schema_validators["raw-response.schema.json"],
            task_sets=task_sets,
            model_catalog=model_catalog,
            version_dir=version_dir,
        )
        run_id = run["record"]["run_id"]
        if run_id in seen_run_ids:
            raise BuildError(f"{result_file}: duplicate run_id {run_id!r}")
        seen_run_ids.add(run_id)
        configuration_key = run["record"]["configuration_key"]
        if configuration_key in configuration_keys:
            raise BuildError(
                f"{result_file}: another run has configuration key {configuration_key!r}"
            )
        configuration_keys.add(configuration_key)
        run_records.append(run["record"])
        answer_artifacts.extend(run["answers"])
        outcome_artifacts.append(run["outcomes"])
        raw_artifacts.append(run["raw"])

    run_records.sort(key=lambda run: run["run_id"])
    for run in run_records:
        errors = list(schema_validators["run.schema.json"].iter_errors(run))
        if errors:
            raise BuildError(_schema_error(run["run_id"], errors))
    runs = {
        "schema_version": "1.0",
        "question_set_sha256": question_set_sha256,
        "question_set_size": len(questions),
        "leaderboard": {
            "aggregation_method": "task_macro_average_v0",
            "evaluation_profiles": [
                {
                    "task_family": task_family,
                    "evaluation_profile": task_set["evaluation_profile"],
                }
                for task_family, task_set in sorted(task_sets.items())
            ],
        },
        "runs": run_records,
    }
    runs_bytes = _write_json(version_dir / "runs.json", runs)

    manifest = {
        "schema_version": "1.0",
        "version_name": version_name,
        "question_set_sha256": question_set_sha256,
        "question_set_size": len(questions),
        "schemas": schema_manifest,
        "artifacts": {
            "questions": {
                "path": f"versions/{version_name}/questions.jsonl.zst",
                "records": len(questions),
                **question_artifact,
            },
            "runs": _plain_artifact(
                f"versions/{version_name}/runs.json", runs_bytes, len(run_records)
            ),
            "question_index": _plain_artifact(
                f"versions/{version_name}/question-index.json",
                question_index_bytes,
                len(questions),
            ),
            "answers": sorted(answer_artifacts, key=lambda item: item["path"]),
            "outcomes": sorted(outcome_artifacts, key=lambda item: item["path"]),
            "raw": sorted(raw_artifacts, key=lambda item: item["path"]),
        },
    }
    manifest_errors = list(schema_validators["manifest.schema.json"].iter_errors(manifest))
    if manifest_errors:
        raise BuildError(_schema_error("manifest", manifest_errors))
    _write_json(version_dir / "manifest.json", manifest)
    validate_version(output_dir, version_name=version_name)
    return manifest


def validate_version(root: str | Path, *, version_name: str) -> dict[str, Any]:
    """Validate every artifact and cross-reference in a local bucket tree."""

    validate_version_name(version_name)
    root_dir = Path(root)
    if (root_dir / "README.md").read_text(encoding="utf-8") != BUCKET_README:
        raise BuildError("bucket README is missing or does not match the publication contract")
    validators = {name: _load_validator(root_dir / "schemas" / name) for name in SCHEMA_FILES}
    version_dir = root_dir / "versions" / version_name
    manifest_path = version_dir / "manifest.json"
    manifest = json.loads(manifest_path.read_text(encoding="utf-8"))
    errors = list(validators["manifest.schema.json"].iter_errors(manifest))
    if errors:
        raise BuildError(_schema_error(manifest_path, errors))
    if manifest["version_name"] != version_name:
        raise BuildError(f"{manifest_path}: version name does not match its path")

    for name, descriptor in manifest["schemas"].items():
        path = root_dir / descriptor["path"]
        payload = path.read_bytes()
        if (
            hashlib.sha256(payload).hexdigest() != descriptor["sha256"]
            or len(payload) != descriptor["bytes"]
        ):
            raise BuildError(f"{path}: schema digest or size mismatch")
        if path.name != name:
            raise BuildError(f"{manifest_path}: schema path does not match {name}")

    question_descriptor = manifest["artifacts"]["questions"]
    question_bytes = _verify_compressed(root_dir, question_descriptor, "zstd")
    questions = _read_jsonl_bytes(question_bytes, question_descriptor["path"])
    if len(questions) != question_descriptor["records"]:
        raise BuildError("question artifact record count does not match its contents")
    question_ids = [question["question_id"] for question in questions]
    if question_ids != sorted(question_ids) or len(question_ids) != len(set(question_ids)):
        raise BuildError("published questions must have unique sorted question IDs")
    for question in questions:
        validate_question(question, validators["question.schema.json"])
    if hashlib.sha256(question_bytes).hexdigest() != manifest["question_set_sha256"]:
        raise BuildError("manifest question-set digest does not match questions")
    if len(questions) != manifest["question_set_size"]:
        raise BuildError("manifest question-set size does not match questions")
    question_by_id = {question["question_id"]: question for question in questions}
    task_sets = _task_sets_from_questions(questions)

    runs_descriptor = manifest["artifacts"]["runs"]
    runs_bytes = _verify_plain(root_dir / runs_descriptor["path"], runs_descriptor)
    runs_document = json.loads(runs_bytes)
    if len(runs_bytes) > 1_000_000:
        raise BuildError("runs.json exceeds the 1 MB leaderboard limit")
    if (
        runs_document.get("question_set_sha256") != manifest["question_set_sha256"]
        or runs_document.get("question_set_size") != manifest["question_set_size"]
    ):
        raise BuildError("runs.json does not match the manifest question set")
    expected_leaderboard = {
        "aggregation_method": "task_macro_average_v0",
        "evaluation_profiles": [
            {
                "task_family": task_family,
                "evaluation_profile": task_set["evaluation_profile"],
            }
            for task_family, task_set in sorted(task_sets.items())
        ],
    }
    if "leaderboard" in runs_document and runs_document["leaderboard"] != expected_leaderboard:
        raise BuildError("runs.json has invalid leaderboard aggregation metadata")
    if len(task_sets) > 1 and "leaderboard" not in runs_document:
        raise BuildError("multi-task runs.json is missing leaderboard aggregation metadata")
    runs = runs_document["runs"]
    if len(runs) != runs_descriptor["records"]:
        raise BuildError("run artifact record count does not match its contents")
    run_by_id = {run["run_id"]: run for run in runs}
    if len(run_by_id) != len(runs):
        raise BuildError("runs.json contains duplicate run IDs")
    configuration_keys = {run["configuration_key"] for run in runs}
    if len(configuration_keys) != len(runs):
        raise BuildError("runs.json contains duplicate model configuration keys")
    run_task_families: dict[str, str] = {}
    for run in runs:
        errors = list(validators["run.schema.json"].iter_errors(run))
        if errors:
            raise BuildError(_schema_error(run["run_id"], errors))
        task_family = run.get("task_family")
        if len(task_sets) > 1 and task_family is None:
            raise BuildError(f"multi-task run {run['run_id']!r} is missing its task family")
        if task_family is None:
            matching_families = [
                candidate_family
                for candidate_family, task_set in task_sets.items()
                if run["question_set_sha256"] == task_set["question_set_sha256"]
                and run["question_set_size"] == task_set["question_set_size"]
                and run["evaluation_profile"] == task_set["evaluation_profile"]
            ]
            if len(matching_families) != 1:
                raise BuildError(f"run {run['run_id']!r} has no unique task family")
            task_family = matching_families[0]
        task_set = task_sets.get(task_family)
        if task_set is None or (
            run["question_set_sha256"] != task_set["question_set_sha256"]
            or run["question_set_size"] != task_set["question_set_size"]
            or run["evaluation_profile"] != task_set["evaluation_profile"]
        ):
            raise BuildError(f"run {run['run_id']!r} uses the wrong task question set")
        run_task_families[run["run_id"]] = task_family
    if version_name == "main":
        if not runs:
            raise BuildError("versions/main must contain at least one complete run")
        if any(not run["coverage"]["complete"] for run in runs):
            raise BuildError("versions/main may contain only complete runs without API errors")

    index_descriptor = manifest["artifacts"]["question_index"]
    index_bytes = _verify_plain(root_dir / index_descriptor["path"], index_descriptor)
    index = json.loads(index_bytes)
    if index["question_set_sha256"] != manifest["question_set_sha256"]:
        raise BuildError("question index has the wrong question-set digest")
    indexed = index["questions"]
    if len(indexed) != index_descriptor["records"]:
        raise BuildError("question-index record count does not match its contents")
    if [entry["question_id"] for entry in indexed] != question_ids:
        raise BuildError("question index does not match published questions")
    for entry, question in zip(indexed, questions, strict=True):
        digest = entry.pop("question_sha256", None)
        try:
            if entry != question or digest != sha256_json(question):
                raise BuildError(f"question index mismatch for {question['question_id']}")
        finally:
            if digest is not None:
                entry["question_sha256"] = digest

    answers_seen: set[tuple[str, str, int]] = set()
    normalized_answer_state: dict[
        tuple[str, str, int],
        tuple[str, Mapping[str, Any] | None, Mapping[str, Any]],
    ] = {}
    answer_usage_records: list[dict[str, Any]] = []
    expected_outcomes: dict[tuple[str, str], bool | None] = {}
    per_run_answers: dict[str, int] = dict.fromkeys(run_by_id, 0)
    per_run_stats: dict[str, dict[str, Any]] = {
        run_id: {
            "completed": 0,
            "api_errors": 0,
            "correct": 0,
            "format_failures": 0,
            "token_values": [],
            "cost_values": [],
            "tokens_complete": True,
            "cost_complete": True,
        }
        for run_id in run_by_id
    }
    for descriptor in manifest["artifacts"]["answers"]:
        answer_bytes = _verify_compressed(root_dir, descriptor, "gzip")
        if descriptor["records"] != 1:
            raise BuildError(f"{descriptor['path']}: answer artifact must contain one record")
        answer = json.loads(answer_bytes)
        errors = list(validators["answer.schema.json"].iter_errors(answer))
        if errors:
            raise BuildError(_schema_error(descriptor["path"], errors))
        key = (answer["run_id"], answer["question_id"], answer["completion_index"])
        if key in answers_seen:
            raise BuildError(f"duplicate normalized answer {key!r}")
        if answer["completion_index"] != 0:
            raise BuildError(f"answer {key!r} is not the single canonical completion")
        answers_seen.add(key)
        normalized_answer_state[key] = (
            answer["response"]["status"],
            answer["error"],
            answer["usage"],
        )
        answer_usage_records.append(
            {
                "run_id": answer["run_id"],
                "question_id": answer["question_id"],
                "usage": answer["usage"],
            }
        )
        expected_outcomes[(answer["run_id"], answer["question_id"])] = answer["scoring"]["correct"]
        run = run_by_id.get(answer["run_id"])
        answer_question = question_by_id.get(answer["question_id"])
        if run is None or answer_question is None:
            raise BuildError(f"answer {key!r} references an unknown run or question")
        if answer_question["metadata"]["task_family"] != run_task_families[run["run_id"]]:
            raise BuildError(f"answer {key!r} belongs to the wrong task family")
        if answer["question_sha256"] != sha256_json(answer_question):
            raise BuildError(f"answer {key!r} has the wrong question digest")
        _validate_answer_scoring(answer, answer_question)
        expected_path = (
            f"versions/{version_name}/answers/{answer['run_id']}/{answer['question_id']}.json.gz"
        )
        if descriptor["path"] != expected_path:
            raise BuildError(f"answer {key!r} is stored at the wrong path")
        per_run_answers[answer["run_id"]] += 1
        stats = per_run_stats[answer["run_id"]]
        status = answer["response"]["status"]
        stats["completed"] += status == "completed"
        stats["api_errors"] += status == "api_error"
        stats["correct"] += answer["scoring"]["correct"] is True
        stats["format_failures"] += answer["scoring"]["parse_error"] is not None
        usage_tokens, usage_cost = _usage_totals(answer["usage"])
        if status == "completed" and usage_tokens is not None:
            stats["token_values"].append(usage_tokens)
        else:
            stats["tokens_complete"] = False
        if status == "completed" and usage_cost is not None:
            stats["cost_values"].append(usage_cost)
        else:
            stats["cost_complete"] = False

    validate_batch_usage_allocations(
        answer_usage_records, context=f"versions/{version_name} answers"
    )

    outcome_runs_seen: set[str] = set()
    for descriptor in manifest["artifacts"].get("outcomes", []):
        outcome_bytes = _verify_compressed(root_dir, descriptor, "gzip")
        outcome_index = json.loads(outcome_bytes)
        if (
            not isinstance(outcome_index, dict)
            or set(outcome_index)
            != {
                "schema_version",
                "run_id",
                "question_set_sha256",
                "question_set_size",
                "outcomes",
            }
            or outcome_index["schema_version"] != "1.0"
        ):
            raise BuildError(f"{descriptor['path']}: invalid outcome index")
        run_id = outcome_index["run_id"]
        run = run_by_id.get(run_id)
        if run is None or run_id in outcome_runs_seen:
            raise BuildError(f"{descriptor['path']}: outcome index has an unknown or duplicate run")
        outcome_runs_seen.add(run_id)
        expected_relative_path = f"outcomes/{run_id}.json.gz"
        expected_path = f"versions/{version_name}/{expected_relative_path}"
        if (
            descriptor["path"] != expected_path
            or run.get("outcome_index_path") != expected_relative_path
        ):
            raise BuildError(f"{descriptor['path']}: outcome index is stored at the wrong path")
        if (
            outcome_index["question_set_sha256"] != run["question_set_sha256"]
            or outcome_index["question_set_size"] != run["question_set_size"]
        ):
            raise BuildError(f"{descriptor['path']}: outcome index has the wrong question set")
        outcomes = outcome_index["outcomes"]
        if not isinstance(outcomes, list) or len(outcomes) != descriptor["records"]:
            raise BuildError(f"{descriptor['path']}: outcome record count mismatch")
        question_ids_for_run = sorted(
            question_id
            for candidate_run_id, question_id in expected_outcomes
            if candidate_run_id == run_id
        )
        if [row.get("question_id") for row in outcomes if isinstance(row, dict)] != (
            question_ids_for_run
        ) or len(outcomes) != len(question_ids_for_run):
            raise BuildError(f"{descriptor['path']}: outcomes do not match run answers")
        for row in outcomes:
            if (
                not isinstance(row, dict)
                or set(row) != {"question_id", "correct"}
                or (row["correct"] is not None and not isinstance(row["correct"], bool))
            ):
                raise BuildError(f"{descriptor['path']}: invalid outcome record")
            outcome_key = (run_id, row["question_id"])
            if (
                outcome_key not in expected_outcomes
                or expected_outcomes[outcome_key] != row["correct"]
            ):
                raise BuildError(f"{descriptor['path']}: outcome disagrees with its answer")

    runs_with_outcome_paths = {
        run_id for run_id, run in run_by_id.items() if "outcome_index_path" in run
    }
    if outcome_runs_seen != runs_with_outcome_paths:
        raise BuildError("run outcome indexes do not match run metadata")

    raw_seen: set[tuple[str, str, int]] = set()
    per_run_raw: dict[str, int] = dict.fromkeys(run_by_id, 0)
    for descriptor in manifest["artifacts"]["raw"]:
        archive_run_id: str | None = None
        previous_key: tuple[str, int] | None = None
        raw_records = 0
        for envelope in _iter_compressed_jsonl(root_dir, descriptor, "zstd"):
            raw_records += 1
            errors = list(validators["raw-response.schema.json"].iter_errors(envelope))
            if errors:
                raise BuildError(_schema_error(descriptor["path"], errors))
            if archive_run_id is None:
                archive_run_id = envelope["run_id"]
            elif envelope["run_id"] != archive_run_id:
                raise BuildError(f"{descriptor['path']}: raw archive must contain one run")
            if envelope["run_id"] not in run_by_id or envelope["question_id"] not in question_by_id:
                raise BuildError("raw response references an unknown run or question")
            raw_key = (
                envelope["run_id"],
                envelope["question_id"],
                envelope["completion_index"],
            )
            if raw_key in raw_seen or raw_key not in answers_seen:
                raise BuildError(f"raw response {raw_key!r} has no unique normalized answer")
            ordered_key = (envelope["question_id"], envelope["completion_index"])
            if previous_key is not None and ordered_key <= previous_key:
                raise BuildError(f"{descriptor['path']}: raw responses are not uniquely sorted")
            previous_key = ordered_key
            raw_seen.add(raw_key)
            per_run_raw[envelope["run_id"]] += 1
            question = question_by_id[envelope["question_id"]]
            if envelope["question_sha256"] != sha256_json(question):
                raise BuildError(f"raw response {raw_key!r} has the wrong question digest")
            request_body = {
                "model": run_by_id[envelope["run_id"]]["model"]["model_id"],
                "messages": [{"role": "user", "content": question["prompt"]}],
                **run_by_id[envelope["run_id"]]["generation_parameters"],
            }
            if envelope["request"]["body_sha256"] != sha256_json(request_body):
                raise BuildError(f"raw response {raw_key!r} has the wrong request digest")
            answer_status, answer_error, answer_usage = normalized_answer_state[raw_key]
            if (
                envelope["response"]["status"] != answer_status
                or envelope["error"] != answer_error
                or ("usage" in envelope and envelope["usage"] != answer_usage)
            ):
                raise BuildError(f"raw response {raw_key!r} disagrees with its normalized answer")
            if envelope["response"]["status"] == "completed" and not isinstance(
                envelope["response"]["raw"], Mapping
            ):
                raise BuildError(f"raw response {raw_key!r} is missing its provider payload")

        if raw_records != descriptor["records"]:
            raise BuildError(f"{descriptor['path']}: raw record count mismatch")
        if archive_run_id is None:
            raise BuildError(f"{descriptor['path']}: raw archive is empty")
        expected_raw_path = f"versions/{version_name}/raw/{archive_run_id}.jsonl.zst"
        if descriptor["path"] != expected_raw_path:
            raise BuildError(f"{descriptor['path']}: raw archive is stored at the wrong path")
        run = run_by_id.get(archive_run_id)
        if run is None or run["raw_archive"] != descriptor:
            raise BuildError(f"{descriptor['path']}: run metadata does not match raw archive")

    for run_id, run in run_by_id.items():
        if per_run_answers[run_id] != run["coverage"]["attempted"]:
            raise BuildError(f"run {run_id!r} answer coverage does not match metadata")
        if per_run_raw[run_id] != run["coverage"]["attempted"]:
            raise BuildError(f"run {run_id!r} raw coverage does not match metadata")
        stats = per_run_stats[run_id]
        expected_coverage = {
            "attempted": per_run_answers[run_id],
            "completed": stats["completed"],
            "api_errors": stats["api_errors"],
            "missing": run["question_set_size"] - per_run_answers[run_id],
            "complete": (
                per_run_answers[run_id] == run["question_set_size"] and stats["api_errors"] == 0
            ),
        }
        accuracy = stats["correct"] / stats["completed"] if stats["completed"] else None
        expected_metrics = {
            "scored": stats["completed"],
            "correct": stats["correct"],
            "accuracy": accuracy,
            "format_failures": stats["format_failures"],
        }
        if "total_tokens" in run["metrics"] or "total_cost_usd" in run["metrics"]:
            expected_metrics.update(
                {
                    "total_tokens": (
                        sum(stats["token_values"])
                        if stats["completed"] and stats["tokens_complete"]
                        else None
                    ),
                    "total_cost_usd": (
                        math.fsum(stats["cost_values"])
                        if stats["completed"] and stats["cost_complete"]
                        else None
                    ),
                }
            )
        if run["coverage"] != expected_coverage or run["metrics"] != expected_metrics:
            raise BuildError(f"run {run_id!r} aggregate metadata does not match answers")
    if answers_seen != raw_seen:
        raise BuildError("normalized answers and raw response envelopes do not match")

    described_paths = {
        descriptor["path"]
        for descriptor in (
            manifest["artifacts"]["questions"],
            manifest["artifacts"]["runs"],
            manifest["artifacts"]["question_index"],
            *manifest["artifacts"]["answers"],
            *manifest["artifacts"].get("outcomes", []),
            *manifest["artifacts"]["raw"],
        )
    }
    actual_paths = {
        path.relative_to(root_dir).as_posix()
        for path in version_dir.rglob("*")
        if path.is_file() and path.name != "manifest.json"
    }
    if actual_paths != described_paths:
        raise BuildError("version contains missing or unmanifested artifacts")
    return manifest


def promote_version(
    *,
    source_root: str | Path,
    source_version: str,
    output: str | Path,
) -> dict[str, Any]:
    """Build a local future ``main`` tree from one validated named version."""

    validate_version_name(source_version)
    if source_version == "main":
        raise BuildError("promotion source must be a named experimental version")
    source = Path(source_root)
    manifest = validate_version(source, version_name=source_version)
    output_dir = Path(output)
    if output_dir.exists() and any(output_dir.iterdir()):
        raise BuildError(f"refusing to overwrite non-empty promotion directory {output_dir}")
    output_dir.mkdir(parents=True, exist_ok=True)
    shutil.copyfile(source / "README.md", output_dir / "README.md")
    shutil.copytree(source / "schemas", output_dir / "schemas")
    destination = output_dir / "versions" / "main"
    shutil.copytree(source / "versions" / source_version, destination)

    promoted = json.loads(canonical_json(manifest))
    promoted["version_name"] = "main"
    old_prefix = f"versions/{source_version}/"
    new_prefix = "versions/main/"
    artifacts = promoted["artifacts"]
    descriptors = [
        artifacts["questions"],
        artifacts["runs"],
        artifacts["question_index"],
        *artifacts["answers"],
        *artifacts.get("outcomes", []),
        *artifacts["raw"],
    ]
    for descriptor in descriptors:
        if not descriptor["path"].startswith(old_prefix):
            raise BuildError("source manifest contains an artifact outside its version")
        descriptor["path"] = new_prefix + descriptor["path"].removeprefix(old_prefix)
    runs_path = destination / "runs.json"
    runs_document = json.loads(runs_path.read_text(encoding="utf-8"))
    for run in runs_document["runs"]:
        raw_path = run["raw_archive"]["path"]
        if not raw_path.startswith(old_prefix):
            raise BuildError("source run metadata points outside its version")
        run["raw_archive"]["path"] = new_prefix + raw_path.removeprefix(old_prefix)
    runs_bytes = _write_json(runs_path, runs_document)
    promoted["artifacts"]["runs"] = _plain_artifact(
        "versions/main/runs.json", runs_bytes, len(runs_document["runs"])
    )
    _write_json(destination / "manifest.json", promoted)
    return validate_version(output_dir, version_name="main")


def _validate_answer_scoring(answer: Mapping[str, Any], question: Mapping[str, Any]) -> None:
    status = answer["response"]["status"]
    if status == "completed":
        expected = score_multiple_choice(
            answer["response"]["content"],
            {choice["choice_id"] for choice in question["choices"]},
            question["answer_choice_id"],
        )
        expected_scoring = {
            "metric": "exact_match",
            "parsed_answer": expected.parsed_answer,
            "value": expected.value,
            "correct": expected.correct,
            "parse_error": expected.parse_error,
        }
        if answer["scoring"] != expected_scoring or answer["error"] is not None:
            raise BuildError(
                f"answer {answer['run_id']}/{answer['question_id']} has an invalid score"
            )
    else:
        expected_scoring = {
            "metric": "exact_match",
            "parsed_answer": None,
            "value": None,
            "correct": None,
            "parse_error": None,
        }
        completion_fields = ("content", "reasoning", "finish_reason")
        if (
            answer["scoring"] != expected_scoring
            or any(answer["response"][field] is not None for field in completion_fields)
            or not isinstance(answer["error"], Mapping)
        ):
            raise BuildError(
                f"answer {answer['run_id']}/{answer['question_id']} has an invalid API error"
            )


def _convert_run(
    *,
    result_file: Path,
    result_validator: Draft202012Validator,
    answer_validator: Draft202012Validator,
    raw_validator: Draft202012Validator,
    task_sets: Mapping[str, Mapping[str, Any]],
    model_catalog: Mapping[str, Mapping[str, str]] | None,
    version_dir: Path,
) -> dict[str, Any]:
    records = _iter_jsonl_file(result_file)
    try:
        first_record = next(records)
    except StopIteration as exc:
        raise BuildError(f"{result_file}: no result records found") from exc
    validate_result(first_record, result_validator)
    task_family = first_record["question"]["metadata"]["task_family"]
    task_set = task_sets.get(task_family)
    if task_set is None:
        raise BuildError(f"{result_file}: unknown task family {task_family!r}")
    question_by_id = task_set["question_by_id"]
    question_digest = task_set["question_digest"]
    question_set_sha256 = task_set["question_set_sha256"]
    question_set_size = task_set["question_set_size"]
    evaluation_profile = task_set["evaluation_profile"]
    run_id = first_record["run_id"]
    model = json.loads(canonical_json(first_record["model"]))
    generation_parameters = json.loads(canonical_json(first_record["generation_parameters"]))
    model_json = canonical_json(model)
    parameters_json = canonical_json(generation_parameters)
    configuration_key = "cfg-" + sha256_json(
        {
            "model": model,
            "generation_parameters": generation_parameters,
            "evaluation_profile": evaluation_profile,
        }
    )
    published_model = dict(model)
    if model_catalog is not None:
        model_metadata = model_catalog.get(model["model_id"])
        if model_metadata is None:
            raise BuildError(
                f"{result_file}: model {model['model_id']!r} is missing from the model catalog"
            )
        published_model.update(model_metadata)
    answer_descriptors: list[dict[str, Any]] = []
    outcome_rows: list[dict[str, Any]] = []
    completed = api_errors = correct = format_failures = 0
    token_values: list[int] = []
    cost_values: list[float] = []
    tokens_complete = True
    cost_complete = True
    usage_records: list[dict[str, Any]] = []
    record_count = 0
    started_at: str | None = None
    completed_at: str | None = None
    previous_key: tuple[str, int] | None = None
    run_answer_dir = version_dir / "answers" / run_id
    run_answer_dir.mkdir()
    raw_path = version_dir / "raw" / f"{run_id}.jsonl.zst"
    raw_content_digest = hashlib.sha256()
    raw_content_bytes = 0
    with raw_path.open("wb") as raw_file:  # noqa: SIM117 - writer depends on raw_file
        with zstandard.ZstdCompressor(level=ZSTD_LEVEL).stream_writer(
            raw_file, closefd=False
        ) as raw_writer:
            for record_index, record in enumerate(chain((first_record,), records)):
                if record_index:
                    validate_result(record, result_validator)
                if record["run_id"] != run_id:
                    raise BuildError(f"{result_file}: must contain exactly one run ID")
                if (
                    canonical_json(record["model"]) != model_json
                    or canonical_json(record["generation_parameters"]) != parameters_json
                ):
                    raise BuildError(
                        f"{result_file}: model and generation parameters must be constant"
                    )
                ordered_key = (record["question_id"], record["completion_index"])
                if previous_key is not None and ordered_key <= previous_key:
                    raise BuildError(
                        f"{result_file}: results must have unique sorted question keys"
                    )
                previous_key = ordered_key
                question_id = record["question_id"]
                if (
                    record["question_set_sha256"] != question_set_sha256
                    or record["question_set_size"] != question_set_size
                    or question_by_id.get(question_id) != record["question"]
                    or question_digest.get(question_id) != record["question_sha256"]
                ):
                    raise BuildError(
                        f"{result_file}: result does not match the published question set"
                    )

                record_count += 1
                status = record["response"]["status"]
                completed += status == "completed"
                api_errors += status == "api_error"
                correct += record["scoring"]["correct"] is True
                format_failures += record["scoring"]["parse_error"] is not None
                usage_tokens, usage_cost = _usage_totals(record["usage"])
                if status == "completed" and usage_tokens is not None:
                    token_values.append(usage_tokens)
                else:
                    tokens_complete = False
                if status == "completed" and usage_cost is not None:
                    cost_values.append(usage_cost)
                else:
                    cost_complete = False
                usage_records.append(
                    {
                        "run_id": run_id,
                        "question_id": question_id,
                        "usage": record["usage"],
                    }
                )
                evaluated_at = record["evaluated_at"]
                started_at = evaluated_at if started_at is None else min(started_at, evaluated_at)
                completed_at = (
                    evaluated_at if completed_at is None else max(completed_at, evaluated_at)
                )
                answer = {
                    "schema_version": "1.0",
                    "run_id": run_id,
                    "question_id": question_id,
                    "question_sha256": record["question_sha256"],
                    "completion_index": record["completion_index"],
                    "evaluated_at": evaluated_at,
                    "response": {
                        key: record["response"][key]
                        for key in (
                            "status",
                            "content",
                            "reasoning",
                            "finish_reason",
                            "latency_seconds",
                        )
                    },
                    "usage": record["usage"],
                    "scoring": record["scoring"],
                    "error": record["error"],
                    "raw_archive_path": f"raw/{run_id}.jsonl.zst",
                }
                answer_errors = list(answer_validator.iter_errors(answer))
                if answer_errors:
                    raise BuildError(_schema_error(question_id, answer_errors))
                answer_path = run_answer_dir / f"{question_id}.json.gz"
                answer_bytes = f"{canonical_json(answer)}\n".encode()
                artifact = _write_gzip(answer_path, answer_bytes)
                answer_descriptors.append(
                    {
                        "path": str(answer_path.relative_to(version_dir.parent.parent)),
                        "records": 1,
                        **artifact,
                    }
                )
                outcome_rows.append(
                    {
                        "question_id": question_id,
                        "correct": record["scoring"]["correct"],
                    }
                )

                envelope = {
                    "schema_version": "1.0",
                    "run_id": run_id,
                    "question_id": question_id,
                    "question_sha256": record["question_sha256"],
                    "completion_index": record["completion_index"],
                    "evaluated_at": evaluated_at,
                    "request": {
                        "body_sha256": sha256_json(
                            {
                                "model": model["model_id"],
                                "messages": [
                                    {
                                        "role": "user",
                                        "content": record["question"]["prompt"],
                                    }
                                ],
                                **generation_parameters,
                            }
                        )
                    },
                    "response": {
                        "status": status,
                        "raw": record["response"]["raw"],
                    },
                    "usage": record["usage"],
                    "error": record["error"],
                }
                raw_errors = list(raw_validator.iter_errors(envelope))
                if raw_errors:
                    raise BuildError(_schema_error(question_id, raw_errors))
                envelope_bytes = f"{canonical_json(envelope)}\n".encode()
                raw_writer.write(envelope_bytes)
                raw_content_digest.update(envelope_bytes)
                raw_content_bytes += len(envelope_bytes)

    validate_batch_usage_allocations(usage_records, context=str(result_file))

    outcome_document = {
        "schema_version": "1.0",
        "run_id": run_id,
        "question_set_sha256": question_set_sha256,
        "question_set_size": question_set_size,
        "outcomes": outcome_rows,
    }
    outcome_path = version_dir / "outcomes" / f"{run_id}.json.gz"
    outcome_bytes = f"{canonical_json(outcome_document)}\n".encode()
    outcome_artifact = {
        "path": str(outcome_path.relative_to(version_dir.parent.parent)),
        "records": len(outcome_rows),
        **_write_gzip(outcome_path, outcome_bytes),
    }
    raw_artifact = {
        "path": str(raw_path.relative_to(version_dir.parent.parent)),
        "records": record_count,
        "content_sha256": raw_content_digest.hexdigest(),
        "content_bytes": raw_content_bytes,
        "artifact_sha256": sha256_file(raw_path),
        "artifact_bytes": raw_path.stat().st_size,
    }
    accuracy = correct / completed if completed else None
    total_tokens = sum(token_values) if completed and tokens_complete else None
    total_cost_usd = math.fsum(cost_values) if completed and cost_complete else None
    if started_at is None or completed_at is None:
        raise BuildError(f"{result_file}: no result records found")
    run_record = {
        "schema_version": "1.0",
        "run_id": run_id,
        "configuration_key": configuration_key,
        "question_set_sha256": question_set_sha256,
        "question_set_size": question_set_size,
        "model": published_model,
        "generation_parameters": generation_parameters,
        "task_family": task_family,
        "evaluation_profile": evaluation_profile,
        "started_at": started_at,
        "completed_at": completed_at,
        "coverage": {
            "attempted": record_count,
            "completed": completed,
            "api_errors": api_errors,
            "missing": question_set_size - record_count,
            "complete": record_count == question_set_size and api_errors == 0,
        },
        "metrics": {
            "scored": completed,
            "correct": correct,
            "accuracy": accuracy,
            "format_failures": format_failures,
            "total_tokens": total_tokens,
            "total_cost_usd": total_cost_usd,
        },
        "answer_prefix": f"answers/{run_id}/",
        "outcome_index_path": f"outcomes/{run_id}.json.gz",
        "raw_archive": {
            key: raw_artifact[key]
            for key in (
                "path",
                "records",
                "content_sha256",
                "content_bytes",
                "artifact_sha256",
                "artifact_bytes",
            )
        },
    }
    return {
        "record": run_record,
        "answers": answer_descriptors,
        "outcomes": outcome_artifact,
        "raw": raw_artifact,
    }


def _usage_totals(usage: Mapping[str, Any]) -> tuple[int | None, float | None]:
    """Read comparable token and USD totals from one gateway usage object."""

    total_tokens = usage.get("total_tokens")
    if isinstance(total_tokens, bool) or not isinstance(total_tokens, int):
        prompt_tokens = usage.get("prompt_tokens")
        completion_tokens = usage.get("completion_tokens")
        if (
            isinstance(prompt_tokens, bool)
            or not isinstance(prompt_tokens, int)
            or prompt_tokens < 0
            or isinstance(completion_tokens, bool)
            or not isinstance(completion_tokens, int)
            or completion_tokens < 0
        ):
            normalized_tokens = None
        else:
            normalized_tokens = prompt_tokens + completion_tokens
    else:
        normalized_tokens = total_tokens if total_tokens >= 0 else None

    cost = usage.get("cost")
    normalized_cost = (
        float(cost)
        if not isinstance(cost, bool)
        and isinstance(cost, int | float)
        and math.isfinite(cost)
        and cost >= 0
        else None
    )
    return normalized_tokens, normalized_cost


def _iter_jsonl_file(path: Path) -> Iterator[dict[str, Any]]:
    """Read local staging JSONL one record at a time."""

    try:
        with path.open("rb") as source:
            for line_number, raw_line in enumerate(source, start=1):
                if not raw_line.strip():
                    continue
                try:
                    value = json.loads(raw_line)
                except (UnicodeDecodeError, json.JSONDecodeError) as exc:
                    raise BuildError(f"{path}:{line_number}: invalid JSON") from exc
                if not isinstance(value, dict):
                    raise BuildError(f"{path}:{line_number}: each record must be an object")
                yield value
    except OSError as exc:
        raise BuildError(f"cannot read result file {path}: {exc}") from exc


def _write_json(path: Path, value: Any) -> bytes:
    payload = f"{canonical_json(value)}\n".encode()
    path.write_bytes(payload)
    return payload


def _write_zstd(path: Path, content: bytes) -> dict[str, Any]:
    payload = zstandard.ZstdCompressor(level=ZSTD_LEVEL).compress(content)
    path.write_bytes(payload)
    return _compressed_artifact(content, payload)


def _write_gzip(path: Path, content: bytes) -> dict[str, Any]:
    payload = gzip.compress(content, compresslevel=GZIP_LEVEL, mtime=0)
    path.write_bytes(payload)
    return _compressed_artifact(content, payload)


def _compressed_artifact(content: bytes, artifact: bytes) -> dict[str, Any]:
    return {
        "content_sha256": hashlib.sha256(content).hexdigest(),
        "content_bytes": len(content),
        "artifact_sha256": hashlib.sha256(artifact).hexdigest(),
        "artifact_bytes": len(artifact),
    }


def _plain_artifact(path: str, content: bytes, records: int) -> dict[str, Any]:
    digest = hashlib.sha256(content).hexdigest()
    return {
        "path": path,
        "records": records,
        "content_sha256": digest,
        "content_bytes": len(content),
        "artifact_sha256": digest,
        "artifact_bytes": len(content),
    }


def _verify_plain(path: Path, descriptor: Mapping[str, Any]) -> bytes:
    payload = path.read_bytes()
    digest = hashlib.sha256(payload).hexdigest()
    if digest != descriptor["artifact_sha256"] or len(payload) != descriptor["artifact_bytes"]:
        raise BuildError(f"{path}: artifact digest or size mismatch")
    if digest != descriptor.get("content_sha256") or len(payload) != descriptor.get(
        "content_bytes"
    ):
        raise BuildError(f"{path}: content digest or size mismatch")
    return payload


def _iter_compressed_jsonl(
    root: Path, descriptor: Mapping[str, Any], compression: str
) -> Iterator[dict[str, Any]]:
    """Validate and yield compressed JSONL without materializing the archive."""

    path = root / descriptor["path"]
    try:
        artifact_bytes = path.stat().st_size
    except OSError as exc:
        raise BuildError(f"cannot read compressed artifact {path}: {exc}") from exc
    if (
        artifact_bytes != descriptor["artifact_bytes"]
        or sha256_file(path) != descriptor["artifact_sha256"]
    ):
        raise BuildError(f"{path}: compressed artifact digest or size mismatch")

    content_digest = hashlib.sha256()
    content_bytes = 0
    try:
        with path.open("rb") as compressed:
            reader = (
                gzip.GzipFile(fileobj=compressed, mode="rb")
                if compression == "gzip"
                else zstandard.ZstdDecompressor().stream_reader(compressed)
            )
            with reader, io.BufferedReader(reader) as buffered:
                for line_number, raw_line in enumerate(buffered, start=1):
                    content_digest.update(raw_line)
                    content_bytes += len(raw_line)
                    if not raw_line.endswith(b"\n"):
                        raise BuildError(f"{path}: JSONL must end with LF")
                    try:
                        text = raw_line[:-1].decode("utf-8")
                        value = json.loads(text)
                    except (UnicodeDecodeError, json.JSONDecodeError) as exc:
                        raise BuildError(f"{path}:{line_number}: invalid JSON") from exc
                    if not isinstance(value, dict) or canonical_json(value) != text:
                        raise BuildError(f"{path}:{line_number}: record is not canonical JSON")
                    yield value
    except (gzip.BadGzipFile, zstandard.ZstdError, EOFError, OSError) as exc:
        raise BuildError(f"{path}: invalid {compression} data") from exc

    if (
        content_digest.hexdigest() != descriptor["content_sha256"]
        or content_bytes != descriptor["content_bytes"]
    ):
        raise BuildError(f"{path}: decompressed content digest or size mismatch")


def _verify_compressed(root: Path, descriptor: Mapping[str, Any], compression: str) -> bytes:
    path = root / descriptor["path"]
    payload = path.read_bytes()
    if (
        hashlib.sha256(payload).hexdigest() != descriptor["artifact_sha256"]
        or len(payload) != descriptor["artifact_bytes"]
    ):
        raise BuildError(f"{path}: compressed artifact digest or size mismatch")
    try:
        content = (
            gzip.decompress(payload)
            if compression == "gzip"
            else zstandard.ZstdDecompressor().decompress(payload)
        )
    except (gzip.BadGzipFile, zstandard.ZstdError) as exc:
        raise BuildError(f"{path}: invalid {compression} data") from exc
    if (
        hashlib.sha256(content).hexdigest() != descriptor["content_sha256"]
        or len(content) != descriptor["content_bytes"]
    ):
        raise BuildError(f"{path}: decompressed content digest or size mismatch")
    return content


def _read_jsonl_bytes(payload: bytes, label: str) -> list[dict[str, Any]]:
    try:
        text = payload.decode("utf-8")
    except UnicodeDecodeError as exc:
        raise BuildError(f"{label}: JSONL is not UTF-8") from exc
    if not text.endswith("\n"):
        raise BuildError(f"{label}: JSONL must end with LF")
    records: list[dict[str, Any]] = []
    for line_number, line in enumerate(text.splitlines(), start=1):
        try:
            value = json.loads(line)
        except json.JSONDecodeError as exc:
            raise BuildError(f"{label}:{line_number}: invalid JSON") from exc
        if not isinstance(value, dict) or canonical_json(value) != line:
            raise BuildError(f"{label}:{line_number}: record is not canonical JSON")
        records.append(value)
    return records


def _load_validator(path: str | Path) -> Draft202012Validator:
    schema_path = Path(path)
    try:
        schema = json.loads(schema_path.read_text(encoding="utf-8"))
    except (OSError, json.JSONDecodeError) as exc:
        raise BuildError(f"cannot load schema {schema_path}: {exc}") from exc
    Draft202012Validator.check_schema(schema)
    return Draft202012Validator(schema, format_checker=FormatChecker())


def _schema_error(label: object, errors: list[Any]) -> str:
    details = "; ".join(
        f"{'.'.join(str(part) for part in error.path) or '<record>'}: {error.message}"
        for error in sorted(errors, key=lambda error: list(error.path))
    )
    return f"{label}: {details}"
