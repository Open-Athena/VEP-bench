"""Verify the archived MPRA diagnostic without network or model calls."""

import gzip
import hashlib
import json
import math
from collections import Counter
from pathlib import Path
from statistics import mean, median, pstdev

ROOT = Path(__file__).resolve().parents[1] / "docs/analysis/mpra-v1-v2-2026-09-08"


def ranks(values):
    ordered = sorted(range(len(values)), key=values.__getitem__)
    result = [0.0] * len(values)
    start = 0
    while start < len(ordered):
        end = start + 1
        while end < len(ordered) and values[ordered[end]] == values[ordered[start]]:
            end += 1
        for index in ordered[start:end]:
            result[index] = (start + 1 + end) / 2
        start = end
    return result


def pearson(left, right):
    xmean, ymean = mean(left), mean(right)
    x = [value - xmean for value in left]
    y = [value - ymean for value in right]
    denominator = math.sqrt(sum(value * value for value in x) * sum(value * value for value in y))
    return sum(a * b for a, b in zip(x, y, strict=True)) / denominator if denominator else 0.0


def correlations(rows):
    if len(rows) < 3:
        return {"n": len(rows), "pearson": None, "spearman": None}
    observed = [row["effect"] for row in rows]
    predicted = [row["prediction"] for row in rows]
    return {
        "n": len(rows),
        "pearson": pearson(observed, predicted),
        "spearman": pearson(ranks(observed), ranks(predicted)),
    }


def check(actual, expected, label):
    if isinstance(actual, float) and isinstance(expected, (float, int)):
        valid = math.isclose(actual, expected, rel_tol=0, abs_tol=1e-12)
    else:
        valid = actual == expected
    if not valid:
        raise ValueError(f"{label}: computed {actual!r}, expected {expected!r}")


def main():
    compressed = (ROOT / "inputs.json.gz").read_bytes()
    raw = gzip.decompress(compressed)
    provenance = json.loads((ROOT / "provenance.json").read_text())
    comparison_bytes = (ROOT / "comparison.json").read_bytes()
    for content, field in [
        (compressed, "inputs_gzip_sha256"),
        (raw, "inputs_sha256"),
        (comparison_bytes, "comparison_sha256"),
    ]:
        check(hashlib.sha256(content).hexdigest(), provenance[field], field)
    inputs = json.loads(raw)
    comparison = json.loads(comparison_bytes)
    panels = {panel["element"]: panel for panel in comparison["panels"]}
    check(set(inputs["v1"]), set(inputs["v2"]), "element identities")
    check(set(panels), set(inputs["v1"]), "comparison identities")
    check(len(panels), 16, "panel count")
    computed = []
    for element, expected in panels.items():
        common = {r["vcf_key"] for r in inputs["v1"][element]} & {
            r["vcf_key"] for r in inputs["v2"][element]
        }
        result = {"common_variants": len(common)}
        for version in ["v1", "v2"]:
            rows = inputs[version][element]
            check(len(rows), 50, f"{version}/{element}: candidate count")
            check(len({r["candidate_id"] for r in rows}), 50, "unique candidate IDs")
            check(len({r["vcf_key"] for r in rows}), 50, "unique allele keys")
            subsets = {
                "all": rows,
                "SIGN": [r for r in rows if r["source_filter"] == "SIGN"],
                "MIN": [r for r in rows if r["source_filter"] == "MIN"],
                "snv": [r for r in rows if r["snv"]],
                "common": [r for r in rows if r["vcf_key"] in common],
            }
            for subset, selected in subsets.items():
                for metric, value in correlations(selected).items():
                    result[f"{version}_{subset}_{metric}"] = value
            effects = [r["effect"] for r in rows]
            for suffix, value in {
                "effect_sd": pstdev(effects),
                "effect_range": max(effects) - min(effects),
                "median_abs_effect": median(abs(x) for x in effects),
                "near_zero_abs_lt_0.1": sum(abs(x) < 0.1 for x in effects),
                "median_barcodes": median(r["barcode_count"] for r in rows),
                "unique_predictions": len({r["prediction"] for r in rows}),
            }.items():
                result[f"{version}_{suffix}"] = value
        for metric in ["pearson", "spearman"]:
            result[f"delta_{metric}"] = result[f"v2_all_{metric}"] - result[f"v1_all_{metric}"]
        for key, value in result.items():
            check(value, expected[key], f"{element}/{key}")
        computed.append(result)
    for version in ["v1", "v2"]:
        rows = [r for panel in inputs[version].values() for r in panel]
        summary = {
            "n": len(rows),
            "filters": {
                name: Counter(r["source_filter"] for r in rows)[name] for name in ["SIGN", "MIN"]
            },
            "snv": sum(r["snv"] for r in rows),
            "median_abs_effect": median(abs(r["effect"]) for r in rows),
            "near_zero_abs_lt_0_1": sum(abs(r["effect"]) < 0.1 for r in rows),
            "median_barcodes": median(r["barcode_count"] for r in rows),
            "p_ge_0_05": sum(r["p_value"] >= 0.05 for r in rows),
        }
        for subset in ["all", "SIGN", "MIN", "snv", "common"]:
            for metric in ["pearson", "spearman"]:
                values = [p[f"{version}_{subset}_{metric}"] for p in computed]
                values = [value for value in values if value is not None]
                summary[f"mean_{subset}_{metric}"] = mean(values) if values else None
        for key, value in summary.items():
            check(value, comparison["summary"][version][key], f"{version}/{key}")
    for key, value in {
        "overlap_variants": sum(p["common_variants"] for p in computed),
        "panels_sd_increased": sum(p["v2_effect_sd"] > p["v1_effect_sd"] for p in computed),
        "panels_range_increased": sum(
            p["v2_effect_range"] > p["v1_effect_range"] for p in computed
        ),
        "panels_spearman_declined": sum(p["delta_spearman"] < 0 for p in computed),
    }.items():
        check(value, comparison["summary"][key], key)
    print(
        "Verified archive hashes, 1,600 candidate inputs, "
        "all panel/subset metrics and summary statistics."
    )


if __name__ == "__main__":
    main()
