"""Deterministic JSON and JSONL helpers shared across VEP-bench domains."""

import hashlib
import json
from pathlib import Path
from typing import Any

from vepbench.errors import BuildError


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
    """Return the SHA-256 digest of a value's canonical JSON representation."""

    return hashlib.sha256(canonical_json(value).encode("utf-8")).hexdigest()


def sha256_file(path: str | Path) -> str:
    """Return the SHA-256 digest of a file without loading it all into memory."""

    digest = hashlib.sha256()
    with Path(path).open("rb") as source:
        for chunk in iter(lambda: source.read(1024 * 1024), b""):
            digest.update(chunk)
    return digest.hexdigest()


def read_jsonl(path: str | Path) -> list[dict[str, Any]]:
    """Read a non-empty JSONL file whose records must all be objects."""

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
