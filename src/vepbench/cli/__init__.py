"""Typed, evaluation-first VEP-bench command-line interface."""

from __future__ import annotations

import os
import re
import sys
from collections.abc import Sequence
from datetime import UTC, datetime
from pathlib import Path
from typing import Literal

from cyclopts import App
from cyclopts.exceptions import CycloptsError

from ..artifacts import read_jsonl
from ..config import load_model_profile, load_task_profile
from ..errors import BuildError
from ..evaluation.batch import (
    collect_batch_file,
    merge_batch_result_files,
    refresh_batch_state,
    submit_batch_file,
)
from ..evaluation.core import ProviderError, evaluate_file
from ..questions import build_file, fetch_questions, load_template
from ..resources import QUESTION_SCHEMA, RESULT_SCHEMA

app = App(
    name="vepbench",
    help="Evaluate language models on deterministic variant-effect questions.",
    version_flags=(),
    exit_on_error=False,
    print_error=False,
    result_action="return_value",
)
questions_app = App(
    name="questions",
    help="Fetch or build benchmark question sets.",
    version_flags=(),
)
batch_app = App(
    name="batch",
    help="Manage asynchronous OpenRouter batch evaluations.",
    version_flags=(),
)
app.command(questions_app)
app.command(batch_app)


@questions_app.command
def fetch(
    *,
    version: str = "main",
    output: Path | None = None,
    cache_dir: Path = Path(".vepbench/questions"),
) -> int:
    """Fetch, verify, and cache a published question set."""

    fetched = fetch_questions(version=version, output=output, cache_dir=cache_dir)
    disposition = "using cached" if fetched.from_cache else "fetched"
    print(
        f"{disposition} {fetched.records} question(s) at {fetched.output} "
        f"(sha256 {fetched.content_sha256})"
    )
    return 0


@questions_app.command
def build(
    *,
    task: Path,
    output: Path | None = None,
    schema: Path = QUESTION_SCHEMA,
) -> int:
    """Build deterministic questions from a versioned task descriptor."""

    profile = load_task_profile(task)
    template = load_template(profile.prompt_path)
    template_type = template.get("task_type", "multiple_choice")
    if template_type != profile.question_type:
        raise BuildError(
            f"{profile.prompt_path}: template task type {template_type!r} does not match "
            f"descriptor type {profile.question_type!r}"
        )
    destination = output or Path(".vepbench/questions") / f"{profile.task_family}.jsonl"
    count, digest = build_file(
        profile.question_source_path,
        profile.prompt_path,
        schema,
        destination,
    )
    print(f"wrote {count} question(s) to {destination} (sha256 {digest})")
    return 0


@app.command
def evaluate(
    *,
    model: str | None = None,
    model_profile: Path | None = None,
    task: Path | None = None,
    questions: Path | None = None,
    question_version: str = "main",
    schema: Path = QUESTION_SCHEMA,
    result_schema: Path = RESULT_SCHEMA,
    output: Path | None = None,
    run_id: str | None = None,
    direct: bool = False,
    batch_state: Path | None = None,
    batch_offset: int = 0,
    batch_size: int | None = None,
    concurrency: int = 8,
    resume: bool = False,
    temperature: float | None = None,
    max_tokens: int | None = None,
    reasoning_effort: Literal["minimal", "low", "medium", "high", "xhigh", "max"] | None = None,
) -> int:
    """Evaluate one model through OpenRouter using direct or batch requests."""

    if (model is None) == (model_profile is None):
        raise BuildError("provide exactly one of --model or --model-profile")
    if model_profile is not None and task is None:
        raise BuildError("--model-profile requires --task")
    if task is not None and max_tokens is not None:
        raise BuildError(
            "--max-tokens cannot override a task descriptor; "
            "update the versioned descriptor instead"
        )
    api_key = os.environ.get("OPENROUTER_API_KEY")
    if not api_key:
        raise BuildError("OPENROUTER_API_KEY is not set")
    question_path = _question_path(questions, version=question_version)

    generation_parameters: dict[str, object] = {}
    if task is not None:
        task_profile = load_task_profile(task)
        _validate_question_family(question_path, task_profile.task_family)
        generation_parameters.update(task_profile.generation_parameters)
    if model_profile is not None:
        profile = load_model_profile(model_profile)
        model_id = profile.model_id
        profile_label = profile.label
        overlapping = generation_parameters.keys() & profile.generation_parameters.keys()
        if overlapping:
            raise BuildError(
                "task and model profiles define the same generation parameter(s): "
                f"{sorted(overlapping)}"
            )
        generation_parameters.update(profile.generation_parameters)
    else:
        if model is None:  # pragma: no cover - guarded above
            raise AssertionError("model selection disappeared")
        model_id = model
        profile_label = model
        generation_parameters.update({"temperature": 0.0, "max_tokens": 4096})
    if temperature is not None:
        generation_parameters["temperature"] = temperature
    if max_tokens is not None:
        generation_parameters["max_tokens"] = max_tokens
    if reasoning_effort is not None:
        generation_parameters["reasoning"] = {"effort": reasoning_effort, "exclude": False}

    timestamp = datetime.now(UTC).strftime("%Y%m%dT%H%M%SZ")
    model_slug = re.sub(r"[^A-Za-z0-9._-]+", "-", profile_label).strip("-")
    resolved_run_id = run_id or f"{model_slug}-{timestamp}"
    destination = output or Path(".vepbench/results") / f"{resolved_run_id}.jsonl"
    if not direct:
        if resume:
            raise BuildError("--resume requires --direct")
        state_path = batch_state or Path(".vepbench/batches") / f"{resolved_run_id}.json"
        submission = submit_batch_file(
            questions_path=question_path,
            question_schema_path=schema,
            state_path=state_path,
            result_output=destination,
            run_id=resolved_run_id,
            model_id=model_id,
            api_key=api_key,
            generation_parameters=generation_parameters,
            batch_offset=batch_offset,
            batch_size=batch_size,
        )
        print(
            f"submitted {submission.requests} request(s) as OpenRouter batch "
            f"{submission.batch_id} ({submission.status}); state: {submission.state_path}"
        )
        return 0
    if batch_offset != 0 or batch_size is not None:
        raise BuildError("--batch-offset and --batch-size cannot be used with --direct")
    evaluation = evaluate_file(
        questions_path=question_path,
        question_schema_path=schema,
        result_schema_path=result_schema,
        output=destination,
        run_id=resolved_run_id,
        model_id=model_id,
        api_key=api_key,
        generation_parameters=generation_parameters,
        progress=lambda done, total, errors: print(
            f"evaluated {done}/{total} ({errors} API error(s))", flush=True
        ),
        concurrency=concurrency,
        resume=resume,
    )
    print(
        f"wrote {evaluation.completed + evaluation.api_errors} result(s) to "
        f"{evaluation.output} ({evaluation.api_errors} API error(s))"
    )
    return 0 if evaluation.is_complete else 1


@batch_app.command(name="status")
def batch_status(*, state: Path) -> int:
    """Refresh the status of a submitted OpenRouter batch."""

    api_key = _api_key()
    status = refresh_batch_state(state_path=state, api_key=api_key)
    print(
        f"batch {status.batch_id}: {status.status} "
        f"(completed={status.completed}, failed={status.failed}, total={status.total})"
    )
    return 0


@batch_app.command(name="collect")
def batch_collect(
    *,
    state: Path,
    questions: Path | None = None,
    question_version: str = "main",
    schema: Path = QUESTION_SCHEMA,
    result_schema: Path = RESULT_SCHEMA,
) -> int:
    """Collect and score a completed OpenRouter batch."""

    collected = collect_batch_file(
        state_path=state,
        questions_path=_question_path(questions, version=question_version),
        question_schema_path=schema,
        result_schema_path=result_schema,
        api_key=_api_key(),
    )
    print(
        f"wrote {collected.completed + collected.api_errors} result(s) to "
        f"{collected.output} ({collected.api_errors} API error(s))"
    )
    return 0 if collected.is_complete else 1


@batch_app.command(name="merge")
def batch_merge(
    inputs: tuple[Path, ...],
    *,
    output: Path,
    questions: Path | None = None,
    question_version: str = "main",
    schema: Path = QUESTION_SCHEMA,
    result_schema: Path = RESULT_SCHEMA,
) -> int:
    """Merge collected batch chunks into one complete result file."""

    if not inputs:
        raise BuildError("at least one batch result input is required")
    merged = merge_batch_result_files(
        result_paths=list(inputs),
        questions_path=_question_path(questions, version=question_version),
        question_schema_path=schema,
        result_schema_path=result_schema,
        output=output,
    )
    print(
        f"wrote {merged.completed + merged.api_errors} merged result(s) to "
        f"{merged.output} ({merged.api_errors} API error(s))"
    )
    return 0 if merged.is_complete else 1


def _question_path(path: Path | None, *, version: str) -> Path:
    if path is not None:
        return path
    fetched = fetch_questions(version=version)
    print(f"using published questions at {fetched.output} (sha256 {fetched.content_sha256})")
    return fetched.output


def _validate_question_family(path: Path, expected: str) -> None:
    families = {question.get("metadata", {}).get("task_family") for question in read_jsonl(path)}
    if None in families:
        raise BuildError(f"{path}: question is missing metadata.task_family")
    if families != {expected}:
        raise BuildError(
            f"{path}: task families {sorted(families, key=str)} do not match task {expected!r}"
        )


def _api_key() -> str:
    api_key = os.environ.get("OPENROUTER_API_KEY")
    if not api_key:
        raise BuildError("OPENROUTER_API_KEY is not set")
    return api_key


def main(argv: Sequence[str] | None = None) -> int:
    """Run the CLI while keeping programmatic invocations free of ``SystemExit``."""

    try:
        result = app(argv)
    except CycloptsError as exc:
        print(f"vepbench: {exc}", file=sys.stderr)
        return 2
    except (BuildError, ProviderError, OSError) as exc:
        print(f"vepbench: {exc}", file=sys.stderr)
        return 2
    if result is None:
        return 0
    if isinstance(result, bool):
        return 0 if result else 1
    if isinstance(result, int):
        return result
    return 0


__all__ = ["app", "main"]
