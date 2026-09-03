"""Validated static explorer build configuration."""

from dataclasses import dataclass
from pathlib import Path

from vepbench.config.loader import load_yaml_mapping
from vepbench.errors import BuildError


@dataclass(frozen=True)
class SiteConfig:
    """Resolved inputs for one explorer build."""

    source_path: Path
    data_base_url: str
    assets_dir: Path
    observable_config: Path
    question_metadata_sources: tuple[Path, ...]


def load_site_config(path: str | Path) -> SiteConfig:
    """Load a strict, human-maintained explorer configuration."""

    source_path, raw = load_yaml_mapping(path, label="site config")
    required = {
        "schema_version",
        "data_base_url",
        "assets_dir",
        "observable_config",
        "question_metadata_sources",
    }
    missing = required - raw.keys()
    unknown = raw.keys() - required
    if missing or unknown:
        raise BuildError(
            f"{source_path}: invalid site config fields; "
            f"missing={sorted(missing)}, unknown={sorted(unknown)}"
        )
    if raw["schema_version"] != "1.0":
        raise BuildError(f"{source_path}: unsupported site config schema version")
    for field in ("data_base_url", "assets_dir", "observable_config"):
        if not isinstance(raw[field], str) or not raw[field]:
            raise BuildError(f"{source_path}: {field} must be a non-empty string")
    metadata_sources = raw["question_metadata_sources"]
    if (
        not isinstance(metadata_sources, list)
        or not metadata_sources
        or not all(isinstance(item, str) and item for item in metadata_sources)
    ):
        raise BuildError(f"{source_path}: question_metadata_sources must be a non-empty list")
    config_dir = source_path.resolve().parent
    return SiteConfig(
        source_path=source_path.resolve(),
        data_base_url=raw["data_base_url"],
        assets_dir=(config_dir / raw["assets_dir"]).resolve(),
        observable_config=(config_dir / raw["observable_config"]).resolve(),
        question_metadata_sources=tuple((config_dir / item).resolve() for item in metadata_sources),
    )
