"""Deterministic score-space sampling of validated, unique assay alleles."""

from __future__ import annotations

import bisect
import hashlib
import json
import math
from collections.abc import Sequence
from dataclasses import dataclass
from typing import Any

from vepbench.errors import BuildError


class SamplingError(BuildError, ValueError):
    """An eligible population cannot satisfy the pinned sampling protocol."""


SAMPLING_CONFIG = {
    "panel_size": 50,
    "score_bins": 5,
    "anchor_policy": "p01_p99",
    "seed": "2026090400",
    "algorithm": "sha256_score_space_v1",
}
SAMPLING_CONVENTIONS = {
    "anchor_policy": "p01_p99",
    "interpolation": "hyndman_fan_type_7",
    "cutpoint_equality": "right_bin",
    "tails": "outer_bins_without_score_clipping",
    "sparse_bins": "capped_even_allocation_ascending_bin_remainders",
}


def validate_sampling_config(value: Any) -> None:
    if value != SAMPLING_CONFIG:
        raise BuildError("unsupported score-space sampling configuration")


def validate_sampling_provenance(
    document: Any, selected: Sequence[tuple[float, int]], *, seed: str, algorithm: str
) -> None:
    """Check the bin boundaries, capacities and selected-score allocation offline."""
    try:
        lower, upper = document["anchors"]
        cuts = document["cutpoints"]
        counts = document["population_counts"]
        allocations = document["allocations"]
        if (
            any(document.get(k) != v for k, v in SAMPLING_CONVENTIONS.items())
            or document["seed"] != seed
            or document["algorithm"] != algorithm
            or not math.isfinite(lower)
            or not math.isfinite(upper)
            or lower >= upper
            or len(counts) != 5
            or tuple(allocations) != allocate_bins(counts)
            or list(cuts) != [(1 - i / 5) * lower + (i / 5) * upper for i in range(1, 5)]
            or len(selected) != 50
        ):
            raise SamplingError("invalid sampling parameters")
        observed = [0] * 5
        for score, index in selected:
            if (
                isinstance(index, bool)
                or not isinstance(index, int)
                or not math.isfinite(score)
                or index != bisect.bisect_right(cuts, score)
            ):
                raise SamplingError("score-bin membership mismatch")
            observed[index] += 1
        if observed != list(allocations):
            raise SamplingError("selected bin allocation mismatch")
    except (KeyError, TypeError, ValueError, IndexError) as exc:
        raise BuildError("invalid score-space sampling provenance") from exc


@dataclass(frozen=True)
class ScoredAllele:
    key: str
    score: float


@dataclass(frozen=True)
class ScorePanel:
    anchors: tuple[float, float]
    cutpoints: tuple[float, ...]
    population_counts: tuple[int, ...]
    allocations: tuple[int, ...]
    selected: tuple[tuple[ScoredAllele, int], ...]

    def provenance(self) -> dict[str, Any]:
        return {
            **SAMPLING_CONVENTIONS,
            "anchors": list(self.anchors),
            "cutpoints": list(self.cutpoints),
            "population_counts": list(self.population_counts),
            "allocations": list(self.allocations),
        }


def quantile(ordered: Sequence[float], probability: float) -> float:
    """Hyndman-Fan type 7; callers supply a nonempty sorted finite sequence."""
    if not ordered or not 0 <= probability <= 1:
        raise SamplingError("quantile requires values and a probability in [0, 1]")
    position = (len(ordered) - 1) * probability
    lower = math.floor(position)
    upper = math.ceil(position)
    fraction = position - lower
    return (1 - fraction) * ordered[lower] + fraction * ordered[upper]


def allocate_bins(capacities: Sequence[int], panel_size: int = 50) -> tuple[int, ...]:
    """Even allocation capped by population, breaking remainder ties by bin index."""
    if (
        isinstance(panel_size, bool)
        or not isinstance(panel_size, int)
        or panel_size < 1
        or not capacities
        or any(isinstance(n, bool) or not isinstance(n, int) or n < 0 for n in capacities)
        or sum(capacities) < panel_size
    ):
        raise SamplingError("invalid or insufficient bin capacity")
    allocations = [0] * len(capacities)
    remaining = panel_size
    while remaining:
        for index, capacity in enumerate(capacities):
            if allocations[index] < capacity:
                allocations[index] += 1
                remaining -= 1
                if not remaining:
                    break
    return tuple(allocations)


def sampling_digest(seed: str, question_key: str, bin_index: int, allele_key: str) -> str:
    payload = json.dumps([seed, question_key, bin_index, allele_key], separators=(",", ":"))
    return hashlib.sha256(payload.encode("utf-8")).hexdigest()


def sample_score_bins(
    alleles: Sequence[ScoredAllele],
    *,
    question_key: str,
    seed: str,
    bin_count: int = 5,
    panel_size: int = 50,
) -> ScorePanel:
    """Sample equally spaced score bins, retaining overflow in the outer bins.

    Cutpoint equality goes into the bin to its right. Repeated cutpoints leave
    empty bins, never split score ties. Collapsed anchors are reported as an
    error, with no implicit policy fallback. Returned bin indices are zero-based.
    """
    if isinstance(bin_count, bool) or not isinstance(bin_count, int) or bin_count < 1:
        raise SamplingError("bin_count must be a positive integer")
    allocate_bins([len(alleles)], panel_size)
    if len(alleles) < panel_size:
        raise SamplingError("fewer eligible alleles than panel size")
    if any(not allele.key for allele in alleles) or len({a.key for a in alleles}) != len(alleles):
        raise SamplingError("allele keys must be nonempty and unique")
    if any(isinstance(a.score, bool) or not math.isfinite(a.score) for a in alleles):
        raise SamplingError("scores must be finite numbers")
    ordered = sorted(a.score for a in alleles)
    lower, upper = quantile(ordered, 0.01), quantile(ordered, 0.99)
    if lower >= upper:
        raise SamplingError("collapsed score anchors")
    cutpoints = tuple(
        (1 - index / bin_count) * lower + (index / bin_count) * upper
        for index in range(1, bin_count)
    )
    bins: list[list[ScoredAllele]] = [[] for _ in range(bin_count)]
    for allele in alleles:
        bins[bisect.bisect_right(cutpoints, allele.score)].append(allele)
    counts = tuple(map(len, bins))
    allocations = allocate_bins(counts, panel_size)
    selected: list[tuple[ScoredAllele, int]] = []
    for index, (population, count) in enumerate(zip(bins, allocations, strict=True)):
        population.sort(key=lambda a: (sampling_digest(seed, question_key, index, a.key), a.key))
        selected.extend((allele, index) for allele in population[:count])
    return ScorePanel((lower, upper), cutpoints, counts, allocations, tuple(selected))
