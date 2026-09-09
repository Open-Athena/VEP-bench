"""Typed CLI for static explorer development."""

from __future__ import annotations

import os
import shutil
import subprocess
import sys
import tempfile
from collections.abc import Sequence
from pathlib import Path

from cyclopts import App
from cyclopts.exceptions import CycloptsError

from vepbench.errors import BuildError

from .annotations import (
    ENSEMBL_URL,
    EnsemblCache,
    generate_annotations,
    load_annotations,
    write_annotation_json,
)
from .config import load_site_config
from .site import build_question_metadata, build_site, load_assay_publications

app = App(
    name="vepbench-site",
    help="Build and test the VEP-bench static explorer.",
    version_flags=(),
    exit_on_error=False,
    print_error=False,
    result_action="return_value",
)
qa_app = App(
    name="qa",
    help="Prepare deterministic browser QA inputs.",
    version_flags=(),
)
app.command(qa_app)


@app.command
def build(*, config: Path, output: Path = Path("_site")) -> int:
    """Validate data and build the Observable static explorer."""

    settings = load_site_config(config)
    if output.exists() and any(output.iterdir()):
        raise BuildError(f"refusing to overwrite non-empty site directory {output}")
    project_root = settings.observable_config.parent
    observable = project_root / "node_modules/.bin/observable"
    if not observable.is_file():
        raise BuildError(
            f"Observable Framework is not installed; run `npm ci --prefix {project_root}`"
        )
    annotations = (
        load_annotations(settings.variant_annotations)
        if settings.variant_annotations is not None and settings.variant_annotations.is_file()
        else None
    )
    if settings.variant_annotations is not None and annotations is None:
        print("VEP annotations are unavailable; showing retained genomic provenance only.")
    with tempfile.TemporaryDirectory(prefix="vepbench-observable-") as temporary:
        temporary_project = Path(temporary)
        source = temporary_project / "web"
        question_metadata = build_question_metadata(
            source_paths=settings.question_metadata_sources,
            assay_publications=load_assay_publications(settings.assay_publications),
            variant_annotations=annotations,
        )
        manifest = build_site(
            assets_dir=settings.assets_dir,
            output=source,
            data_base_url=settings.data_base_url,
            question_metadata=question_metadata,
        )
        shutil.copy2(settings.observable_config, temporary_project / "observablehq.config.js")
        completed = subprocess.run(
            [str(observable), "build"],
            cwd=temporary_project,
            check=False,
            env={
                **os.environ,
                "OBSERVABLE_TELEMETRY_DISABLE": "true",
                "VEPBENCH_OBSERVABLE_OUTPUT": str(output.resolve()),
            },
        )
        if completed.returncode != 0:
            raise BuildError(
                f"Observable Framework build failed with exit code {completed.returncode}"
            )
    print(f"built {output} against {manifest['data_base_url']}")
    return 0


@app.command
def annotate(
    *,
    config: Path,
    cache: Path = Path(".vepbench/ensembl"),
    output: Path | None = None,
    ensembl_url: str = ENSEMBL_URL,
) -> int:
    """Fetch and save VEP display annotations; subsequent builds remain offline."""
    settings = load_site_config(config)
    destination = output or settings.variant_annotations
    if destination is None:
        raise BuildError("provide --output or configure variant_annotations")
    annotations = generate_annotations(
        settings.question_metadata_sources, EnsemblCache(cache, base_url=ensembl_url)
    )
    write_annotation_json(destination, annotations)
    print(f"wrote VEP annotations to {destination}")
    return 0


@qa_app.command(name="fixture")
def qa_fixture(
    *,
    questions: tuple[Path, ...],
    output: Path,
    site_root: Path | None = None,
    data_base_url: str | None = None,
    question_id: str = "satmut-mpra-ranking-v2:F9",
    prediction: str = "unused-for-ranking",
    alternate_model: bool = False,
) -> int:
    """Build deterministic fake publication data for browser QA."""

    try:
        from .browser_qa import prepare_fixture
    except ModuleNotFoundError as exc:
        if exc.name == "vepbench_publishing":
            raise BuildError("browser QA requires the explorer `qa` extra") from exc
        raise
    manifest = prepare_fixture(
        questions_path=questions,
        output=output,
        selected_question_id=question_id,
        prediction=prediction,
        include_alternate_model=alternate_model,
        site_root=site_root,
        data_base_url=data_base_url,
    )
    print(
        f"prepared deterministic browser QA for {manifest['question_set_size']} "
        f"question(s) at {output}"
    )
    return 0


def main(argv: Sequence[str] | None = None) -> int:
    try:
        result = app(argv)
    except CycloptsError as exc:
        print(f"vepbench-site: {exc}", file=sys.stderr)
        return 2
    except (BuildError, OSError) as exc:
        print(f"vepbench-site: {exc}", file=sys.stderr)
        return 2
    return result if isinstance(result, int) and not isinstance(result, bool) else 0


__all__ = ["app", "main"]
