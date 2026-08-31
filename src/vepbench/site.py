"""Assemble the static explorer without embedding published benchmark data."""

import shutil
from pathlib import Path
from typing import Any

from .builder import BuildError, canonical_json

OFFICIAL_DATA_BASE_URL = (
    "https://huggingface.co/buckets/open-athena/vepbench/resolve/versions/main"
)


def build_site(
    *,
    assets_dir: str | Path,
    output: str | Path,
    data_base_url: str = OFFICIAL_DATA_BASE_URL,
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
    return config
