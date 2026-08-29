"""Validated OpenRouter model profiles for reproducible local evaluations."""

from __future__ import annotations

import json
import re
from dataclasses import dataclass
from pathlib import Path
from typing import Any

import yaml

from .builder import BuildError, canonical_json, sha256_file
from .evaluator import validate_generation_parameters

PROFILE_ID = re.compile(r"^[A-Za-z0-9][A-Za-z0-9._-]*$")


@dataclass(frozen=True)
class ModelProfile:
    """One immutable model and generation-parameter configuration."""

    label: str
    model_id: str
    generation_parameters: dict[str, Any]
    source_path: Path
    content_sha256: str


def load_model_profile(path: str | Path) -> ModelProfile:
    """Load a strict versioned YAML model profile."""

    source_path = Path(path)
    try:
        raw = yaml.safe_load(source_path.read_text(encoding="utf-8"))
    except yaml.YAMLError as exc:
        raise BuildError(f"{source_path}: invalid YAML: {exc}") from exc
    if not isinstance(raw, dict):
        raise BuildError(f"{source_path}: model profile must be a mapping")

    required = {"schema_version", "label", "model", "generation"}
    missing = required - raw.keys()
    unknown = raw.keys() - required
    if missing:
        raise BuildError(f"{source_path}: missing profile field(s): {sorted(missing)}")
    if unknown:
        raise BuildError(f"{source_path}: unknown profile field(s): {sorted(unknown)}")
    if raw["schema_version"] != "1.0":
        raise BuildError(
            f"{source_path}: unsupported model profile schema_version "
            f"{raw['schema_version']!r}"
        )

    label = raw["label"]
    if not isinstance(label, str) or not PROFILE_ID.fullmatch(label) or len(label) > 100:
        raise BuildError(f"{source_path}: invalid profile label {label!r}")
    model_id = raw["model"]
    if not isinstance(model_id, str) or not model_id.strip():
        raise BuildError(f"{source_path}: model must be a non-empty string")
    if not isinstance(raw["generation"], dict):
        raise BuildError(f"{source_path}: generation must be a mapping")

    # Round-trip through canonical JSON to reject non-JSON and non-finite YAML values
    # and to detach the immutable profile from PyYAML's mutable parse tree.
    generation_parameters = json.loads(canonical_json(raw["generation"]))
    validate_generation_parameters(generation_parameters)
    return ModelProfile(
        label=label,
        model_id=model_id,
        generation_parameters=generation_parameters,
        source_path=source_path,
        content_sha256=sha256_file(source_path),
    )
