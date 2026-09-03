"""Validated human-maintained publication configuration."""

from dataclasses import dataclass
from pathlib import Path

from vepbench.config.loader import load_yaml_mapping
from vepbench.errors import BuildError


@dataclass(frozen=True)
class PublishingConfig:
    """Resolved defaults for local publication and remote bucket operations."""

    source_path: Path
    bucket: str
    model_catalog: Path


def load_publishing_config(path: str | Path) -> PublishingConfig:
    """Load a strict publication configuration with relative path resolution."""

    source_path, raw = load_yaml_mapping(path, label="publishing config")
    required = {"schema_version", "bucket", "model_catalog"}
    missing = required - raw.keys()
    unknown = raw.keys() - required
    if missing or unknown:
        raise BuildError(
            f"{source_path}: invalid publishing config fields; "
            f"missing={sorted(missing)}, unknown={sorted(unknown)}"
        )
    if raw["schema_version"] != "1.0":
        raise BuildError(f"{source_path}: unsupported publishing config schema version")
    for field in ("bucket", "model_catalog"):
        if not isinstance(raw[field], str) or not raw[field]:
            raise BuildError(f"{source_path}: {field} must be a non-empty string")
    if "/" not in raw["bucket"] or raw["bucket"].startswith("/"):
        raise BuildError(f"{source_path}: bucket must be an owner/name identifier")
    config_dir = source_path.resolve().parent
    return PublishingConfig(
        source_path=source_path.resolve(),
        bucket=raw["bucket"],
        model_catalog=(config_dir / raw["model_catalog"]).resolve(),
    )
