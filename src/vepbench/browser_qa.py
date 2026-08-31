"""Build a complete offline publication for deterministic browser QA."""

from __future__ import annotations

import tempfile
from collections.abc import Mapping
from datetime import UTC, datetime
from pathlib import Path
from typing import Any

from .builder import BuildError, canonical_json, read_jsonl
from .evaluator import evaluate_file
from .publication import build_version, promote_version

PROJECT_ROOT = Path(__file__).resolve().parents[2]
FIXED_TIME = datetime(2026, 8, 31, 12, 0, tzinfo=UTC)
DEFAULT_QUESTION_ID = "vep-most-severe-v1:17:10511998:T:C"
DEFAULT_PREDICTION = "C17"


class OfflineBrowserTransport:
    """Return one deterministic, schema-compatible response without network access."""

    def __init__(self, prediction: str) -> None:
        self.prediction = prediction

    def complete(self, request_body: Mapping[str, Any], api_key: str) -> dict[str, Any]:
        del request_body, api_key
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
                            "Deterministic response generated for browser QA.\n"
                            f"FINAL: {self.prediction}"
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
    questions_path: Path,
    output: Path,
    selected_question_id: str,
    prediction: str,
    site_root: Path | None = None,
    data_base_url: str | None = None,
) -> dict[str, Any]:
    """Build and promote a complete fake run, optionally configuring a site copy."""

    questions = read_jsonl(questions_path)
    selected = next(
        (question for question in questions if question["question_id"] == selected_question_id),
        None,
    )
    if selected is None:
        raise BuildError(f"browser QA question {selected_question_id!r} was not generated")
    invalid_questions = [
        question["question_id"]
        for question in questions
        if prediction not in {choice["choice_id"] for choice in question["choices"]}
    ]
    if invalid_questions:
        raise BuildError(
            f"browser QA prediction {prediction!r} is not valid for {invalid_questions[0]!r}"
        )
    if (site_root is None) != (data_base_url is None):
        raise BuildError("site_root and data_base_url must be supplied together")

    with tempfile.TemporaryDirectory(prefix="vepbench-browser-qa-") as temporary:
        work = Path(temporary)
        results = work / "results"
        results.mkdir()
        evaluate_file(
            questions_path=questions_path,
            question_schema_path=PROJECT_ROOT / "schemas/question.schema.json",
            result_schema_path=PROJECT_ROOT / "schemas/result.schema.json",
            output=results / "browser-qa.jsonl",
            run_id="browser-qa",
            model_id="synthetic/browser-qa",
            api_key="offline-browser-qa",
            generation_parameters={
                "max_tokens": 256,
                "reasoning": {"effort": "low"},
                "temperature": 0.0,
            },
            transport=OfflineBrowserTransport(prediction),
            now=lambda: FIXED_TIME,
            monotonic=lambda: 0.0,
            concurrency=8,
        )
        candidate = work / "candidate"
        build_version(
            questions_path=questions_path,
            results_dir=results,
            result_schema_path=PROJECT_ROOT / "schemas/result.schema.json",
            schemas_dir=PROJECT_ROOT / "schemas",
            output=candidate,
            version_name="browser-qa",
        )
        manifest = promote_version(
            source_root=candidate,
            source_version="browser-qa",
            output=output,
        )

    if site_root is not None and data_base_url is not None:
        config_path = site_root / "data/config.json"
        if not config_path.is_file():
            raise BuildError(f"browser QA site config does not exist: {config_path}")
        config = {
            "schema_version": "1.0",
            "version": "main",
            "data_base_url": data_base_url,
        }
        config_path.write_text(f"{canonical_json(config)}\n", encoding="utf-8", newline="\n")

    return manifest
