"""Assemble the static explorer without embedding prompts or model responses."""

import json
import shutil
from collections.abc import Mapping, Sequence
from pathlib import Path
from typing import Any

from .builder import BuildError, canonical_json, read_jsonl

OFFICIAL_DATA_BASE_URL = "https://huggingface.co/buckets/open-athena/vepbench/resolve/versions/main"


def build_question_metadata(
    *,
    source_paths: Sequence[str | Path],
    consequence_overrides: Mapping[str, Mapping[str, str]] | None = None,
) -> dict[str, Any]:
    """Build safe display metadata without changing model-visible questions."""

    overrides = consequence_overrides or {}
    by_task_family: dict[str, dict[str, dict[str, str]]] = {}
    for source_path in source_paths:
        for record in read_jsonl(source_path):
            task_family = record.get("task_family")
            source_record_id = record.get("source_record_id")
            if not isinstance(task_family, str) or not isinstance(source_record_id, str):
                raise BuildError(f"{source_path}: display metadata record has invalid identity")
            task_records = by_task_family.setdefault(task_family, {})
            if source_record_id in task_records:
                raise BuildError(
                    f"{source_path}: duplicate display metadata record {source_record_id!r}"
                )
            source_metadata = record.get("source_metadata", {})
            consequence = (
                source_metadata.get("vep_consequence")
                if isinstance(source_metadata, dict)
                else None
            )
            if consequence is None:
                consequence = overrides.get(task_family, {}).get(source_record_id)
            if not isinstance(consequence, str) or not consequence:
                raise BuildError(
                    f"{source_path}: {source_record_id!r} is missing consequence metadata"
                )
            task_records[source_record_id] = {"consequence": consequence}
    return {
        "schema_version": "1.0",
        "by_task_family": {
            task_family: dict(sorted(records.items()))
            for task_family, records in sorted(by_task_family.items())
        },
    }


def build_site(
    *,
    assets_dir: str | Path,
    output: str | Path,
    data_base_url: str = OFFICIAL_DATA_BASE_URL,
    question_metadata: Mapping[str, Any] | None = None,
) -> dict[str, Any]:
    """Stage Observable sources with a fixed official ``main`` data source."""

    if data_base_url != OFFICIAL_DATA_BASE_URL:
        raise BuildError("production site data URL must be the canonical HF Bucket main prefix")
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

    config = {
        "schema_version": "1.0",
        "version": "main",
        "data_base_url": data_base_url,
    }
    data_dir = output_dir / "data"
    data_dir.mkdir(exist_ok=True)
    (data_dir / "config.json").write_text(
        f"{canonical_json(config)}\n", encoding="utf-8", newline="\n"
    )
    metadata = question_metadata or {"schema_version": "1.0", "by_task_family": {}}
    try:
        metadata_payload = json.loads(canonical_json(dict(metadata)))
    except (TypeError, ValueError) as exc:
        raise BuildError("question display metadata must be JSON-serializable") from exc
    if metadata_payload.get("schema_version") != "1.0" or not isinstance(
        metadata_payload.get("by_task_family"), dict
    ):
        raise BuildError("question display metadata has an invalid shape")
    (data_dir / "question-metadata.json").write_text(
        f"{canonical_json(metadata_payload)}\n", encoding="utf-8", newline="\n"
    )
    return config
