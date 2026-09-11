"""Check panel-level uncertainty without model calls or publication downloads."""

import copy
import math

import pytest
from vepbench_blog_analysis.strata_intervals import estimate_intervals


def snapshot(scores: list[float]) -> dict:
    group = {"task_family": "sge", "axis": "allele_type", "category": "SNV"}
    panels = [
        {"question_id": f"gene-{i}", "spearman_rho": score, "valid": True}
        for i, score in enumerate(scores)
    ]
    return {
        "policy": {"minimum_panels_per_task": 5},
        "coverage": [
            {
                **group,
                "status": "eligible",
                "eligible_panels": len(panels),
                "panels": [{**panel, "status": "eligible"} for panel in panels],
            }
        ],
        "results": [
            {
                **group,
                "run_id": "model-high",
                "panels": panels,
                "mean_spearman_rho": sum(scores) / len(scores),
            }
        ],
    }


def test_t_interval_uses_sample_variance_and_keeps_zero_penalties() -> None:
    source = snapshot([-1, -0.5, 0, 0.5, 1])
    source["results"][0]["panels"][2]["valid"] = False
    original = copy.deepcopy(source)
    result = estimate_intervals(source)
    row = result["intervals"][0]
    # Mean 0, sample variance 0.625, n=5; 97.5th t percentile with 4 df = 2.776445.
    margin = 2.7764451051977987 * math.sqrt(0.625 / 5)
    assert row["spearman_ci_low"] == pytest.approx(-margin)
    assert row["spearman_ci_high"] == pytest.approx(margin)
    assert row["eligible_panels"] == 5
    assert source == original
    assert result == estimate_intervals(source)


def test_bounds_are_unclipped_and_constant_scores_have_no_estimated_interval() -> None:
    row = estimate_intervals(snapshot([-1, -1, 1, 1, 1]))["intervals"][0]
    assert row["spearman_ci_high"] > 1
    row = estimate_intervals(snapshot([0] * 5))["intervals"][0]
    assert row["spearman_ci_status"] == "constant_scores"
    assert row["spearman_ci_low"] is None and row["spearman_ci_high"] is None


@pytest.mark.parametrize(
    "problem", ["duplicate", "missing", "coverage", "minimum", "mean", "nonfinite", "penalty"]
)
def test_changed_membership_or_scores_cannot_silently_change_the_estimate(problem: str) -> None:
    source = snapshot([0, 0.2, 0.4, 0.6, 0.8])
    row = source["results"][0]
    if problem == "duplicate":
        source["results"].append(copy.deepcopy(row))
    elif problem == "missing":
        row["panels"].pop()
    elif problem == "coverage":
        source["coverage"][0]["status"] = "insufficient_coverage"
    elif problem == "minimum":
        source["policy"]["minimum_panels_per_task"] = 6
    elif problem == "mean":
        row["mean_spearman_rho"] = 0.8
    elif problem == "nonfinite":
        row["panels"][0]["spearman_rho"] = float("nan")
    else:
        row["panels"][1]["valid"] = False
    with pytest.raises(ValueError):
        estimate_intervals(source)
