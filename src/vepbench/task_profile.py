"""Validated task-level evaluation settings."""

from __future__ import annotations

import json
import re
from dataclasses import dataclass
from pathlib import Path
from typing import Any

import yaml

from .builder import BuildError, canonical_json, sha256_file
from .evaluator import validate_generation_parameters

TASK_FAMILY = re.compile(r"^[A-Za-z0-9][A-Za-z0-9._-]*$")


@dataclass(frozen=True)
class TaskProfile:
    """Evaluation settings shared by every run of one task."""

    task_family: str
    generation_parameters: dict[str, Any]
    source_path: Path
    content_sha256: str


def load_task_profile(path: str | Path) -> TaskProfile:
    """Load a strict versioned task profile."""

    source_path = Path(path)
    try:
        raw = yaml.safe_load(source_path.read_text(encoding="utf-8"))
    except yaml.YAMLError as exc:
        raise BuildError(f"{source_path}: invalid YAML: {exc}") from exc
    if not isinstance(raw, dict):
        raise BuildError(f"{source_path}: task profile must be a mapping")

    required = {"schema_version", "task_family", "generation"}
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
    if not isinstance(raw["generation"], dict):
        raise BuildError(f"{source_path}: generation must be a mapping")
    if set(raw["generation"]) != {"max_tokens"}:
        raise BuildError(f"{source_path}: task generation must contain exactly max_tokens")

    generation_parameters = json.loads(canonical_json(raw["generation"]))
    validate_generation_parameters(generation_parameters)
    return TaskProfile(
        task_family=task_family,
        generation_parameters=generation_parameters,
        source_path=source_path,
        content_sha256=sha256_file(source_path),
    )
