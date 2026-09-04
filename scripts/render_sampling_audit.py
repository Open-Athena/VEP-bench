"""Render compact tables and static figures from primary-population audits."""

from __future__ import annotations

import argparse
import csv
import gzip
import json
from pathlib import Path


def annotate_window_selection(reports: list[dict]) -> dict:
    """Compare existing windows with selection by P95-P05, capacity, then identity."""
    selection = {}
    for report in reports:
        if report["task"] not in {"sge", "opensplice_snv"}:
            continue
        candidates = [
            p
            for p in report["populations"]
            if p["eligibility"] == "all_alleles"
            and p["count"] >= 50
            and "error" not in p["policies"]["p01_p99"]
        ]
        if report["task"] == "sge":
            ordered = sorted(
                candidates, key=lambda p: (-p["robust_range"], -p["count"], p["unit"], p["exon"])
            )
        else:
            ordered = sorted(
                candidates, key=lambda p: (-p["robust_range"], -p["count"], p["gene"], p["unit"])
            )
        winners = {}
        for p in ordered:
            winners.setdefault(p.get("gene", p["unit"]), p)
        selected = list(winners.values())
        if report["task"] == "opensplice_snv":
            selected = selected[:20]
        keys = {p["key"] for p in selected}
        for p in report["populations"]:
            p["reselected"] = p["key"] in keys
        selection[report["task"]] = {
            "rule": (
                "descending P95-P05, descending eligible count, ascending gene and window identity"
            ),
            "selected_keys": sorted(keys),
            "current_keys_retained": sum(p.get("current_selected", False) for p in selected),
            "candidate_genes": len(winners),
        }
    return selection


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
    selection = annotate_window_selection(reports)
    (args.output / "window-selection.json").write_text(
        json.dumps(selection, indent=2, sort_keys=True) + "\n"
    )
    summary_bytes = (json.dumps(reports, indent=2, sort_keys=True) + "\n").encode()
    (args.output / "summary.json.gz").write_bytes(gzip.compress(summary_bytes, mtime=0))
    (args.output / "summary.json").unlink(missing_ok=True)
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
    with (args.output / "feasibility.tsv").open("w", newline="") as handle:
        writer = csv.writer(handle, delimiter="\t", lineterminator="\n")
        writer.writerow(
            ["task", "cohort", "policy", "windows", "exact_five_by_ten", "sparse", "invalid"]
        )
        for report in reports:
            expanded = [
                p for p in report["populations"] if p["eligibility"] in {"SIGN_MIN", "all_alleles"}
            ]
            for cohort in ("all", "current_selected", "reselected"):
                populations = [p for p in expanded if cohort == "all" or p.get(cohort, False)]
                if not populations:
                    continue
                for policy in ("minmax", "p01_p99"):
                    exact = sum(
                        p["policies"].get(policy, {}).get("exact_five_by_ten", False)
                        for p in populations
                    )
                    invalid = sum(
                        "error" in p["policies"].get(policy, {}) or not p["count"]
                        for p in populations
                    )
                    writer.writerow(
                        [
                            report["task"],
                            cohort,
                            policy,
                            len(populations),
                            exact,
                            len(populations) - exact - invalid,
                            invalid,
                        ]
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
    # Keep the full native score scale visible for each proposed question.
    for task, prefix, xlabel in (
        ("mpra", "satmut_mpra:", "Measured log2 activity effect"),
        ("sge", "sge:", "Measured damage score (reviewed source direction)"),
        ("opensplice", "opensplice:", "Measured signed delta PSI"),
    ):
        lookup = {
            p["key"]: p
            for p in summaries
            if p["key"].startswith(prefix)
            and (p.get("reselected") or p["eligibility"] == "SIGN_MIN")
        }
        if not lookup:
            continue
        figure, axes = plt.subplots(
            (len(lookup) + 3) // 4,
            4,
            figsize=(13, 2.3 * ((len(lookup) + 3) // 4)),
            layout="constrained",
            squeeze=False,
        )
        index = 0
        with (args.analysis / "populations.jsonl").open() as handle:
            for line in handle:
                population = json.loads(line)
                summary = lookup.get(population["key"])
                if summary is None:
                    continue
                axis = axes.flat[index]
                index += 1
                scores = [row["score"] for row in population["rows"]]
                axis.hist(scores, bins=50, color="#b9ced1")
                for cutpoint in summary["policies"]["p01_p99"]["cutpoints"]:
                    axis.axvline(cutpoint, color="#17677d", linewidth=1)
                axis.set_title(
                    f"{summary.get('gene', summary['unit'])} (n={summary['count']})", fontsize=10
                )
                axis.set_yscale("log")
                axis.tick_params(labelsize=8)
        for axis in list(axes.flat)[index:]:
            axis.set_visible(False)
        figure.suptitle(
            f"{task}: expanded populations in proposed windows\n"
            "Lines: P01-P99 score-bin boundaries; log count axis"
        )
        figure.supxlabel(xlabel)
        figure.savefig(args.output / f"{task}-score-distributions.png", dpi=160)
        plt.close(figure)
    print(f"Wrote tables and figures to {args.output}")


if __name__ == "__main__":
    main()
