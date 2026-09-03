"""Strict helpers for human-maintained YAML configuration."""

import json
from pathlib import Path
from typing import Any

import yaml
from yaml.constructor import ConstructorError
from yaml.nodes import MappingNode

from ..artifacts import canonical_json
from ..errors import BuildError


class _UniqueKeyLoader(yaml.SafeLoader):
    """Safe YAML loader that rejects duplicate mapping keys."""


def _construct_unique_mapping(
    loader: _UniqueKeyLoader, node: MappingNode, deep: bool = False
) -> dict[Any, Any]:
    mapping: dict[Any, Any] = {}
    for key_node, value_node in node.value:
        key = loader.construct_object(key_node, deep=deep)
        try:
            duplicate = key in mapping
        except TypeError as exc:
            raise ConstructorError(
                "while constructing a mapping",
                node.start_mark,
                "found an unhashable mapping key",
                key_node.start_mark,
            ) from exc
        if duplicate:
            raise ConstructorError(
                "while constructing a mapping",
                node.start_mark,
                f"found duplicate key {key!r}",
                key_node.start_mark,
            )
        mapping[key] = loader.construct_object(value_node, deep=deep)
    return mapping


_UniqueKeyLoader.add_constructor(
    yaml.resolver.BaseResolver.DEFAULT_MAPPING_TAG,
    _construct_unique_mapping,
)


def load_yaml_mapping(path: str | Path, *, label: str) -> tuple[Path, dict[str, Any]]:
    """Load a YAML mapping and normalize it to JSON-compatible values."""

    source_path = Path(path)
    try:
        raw = yaml.load(source_path.read_text(encoding="utf-8"), Loader=_UniqueKeyLoader)
    except OSError as exc:
        raise BuildError(f"cannot read {label} {source_path}: {exc}") from exc
    except yaml.YAMLError as exc:
        raise BuildError(f"{source_path}: invalid YAML: {exc}") from exc
    if not isinstance(raw, dict):
        raise BuildError(f"{source_path}: {label} must be a mapping")
    try:
        normalized = json.loads(canonical_json(raw))
    except (TypeError, ValueError) as exc:
        raise BuildError(
            f"{source_path}: {label} must contain only JSON-compatible values"
        ) from exc
    if not isinstance(normalized, dict):  # pragma: no cover - guarded above
        raise AssertionError("normalized YAML mapping changed type")
    return source_path, normalized
