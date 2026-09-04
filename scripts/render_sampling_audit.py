"""Render compact tables and static figures from primary-population audits."""

from __future__ import annotations

import argparse
import csv
import json
from pathlib import Path


def main() -> None:
    import matplotlib

    matplotlib.use("Agg")
    import matplotlib.pyplot as plt

    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--analysis", type=Path, required=True)
    parser.add_argument("--output", type=Path, required=True)
    args = parser.parse_args()
    args.output.mkdir(parents=True, exist_ok=True)
    summaries = []
    reports = []
    for path in sorted(args.analysis.glob("*.json")):
        report = json.loads(path.read_text())
        if "populations" not in report:
            continue
        for population in report["populations"]:
            for values in population["policies"].values():
                values.pop("selected", None)
        reports.append(report)
        summaries.extend(report["populations"])
    (args.output / "summary.json").write_text(json.dumps(reports, indent=2, sort_keys=True) + "\n")
    table = args.output / "occupancy.tsv"
    with table.open("w", newline="") as handle:
        fields = [
            "key",
            "eligible",
            "unique_scores",
            "policy",
            "counts",
            "allocations",
            "exact_five_by_ten",
            "error",
        ]
        writer = csv.DictWriter(handle, fieldnames=fields, delimiter="\t", lineterminator="\n")
        writer.writeheader()
        for population in summaries:
            for policy in ("minmax", "p01_p99"):
                values = population["policies"].get(policy, {})
                writer.writerow(
                    {
                        "key": population["key"],
                        "eligible": population["count"],
                        "unique_scores": population["unique_scores"],
                        "policy": policy,
                        "counts": json.dumps(values.get("population_counts")),
                        "allocations": json.dumps(values.get("allocations")),
                        "exact_five_by_ten": values.get("exact_five_by_ten", False),
                        "error": values.get("error") or ".",
                    }
                )
    mpra = [
        p
        for p in summaries
        if p["key"].startswith("satmut_mpra:") and p["eligibility"] == "SIGN_MIN"
    ]
    if mpra:
        figure, axes = plt.subplots(1, 2, figsize=(10, 7), sharey=True, layout="constrained")
        maximum = max(
            min(p["policies"][policy]["population_counts"])
            for p in mpra
            for policy in ("minmax", "p01_p99")
        )
        for axis, policy, title in zip(
            axes, ("minmax", "p01_p99"), ("Minimum-maximum anchors", "P01-P99 anchors"), strict=True
        ):
            values = [min(p["policies"][policy]["population_counts"]) for p in mpra]
            axis.barh([p["unit"] for p in mpra], values, color="#257c91")
            axis.axvline(10, color="#b94432", linestyle="--", linewidth=1.5)
            axis.set(title=title, xlabel="Variants in the least populated score bin")
            axis.set_xlim(0, maximum * 1.25)
            for index, value in enumerate(values):
                axis.text(value + 0.4, index, str(value), va="center", fontsize=9)
        axes[0].invert_yaxis()
        figure.suptitle(
            "MPRA: five score bins from SIGN + MIN measurements\nDashed line: ten variants per bin",
            fontsize=13,
        )
        figure.savefig(args.output / "mpra-bin-capacity.png", dpi=160)
        plt.close(figure)
        lookup = {p["key"]: p for p in mpra}
        figure, axes = plt.subplots(4, 4, figsize=(13, 9), layout="constrained")
        index = 0
        with (args.analysis / "populations.jsonl").open() as handle:
            for line in handle:
                population = json.loads(line)
                if population["key"] not in lookup:
                    continue
                summary = lookup[population["key"]]
                axis = axes.flat[index]
                index += 1
                scores = [row["score"] for row in population["rows"]]
                axis.hist(scores, bins=50, color="#b9ced1")
                for cutpoint in summary["policies"]["p01_p99"]["cutpoints"]:
                    axis.axvline(cutpoint, color="#17677d", linewidth=1)
                axis.set_title(f"{summary['unit']} (n={summary['count']})", fontsize=10)
                axis.set_yscale("log")
                axis.tick_params(labelsize=8)
        figure.suptitle(
            "MPRA full barcode-qualified populations\n"
            "Lines: P01-P99 score-bin boundaries; original scores, log count axis"
        )
        figure.supxlabel("Measured log2 activity effect")
        figure.savefig(args.output / "mpra-score-distributions.png", dpi=160)
        plt.close(figure)
    print(f"Wrote tables and figures to {args.output}")


if __name__ == "__main__":
    main()
