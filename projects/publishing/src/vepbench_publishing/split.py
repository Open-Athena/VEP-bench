"""Export a complete combined evaluation into provenance-linked task runs."""

import hashlib
import json
import re
import tempfile
from collections import defaultdict
from pathlib import Path
from typing import Any

from jsonschema import Draft202012Validator, FormatChecker

from vepbench.artifacts import canonical_json, read_jsonl, sha256_file, sha256_json
from vepbench.errors import BuildError
from vepbench.evaluation.core import validate_result
from vepbench.questions.validation import validate_question
from vepbench.resources import QUESTION_SCHEMA, RESULT_SCHEMA


def split_results(*, questions: Path, results: Path, output: Path) -> dict[str, Any]:
    """Write task exports atomically; retain original identity inside raw metadata."""

    if output.exists():
        raise BuildError(f"refusing to overwrite export directory {output}")
    source_questions = read_jsonl(questions)
    ids = [question["question_id"] for question in source_questions]
    if not ids or ids != sorted(set(ids)):
        raise BuildError("source questions must have unique sorted question IDs")
    question_bytes = b"".join(
        (canonical_json(question) + "\n").encode() for question in source_questions
    )
    source_digest = hashlib.sha256(question_bytes).hexdigest()
    if source_digest != sha256_file(questions):
        raise BuildError("source questions must use canonical JSON, UTF-8, and LF endings")
    validator = Draft202012Validator(json.loads(QUESTION_SCHEMA.read_text()))
    by_task: dict[str, list[dict[str, Any]]] = defaultdict(list)
    for question in source_questions:
        validate_question(question, validator)
        family = question["metadata"]["task_family"]
        if not re.fullmatch(r"[a-z0-9][a-z0-9_-]*", family):
            raise BuildError(f"unsafe task family {family!r}")
        by_task[family].append(question)
    by_id = {question["question_id"]: question for question in source_questions}
    validator = Draft202012Validator(
        json.loads(RESULT_SCHEMA.read_text()), format_checker=FormatChecker()
    )
    manifest: dict[str, Any] = {
        "schema_version": "1.0",
        "source_questions_sha256": source_digest,
        "source_question_set_size": len(ids),
        "source_results_sha256": sha256_file(results),
        "tasks": {},
    }
    output.parent.mkdir(parents=True, exist_ok=True)
    with tempfile.TemporaryDirectory(prefix=".task-export-", dir=output.parent) as temporary:
        staging = Path(temporary) / "export"
        (staging / "questions").mkdir(parents=True)
        for family, task_questions in sorted(by_task.items()):
            path = staging / "questions" / f"{family}.jsonl"
            path.write_bytes(
                b"".join((canonical_json(question) + "\n").encode() for question in task_questions)
            )
            (staging / "results" / family).mkdir(parents=True)
            manifest["tasks"][family] = {
                "questions": path.relative_to(staging).as_posix(),
                "question_set_sha256": sha256_file(path),
                "question_set_size": len(task_questions),
                "records": [],
            }
        previous = None
        source_identity = None
        seen = set()
        with results.open("rb") as source:
            for line in source:
                record = json.loads(line)
                validate_result(record, validator)
                question_id = record["question_id"]
                if previous is not None and question_id <= previous:
                    raise BuildError("source results must have one sorted record per question")
                previous = question_id
                if (
                    record["question_set_sha256"] != source_digest
                    or record["question_set_size"] != len(ids)
                    or record["question"] != by_id.get(question_id)
                ):
                    raise BuildError(f"{question_id}: source question-set provenance mismatch")
                if record["response"]["status"] != "completed":
                    raise BuildError(f"{question_id}: source run contains an API failure")
                identity = canonical_json(
                    [record["run_id"], record["model"], record["generation_parameters"]]
                )
                if source_identity is not None and identity != source_identity:
                    raise BuildError("source results must contain one consistent run configuration")
                source_identity = identity
                seen.add(question_id)
                family = record["question"]["metadata"]["task_family"]
                task = manifest["tasks"][family]
                raw = record["response"]["raw"]
                if "_vepbench_task_export" in raw:
                    raise BuildError("source results already contain task-export provenance")
                provenance = {
                    "source_run_id": record["run_id"],
                    "source_question_set_sha256": source_digest,
                    "source_question_set_size": len(ids),
                    "source_results_sha256": manifest["source_results_sha256"],
                    "source_record_sha256": hashlib.sha256(line).hexdigest(),
                    "source_raw_sha256": sha256_json(raw),
                }
                raw["_vepbench_task_export"] = provenance
                record["run_id"] = f"{record['run_id']}-{family.replace('_', '-')}"
                record["question_set_sha256"] = task["question_set_sha256"]
                record["question_set_size"] = task["question_set_size"]
                validate_result(record, validator)
                destination = staging / "results" / family / f"{record['run_id']}.jsonl"
                exported = (canonical_json(record) + "\n").encode()
                with destination.open("ab") as target:
                    target.write(exported)
                task["run_id"] = record["run_id"]
                task["results"] = destination.relative_to(staging).as_posix()
                task["records"].append(
                    {
                        "question_id": question_id,
                        "source_record_sha256": provenance["source_record_sha256"],
                        "exported_record_sha256": hashlib.sha256(exported).hexdigest(),
                    }
                )
        if seen != set(ids):
            raise BuildError("source run is incomplete")
        if sha256_file(results) != manifest["source_results_sha256"]:
            raise BuildError("source results changed during export")
        for task in manifest["tasks"].values():
            task["results_sha256"] = sha256_file(staging / task["results"])
        (staging / "export.json").write_text(canonical_json(manifest) + "\n", encoding="utf-8")
        staging.rename(output)
    return manifest
