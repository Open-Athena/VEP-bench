"""Local OpenRouter evaluation and deterministic benchmark scoring."""

import http.client
import json
import math
import re
import time
import urllib.error
import urllib.request
from collections.abc import Callable, Iterable, Mapping
from concurrent.futures import ThreadPoolExecutor
from dataclasses import dataclass
from datetime import UTC, datetime
from pathlib import Path
from typing import Any, Protocol

from jsonschema import Draft202012Validator, FormatChecker

from .builder import (
    BuildError,
    canonical_json,
    is_finite_number,
    read_jsonl,
    sha256_file,
    sha256_json,
    validate_question,
)

OPENROUTER_ENDPOINT = "https://openrouter.ai/api/v1/chat/completions"
FINAL_ANSWER = re.compile(r"^FINAL:[ \t]*([A-Za-z0-9_-]+)[ \t]*\r?$", re.MULTILINE)
RECORD_ID = re.compile(r"^[A-Za-z0-9][A-Za-z0-9._:-]*$")
RESULT_TYPES = ("correct", "incorrect", "refusal", "token_limit", "format_error")


@dataclass
class ProviderError(RuntimeError):
    """A provider request failed or returned an unusable response."""

    message: str
    status_code: int | None = None
    raw_response: dict[str, Any] | None = None

    def __str__(self) -> str:
        return self.message


class CompletionTransport(Protocol):
    def complete(self, request_body: Mapping[str, Any], api_key: str) -> dict[str, Any]: ...


class OpenRouterTransport:
    """Minimal non-streaming OpenRouter Chat Completions transport."""

    def __init__(self, *, endpoint: str = OPENROUTER_ENDPOINT, timeout: int = 900):
        self.endpoint = endpoint
        self.timeout = timeout

    def complete(self, request_body: Mapping[str, Any], api_key: str) -> dict[str, Any]:
        request = urllib.request.Request(
            self.endpoint,
            data=canonical_json(request_body).encode("utf-8"),
            headers={
                "Authorization": f"Bearer {api_key}",
                "Content-Type": "application/json",
                "User-Agent": "VEP-bench/0.1",
                "X-OpenRouter-Metadata": "enabled",
            },
            method="POST",
        )
        try:
            with urllib.request.urlopen(request, timeout=self.timeout) as response:
                payload = response.read()
        except urllib.error.HTTPError as exc:
            try:
                payload = exc.read()
            except TimeoutError, http.client.HTTPException, OSError:
                payload = b""
            raw = _decode_json_object(payload)
            message = _provider_error_message(raw) or f"OpenRouter returned HTTP {exc.code}"
            raise ProviderError(message, status_code=exc.code, raw_response=raw) from exc
        except urllib.error.URLError as exc:
            raise ProviderError(f"OpenRouter request failed: {exc.reason}") from exc
        except (TimeoutError, http.client.HTTPException, OSError) as exc:
            raise ProviderError(f"OpenRouter request failed: {exc}") from exc

        raw = _decode_json_object(payload)
        if raw is None:
            raise ProviderError("OpenRouter returned a non-object JSON response")
        if error := _provider_error_message(raw):
            raise ProviderError(error, raw_response=raw)
        return raw


@dataclass(frozen=True)
class Score:
    parsed_answer: str | None
    value: int
    correct: bool
    parse_error: str | None


@dataclass(frozen=True)
class RankingScore:
    parsed_answer: dict[str, float] | None
    value: float
    spearman_rho: float
    pearson_r: float
    valid: bool
    parse_error: str | None


@dataclass(frozen=True)
class EvaluationSummary:
    run_id: str
    output: Path
    completed: int
    api_errors: int

    @property
    def is_complete(self) -> bool:
        return self.api_errors == 0


def score_multiple_choice(
    content: str | None, valid_choice_ids: set[str], answer_choice_id: str
) -> Score:
    matches = FINAL_ANSWER.findall(content or "")
    if not matches:
        return Score(None, 0, False, "missing FINAL: <choice-id> line")
    selected = matches[-1]
    if selected not in valid_choice_ids:
        return Score(selected, 0, False, f"unknown choice ID {selected!r}")
    correct = selected == answer_choice_id
    return Score(selected, int(correct), correct, None)


def classify_result_type(
    *,
    correct: bool,
    parse_error: str | None,
    finish_reason: str | None,
    refusal: Any = None,
) -> str:
    """Classify one completed response into the public flat result taxonomy."""

    if (isinstance(refusal, str) and refusal.strip()) or finish_reason == "content_filter":
        return "refusal"
    if parse_error is None:
        return "correct" if correct else "incorrect"
    if finish_reason == "length":
        return "token_limit"
    return "format_error"


def score_completed_response(
    content: str | None,
    valid_choice_ids: set[str],
    answer_choice_id: str,
    *,
    finish_reason: str | None,
    refusal: Any = None,
) -> tuple[Score, str]:
    """Score and classify a completed response with refusal precedence."""

    score = score_multiple_choice(content, valid_choice_ids, answer_choice_id)
    result_type = classify_result_type(
        correct=score.correct,
        parse_error=score.parse_error,
        finish_reason=finish_reason,
        refusal=refusal,
    )
    if result_type == "refusal" and score.correct:
        score = Score(score.parsed_answer, 0, False, score.parse_error)
    return score, result_type


def result_type_for_record(record: Mapping[str, Any]) -> str | None:
    """Derive a result type, including for legacy records that did not store one."""

    response = record["response"]
    if response["status"] != "completed":
        return None
    scoring = record["scoring"]
    if scoring["metric"] != "exact_match":
        return None
    return classify_result_type(
        correct=scoring["correct"],
        parse_error=scoring["parse_error"],
        finish_reason=response["finish_reason"],
        refusal=_provider_refusal(response.get("raw")),
    )


def score_ranking(
    content: str | None,
    reference_scores: Mapping[str, float],
) -> RankingScore:
    """Parse a strict final JSON mapping and compute within-question correlations."""

    parsed, parse_error = _last_final_json_object(content)
    if parse_error is not None:
        return _invalid_ranking_score(parse_error)
    assert parsed is not None
    expected_ids = set(reference_scores)
    observed_ids = set(parsed)
    missing = sorted(expected_ids - observed_ids)
    extra = sorted(observed_ids - expected_ids)
    if missing or extra:
        details = []
        if missing:
            details.append(f"missing candidate IDs {missing}")
        if extra:
            details.append(f"additional candidate IDs {extra}")
        return _invalid_ranking_score("; ".join(details))

    predictions: dict[str, float] = {}
    for candidate_id, value in parsed.items():
        if isinstance(value, bool) or not isinstance(value, int | float):
            return _invalid_ranking_score(f"prediction for {candidate_id!r} must be a JSON number")
        try:
            prediction = float(value)
        except OverflowError:
            return _invalid_ranking_score(f"prediction for {candidate_id!r} must be finite")
        if not math.isfinite(prediction):
            return _invalid_ranking_score(f"prediction for {candidate_id!r} must be finite")
        predictions[candidate_id] = prediction

    candidate_ids = list(reference_scores)
    predicted = [predictions[candidate_id] for candidate_id in candidate_ids]
    reference = []
    for candidate_id in candidate_ids:
        value = reference_scores[candidate_id]
        if not is_finite_number(value):
            return _invalid_ranking_score(f"reference score for {candidate_id!r} must be finite")
        reference.append(float(value))
    spearman = _pearson(_average_ranks(predicted), _average_ranks(reference))
    pearson = _pearson(predicted, reference)
    return RankingScore(predictions, spearman, spearman, pearson, True, None)


def _last_final_json_object(content: str | None) -> tuple[dict[str, Any] | None, str | None]:
    duplicate_error: str | None = None
    for line in reversed((content or "").splitlines()):
        match = re.fullmatch(r"FINAL:[ \t]*(\{.*\})[ \t]*", line)
        if match is None:
            continue
        duplicate_error = None

        def reject_duplicate_keys(pairs: list[tuple[str, Any]]) -> dict[str, Any]:
            value: dict[str, Any] = {}
            for key, item in pairs:
                if key in value:
                    raise ValueError(f"duplicate candidate ID {key!r}")
                value[key] = item
            return value

        try:
            parsed = json.loads(
                match.group(1),
                object_pairs_hook=reject_duplicate_keys,
                parse_constant=lambda value: (_ for _ in ()).throw(
                    ValueError(f"non-finite JSON number {value}")
                ),
            )
        except json.JSONDecodeError:
            continue
        except ValueError as exc:
            duplicate_error = str(exc)
            break
        if not isinstance(parsed, dict):
            continue
        return parsed, None
    return None, duplicate_error or "missing FINAL: <json-object> line"


def _invalid_ranking_score(message: str) -> RankingScore:
    return RankingScore(None, 0.0, 0.0, 0.0, False, message)


def _average_ranks(values: list[float]) -> list[float]:
    order = sorted(range(len(values)), key=values.__getitem__)
    ranks = [0.0] * len(values)
    start = 0
    while start < len(order):
        end = start + 1
        while end < len(order) and values[order[end]] == values[order[start]]:
            end += 1
        average = (start + 1 + end) / 2
        for index in order[start:end]:
            ranks[index] = average
        start = end
    return ranks


def _pearson(left: list[float], right: list[float]) -> float:
    if len(left) != len(right) or not left:
        raise ValueError("correlation vectors must have the same non-zero length")
    left_scale = max(abs(value) for value in left)
    right_scale = max(abs(value) for value in right)
    if left_scale == 0 or right_scale == 0:
        return 0.0
    scaled_left = [value / left_scale for value in left]
    scaled_right = [value / right_scale for value in right]
    left_mean = math.fsum(scaled_left) / len(scaled_left)
    right_mean = math.fsum(scaled_right) / len(scaled_right)
    left_delta = [value - left_mean for value in scaled_left]
    right_delta = [value - right_mean for value in scaled_right]
    left_ss = math.fsum(value * value for value in left_delta)
    right_ss = math.fsum(value * value for value in right_delta)
    if left_ss == 0 or right_ss == 0:
        return 0.0
    covariance = math.fsum(
        left_value * right_value
        for left_value, right_value in zip(left_delta, right_delta, strict=True)
    )
    return max(-1.0, min(1.0, covariance / math.sqrt(left_ss * right_ss)))


def validate_generation_parameters(parameters: Mapping[str, Any]) -> None:
    """Validate parameters that are merged into an OpenRouter request."""

    reserved = {"messages", "model", "n", "stream"} & parameters.keys()
    if reserved:
        raise BuildError(f"generation parameters contain reserved field(s): {sorted(reserved)}")
    for name in ("max_tokens", "max_completion_tokens"):
        if name in parameters and (
            isinstance(parameters[name], bool)
            or not isinstance(parameters[name], int)
            or parameters[name] < 1
        ):
            raise BuildError(f"{name} must be a positive integer")
    if "temperature" in parameters and (
        isinstance(parameters["temperature"], bool)
        or not isinstance(parameters["temperature"], int | float)
        or not math.isfinite(parameters["temperature"])
    ):
        raise BuildError("temperature must be finite")
    if "seed" in parameters and (
        isinstance(parameters["seed"], bool) or not isinstance(parameters["seed"], int)
    ):
        raise BuildError("seed must be an integer")
    canonical_json(dict(parameters))


def validate_run_id(run_id: str) -> None:
    """Validate a run identifier before any provider request is made."""

    if not RECORD_ID.fullmatch(run_id) or len(run_id) > 200:
        raise BuildError(f"invalid run_id {run_id!r}")


def evaluate_file(
    *,
    questions_path: str | Path,
    question_schema_path: str | Path,
    result_schema_path: str | Path,
    output: str | Path,
    run_id: str,
    model_id: str,
    api_key: str,
    generation_parameters: Mapping[str, Any] | None = None,
    transport: CompletionTransport | None = None,
    now: Callable[[], datetime] | None = None,
    monotonic: Callable[[], float] | None = None,
    progress: Callable[[int, int, int], None] | None = None,
    concurrency: int = 1,
    resume: bool = False,
) -> EvaluationSummary:
    validate_run_id(run_id)
    if not model_id:
        raise BuildError("model_id must be non-empty")
    if concurrency < 1:
        raise BuildError("concurrency must be positive")
    if generation_parameters is None:
        resolved_parameters: dict[str, Any] = {
            "temperature": 0.0,
            "max_tokens": 4096,
        }
    else:
        validate_generation_parameters(generation_parameters)
        resolved_parameters = json.loads(canonical_json(dict(generation_parameters)))
    validate_generation_parameters(resolved_parameters)

    questions_file = Path(questions_path)
    questions = read_jsonl(questions_file)
    schema = json.loads(Path(question_schema_path).read_text(encoding="utf-8"))
    Draft202012Validator.check_schema(schema)
    question_validator = Draft202012Validator(schema)
    for question in questions:
        validate_question(question, question_validator)
    question_ids = [question["question_id"] for question in questions]
    if question_ids != sorted(question_ids):
        raise BuildError(f"{questions_file}: questions must be sorted by question_id")
    if len(question_ids) != len(set(question_ids)):
        raise BuildError(f"{questions_file}: duplicate question IDs")
    result_schema = json.loads(Path(result_schema_path).read_text(encoding="utf-8"))
    Draft202012Validator.check_schema(result_schema)
    result_validator = Draft202012Validator(result_schema, format_checker=FormatChecker())

    output_path = Path(output)
    output_path.parent.mkdir(parents=True, exist_ok=True)
    if output_path.exists() and not resume:
        raise BuildError(f"refusing to overwrite existing run file {output_path}")

    client = transport or OpenRouterTransport()
    clock = now or (lambda: datetime.now(UTC))
    timer = monotonic or time.monotonic
    question_set_sha256 = sha256_file(questions_file)
    existing = (
        _validated_resume_prefix(
            output_path=output_path,
            questions=questions,
            result_validator=result_validator,
            run_id=run_id,
            model_id=model_id,
            generation_parameters=resolved_parameters,
            question_set_sha256=question_set_sha256,
        )
        if resume
        else []
    )
    completed = sum(result["response"]["status"] == "completed" for result in existing)
    api_errors = sum(result["response"]["status"] == "api_error" for result in existing)
    pending_questions = questions[len(existing) :]
    if progress is not None and existing:
        progress(len(existing), len(questions), api_errors)

    def evaluate_question(question: Mapping[str, Any]) -> tuple[dict[str, Any], bool]:
        request_body: dict[str, Any] = {
            "model": model_id,
            "messages": [{"role": "user", "content": question["prompt"]}],
            **resolved_parameters,
        }
        started = timer()
        try:
            raw = client.complete(request_body, api_key)
            latency = timer() - started
            result = completed_result(
                raw=raw,
                question=question,
                question_set_sha256=question_set_sha256,
                question_set_size=len(questions),
                run_id=run_id,
                model_id=model_id,
                generation_parameters=resolved_parameters,
                evaluated_at=clock(),
                latency_seconds=latency,
            )
            return result, False
        except ProviderError as exc:
            latency = timer() - started
            result = error_result(
                error=exc,
                question=question,
                question_set_sha256=question_set_sha256,
                question_set_size=len(questions),
                run_id=run_id,
                model_id=model_id,
                generation_parameters=resolved_parameters,
                evaluated_at=clock(),
                latency_seconds=latency,
            )
            return result, True

    mode = "a" if output_path.exists() else "x"
    with (
        output_path.open(mode, encoding="utf-8", newline="\n") as output_file,
        ThreadPoolExecutor(max_workers=concurrency) as executor,
    ):
        evaluated = executor.map(evaluate_question, pending_questions, buffersize=concurrency)
        for result, api_error in evaluated:
            if api_error:
                api_errors += 1
            else:
                completed += 1
            validate_result(result, result_validator)
            output_file.write(f"{canonical_json(result)}\n")
            output_file.flush()
            if progress is not None:
                progress(completed + api_errors, len(questions), api_errors)

    return EvaluationSummary(run_id, output_path, completed, api_errors)


def _validated_resume_prefix(
    *,
    output_path: Path,
    questions: list[dict[str, Any]],
    result_validator: Draft202012Validator,
    run_id: str,
    model_id: str,
    generation_parameters: Mapping[str, Any],
    question_set_sha256: str,
) -> list[dict[str, Any]]:
    """Return a validated ordered prefix from an interrupted direct run."""

    if not output_path.exists() or output_path.stat().st_size == 0:
        return []
    records = read_jsonl(output_path)
    if len(records) > len(questions):
        raise BuildError(f"cannot resume {output_path}: result count exceeds question count")
    for index, result in enumerate(records):
        validate_result(result, result_validator)
        question = questions[index]
        expected = {
            "run_id": run_id,
            "question_id": question["question_id"],
            "question_set_sha256": question_set_sha256,
            "question_set_size": len(questions),
            "question": question,
            "generation_parameters": dict(generation_parameters),
        }
        observed = {field: result.get(field) for field in expected}
        if observed != expected:
            raise BuildError(
                f"cannot resume {output_path}: record {index + 1} does not match "
                "the requested run and ordered question set"
            )
        if result["model"]["model_id"] != model_id:
            raise BuildError(
                f"cannot resume {output_path}: record {index + 1} uses model "
                f"{result['model']['model_id']!r}, expected {model_id!r}"
            )
    return records


def validate_result(result: Mapping[str, Any], validator: Draft202012Validator) -> None:
    """Validate a result record, including invariants JSON Schema cannot express."""

    errors = sorted(validator.iter_errors(result), key=lambda error: list(error.path))
    if errors:
        details = "; ".join(
            f"{'.'.join(str(part) for part in error.path) or '<record>'}: {error.message}"
            for error in errors
        )
        raise BuildError(f"{result.get('question_id', '<unknown>')}: {details}")

    question = result["question"]
    if result["question_id"] != question["question_id"]:
        raise BuildError(f"{result['question_id']}: embedded question_id does not match")
    if result["question_sha256"] != sha256_json(question):
        raise BuildError(f"{result['question_id']}: question digest does not match snapshot")
    task_type = question["task_type"]
    if task_type == "multiple_choice":
        choice_ids = [choice["choice_id"] for choice in question["choices"]]
        if len(choice_ids) != len(set(choice_ids)):
            raise BuildError(f"{result['question_id']}: result choice IDs must be unique")
        if choice_ids.count(question["answer_choice_id"]) != 1:
            raise BuildError(
                f"{result['question_id']}: result answer_choice_id must identify exactly one choice"
            )
        for choice in question["choices"]:
            rendered = f"{choice['choice_id']}. {choice['text']}"
            if question["prompt"].count(rendered) != 1:
                raise BuildError(
                    f"{result['question_id']}: result prompt must contain choice "
                    f"{rendered!r} exactly once"
                )
    else:
        candidate_ids = [candidate["candidate_id"] for candidate in question["candidates"]]
        if len(candidate_ids) != len(set(candidate_ids)):
            raise BuildError(f"{result['question_id']}: result candidate IDs must be unique")
        for candidate in question["candidates"]:
            if not is_finite_number(candidate["reference_score"]):
                raise BuildError(f"{result['question_id']}: reference scores must be finite")
            rendered = (
                f"{candidate['chrom']}\t{candidate['pos']}\t{candidate['candidate_id']}\t"
                f"{candidate['ref']}\t{candidate['alt']}"
            )
            if question["prompt"].count(rendered) != 1:
                raise BuildError(
                    f"{result['question_id']}: result prompt must contain candidate row "
                    f"{rendered!r} exactly once"
                )

    status = result["response"]["status"]
    if status == "completed":
        try:
            snapshot = provider_response_snapshot(result["response"].get("raw"))
        except ProviderError as exc:
            raise BuildError(
                f"{result['question_id']}: invalid raw provider completion: {exc}"
            ) from exc
        normalized_response = {
            field: result["response"][field] for field in ("content", "reasoning", "finish_reason")
        }
        raw_response = {
            field: snapshot[field] for field in ("content", "reasoning", "finish_reason")
        }
        if normalized_response != raw_response:
            raise BuildError(
                f"{result['question_id']}: normalized response does not match raw provider payload"
            )
        if result["model"]["upstream_provider"] != snapshot["upstream_provider"]:
            raise BuildError(
                f"{result['question_id']}: upstream provider does not match raw provider payload"
            )
        if task_type == "multiple_choice":
            expected, expected_result_type = score_completed_response(
                snapshot["content"],
                set(choice_ids),
                question["answer_choice_id"],
                finish_reason=snapshot["finish_reason"],
                refusal=snapshot["refusal"],
            )
            expected_scoring = {
                "metric": "exact_match",
                "parsed_answer": expected.parsed_answer,
                "value": expected.value,
                "correct": expected.correct,
                "parse_error": expected.parse_error,
            }
            actual_scoring = dict(result["scoring"])
            has_result_type = "result_type" in actual_scoring
            actual_result_type = actual_scoring.pop("result_type", None)
            if actual_scoring != expected_scoring:
                raise BuildError(f"{result['question_id']}: stored score does not match response")
            if has_result_type and actual_result_type != expected_result_type:
                raise BuildError(
                    f"{result['question_id']}: stored result type does not match response"
                )
        elif result["scoring"] != _score_question(snapshot["content"], question):
            raise BuildError(f"{result['question_id']}: stored score does not match response")
        if result["error"] is not None:
            raise BuildError(f"{result['question_id']}: completed response must not have error")
    else:
        expected_scoring = _null_scoring(question)
        actual_scoring = dict(result["scoring"])
        has_result_type = "result_type" in actual_scoring
        actual_result_type = actual_scoring.pop("result_type", None)
        if actual_scoring != expected_scoring or (
            has_result_type and actual_result_type is not None
        ):
            raise BuildError(f"{result['question_id']}: API error must have null scoring")
        if any(
            result["response"][field] is not None
            for field in ("content", "reasoning", "finish_reason")
        ):
            raise BuildError(f"{result['question_id']}: API error has completion data")
        if not isinstance(result["error"], Mapping):
            raise BuildError(f"{result['question_id']}: API error must include error details")


def _score_question(content: str | None, question: Mapping[str, Any]) -> dict[str, Any]:
    if question["task_type"] == "multiple_choice":
        score = score_multiple_choice(
            content,
            {choice["choice_id"] for choice in question["choices"]},
            question["answer_choice_id"],
        )
        return {
            "metric": "exact_match",
            "parsed_answer": score.parsed_answer,
            "value": score.value,
            "correct": score.correct,
            "parse_error": score.parse_error,
        }
    ranking_score = score_ranking(
        content,
        {
            candidate["candidate_id"]: candidate["reference_score"]
            for candidate in question["candidates"]
        },
    )
    return {
        "metric": "rank_correlation",
        "parsed_answer": ranking_score.parsed_answer,
        "value": ranking_score.value,
        "spearman_rho": ranking_score.spearman_rho,
        "pearson_r": ranking_score.pearson_r,
        "valid": ranking_score.valid,
        "parse_error": ranking_score.parse_error,
    }


def _null_scoring(question: Mapping[str, Any]) -> dict[str, Any]:
    if question["task_type"] == "multiple_choice":
        return {
            "metric": "exact_match",
            "parsed_answer": None,
            "value": None,
            "correct": None,
            "parse_error": None,
        }
    return {
        "metric": "rank_correlation",
        "parsed_answer": None,
        "value": None,
        "spearman_rho": None,
        "pearson_r": None,
        "valid": None,
        "parse_error": None,
    }


def validate_batch_usage_allocations(records: Iterable[Mapping[str, Any]], *, context: str) -> None:
    """Validate aggregate batch receipts and their per-result allocations."""

    groups: dict[tuple[str, str], list[tuple[str, float, Mapping[str, Any]]]] = {}
    for record in records:
        usage = record.get("usage")
        if not isinstance(usage, Mapping):
            continue
        provenance = usage.get("vepbench")
        if not isinstance(provenance, Mapping):
            continue
        if provenance.get("cost_source") != "allocated_batch_total":
            continue

        run_id = record.get("run_id")
        question_id = record.get("question_id")
        batch_id = provenance.get("batch_id")
        batch_usage = provenance.get("batch_usage")
        allocation_method = provenance.get("cost_allocation")
        batch_question_ids = provenance.get("batch_question_ids")
        allocated_cost = usage.get("cost")
        if (
            not isinstance(run_id, str)
            or not isinstance(question_id, str)
            or not isinstance(batch_id, str)
            or not batch_id
            or not isinstance(batch_usage, Mapping)
            or allocation_method not in {"proportional_total_tokens", "equal"}
            or not isinstance(batch_question_ids, list)
            or not batch_question_ids
            or any(not isinstance(item, str) or not item for item in batch_question_ids)
            or batch_question_ids != sorted(set(batch_question_ids))
            or question_id not in batch_question_ids
            or not _is_nonnegative_number(allocated_cost)
            or not _is_nonnegative_number(batch_usage.get("cost"))
        ):
            raise BuildError(f"{context}: invalid allocated batch cost provenance")
        assert isinstance(allocated_cost, int | float)
        groups.setdefault((run_id, batch_id), []).append(
            (question_id, float(allocated_cost), provenance)
        )

    for (run_id, batch_id), allocated in groups.items():
        reference = allocated[0][2]
        expected_question_ids = reference["batch_question_ids"]
        for _, _, provenance in allocated[1:]:
            if (
                provenance.get("batch_usage") != reference["batch_usage"]
                or provenance.get("cost_allocation") != reference["cost_allocation"]
                or provenance.get("batch_question_ids") != expected_question_ids
            ):
                raise BuildError(
                    f"{context}: batch {batch_id!r} has inconsistent allocation provenance"
                )
        actual_question_ids = sorted(question_id for question_id, _, _ in allocated)
        if actual_question_ids != expected_question_ids:
            raise BuildError(
                f"{context}: batch {batch_id!r} allocations do not cover its recorded members"
            )
        allocated_total = math.fsum(cost for _, cost, _ in allocated)
        receipt_total = float(reference["batch_usage"]["cost"])
        rounding_tolerance = max(math.ulp(allocated_total), math.ulp(receipt_total))
        if not math.isclose(
            allocated_total,
            receipt_total,
            rel_tol=0.0,
            abs_tol=rounding_tolerance,
        ):
            raise BuildError(
                f"{context}: batch {batch_id!r} allocations do not sum to its receipt "
                f"for run {run_id!r}"
            )


def _is_nonnegative_number(value: Any) -> bool:
    return (
        not isinstance(value, bool)
        and isinstance(value, int | float)
        and math.isfinite(value)
        and value >= 0
    )


def completed_result(
    *,
    raw: dict[str, Any],
    question: Mapping[str, Any],
    question_set_sha256: str,
    question_set_size: int,
    run_id: str,
    model_id: str,
    generation_parameters: Mapping[str, Any],
    evaluated_at: datetime,
    latency_seconds: float | None,
    provider_response: dict[str, Any] | None = None,
) -> dict[str, Any]:
    snapshot = provider_response_snapshot(raw)
    if question["task_type"] == "multiple_choice":
        score, result_type = score_completed_response(
            snapshot["content"],
            {choice["choice_id"] for choice in question["choices"]},
            question["answer_choice_id"],
            finish_reason=snapshot["finish_reason"],
            refusal=snapshot["refusal"],
        )
        scoring = {
            "metric": "exact_match",
            "parsed_answer": score.parsed_answer,
            "value": score.value,
            "correct": score.correct,
            "parse_error": score.parse_error,
            "result_type": result_type,
        }
    else:
        scoring = _score_question(snapshot["content"], question)

    usage = raw.get("usage")
    if not isinstance(usage, dict):
        usage = {}
    return _result_base(
        question=question,
        question_set_sha256=question_set_sha256,
        question_set_size=question_set_size,
        run_id=run_id,
        model_id=model_id,
        upstream_provider=snapshot["upstream_provider"],
        generation_parameters=generation_parameters,
        evaluated_at=evaluated_at,
        response={
            "status": "completed",
            "content": snapshot["content"],
            "reasoning": snapshot["reasoning"],
            "finish_reason": snapshot["finish_reason"],
            "latency_seconds": latency_seconds,
            "raw": raw if provider_response is None else provider_response,
        },
        scoring=scoring,
        usage=usage,
        error=None,
    )


def error_result(
    *,
    error: ProviderError,
    question: Mapping[str, Any],
    question_set_sha256: str,
    question_set_size: int,
    run_id: str,
    model_id: str,
    generation_parameters: Mapping[str, Any],
    evaluated_at: datetime,
    latency_seconds: float | None,
) -> dict[str, Any]:
    return _result_base(
        question=question,
        question_set_sha256=question_set_sha256,
        question_set_size=question_set_size,
        run_id=run_id,
        model_id=model_id,
        upstream_provider=None,
        generation_parameters=generation_parameters,
        evaluated_at=evaluated_at,
        response={
            "status": "api_error",
            "content": None,
            "reasoning": None,
            "finish_reason": None,
            "latency_seconds": latency_seconds,
            "raw": error.raw_response,
        },
        scoring={
            **_null_scoring(question),
            **({"result_type": None} if question["task_type"] == "multiple_choice" else {}),
        },
        usage={},
        error={
            "type": "provider_error",
            "message": str(error),
            "status_code": error.status_code,
        },
    )


def _result_base(
    *,
    question: Mapping[str, Any],
    question_set_sha256: str,
    question_set_size: int,
    run_id: str,
    model_id: str,
    upstream_provider: str | None,
    generation_parameters: Mapping[str, Any],
    evaluated_at: datetime,
    response: Mapping[str, Any],
    scoring: Mapping[str, Any],
    usage: Mapping[str, Any],
    error: Mapping[str, Any] | None,
) -> dict[str, Any]:
    return {
        "schema_version": "2.0" if question["task_type"] == "ranking" else "1.0",
        "run_id": run_id,
        "question_id": question["question_id"],
        "question_sha256": sha256_json(question),
        "question_set_sha256": question_set_sha256,
        "question_set_size": question_set_size,
        "completion_index": 0,
        "evaluated_at": evaluated_at.astimezone(UTC).isoformat().replace("+00:00", "Z"),
        "question": dict(question),
        "model": {
            "gateway": "openrouter",
            "model_id": model_id,
            "model_revision": None,
            "upstream_provider": upstream_provider,
        },
        "generation_parameters": dict(generation_parameters),
        "response": dict(response),
        "scoring": dict(scoring),
        "usage": dict(usage),
        "error": None if error is None else dict(error),
    }


def _first_choice(raw: Mapping[str, Any]) -> tuple[dict[str, Any], dict[str, Any]]:
    choices = raw.get("choices")
    if not isinstance(choices, list) or not choices or not isinstance(choices[0], dict):
        raise ProviderError("OpenRouter response has no completion choice", raw_response=dict(raw))
    choice = choices[0]
    message = choice.get("message")
    if not isinstance(message, dict):
        raise ProviderError("OpenRouter response choice has no message", raw_response=dict(raw))
    return choice, message


def _completion_payload(raw: Any) -> Mapping[str, Any]:
    if not isinstance(raw, Mapping):
        raise ProviderError("OpenRouter response is not an object")
    response = raw.get("response")
    if isinstance(response, Mapping) and isinstance(response.get("body"), Mapping):
        return response["body"]
    return raw


def provider_response_snapshot(raw: Any) -> dict[str, Any]:
    """Extract normalized completion fields from a direct or batch payload."""

    body = _completion_payload(raw)
    choice, message = _first_choice(body)
    content = message.get("content")
    if content is not None and not isinstance(content, str):
        raise ProviderError("OpenRouter response content is not a string", raw_response=dict(body))
    finish_reason = choice.get("finish_reason")
    if finish_reason is not None and not isinstance(finish_reason, str):
        raise ProviderError(
            "OpenRouter response finish_reason is not a string", raw_response=dict(body)
        )
    return {
        "content": content,
        "reasoning": _extract_reasoning(message),
        "finish_reason": finish_reason,
        "upstream_provider": _extract_provider(body),
        "refusal": message.get("refusal"),
    }


def _provider_refusal(raw: Any) -> Any:
    """Return a structured refusal from a direct or batch OpenRouter payload."""

    try:
        body = _completion_payload(raw)
    except ProviderError:
        return None
    choices = body.get("choices")
    if not isinstance(choices, list) or not choices or not isinstance(choices[0], Mapping):
        return None
    message = choices[0].get("message")
    return message.get("refusal") if isinstance(message, Mapping) else None


def _extract_reasoning(message: Mapping[str, Any]) -> str | None:
    for key in ("reasoning", "reasoning_content"):
        value = message.get(key)
        if isinstance(value, str) and value:
            return value
    details = message.get("reasoning_details")
    if not isinstance(details, list):
        return None
    exposed: list[str] = []
    for detail in details:
        if not isinstance(detail, dict):
            continue
        if detail.get("type") == "reasoning.summary":
            value = detail.get("summary")
        elif detail.get("type") == "reasoning.text":
            value = detail.get("text")
        else:
            continue
        if isinstance(value, str) and value:
            exposed.append(value)
    return "\n\n".join(exposed) or None


def _extract_provider(raw: Mapping[str, Any]) -> str | None:
    provider = raw.get("provider")
    metadata = raw.get("openrouter_metadata")
    if provider is None and isinstance(metadata, dict):
        provider = metadata.get("provider")
        endpoints = metadata.get("endpoints")
        if provider is None and isinstance(endpoints, dict):
            available = endpoints.get("available")
            if isinstance(available, list):
                selected = next(
                    (
                        endpoint.get("provider")
                        for endpoint in available
                        if isinstance(endpoint, dict) and endpoint.get("selected") is True
                    ),
                    None,
                )
                provider = selected
        if provider is None:
            attempts = metadata.get("attempts")
            if isinstance(attempts, list):
                successful = [
                    attempt.get("provider")
                    for attempt in attempts
                    if isinstance(attempt, dict)
                    and isinstance(attempt.get("status"), int)
                    and 200 <= attempt["status"] < 300
                ]
                if successful:
                    provider = successful[-1]
    if provider is None:
        return None
    return provider if isinstance(provider, str) else canonical_json(provider)


def _decode_json_object(payload: bytes) -> dict[str, Any] | None:
    try:
        value = json.loads(payload.decode("utf-8"))
    except UnicodeDecodeError, json.JSONDecodeError:
        return None
    return value if isinstance(value, dict) else None


def _provider_error_message(raw: Mapping[str, Any] | None) -> str | None:
    if not raw or "error" not in raw:
        return None
    error = raw["error"]
    if isinstance(error, str):
        return error
    if isinstance(error, dict) and isinstance(error.get("message"), str):
        return error["message"]
    return "OpenRouter returned an API error"
