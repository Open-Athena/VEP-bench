"""Deterministic score-space sampling for task-preparation experiments.

This module is not yet used by the production task generators. Callers supply
one already validated, deduplicated assay/window at a time.
"""

from __future__ import annotations

import bisect
import hashlib
import json
import math
from collections.abc import Sequence
from dataclasses import dataclass
from typing import Literal

AnchorPolicy = Literal["minmax", "p01_p99"]


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


def quantile(ordered: Sequence[float], probability: float) -> float:
    """Hyndman-Fan type 7; callers supply a nonempty sorted finite sequence."""
    if not ordered or not 0 <= probability <= 1:
        raise ValueError("quantile requires values and a probability in [0, 1]")
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
        raise ValueError("invalid or insufficient bin capacity")
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
    anchor_policy: AnchorPolicy = "p01_p99",
    bin_count: int = 5,
    panel_size: int = 50,
) -> ScorePanel:
    """Sample equally spaced score bins, retaining overflow in the outer bins.

    Cutpoint equality goes into the bin to its right. Repeated cutpoints leave
    empty bins, never split score ties. Collapsed anchors are reported as an
    error, with no implicit policy fallback. Returned bin indices are zero-based.
    """
    if isinstance(bin_count, bool) or not isinstance(bin_count, int) or bin_count < 1:
        raise ValueError("bin_count must be a positive integer")
    allocate_bins([len(alleles)], panel_size)
    if len(alleles) < panel_size:
        raise ValueError("fewer eligible alleles than panel size")
    if any(not allele.key for allele in alleles) or len({a.key for a in alleles}) != len(alleles):
        raise ValueError("allele keys must be nonempty and unique")
    if any(isinstance(a.score, bool) or not math.isfinite(a.score) for a in alleles):
        raise ValueError("scores must be finite numbers")
    ordered = sorted(a.score for a in alleles)
    if anchor_policy == "minmax":
        lower, upper = ordered[0], ordered[-1]
    elif anchor_policy == "p01_p99":
        lower, upper = quantile(ordered, 0.01), quantile(ordered, 0.99)
    else:
        raise ValueError("unknown anchor policy")
    if lower >= upper:
        raise ValueError("collapsed score anchors")
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
