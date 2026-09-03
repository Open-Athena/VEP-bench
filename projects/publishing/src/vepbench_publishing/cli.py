"""Typed CLI for private publication and bucket workflows."""

from __future__ import annotations

import sys
from collections.abc import Sequence
from pathlib import Path

from cyclopts import App
from cyclopts.exceptions import CycloptsError

from vepbench.errors import BuildError
from vepbench.resources import RESULT_SCHEMA, SCHEMAS_DIR

from .config import load_publishing_config
from .publication import build_version, promote_version, validate_version

app = App(
    name="vepbench-publish",
    help="Build, validate, and upload VEP-bench publication versions.",
    exit_on_error=False,
    print_error=False,
    result_action="return_value",
)
version_app = App(name="version", help="Build and validate local publication versions.")
bucket_app = App(name="bucket", help="Plan and apply Hugging Face Bucket updates.")
app.command(version_app)
app.command(bucket_app)


@version_app.command(name="build")
def version_build(
    *,
    version: str,
    questions: tuple[Path, ...],
    results_dir: tuple[Path, ...],
    config: Path,
    model_catalog: Path | None = None,
    output: Path,
    result_schema: Path = RESULT_SCHEMA,
    schemas_dir: Path = SCHEMAS_DIR,
) -> int:
    """Build a validated local bucket version."""

    settings = load_publishing_config(config)
    manifest = build_version(
        questions_path=questions,
        results_dir=results_dir,
        result_schema_path=result_schema,
        schemas_dir=schemas_dir,
        model_catalog_path=model_catalog or settings.model_catalog,
        output=output,
        version_name=version,
    )
    print(
        f"built versions/{version} with {manifest['question_set_size']} question(s) and "
        f"{manifest['artifacts']['runs']['records']} run(s) at {output}"
    )
    return 0


@version_app.command(name="validate")
def version_validate(*, version: str, root: Path) -> int:
    """Validate a complete local bucket version."""

    manifest = validate_version(root, version_name=version)
    print(
        f"validated versions/{version} with {manifest['question_set_size']} question(s) and "
        f"{manifest['artifacts']['runs']['records']} run(s)"
    )
    return 0


@version_app.command(name="promote")
def version_promote(*, source_root: Path, source_version: str, output: Path) -> int:
    """Build a future main tree from a named local version."""

    manifest = promote_version(
        source_root=source_root,
        source_version=source_version,
        output=output,
    )
    print(
        f"promoted versions/{source_version} into a validated local versions/main tree "
        f"with {manifest['question_set_size']} question(s) at {output}"
    )
    return 0


@bucket_app.command(name="plan")
def bucket_plan(
    *,
    root: Path,
    version: str,
    plan: Path,
    config: Path,
    bucket: str | None = None,
    promote_main: bool = False,
) -> int:
    """Save a reviewed, prefix-scoped Hugging Face Bucket sync plan."""

    try:
        from .bucket import create_bucket_plan, require_hf_token
    except ModuleNotFoundError as exc:
        if exc.name == "huggingface_hub":
            raise BuildError("bucket commands require the publishing `bucket` extra") from exc
        raise

    settings = load_publishing_config(config)
    summary = create_bucket_plan(
        root=root,
        version_name=version,
        bucket_id=bucket or settings.bucket,
        plan_path=plan,
        token=require_hf_token(),
        promote_main=promote_main,
    )
    print(
        f"saved plan for {summary.destination}: uploads={summary.uploads}, "
        f"deletes={summary.deletes}, skips={summary.skips}, upload_bytes={summary.total_size}"
    )
    return 0


@bucket_app.command(name="apply")
def bucket_apply(*, plan: Path, confirm_destination: str, promote_main: bool = False) -> int:
    """Apply a reviewed plan and publish its manifest last."""

    try:
        from .bucket import apply_bucket_plan, require_hf_token
    except ModuleNotFoundError as exc:
        if exc.name == "huggingface_hub":
            raise BuildError("bucket commands require the publishing `bucket` extra") from exc
        raise

    summary = apply_bucket_plan(
        plan_path=plan,
        confirm_destination=confirm_destination,
        token=require_hf_token(),
        promote_main=promote_main,
    )
    print(
        f"published and verified {summary.destination}: "
        f"uploads={summary.uploads}, deletes={summary.deletes}"
    )
    return 0


def main(argv: Sequence[str] | None = None) -> int:
    try:
        result = app(argv)
    except CycloptsError as exc:
        print(f"vepbench-publish: {exc}", file=sys.stderr)
        return 2
    except (BuildError, OSError) as exc:
        print(f"vepbench-publish: {exc}", file=sys.stderr)
        return 2
    return result if isinstance(result, int) and not isinstance(result, bool) else 0


__all__ = ["app", "main"]
