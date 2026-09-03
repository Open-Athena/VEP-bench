"""Question-contract validation shared by builders, evaluators, and publishers."""

import math
from collections.abc import Mapping
from typing import Any

from jsonschema import Draft202012Validator

from ..errors import BuildError


def is_finite_number(value: Any) -> bool:
    """Return whether a JSON number has a finite binary-float representation."""

    if isinstance(value, bool) or not isinstance(value, int | float):
        return False
    try:
        return math.isfinite(float(value))
    except OverflowError:
        return False


def candidate_vcf_key(candidate: Mapping[str, Any]) -> tuple[str, int, str, str]:
    """Return the canonical visible ordering key for a ranking candidate."""

    return (candidate["chrom"], candidate["pos"], candidate["ref"], candidate["alt"])


def validate_question(question: Mapping[str, Any], validator: Draft202012Validator) -> None:
    """Validate one question's schema and cross-field invariants."""

    errors = sorted(validator.iter_errors(question), key=lambda error: list(error.path))
    if errors:
        details = "; ".join(
            f"{'.'.join(str(part) for part in error.path) or '<record>'}: {error.message}"
            for error in errors
        )
        raise BuildError(f"{question.get('question_id', '<unknown>')}: {details}")

    if question["task_type"] == "multiple_choice":
        choice_ids = [choice["choice_id"] for choice in question["choices"]]
        if len(choice_ids) != len(set(choice_ids)):
            raise BuildError(f"{question['question_id']}: choice IDs must be unique")
        if choice_ids.count(question["answer_choice_id"]) != 1:
            raise BuildError(
                f"{question['question_id']}: answer_choice_id must identify exactly one choice"
            )
        for choice in question["choices"]:
            rendered = f"{choice['choice_id']}. {choice['text']}"
            if question["prompt"].count(rendered) != 1:
                raise BuildError(
                    f"{question['question_id']}: prompt must contain choice "
                    f"{rendered!r} exactly once"
                )
        return

    candidates = question["candidates"]
    candidate_ids = [candidate["candidate_id"] for candidate in candidates]
    if len(candidate_ids) != len(set(candidate_ids)):
        raise BuildError(f"{question['question_id']}: candidate IDs must be unique")
    variant_keys = [
        (candidate["chrom"], candidate["pos"], candidate["ref"], candidate["alt"])
        for candidate in candidates
    ]
    if len(variant_keys) != len(set(variant_keys)):
        raise BuildError(f"{question['question_id']}: candidate VCF keys must be unique")
    if candidates != sorted(candidates, key=candidate_vcf_key):
        raise BuildError(
            f"{question['question_id']}: candidates must be sorted by CHROM, POS, REF, ALT"
        )
    for candidate in candidates:
        if not is_finite_number(candidate["reference_score"]):
            raise BuildError(f"{question['question_id']}: reference scores must be finite")
        rendered = (
            f"{candidate['chrom']}\t{candidate['pos']}\t{candidate['candidate_id']}\t"
            f"{candidate['ref']}\t{candidate['alt']}"
        )
        if question["prompt"].count(rendered) != 1:
            raise BuildError(
                f"{question['question_id']}: prompt must contain candidate row "
                f"{rendered!r} exactly once"
            )
