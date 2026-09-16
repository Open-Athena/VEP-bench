"""Matched-panel uncertainty uses the frozen membership and saved scores."""

import copy
import gzip
import hashlib
import json
from pathlib import Path

import pytest
from vepbench_blog_analysis import specialist_intervals, strata_intervals

POST = Path(__file__).parents[2] / "explorer/web/blog/introducing-vep-bench"


def test_committed_intervals_replay_with_pinned_sources() -> None:
    raw = (POST / "specialist-comparison.json.gz").read_bytes()
    snapshot = json.loads(gzip.decompress(raw))
    assert snapshot["plot_sha256"] == hashlib.sha256(
        (POST / "specialist-plots.js").read_bytes()
    ).hexdigest()
    assert snapshot["display_sha256"] == hashlib.sha256(
        (POST / "specialists.js").read_bytes()
    ).hexdigest()
    saved = json.loads((POST / "specialist-intervals.json").read_text())
    result = specialist_intervals.estimate_intervals(snapshot)
    assert result["intervals"] == saved["intervals"]
    assert result["statistics"] == saved["statistics"]
    assert saved["snapshot_sha256"] == hashlib.sha256(raw).hexdigest()
    assert saved["analysis_sha256"] == hashlib.sha256(
        Path(specialist_intervals.__file__).read_bytes()
    ).hexdigest()
    assert saved["estimator_sha256"] == hashlib.sha256(
        Path(strata_intervals.__file__).read_bytes()
    ).hexdigest()
    assert len(result["intervals"]) == len(snapshot["results"])
    assert {row["category"] for row in result["intervals"]} == {
        "all_covered", "excluding_avi_model_selection"
    }


def test_matched_interval_rejects_changed_coverage_without_mutating_input() -> None:
    snapshot = json.loads(gzip.decompress((POST / "specialist-comparison.json.gz").read_bytes()))
    original = copy.deepcopy(snapshot)
    specialist_intervals.estimate_intervals(snapshot)
    assert snapshot == original
    snapshot["results"][0]["panels"].pop()
    with pytest.raises(ValueError, match="membership"):
        specialist_intervals.estimate_intervals(snapshot)


def test_unfinished_predictions_cannot_have_error_bars() -> None:
    with pytest.raises(ValueError, match="complete"):
        specialist_intervals.estimate_intervals({"status": "awaiting_inference"})
