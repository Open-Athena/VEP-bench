"""Build a complete offline publication for deterministic browser QA."""

from __future__ import annotations

import tempfile
from collections.abc import Mapping, Sequence
from datetime import UTC, datetime
from pathlib import Path
from typing import Any

from vepbench_publishing.publication import build_version, promote_version

from vepbench.artifacts import canonical_json, read_jsonl
from vepbench.errors import BuildError
from vepbench.evaluation.core import evaluate_file
from vepbench.resources import QUESTION_SCHEMA, RESULT_SCHEMA, SCHEMAS_DIR

FIXED_TIME = datetime(2026, 8, 31, 12, 0, tzinfo=UTC)
DEFAULT_QUESTION_ID = "satmut-mpra-ranking-v1:F9"
DEFAULT_PREDICTION = "unused-for-ranking"


class OfflineBrowserTransport:
    """Return one deterministic, schema-compatible response without network access."""

    def __init__(self, prediction: str | Mapping[str, float]) -> None:
        self.prediction = prediction

    def complete(self, request_body: Mapping[str, Any], api_key: str) -> dict[str, Any]:
        del request_body, api_key
        final = (
            self.prediction if isinstance(self.prediction, str) else canonical_json(self.prediction)
        )
        return {
            "id": "browser-qa-generation",
            "model": "synthetic/browser-qa",
            "provider": "Offline fixture",
            "choices": [
                {
                    "finish_reason": "stop",
                    "message": {
                        "role": "assistant",
                        "content": (
                            f"Deterministic response generated for browser QA.\nFINAL: {final}"
                        ),
                        "reasoning": ("Synthetic provider-exposed reasoning for display testing."),
                    },
                }
            ],
            "usage": {
                "prompt_tokens": 100,
                "completion_tokens": 12,
                "total_tokens": 112,
                "cost": 0,
            },
        }


def prepare_fixture(
    *,
    questions_path: Path | Sequence[Path],
    output: Path,
    selected_question_id: str,
    prediction: str,
    include_alternate_model: bool = False,
    site_root: Path | None = None,
    data_base_url: str | None = None,
) -> dict[str, Any]:
    """Build and promote a complete fake run, optionally configuring a site copy."""

    question_files = [questions_path] if isinstance(questions_path, Path) else list(questions_path)
    if not question_files:
        raise BuildError("browser QA requires at least one question file")
    question_sets = [(path, read_jsonl(path)) for path in question_files]
    empty_files = [path for path, task_questions in question_sets if not task_questions]
    if empty_files:
        raise BuildError(f"browser QA question file is empty: {empty_files[0]}")
    questions = [question for _, task_questions in question_sets for question in task_questions]
    selected = next(
        (question for question in questions if question["question_id"] == selected_question_id),
        None,
    )
    if selected is None:
        raise BuildError(f"browser QA question {selected_question_id!r} was not generated")
    if (site_root is None) != (data_base_url is None):
        raise BuildError("site_root and data_base_url must be supplied together")

    with tempfile.TemporaryDirectory(prefix="vepbench-browser-qa-") as temporary:
        work = Path(temporary)
        results = work / "results"
        results.mkdir()
        model_ids = ["synthetic/browser-qa"]
        if include_alternate_model:
            model_ids.append("synthetic/browser-qa-alternate")
        for model_index, model_id in enumerate(model_ids):
            for task_index, (question_file, task_questions) in enumerate(question_sets):
                task_type = task_questions[0]["task_type"]
                if any(question["task_type"] != task_type for question in task_questions):
                    raise BuildError(f"browser QA question file mixes task types: {question_file}")
                if task_type == "ranking":
                    candidate_ids = [
                        candidate["candidate_id"] for candidate in task_questions[0]["candidates"]
                    ]
                    task_prediction: str | Mapping[str, float] = {
                        candidate_id: float(
                            index if model_index == 0 else len(candidate_ids) - index
                        )
                        for index, candidate_id in enumerate(candidate_ids)
                    }
                    invalid_questions = [
                        question["question_id"]
                        for question in task_questions
                        if [candidate["candidate_id"] for candidate in question["candidates"]]
                        != candidate_ids
                    ]
                else:
                    primary_prediction = (
                        prediction
                        if any(
                            question["question_id"] == selected_question_id
                            for question in task_questions
                        )
                        else task_questions[0]["answer_choice_id"]
                    )
                    task_prediction = (
                        primary_prediction
                        if model_index == 0
                        else next(
                            choice["choice_id"]
                            for choice in task_questions[0]["choices"]
                            if choice["choice_id"] != primary_prediction
                        )
                    )
                    invalid_questions = [
                        question["question_id"]
                        for question in task_questions
                        if task_prediction
                        not in {choice["choice_id"] for choice in question["choices"]}
                    ]
                if invalid_questions:
                    raise BuildError(
                        f"browser QA prediction is not valid for {invalid_questions[0]!r}"
                    )
                run_prefix = "browser-qa" if model_index == 0 else "browser-qa-alternate"
                run_id = run_prefix if task_index == 0 else f"{run_prefix}-task-{task_index + 1}"
                evaluate_file(
                    questions_path=question_file,
                    question_schema_path=QUESTION_SCHEMA,
                    result_schema_path=RESULT_SCHEMA,
                    output=results / f"{run_id}.jsonl",
                    run_id=run_id,
                    model_id=model_id,
                    api_key="offline-browser-qa",
                    generation_parameters={
                        "max_tokens": 256,
                        "reasoning": {"effort": "low"},
                        "temperature": 0.0,
                    },
                    transport=OfflineBrowserTransport(task_prediction),
                    now=lambda: FIXED_TIME,
                    monotonic=lambda: 0.0,
                    concurrency=8,
                )
        candidate = work / "candidate"
        model_catalog = work / "model-catalog.json"
        catalog_document = {
            "schema_version": "1.1",
            "models": {
                "synthetic/browser-qa": {
                    "family": "Synthetic browser QA",
                    "release_date": "2026-08-01",
                    "knowledge_cutoff": "2026-05",
                    "knowledge_cutoff_url": "https://example.test/models/browser-qa",
                },
                **(
                    {
                        "synthetic/browser-qa-alternate": {
                            "family": "Synthetic browser QA alternate",
                            "release_date": "2026-08-02",
                            "knowledge_cutoff": "2026-05",
                            "knowledge_cutoff_url": (
                                "https://example.test/models/browser-qa-alternate"
                            ),
                        }
                    }
                    if include_alternate_model
                    else {}
                ),
            },
        }
        model_catalog.write_text(
            f"{canonical_json(catalog_document)}\n",
            encoding="utf-8",
            newline="\n",
        )
        build_version(
            questions_path=question_files,
            results_dir=results,
            result_schema_path=RESULT_SCHEMA,
            schemas_dir=SCHEMAS_DIR,
            model_catalog_path=model_catalog,
            output=candidate,
            version_name="browser-qa",
        )
        manifest = promote_version(
            source_root=candidate,
            source_version="browser-qa",
            output=output,
        )

    if site_root is not None and data_base_url is not None:
        config_path = _site_config_path(site_root)
        config = {
            "schema_version": "1.0",
            "version": "main",
            "data_base_url": data_base_url,
        }
        config_path.write_text(f"{canonical_json(config)}\n", encoding="utf-8", newline="\n")

    return manifest


def _site_config_path(site_root: Path) -> Path:
    """Locate config before or after Observable fingerprints file attachments."""

    staged = site_root / "data/config.json"
    if staged.is_file():
        return staged
    compiled = sorted((site_root / "_file/data").glob("config.*.json"))
    if len(compiled) == 1:
        return compiled[0]
    raise BuildError(
        f"browser QA site must contain exactly one data config; found {len(compiled)} "
        f"compiled candidates under {site_root}"
    )
