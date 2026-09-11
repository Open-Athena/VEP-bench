"""Offline checks for cohort handling and SciPy's directional test."""

import copy

import pytest
from vepbench_blog_analysis.sge_cutoff import add_statistics


def analysis(before: list[float], after: list[float]) -> dict:
    return {
        "comparison_count": 1,
        "unavailable": [],
        "summaries": [{"run_id": "model-high", "before_n": len(before), "after_n": len(after)}],
        "scores": [
            {"run_id": "model-high", "relation": relation, "spearman_rho": score}
            for relation, values in (("Before cutoff", before), ("After cutoff", after))
            for score in values
        ],
    }


def test_scipy_tests_the_before_advantage_and_preserves_input() -> None:
    original = analysis([1, 1, 1], [0, 0, 0])
    saved = copy.deepcopy(original)
    result = add_statistics(original)
    assert original == saved
    assert result["summaries"][0]["p_value"] == pytest.approx(1 / 20)
    assert result["summaries"][0]["permutations"] == 20
    assert result["statistics"]["test"] == "scipy.stats.permutation_test"
    assert result["statistics"]["alternative"] == "greater"
    assert "scipy" in result["statistics"]["software"]
    assert add_statistics(analysis([0, 0, 0], [1, 1, 1]))["summaries"][0]["p_value"] == 1


def test_unknown_panels_are_excluded_and_degenerate_groups_remain_untested() -> None:
    source = analysis([0, 0], [0, 0])
    source["scores"].append({"run_id": "model-high", "relation": "Unknown", "spearman_rho": 1})
    row = add_statistics(source)["summaries"][0]
    assert row["p_value"] == 1
    assert row["permutations"] == 6
    assert row["before_ci_low"] is None
    assert row["before_ci_high"] is None
    assert row["before_ci_status"] == "constant_scores"
    row = add_statistics(analysis([0], [1, 1]))["summaries"][0]
    assert row["p_value"] is None
    assert row["permutations"] == 0
    assert row["before_ci_status"] == "insufficient_genes"


def test_gene_mean_intervals_are_reproducible_and_keep_group_membership() -> None:
    source = analysis([0.3, 0.4, 0.5, 0.6, 0.7], [-0.5, -0.4, -0.3])
    original = copy.deepcopy(source)
    result = add_statistics(source)
    assert source == original
    assert result == add_statistics(source)
    row = result["summaries"][0]
    assert 0.5 - row["before_ci_low"] == pytest.approx(row["before_ci_high"] - 0.5)
    assert -0.4 - row["after_ci_low"] == pytest.approx(row["after_ci_high"] + 0.4)
    source["scores"].append({"run_id": "model-high", "relation": "Unknown", "spearman_rho": 1})
    assert add_statistics(source)["summaries"] == result["summaries"]
    assert result["statistics"]["confidence_interval"]["method"] == "Student's t"


def test_t_interval_uses_sample_variance_and_is_not_clipped_to_correlation_bounds() -> None:
    row = add_statistics(analysis([-1, 0, 1], [0, 0]))["summaries"][0]
    # Mean 0, sample SD 1, n=3: the 97.5th t percentile with 2 df is 4.302653.
    assert row["before_ci_low"] == pytest.approx(-2.4841377)
    assert row["before_ci_high"] == pytest.approx(2.4841377)


def test_incomplete_collection_and_bad_scores_are_rejected() -> None:
    source = analysis([1, 1], [0, 0])
    source["unavailable"] = ["another model"]
    with pytest.raises(ValueError, match="incomplete"):
        add_statistics(source)
    source = analysis([1, 1], [0, 0])
    source["summaries"][0]["before_n"] = 3
    with pytest.raises(ValueError, match="group sizes"):
        add_statistics(source)
    with pytest.raises(ValueError, match="finite"):
        add_statistics(analysis([float("nan"), 0], [0, 0]))


def test_per_model_p_values_do_not_depend_on_other_models() -> None:
    source = analysis([1, 1, 1], [0, 0, 0])
    source["comparison_count"] = 2
    source["summaries"].append({"run_id": "other", "before_n": 3, "after_n": 3})
    source["scores"].extend({**row, "run_id": "other"} for row in list(source["scores"]))
    assert [row["p_value"] for row in add_statistics(source)["summaries"]] == [0.05, 0.05]
