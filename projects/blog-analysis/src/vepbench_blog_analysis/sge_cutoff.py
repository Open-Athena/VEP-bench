"""Export per-model SGE cutoff statistics using SciPy."""

import argparse
import copy
import json
import math
import subprocess
from datetime import UTC, datetime
from pathlib import Path
from typing import Any

import numpy as np
import scipy
from scipy.stats import permutation_test, sem, t
from vepbench_explorer.config import load_site_config
from vepbench_explorer.site import build_question_metadata, load_assay_publications


def add_statistics(analysis: dict[str, Any]) -> dict[str, Any]:
    """Test the before-cutoff advantage separately for each selected model."""
    if analysis["unavailable"] or len(analysis["summaries"]) != analysis["comparison_count"]:
        raise ValueError("Refusing to export statistics from an incomplete collection")
    result = copy.deepcopy(analysis)
    summaries = result["summaries"]
    for row in summaries:
        records = [score for score in result["scores"] if score["run_id"] == row["run_id"]]
        before, after = (
            np.array([score["spearman_rho"] for score in records if score["relation"] == label])
            for label in ("Before cutoff", "After cutoff")
        )
        if len(before) != row["before_n"] or len(after) != row["after_n"]:
            raise ValueError("Gene scores do not match the reported group sizes")
        if not all(
            np.all(np.isfinite(group)) and np.all(np.abs(group) <= 1) for group in (before, after)
        ):
            raise ValueError("Gene correlations must be finite and between -1 and 1")
        for prefix, group in (("before", before), ("after", after)):
            row[f"{prefix}_ci_low"] = None
            row[f"{prefix}_ci_high"] = None
            if len(group) < 2:
                row[f"{prefix}_ci_status"] = "insufficient_genes"
                continue
            # Do not display zero observed variation as an estimated uncertainty.
            if np.all(group == group[0]):
                row[f"{prefix}_ci_status"] = "constant_scores"
                continue
            interval = t.interval(
                confidence=0.95,
                df=len(group) - 1,
                loc=np.mean(group),
                scale=sem(group, ddof=1),
            )
            if not np.all(np.isfinite(interval)):
                raise ValueError("Could not estimate a finite gene-level confidence interval")
            row[f"{prefix}_ci_low"] = float(interval[0])
            row[f"{prefix}_ci_high"] = float(interval[1])
            row[f"{prefix}_ci_status"] = "estimated"
        row.update(p_value=None, permutations=0)
        if min(len(before), len(after)) < 2:
            continue
        # Exact enumeration is small for the 16-gene SGE task. Fail explicitly
        # if a future task grows beyond this bounded analysis.
        if math.comb(len(before) + len(after), len(after)) > 200_000:
            raise ValueError("Too many allocations for this exact SGE analysis")
        test = permutation_test(
            (before, after),
            lambda x, y, axis: np.mean(x, axis=axis) - np.mean(y, axis=axis),
            permutation_type="independent",
            vectorized=True,
            n_resamples=np.inf,
            batch=256,
            alternative="greater",
        )
        row.update(p_value=float(test.pvalue), permutations=int(test.null_distribution.size))
    result["statistics"] = {
        "test": "scipy.stats.permutation_test",
        "statistic": "mean gene-level Spearman correlation before minus after cutoff",
        "permutation_type": "independent",
        "alternative": "greater",
        "resampling": "exact",
        "adjustment": "none; separate exploratory per-model hypotheses",
        "alpha": 0.05,
        "confidence_interval": {
            "function": "scipy.stats.t.interval",
            "statistic": "mean gene-level Spearman correlation within each date group",
            "method": "Student's t",
            "confidence_level": 0.95,
            "alternative": "two-sided",
            "sampling_unit": "gene panel",
            "degrees_of_freedom": "n - 1 within each date group",
            "standard_error": "scipy.stats.sem with ddof=1",
            "assumptions": (
                "independent, identically distributed normal gene scores within each group"
            ),
        },
        "software": {"numpy": np.__version__, "scipy": scipy.__version__},
    }
    return result


def collect(config: Path) -> dict[str, Any]:
    """Reuse the explorer's source matching, cohort rules, and run selection."""
    settings = load_site_config(config)
    metadata = build_question_metadata(
        source_paths=settings.question_metadata_sources,
        assay_publications=load_assay_publications(settings.assay_publications),
    )
    collected = subprocess.run(
        [
            "node",
            str(Path(__file__).with_name("collect_sge_cutoff.mjs")),
            settings.data_base_url,
            str(settings.assets_dir),
        ],
        input=json.dumps(metadata),
        text=True,
        capture_output=True,
        check=True,
    )
    analysis = json.loads(collected.stdout)
    analysis["retrieved_at"] = datetime.now(UTC).isoformat()
    return analysis


def main() -> None:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--config", type=Path, default=Path("projects/explorer/config/site.yaml"))
    parser.add_argument(
        "--input", type=Path, help="Replay a saved collection without network access"
    )
    parser.add_argument("--save-input", type=Path, help="Save the collection for offline replay")
    parser.add_argument("--output", type=Path, required=True)
    args = parser.parse_args()
    analysis = json.loads(args.input.read_text()) if args.input else collect(args.config)
    result = add_statistics(analysis)
    if args.save_input:
        args.save_input.write_text(json.dumps(analysis, indent=2, sort_keys=True) + "\n")
    args.output.write_text(json.dumps(result, indent=2, sort_keys=True, allow_nan=False) + "\n")
    print(f"Exported {len(result['summaries'])} configurations to {args.output}")
