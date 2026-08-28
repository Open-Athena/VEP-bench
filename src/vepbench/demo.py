"""Reproducible synthetic result used to exercise the static explorer."""

import itertools
import json
from collections.abc import Mapping
from datetime import UTC, datetime
from pathlib import Path
from typing import Any

from .builder import BuildError
from .evaluator import EvaluationSummary, evaluate_file


class FixtureTransport:
    """Return a checked-in OpenRouter-shaped response without network access."""

    def __init__(self, response: Mapping[str, Any]):
        self.response = dict(response)

    def complete(self, request_body: Mapping[str, Any], api_key: str) -> dict[str, Any]:
        return self.response


def build_demo_result(
    *,
    questions_path: str | Path,
    question_schema_path: str | Path,
    result_schema_path: str | Path,
    response_path: str | Path,
    output: str | Path,
) -> EvaluationSummary:
    response = json.loads(Path(response_path).read_text(encoding="utf-8"))
    if not isinstance(response, dict):
        raise BuildError(f"{response_path}: fixture response must be a JSON object")
    ticks = itertools.count(start=0.0, step=0.125)
    return evaluate_file(
        questions_path=questions_path,
        question_schema_path=question_schema_path,
        result_schema_path=result_schema_path,
        output=output,
        run_id="synthetic-demo",
        model_id="synthetic/demo",
        api_key="not-a-real-key",
        transport=FixtureTransport(response),
        now=lambda: datetime(2026, 8, 28, 12, 0, tzinfo=UTC),
        monotonic=lambda: next(ticks),
    )
