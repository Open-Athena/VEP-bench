"""Estimate panel-level t intervals from the frozen variant-stratum scores."""

import argparse
import gzip
import hashlib
import json
import math
from pathlib import Path
from typing import Any

import numpy as np
import scipy
from scipy.stats import sem, t

KEY_FIELDS = ("task_family", "axis", "category", "run_id")


def estimate_intervals(snapshot: dict[str, Any]) -> dict[str, Any]:
    """Keep the frozen coverage and zero penalties when estimating mean uncertainty."""
    coverage = {tuple(row[field] for field in KEY_FIELDS[:3]): row for row in snapshot["coverage"]}
    rows = []
    seen = set()
    for result in snapshot["results"]:
        key = tuple(result[field] for field in KEY_FIELDS)
        group = coverage.get(key[:3])
        if key in seen or group is None or group["status"] != "eligible":
            raise ValueError("Duplicate result or missing eligible coverage")
        seen.add(key)
        expected = [
            panel["question_id"] for panel in group["panels"] if panel["status"] == "eligible"
        ]
        panels = result["panels"]
        n = len(panels)
        if (
            [panel["question_id"] for panel in panels] != expected
            or len(set(expected)) != n
            or n != group["eligible_panels"]
            or n < snapshot["policy"]["minimum_panels_per_task"]
        ):
            raise ValueError("Panel scores do not match the frozen eligible membership")
        scores = np.array([panel["spearman_rho"] for panel in panels], dtype=float)
        if not np.all(np.isfinite(scores)) or np.any(np.abs(scores) > 1):
            raise ValueError("Panel correlations must be finite and between -1 and 1")
        if any(not panel["valid"] and panel["spearman_rho"] != 0 for panel in panels):
            raise ValueError("Invalid original answers must retain their zero penalty")
        mean = result["mean_spearman_rho"]
        if not n or not math.isclose(mean, float(np.mean(scores)), rel_tol=0, abs_tol=1e-12):
            raise ValueError("Stored mean differs from the unweighted panel mean")
        row = {
            **dict(zip(KEY_FIELDS, key, strict=True)),
            "eligible_panels": n,
            "mean_spearman_rho": mean,
            "spearman_ci_low": None,
            "spearman_ci_high": None,
            "spearman_ci_status": "insufficient_panels",
        }
        if n >= 2:
            if np.all(scores == scores[0]):
                # Match the cutoff analysis: no variation is not evidence of certainty.
                row["spearman_ci_status"] = "constant_scores"
            else:
                low, high = t.interval(0.95, df=n - 1, loc=mean, scale=sem(scores, ddof=1))
                if not np.isfinite(low) or not np.isfinite(high):
                    raise ValueError("Could not estimate a finite panel-level interval")
                row.update(
                    spearman_ci_low=float(low),
                    spearman_ci_high=float(high),
                    spearman_ci_status="estimated",
                )
        rows.append(row)
    return {
        "schema_version": 1,
        "statistics": {
            "function": "scipy.stats.t.interval",
            "method": "Student's t",
            "confidence_level": 0.95,
            "alternative": "two-sided",
            "statistic": "unweighted mean within-panel Spearman correlation",
            "sampling_unit": "eligible original panel (gene or regulatory element)",
            "degrees_of_freedom": "n - 1",
            "standard_error": "scipy.stats.sem with ddof=1",
            "assumptions": "independent, identically distributed normal panel scores",
            "interpretation": (
                "Approximate exploratory intervals across selected panels, conditional on saved "
                "answers; shared assay effects and repeated-run variability are not estimated."
            ),
            "adjustment": "none; pointwise intervals, not simultaneous or pairwise tests",
            "clipped": False,
            "software": {"numpy": np.__version__, "scipy": scipy.__version__},
        },
        "intervals": rows,
    }


def main() -> None:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--input", type=Path, required=True, help="Frozen analysis JSON.gz")
    parser.add_argument("--output", type=Path, required=True)
    args = parser.parse_args()
    raw = args.input.read_bytes()
    result = estimate_intervals(json.loads(gzip.decompress(raw)))
    result["snapshot_sha256"] = hashlib.sha256(raw).hexdigest()
    result["analysis_sha256"] = hashlib.sha256(Path(__file__).read_bytes()).hexdigest()
    args.output.write_text(json.dumps(result, indent=2, sort_keys=True, allow_nan=False) + "\n")
    print(f"Exported {len(result['intervals'])} panel-level intervals to {args.output}")


if __name__ == "__main__":
    main()
