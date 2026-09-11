"""Resolve explicit retries while retaining original attempts and receipts."""

import json
import math
from collections.abc import Iterable, Mapping
from copy import deepcopy
from datetime import datetime
from pathlib import Path
from typing import Any

from jsonschema import Draft202012Validator, FormatChecker

from vepbench.artifacts import canonical_json, read_jsonl, sha256_json
from vepbench.errors import BuildError
from vepbench.evaluation.core import validate_batch_usage_allocations, validate_result
from vepbench.resources import RESULT_SCHEMA


def retry_metadata(usage: Mapping[str, Any]) -> dict[str, Any] | None:
    provenance = usage.get("vepbench", {})
    if not isinstance(provenance, dict):
        raise BuildError("invalid VEPBench usage provenance")
    metadata = provenance.get("retry")
    if metadata is not None and (
        not isinstance(metadata, dict)
        or set(metadata) != {"prior_attempt", "source_run_id", "source_record_sha256"}
        or not isinstance(metadata["prior_attempt"], dict)
    ):
        raise BuildError("invalid retry provenance")
    return metadata


def retry_policy(usage: Mapping[str, Any]) -> dict[str, Any] | None:
    retry_metadata(usage)  # Also validate the provenance container.
    policy = usage.get("vepbench", {}).get("retry_policy")
    if policy is not None and (
        not isinstance(policy, dict)
        or set(policy) != {"kind", "initial_max_tokens", "retry_max_tokens"}
        or policy["kind"] != "invalid_truncation_once"
        or type(policy["initial_max_tokens"]) is not int
        or type(policy["retry_max_tokens"]) is not int
        or not 0 < policy["initial_max_tokens"] < policy["retry_max_tokens"]
    ):
        raise BuildError("invalid truncation retry policy")
    return policy


def initial_parameters(record: Mapping[str, Any]) -> dict[str, Any]:
    parameters = dict(record["generation_parameters"])
    policy = retry_policy(record["usage"])
    if policy is not None:
        retried = retry_metadata(record["usage"]) is not None
        expected = policy["retry_max_tokens" if retried else "initial_max_tokens"]
        if parameters.get("max_tokens") != expected or "max_completion_tokens" in parameters:
            raise BuildError("request token limit does not match retry policy")
        parameters["max_tokens"] = policy["initial_max_tokens"]
    return parameters


def retained_parameters(run: Mapping[str, Any], usage: Mapping[str, Any]) -> dict[str, Any]:
    policy = retry_policy(usage)
    if policy != run.get("retry_policy"):
        raise BuildError("answer retry policy does not match run")
    parameters = dict(run["generation_parameters"])
    if policy is not None:
        if parameters.get("max_tokens") != policy["initial_max_tokens"]:
            raise BuildError("run token limit does not match retry policy")
        if retry_metadata(usage) is not None:
            parameters["max_tokens"] = policy["retry_max_tokens"]
    return parameters


def invalid_truncation(record: Mapping[str, Any]) -> bool:
    return (
        record["response"]["status"] == "completed"
        and record["response"]["finish_reason"] == "length"
        and record["scoring"]["parse_error"] is not None
    )


def validate_retry_record(record: Mapping[str, Any]) -> None:
    policy = retry_policy(record["usage"])
    parameters = initial_parameters(record)
    metadata = retry_metadata(record["usage"])
    if metadata is None:
        if policy is not None and invalid_truncation(record):
            raise BuildError("retry policy requires every initial invalid truncation to be retried")
        return
    prior = metadata["prior_attempt"]
    if retry_metadata(prior.get("usage", {})) is not None or retry_policy(prior["usage"]):
        raise BuildError("nested retries are not supported")
    validator = Draft202012Validator(
        json.loads(RESULT_SCHEMA.read_text()), format_checker=FormatChecker()
    )
    validate_result(prior, validator)
    if policy is not None:
        if not invalid_truncation(prior) or record["response"]["status"] != "completed":
            raise BuildError("truncation retry must replace an invalid truncated completion")
    elif prior["response"]["status"] != "api_error" or record["response"]["status"] != "completed":
        raise BuildError("retry must replace an API error with a completed response")
    if parameters != prior["generation_parameters"]:
        raise BuildError("retry changed generation_parameters beyond the declared token limit")
    for field in ("question", "question_set_sha256", "question_set_size"):
        if record[field] != prior[field]:
            raise BuildError(f"retry changed {field}")
    for field in ("gateway", "model_id", "model_revision"):
        if record["model"].get(field) != prior["model"].get(field):
            raise BuildError("retry changed model")
    if datetime.fromisoformat(record["evaluated_at"]) < datetime.fromisoformat(
        prior["evaluated_at"]
    ):
        raise BuildError("retry predates the original attempt")
    source = deepcopy(dict(record))
    source["run_id"] = metadata["source_run_id"]
    del source["usage"]["vepbench"]["retry"]
    if policy is not None:
        del source["usage"]["vepbench"]["retry_policy"]
    if not source["usage"]["vepbench"]:
        del source["usage"]["vepbench"]
    if sha256_json(source) != metadata["source_record_sha256"]:
        raise BuildError("retry source digest does not match")


def attempt_records(records: Iterable[Mapping[str, Any]]) -> list[dict[str, Any]]:
    attempts = []
    for record in records:
        metadata = retry_metadata(record["usage"])
        if metadata is not None:
            prior = metadata["prior_attempt"]
            attempts.append(
                {
                    "run_id": record["run_id"],
                    "question_id": record["question_id"],
                    "usage": prior["usage"],
                }
            )
        attempts.append(
            {
                "run_id": record["run_id"],
                "question_id": record["question_id"],
                "usage": record["usage"],
            }
        )
    return attempts


def validate_attempt_allocations(records: Iterable[Mapping[str, Any]], *, context: str) -> None:
    validate_batch_usage_allocations(attempt_records(records), context=context)


def attempt_totals(records: Iterable[Mapping[str, Any]]) -> tuple[int | None, float | None]:
    """Use full batch token receipts when a failed item has no individual usage."""
    groups: dict[tuple[str, str], list[Mapping[str, Any]]] = {}
    costs = []
    for index, record in enumerate(attempt_records(records)):
        usage = record["usage"]
        provenance = usage.get("vepbench", {})
        key = (record["run_id"], provenance.get("batch_id", f"direct:{index}"))
        groups.setdefault(key, []).append(usage)
        costs.append(usage.get("cost"))
    token_totals: list[int | None] = []
    for usages in groups.values():
        counts = []
        for usage in usages:
            count = usage.get("total_tokens")
            if count is None and all(
                type(usage.get(k)) is int for k in ("prompt_tokens", "completion_tokens")
            ):
                count = usage["prompt_tokens"] + usage["completion_tokens"]
            counts.append(count)
        if all(type(count) is int and count >= 0 for count in counts):
            token_totals.append(sum(count for count in counts if isinstance(count, int)))
        else:
            receipt = usages[0].get("vepbench", {}).get("batch_usage", {}).get("total_tokens")
            known = sum(count for count in counts if type(count) is int and count >= 0)
            token_totals.append(receipt if type(receipt) is int and receipt >= known else None)
    tokens = (
        sum(value for value in token_totals if value is not None)
        if all(value is not None for value in token_totals)
        else None
    )
    cost = (
        math.fsum(costs)
        if all(type(c) in (int, float) and math.isfinite(c) and c >= 0 for c in costs)
        else None
    )
    return tokens, cost


def resolve_retry(*, original: Path, retry: Path, output: Path) -> None:
    """Export a full task using one explicit retry; never select by score."""
    if output.exists():
        raise BuildError(f"refusing to overwrite {output}")
    records = read_jsonl(original)
    retried = read_jsonl(retry)
    if len(retried) != 1 or not records:
        raise BuildError("provide one retry response and a full original task run")
    validator = Draft202012Validator(
        json.loads(RESULT_SCHEMA.read_text()), format_checker=FormatChecker()
    )
    for record in [*records, *retried]:
        validate_result(record, validator)
        if retry_metadata(record["usage"]) is not None or retry_policy(record["usage"]):
            raise BuildError("source already contains retry provenance")
    ids = [record["question_id"] for record in records]
    first = records[0]
    if len({record["question"]["metadata"]["task_family"] for record in records}) != 1:
        raise BuildError("retry resolution requires a single task family")
    if len(records) != first["question_set_size"] or ids != sorted(set(ids)):
        raise BuildError("original run must cover its complete ordered question set")
    identity = ("run_id", "question_set_sha256", "question_set_size", "generation_parameters")
    if any(any(record[field] != first[field] for field in identity) for record in records):
        raise BuildError("original records do not share a run identity")
    failures = [record for record in records if record["response"]["status"] == "api_error"]
    if len(failures) != 1 or failures[0]["question_id"] != retried[0]["question_id"]:
        raise BuildError("retry must target the original run's single API error")
    validate_batch_usage_allocations(records, context=str(original))
    validate_batch_usage_allocations(retried, context=str(retry))
    selected = deepcopy(retried[0])
    selected["run_id"] = first["run_id"]
    selected["usage"].setdefault("vepbench", {})["retry"] = {
        "prior_attempt": failures[0],
        "source_run_id": retried[0]["run_id"],
        "source_record_sha256": sha256_json(retried[0]),
    }
    validate_result(selected, validator)
    validate_retry_record(selected)
    resolved = [
        selected if record["question_id"] == selected["question_id"] else record
        for record in records
    ]
    validate_attempt_allocations(resolved, context=str(output))
    output.parent.mkdir(parents=True, exist_ok=True)
    with output.open("x", encoding="utf-8", newline="\n") as target:
        for record in resolved:
            target.write(canonical_json(record) + "\n")


def resolve_truncations(
    *, original: Path, retry: Path | None, max_tokens: int, output: Path
) -> None:
    """Retain exactly one larger-cap retry of every invalid truncated answer.

    Apply the same policy to tasks with no eligible answers, so aggregation does
    not mix the selective-retry experiment with a uniform single-attempt run.
    """
    if output.exists():
        raise BuildError(f"refusing to overwrite {output}")
    records = read_jsonl(original)
    retried = read_jsonl(retry) if retry is not None else []
    if not records:
        raise BuildError("provide a full original task run")
    validator = Draft202012Validator(
        json.loads(RESULT_SCHEMA.read_text()), format_checker=FormatChecker()
    )
    for record in [*records, *retried]:
        validate_result(record, validator)
        if retry_metadata(record["usage"]) is not None or retry_policy(record["usage"]):
            raise BuildError("source already contains retry provenance")
        if record["response"]["status"] != "completed":
            raise BuildError("truncation resolution requires completed responses")
    first = records[0]
    ids = [record["question_id"] for record in records]
    if len(records) != first["question_set_size"] or ids != sorted(set(ids)):
        raise BuildError("original run must cover its complete ordered question set")
    if len({record["question"]["metadata"]["task_family"] for record in records}) != 1:
        raise BuildError("retry resolution requires a single task family")
    identity = ("run_id", "question_set_sha256", "question_set_size", "generation_parameters")
    if any(any(record[field] != first[field] for field in identity) for record in records):
        raise BuildError("original records do not share a run identity")
    if any(
        any(
            record["model"].get(key) != first["model"].get(key)
            for key in ("gateway", "model_id", "model_revision")
        )
        for record in records
    ):
        raise BuildError("original records do not share a model")
    eligible = {record["question_id"] for record in records if invalid_truncation(record)}
    replacements = {record["question_id"]: record for record in retried}
    if set(replacements) != eligible or len(replacements) != len(retried):
        raise BuildError("provide exactly one retry for every initial invalid truncation")
    policy = {
        "kind": "invalid_truncation_once",
        "initial_max_tokens": first["generation_parameters"].get("max_tokens"),
        "retry_max_tokens": max_tokens,
    }
    retry_policy({"vepbench": {"retry_policy": policy}})
    validate_batch_usage_allocations(records, context=str(original))
    validate_batch_usage_allocations(retried, context=str(retry))
    resolved = []
    for record in records:
        source = replacements.get(record["question_id"], record)
        selected = deepcopy(source)
        selected["run_id"] = first["run_id"] + "-truncation-retry"
        provenance = selected["usage"].setdefault("vepbench", {})
        provenance["retry_policy"] = policy
        if record["question_id"] in replacements:
            provenance["retry"] = {
                "prior_attempt": record,
                "source_run_id": source["run_id"],
                "source_record_sha256": sha256_json(source),
            }
        validate_result(selected, validator)
        validate_retry_record(selected)
        resolved.append(selected)
    validate_attempt_allocations(resolved, context=str(output))
    output.parent.mkdir(parents=True, exist_ok=True)
    with output.open("x", encoding="utf-8", newline="\n") as target:
        for record in resolved:
            target.write(canonical_json(record) + "\n")
