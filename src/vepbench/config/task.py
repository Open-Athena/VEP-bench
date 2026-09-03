"""Validated task-level evaluation settings."""

from __future__ import annotations

import re
from dataclasses import dataclass
from pathlib import Path
from typing import Any

from ..artifacts import sha256_json
from ..errors import BuildError
from ..evaluation.core import validate_generation_parameters
from .loader import load_yaml_mapping

TASK_FAMILY = re.compile(r"^[A-Za-z0-9][A-Za-z0-9._-]*$")


@dataclass(frozen=True)
class TaskProfile:
    """Question inputs and evaluation settings for one benchmark task."""

    task_family: str
    question_type: str
    question_source_path: Path
    prompt_path: Path
    generation_parameters: dict[str, Any]
    source_path: Path
    content_sha256: str


def load_task_profile(path: str | Path) -> TaskProfile:
    """Load a strict versioned task descriptor."""

    source_path, raw = load_yaml_mapping(path, label="task descriptor")

    required = {"schema_version", "task_family", "question", "evaluation"}
    missing = required - raw.keys()
    unknown = raw.keys() - required
    if missing:
        raise BuildError(f"{source_path}: missing task profile field(s): {sorted(missing)}")
    if unknown:
        raise BuildError(f"{source_path}: unknown task profile field(s): {sorted(unknown)}")
    if raw["schema_version"] != "1.0":
        raise BuildError(
            f"{source_path}: unsupported task profile schema_version {raw['schema_version']!r}"
        )

    task_family = raw["task_family"]
    if (
        not isinstance(task_family, str)
        or not TASK_FAMILY.fullmatch(task_family)
        or len(task_family) > 100
    ):
        raise BuildError(f"{source_path}: invalid task_family {task_family!r}")
    question = raw["question"]
    if not isinstance(question, dict) or set(question) != {"type", "source", "prompt"}:
        raise BuildError(f"{source_path}: question must contain exactly type, source, and prompt")
    question_type = question["type"]
    if question_type not in {"multiple_choice", "ranking"}:
        raise BuildError(f"{source_path}: unsupported question type {question_type!r}")
    for field in ("source", "prompt"):
        if not isinstance(question[field], str) or not question[field]:
            raise BuildError(f"{source_path}: question.{field} must be a non-empty path")

    evaluation = raw["evaluation"]
    if not isinstance(evaluation, dict) or set(evaluation) != {"generation"}:
        raise BuildError(f"{source_path}: evaluation must contain exactly generation")
    if not isinstance(evaluation["generation"], dict):
        raise BuildError(f"{source_path}: evaluation.generation must be a mapping")
    if set(evaluation["generation"]) != {"max_tokens"}:
        raise BuildError(f"{source_path}: task generation must contain exactly max_tokens")

    generation_parameters = dict(evaluation["generation"])
    validate_generation_parameters(generation_parameters)
    config_dir = source_path.resolve().parent
    return TaskProfile(
        task_family=task_family,
        question_type=question_type,
        question_source_path=(config_dir / question["source"]).resolve(),
        prompt_path=(config_dir / question["prompt"]).resolve(),
        generation_parameters=generation_parameters,
        source_path=source_path,
        content_sha256=sha256_json(raw),
    )
