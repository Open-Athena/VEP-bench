"""Offline checks for cohort handling and SciPy's directional test."""

import copy
import hashlib
import json
from pathlib import Path

import pytest
from vepbench_blog_analysis.sge_cutoff import add_statistics, frozen_publication

from vepbench.artifacts import canonical_json, sha256_json


def test_frozen_collection_verifies_each_artifact_before_using_saved_scores(tmp_path: Path) -> None:
    question = {"question_id": "sge:one", "task_type": "ranking"}
    digest = hashlib.sha256((canonical_json(question) + "\n").encode()).hexdigest()

    def save(name: str, value: dict) -> dict:
        data = (canonical_json(value) + "\n").encode()
        (tmp_path / name).write_bytes(data)
        return {
            "path": name,
            "artifact_bytes": len(data),
            "content_bytes": len(data),
            "artifact_sha256": hashlib.sha256(data).hexdigest(),
            "content_sha256": hashlib.sha256(data).hexdigest(),
        }

    outcome = {"run_id": "run", "outcomes": [{"question_id": "sge:one", "spearman_rho": 0.5}]}
    manifest = {
        "question_set_size": 1,
        "question_set_sha256": digest,
        "artifacts": {
            "question_index": save(
                "questions.json",
                {"questions": [{**question, "question_sha256": sha256_json(question)}]},
            ),
            "runs": save("runs.json", {"runs": [], "question_set_sha256": digest}),
            "outcomes": [save("outcomes.json", outcome)],
        },
    }
    path = tmp_path / "manifest.json"
    path.write_text(json.dumps(manifest))
    assets = Path(__file__).resolve().parents[2] / "explorer/web"
    result = frozen_publication(tmp_path, path, assets)
    assert result["outcomes"] == [outcome]
    assert result["collection"]["manifest_sha256"] == hashlib.sha256(path.read_bytes()).hexdigest()
    # A same-length score change still fails the pinned content check.
    saved = tmp_path / "outcomes.json"
    saved.write_text(saved.read_text().replace("0.5", "0.9"))
    with pytest.raises(ValueError, match="digest mismatch"):
        frozen_publication(tmp_path, path, assets)


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
