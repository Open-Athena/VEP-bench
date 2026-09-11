"""Execution summaries of the retained responses, including exposed reasoning usage."""

from collections.abc import Iterable, Mapping
from typing import Any


def execution_metrics(records: Iterable[Mapping[str, Any]]) -> dict[str, int | None]:
    completed = 0
    truncated = 0
    total = 0
    maximum = 0
    usage_known = True
    for record in records:
        response = record["response"]
        if response["status"] != "completed":
            continue
        completed += 1
        truncated += response["finish_reason"] == "length"
        tokens = record["usage"].get("completion_tokens")
        if isinstance(tokens, bool) or not isinstance(tokens, int) or tokens < 0:
            usage_known = False
        else:
            total += tokens
            maximum = max(maximum, tokens)
    return {
        "total_output_tokens": total if completed and usage_known else None,
        "max_output_tokens_used": maximum if completed and usage_known else None,
        "truncated_outputs": truncated,
    }
