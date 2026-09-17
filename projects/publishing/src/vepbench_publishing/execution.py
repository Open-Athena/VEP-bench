"""Execution summaries of the retained responses, including exposed reasoning usage."""

import math
from collections.abc import Iterable, Mapping
from typing import Any

from .retry import retry_metadata


def execution_metrics(records: Iterable[Mapping[str, Any]]) -> dict[str, int | float | None]:
    completed = 0
    truncated = 0
    total = 0
    maximum = 0
    usage_known = True
    completed_costs = []
    costs_known = True
    completed_tokens = 0
    tokens_known = True
    for record in records:
        response = record["response"]
        if response["status"] != "completed":
            continue
        completed += 1
        metadata = retry_metadata(record["usage"])
        attempts = (
            [record]
            if metadata is None
            else [
                metadata["prior_attempt"],
                *(entry["record"] for entry in metadata.get("intermediate_attempts", [])),
                record,
            ]
        )
        for attempt in attempts:
            if attempt["response"]["status"] != "completed":
                continue
            cost = attempt["usage"].get("cost")
            if type(cost) in (int, float) and math.isfinite(cost) and cost >= 0:
                completed_costs.append(cost)
            else:
                costs_known = False
            usage = attempt["usage"]
            count = usage.get("total_tokens")
            if count is None and all(
                type(usage.get(key)) is int and usage[key] >= 0
                for key in ("prompt_tokens", "completion_tokens")
            ):
                count = usage["prompt_tokens"] + usage["completion_tokens"]
            if type(count) is int and count >= 0:
                completed_tokens += count
            else:
                tokens_known = False
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
        "completed_response_cost_usd": (
            math.fsum(completed_costs) if completed and costs_known else None
        ),
        "completed_response_total_tokens": (
            completed_tokens if completed and tokens_known else None
        ),
    }
