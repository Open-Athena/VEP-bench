"""Typed CLI for satMutMPRA preparation and validation."""

from __future__ import annotations

import sys
from collections.abc import Sequence
from pathlib import Path

from cyclopts import App
from cyclopts.exceptions import CycloptsError

from vepbench.errors import BuildError

from .configuration import CONFIG, PREPARATION_CONFIG_PATH, load_preparation_config
from .prepare import prepare
from .task import validate_prepared_artifacts

app = App(
    name="vepbench-satmut-mpra",
    help="Prepare and validate the satMutMPRA benchmark task.",
    version_flags=(),
    exit_on_error=False,
    print_error=False,
    result_action="return_value",
)


@app.command
def validate(
    *,
    source: Path | None = None,
    manifest: Path | None = None,
    config: Path = PREPARATION_CONFIG_PATH,
) -> int:
    """Validate committed satMutMPRA source artifacts without network access."""

    settings = (
        CONFIG
        if config.resolve() == PREPARATION_CONFIG_PATH.resolve()
        else load_preparation_config(config)
    )
    source_path = source or settings.resolve_path("output")
    manifest_path = manifest or settings.resolve_path("manifest_output")
    document = validate_prepared_artifacts(source_path, manifest_path)
    print(
        f"validated {document['output']['records']} satMutMPRA source records "
        f"(sha256 {document['output']['sha256']})"
    )
    return 0


@app.command(name="prepare")
def prepare_command(*, skip_cache_upload: bool = False) -> int:
    """Rebuild the canonical task source, optionally without uploading its cache."""

    count, digest = prepare(upload_cache=not skip_cache_upload)
    print(f"wrote {count} source records to {CONFIG.resolve_path('output')} (sha256 {digest})")
    print(f"wrote manifest to {CONFIG.resolve_path('manifest_output')}")
    return 0


def main(argv: Sequence[str] | None = None) -> int:
    try:
        result = app(argv)
    except CycloptsError as exc:
        print(f"vepbench-satmut-mpra: {exc}", file=sys.stderr)
        return 2
    except (BuildError, OSError, RuntimeError) as exc:
        print(f"vepbench-satmut-mpra: {exc}", file=sys.stderr)
        return 2
    return result if isinstance(result, int) and not isinstance(result, bool) else 0


__all__ = ["app", "main"]
