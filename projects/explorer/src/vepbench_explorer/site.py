"""Assemble the static explorer without embedding prompts or model responses."""

import json
import shutil
from collections.abc import Mapping, Sequence
from datetime import date
from pathlib import Path
from typing import Any
from urllib.parse import urlparse

from vepbench.artifacts import canonical_json, read_jsonl, sha256_json
from vepbench.config.loader import load_yaml_mapping
from vepbench.errors import BuildError

ASSAY_PUBLICATION_KINDS = {"assay_repository", "dataset", "paper"}
ASSAY_PUBLICATION_FIELDS = {"date", "kind", "registry", "url"}


def load_assay_publications(path: str | Path) -> dict[str, Any]:
    """Load reviewed first-indexed dates for assay records shown by the explorer."""

    source_path, raw = load_yaml_mapping(path, label="assay publication metadata")
    if set(raw) != {"schema_version", "by_task_family"} or raw["schema_version"] != "1.0":
        raise BuildError(f"{source_path}: invalid assay publication metadata contract")
    families = raw["by_task_family"]
    if not isinstance(families, dict) or not families:
        raise BuildError(f"{source_path}: by_task_family must be a non-empty object")

    normalized: dict[str, dict[str, Any]] = {}
    for task_family, rule in families.items():
        location = f"{source_path}: by_task_family.{task_family}"
        if not isinstance(task_family, str) or not task_family:
            raise BuildError(f"{source_path}: task family names must be non-empty strings")
        if (
            not isinstance(rule, dict)
            or not rule
            or set(rule) - {"default", "records"}
            or not set(rule) & {"default", "records"}
        ):
            raise BuildError(f"{location} must contain only default and/or records")
        normalized_rule: dict[str, Any] = {}
        if "default" in rule:
            normalized_rule["default"] = _assay_publication(rule["default"], f"{location}.default")
        if "records" in rule:
            records = rule["records"]
            if not isinstance(records, dict) or not records:
                raise BuildError(f"{location}.records must be a non-empty object")
            normalized_rule["records"] = {
                record_id: _assay_publication(publication, f"{location}.records.{record_id}")
                for record_id, publication in records.items()
                if isinstance(record_id, str) and record_id
            }
            if len(normalized_rule["records"]) != len(records):
                raise BuildError(f"{location}.records keys must be non-empty strings")
        normalized[task_family] = normalized_rule
    return {
        "schema_version": "1.0",
        "by_task_family": dict(sorted(normalized.items())),
    }


def build_question_metadata(
    *,
    source_paths: Sequence[str | Path],
    assay_publications: Mapping[str, Any],
) -> dict[str, Any]:
    """Build safe display metadata without changing model-visible questions."""

    if assay_publications.get("schema_version") != "1.0" or not isinstance(
        assay_publications.get("by_task_family"), Mapping
    ):
        raise BuildError("assay publication metadata has an invalid shape")
    publication_families = assay_publications["by_task_family"]
    by_task_family: dict[str, dict[str, dict[str, Any]]] = {}
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
            publication_rule = publication_families.get(task_family)
            publication = None
            if isinstance(publication_rule, Mapping):
                records = publication_rule.get("records", {})
                if isinstance(records, Mapping):
                    publication = records.get(source_record_id)
                if publication is None:
                    publication = publication_rule.get("default")
            if not isinstance(publication, Mapping):
                raise BuildError(
                    f"{source_path}: {source_record_id!r} is missing assay publication metadata"
                )
            source_metadata = record.get("source_metadata", {})
            if (
                isinstance(source_metadata, dict)
                and isinstance(source_metadata.get("display_name"), str)
                and source_metadata["display_name"]
            ):
                display_metadata = {
                    "source_record_sha256": sha256_json(record),
                    "element": source_metadata["display_name"],
                    "assay_first_indexed": dict(publication),
                }
            else:
                raise BuildError(f"{source_path}: {source_record_id!r} is missing display metadata")
            task_records[source_record_id] = display_metadata

    unknown_families = set(publication_families) - set(by_task_family)
    if unknown_families:
        raise BuildError(f"unused assay publication task families: {sorted(unknown_families)}")
    for task_family, task_records in by_task_family.items():
        publication_rule = publication_families[task_family]
        configured_records = publication_rule.get("records", {})
        unknown_records = set(configured_records) - set(task_records)
        if unknown_records:
            raise BuildError(
                f"unused assay publication records for {task_family}: {sorted(unknown_records)}"
            )
    return {
        "schema_version": "1.0",
        "by_task_family": {
            task_family: dict(sorted(records.items()))
            for task_family, records in sorted(by_task_family.items())
        },
    }


def _assay_publication(value: Any, location: str) -> dict[str, str]:
    if not isinstance(value, dict) or set(value) != ASSAY_PUBLICATION_FIELDS:
        raise BuildError(f"{location} must contain exactly {sorted(ASSAY_PUBLICATION_FIELDS)}")
    if any(not isinstance(value[field], str) or not value[field] for field in value):
        raise BuildError(f"{location} fields must be non-empty strings")
    try:
        parsed_date = date.fromisoformat(value["date"])
    except ValueError as exc:
        raise BuildError(f"{location}.date must be an ISO 8601 calendar date") from exc
    if parsed_date.isoformat() != value["date"]:
        raise BuildError(f"{location}.date must use YYYY-MM-DD")
    if value["kind"] not in ASSAY_PUBLICATION_KINDS:
        raise BuildError(f"{location}.kind must be one of {sorted(ASSAY_PUBLICATION_KINDS)}")
    parsed_url = urlparse(value["url"])
    if parsed_url.scheme != "https" or not parsed_url.netloc:
        raise BuildError(f"{location}.url must be an absolute HTTPS URL")
    return {field: value[field] for field in sorted(ASSAY_PUBLICATION_FIELDS)}


def build_site(
    *,
    assets_dir: str | Path,
    output: str | Path,
    data_base_url: str,
    question_metadata: Mapping[str, Any] | None = None,
) -> dict[str, Any]:
    """Stage Observable sources with a validated configured data source."""

    if not data_base_url.startswith(("https://", "http://127.0.0.1:")):
        raise BuildError("site data URL must use HTTPS or a loopback HTTP origin")
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
