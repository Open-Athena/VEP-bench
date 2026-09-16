"""Estimate panel-level t intervals from the frozen matched specialist comparison."""

import argparse
import gzip
import hashlib
import json
from pathlib import Path
from typing import Any

from vepbench_blog_analysis import strata_intervals


def estimate_intervals(snapshot: dict[str, Any]) -> dict[str, Any]:
    """Reuse the stratum estimator on each task's matched, equally weighted panels."""
    if snapshot["status"] != "complete":
        raise ValueError("Specialist comparison must be complete")
    result = strata_intervals.estimate_intervals(
        {
            "policy": {"minimum_panels_per_task": 1},
            "coverage": [{**row, "axis": "matched"} for row in snapshot["coverage"]],
            "results": [{**row, "axis": "matched"} for row in snapshot["results"]],
        }
    )
    for row in result["intervals"]:
        del row["axis"]
    result["statistics"]["sampling_unit"] = (
        "eligible original panel (gene, regulatory element or exon)"
    )
    return result


def main() -> None:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--input", type=Path, required=True, help="Frozen comparison JSON.gz")
    parser.add_argument("--output", type=Path, required=True)
    args = parser.parse_args()
    raw = args.input.read_bytes()
    result = estimate_intervals(json.loads(gzip.decompress(raw)))
    result["snapshot_sha256"] = hashlib.sha256(raw).hexdigest()
    result["analysis_sha256"] = hashlib.sha256(Path(__file__).read_bytes()).hexdigest()
    result["estimator_sha256"] = hashlib.sha256(
        Path(strata_intervals.__file__).read_bytes()
    ).hexdigest()
    args.output.write_text(json.dumps(result, indent=2, sort_keys=True, allow_nan=False) + "\n")
    print(f"Exported {len(result['intervals'])} panel-level intervals to {args.output}")


if __name__ == "__main__":
    main()
