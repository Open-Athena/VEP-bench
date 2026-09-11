"""Freeze coverage before rescoring saved answers; no model or annotation calls."""

from __future__ import annotations

import argparse
import gzip
import hashlib
import json
import math
import re
from collections import Counter
from pathlib import Path
from typing import Any

from vepbench.artifacts import canonical_json, sha256_json
from vepbench.evaluation import core
from vepbench.evaluation.core import score_ranking

ALLELE_TYPES = ["SNV", "Insertion", "Deletion", "Other/complex", "Unknown"]
UNKNOWN = "Unknown"
AMBIGUOUS = "Ambiguous"
EFFORT_ORDER = ["none", "minimal", "low", "medium", "high", "xhigh"]


def allele_type(ref: Any, alt: Any) -> str:
    if (
        not isinstance(ref, str)
        or not isinstance(alt, str)
        or ref == alt
        or set(ref + alt) - set("ACGT")
    ):
        return UNKNOWN
    while ref and alt and ref[0] == alt[0]:
        ref, alt = ref[1:], alt[1:]
    while ref and alt and ref[-1] == alt[-1]:
        ref, alt = ref[:-1], alt[:-1]
    if not ref:
        return "Insertion"
    if not alt:
        return "Deletion"
    return "SNV" if len(ref) == len(alt) == 1 else "Other/complex"


def consequence(variant: dict[str, Any]) -> str:
    term = variant.get("most_severe_consequence")
    if isinstance(term, list):
        terms = {item for item in term if isinstance(item, str) and item}
        return next(iter(terms)) if len(terms) == 1 else AMBIGUOUS if terms else UNKNOWN
    if not isinstance(term, str) or not term:
        return UNKNOWN
    return term if re.fullmatch(r"[A-Za-z0-9_]+", term) else AMBIGUOUS


def file_sha256(path: Path) -> str:
    with path.open("rb") as stream:
        return hashlib.file_digest(stream, "sha256").hexdigest()


def read_artifact(root: Path, descriptor: dict[str, Any]) -> Any:
    path = root / descriptor["path"]
    if not path.resolve().is_relative_to(root.resolve()):
        raise ValueError("artifact path escapes publication root")
    if path.stat().st_size != descriptor["artifact_bytes"]:
        raise ValueError(f"artifact size mismatch: {path}")
    if file_sha256(path) != descriptor["artifact_sha256"]:
        raise ValueError(f"artifact digest mismatch: {path}")
    # Published normalized answers are small; refuse unexpectedly large inputs.
    if descriptor["content_bytes"] > 8 * 1024 * 1024:
        raise ValueError(f"artifact exceeds the 8 MiB analysis limit: {path}")
    opener = gzip.open if path.suffix == ".gz" else open
    with opener(path, "rb") as stream:
        content = stream.read(8 * 1024 * 1024 + 1)
    if (
        len(content) != descriptor["content_bytes"]
        or hashlib.sha256(content).hexdigest() != descriptor["content_sha256"]
    ):
        raise ValueError(f"content digest mismatch: {path}")
    return json.loads(content)


def load_inputs(root: Path, manifest_path: Path) -> tuple[dict[str, Any], list, list]:
    manifest = json.loads(manifest_path.read_text())
    artifacts = manifest["artifacts"]
    index = read_artifact(root, artifacts["question_index"])
    questions = index["questions"]
    if len(questions) != manifest["question_set_size"]:
        raise ValueError("question count mismatch")
    seen = set()
    for question in questions:
        original = {k: v for k, v in question.items() if k != "question_sha256"}
        if (
            question["question_id"] in seen
            or sha256_json(original) != question["question_sha256"]
            or question["task_type"] != "ranking"
        ):
            raise ValueError("duplicate, changed, or non-ranking question")
        seen.add(question["question_id"])
    content = "".join(
        canonical_json({k: v for k, v in q.items() if k != "question_sha256"}) + "\n"
        for q in sorted(questions, key=lambda q: q["question_id"])
    )
    if hashlib.sha256(content.encode()).hexdigest() != manifest["question_set_sha256"]:
        raise ValueError("question set digest mismatch")
    runs = read_artifact(root, artifacts["runs"])["runs"]
    return manifest, questions, runs


def select_runs(questions: list, runs: list) -> tuple[list, list]:
    groups, excluded = {}, []
    for run in sorted(runs, key=lambda r: (r["completed_at"], r["run_id"])):
        family = run["evaluation_profile"].split(":")[0]
        subset = sorted(
            (q for q in questions if q["metadata"]["task_family"] == family),
            key=lambda q: q["question_id"],
        )
        content = "".join(
            canonical_json({k: v for k, v in q.items() if k != "question_sha256"}) + "\n"
            for q in subset
        )
        if not run["coverage"]["complete"]:
            excluded.append({"run_id": run["run_id"], "reason": "incomplete_run"})
            continue
        if (
            not subset
            or run["task_type"] != "ranking"
            or run["question_set_size"] != len(subset)
            or run["question_set_sha256"] != hashlib.sha256(content.encode()).hexdigest()
        ):
            raise ValueError(f"incompatible run question set: {run['run_id']}")
        effort = run["generation_parameters"].get("reasoning", {}).get("effort")
        if effort not in EFFORT_ORDER:
            raise ValueError(f"cannot order unrecognized reasoning effort for {run['run_id']}")
        key = (family, run["model"]["model_id"], run["model"].get("model_revision"))
        groups.setdefault(key, []).append(run)
    selected = []
    for (family, _, _), candidates in groups.items():
        candidates.sort(
            key=lambda r: (
                EFFORT_ORDER.index(r["generation_parameters"]["reasoning"]["effort"]),
                r["completed_at"],
                r["run_id"],
            )
        )
        run = candidates[-1]
        for omitted in candidates[:-1]:
            excluded.append(
                {
                    "run_id": omitted["run_id"],
                    "reason": "lower_reasoning_effort"
                    if omitted["generation_parameters"]["reasoning"]["effort"]
                    != run["generation_parameters"]["reasoning"]["effort"]
                    else "superseded_same_effort",
                }
            )
        selected.append(
            {
                k: run[k]
                for k in (
                    "run_id",
                    "configuration_key",
                    "question_set_sha256",
                    "question_set_size",
                    "model",
                    "generation_parameters",
                    "evaluation_profile",
                    "completed_at",
                )
            }
            | {"task_family": family}
        )
    return sorted(selected, key=lambda r: r["run_id"]), sorted(excluded, key=lambda r: r["run_id"])


def specialist_support(questions: list, specialist: dict | None) -> dict | None:
    if specialist is None:
        return None
    required = {
        "name",
        "version",
        "source_sha256",
        "score_definition",
        "direction",
        "input_context",
        "known_overlap",
        "variant_matching_rule",
    }
    if not required <= specialist.get("provenance", {}).keys() or any(
        not specialist["provenance"][field] for field in required
    ):
        raise ValueError("specialist provenance is incomplete")
    by_id = {q["question_id"]: q for q in questions}
    support = {}
    for qid, panel in specialist["panels"].items():
        question = by_id.get(qid)
        if question is None or panel["question_sha256"] != question["question_sha256"]:
            raise ValueError("specialist question identity mismatch")
        ids = {c["candidate_id"] for c in question["candidates"]}
        predictions = panel["predictions"]
        if set(predictions) - ids:
            raise ValueError("unknown specialist candidate ID")
        if any(
            isinstance(v, bool) or not isinstance(v, int | float) or not math.isfinite(v)
            for v in predictions.values()
        ):
            raise ValueError("specialist predictions must be finite numbers; omit unsupported IDs")
        support[qid] = set(predictions)
    return support


def coverage_plan(
    questions: list, annotations: dict, policy: dict, support: dict | None = None
) -> list:
    minimum = policy["minimum_variants_per_panel"]
    minimum_panels = policy["minimum_panels_per_task"]
    if (
        isinstance(minimum, bool)
        or not isinstance(minimum, int)
        or minimum < 2
        or isinstance(minimum_panels, bool)
        or not isinstance(minimum_panels, int)
        or minimum_panels < 1
    ):
        raise ValueError("invalid coverage cutoffs")
    annotation_provenance = annotations.get("annotation", {})
    if not all(
        annotation_provenance.get(k)
        for k in ("provider", "software", "assembly", "consequence_policy", "transcripts")
    ):
        raise ValueError("annotation source, version, assembly and transcript rule are required")
    panels = []
    for q in sorted(questions, key=lambda q: q["question_id"]):
        family = q["metadata"]["task_family"]
        annotation = (
            annotations.get("by_task_family", {})
            .get(family, {})
            .get(q["provenance"]["source_record_id"], {})
        )
        matches = annotation.get("source_record_sha256") == q["provenance"]["source_record_sha256"]
        variants = {}
        for candidate in q["candidates"]:
            cid = candidate["candidate_id"]
            if cid in variants:
                raise ValueError("duplicate candidate ID")
            saved = annotation.get("variants", {}).get(cid, {}) if matches else {}
            kind = allele_type(candidate["ref"], candidate["alt"])
            if saved.get("genomic") and kind != allele_type(
                saved["genomic"].get("ref"), saved["genomic"].get("alt")
            ):
                raise ValueError("genomic and displayed allele type disagree")
            variants[cid] = {"allele_type": kind, "consequence": consequence(saved)}
        panels.append(
            {"question_id": q["question_id"], "task_family": family, "variants": variants}
        )
    consequences = sorted(
        {UNKNOWN, AMBIGUOUS} | {v["consequence"] for p in panels for v in p["variants"].values()}
    )
    rows = []
    for family in sorted({p["task_family"] for p in panels}):
        task_panels = [p for p in panels if p["task_family"] == family]
        for axis, categories in (("allele_type", ALLELE_TYPES), ("consequence", consequences)):
            for category in categories:
                memberships = []
                for panel in task_panels:
                    qid = panel["question_id"]
                    original = sorted(
                        cid for cid, v in panel["variants"].items() if v[axis] == category
                    )
                    ids = (
                        original
                        if support is None
                        else [cid for cid in original if cid in support.get(qid, set())]
                    )
                    memberships.append(
                        {
                            "question_id": qid,
                            "candidate_ids": ids,
                            "excluded_unsupported_ids": sorted(set(original) - set(ids)),
                            "status": "eligible" if len(ids) >= minimum else "below_variant_cutoff",
                        }
                    )
                eligible = [m for m in memberships if m["status"] == "eligible"]
                rows.append(
                    {
                        "task_family": family,
                        "axis": axis,
                        "category": category,
                        "variant_count": sum(
                            len(m["candidate_ids"]) + len(m["excluded_unsupported_ids"])
                            for m in memberships
                        ),
                        "unsupported_variants": sum(
                            len(m["excluded_unsupported_ids"]) for m in memberships
                        ),
                        "eligible_variants": sum(len(m["candidate_ids"]) for m in eligible),
                        "eligible_panels": len(eligible),
                        "total_panels": len(memberships),
                        "excluded_panels": len(memberships) - len(eligible),
                        "below_cutoff_variants": sum(
                            len(m["candidate_ids"])
                            for m in memberships
                            if m["status"] != "eligible"
                        ),
                        "status": "eligible"
                        if len(eligible) >= minimum_panels
                        else "insufficient_coverage",
                        "panels": memberships,
                    }
                )
    return rows


def rescore_panel(question: dict, answer: dict, ids: list[str]) -> dict:
    if answer["question_sha256"] != question["question_sha256"]:
        raise ValueError("answer question digest mismatch")
    if answer["response"]["status"] != "completed":
        raise ValueError("API errors cannot be scored as completed answers")
    references = {c["candidate_id"]: c["reference_score"] for c in question["candidates"]}
    if not ids or len(set(ids)) != len(ids) or set(ids) - references.keys():
        raise ValueError("invalid stratum membership")
    # Validate the complete original response first. A malformed full answer
    # cannot become valid just because its missing IDs are outside this stratum.
    original = score_ranking(answer["response"]["content"], references)
    subset = {cid: references[cid] for cid in ids}
    score = original
    if original.valid:
        score = score_ranking(
            "FINAL: " + canonical_json({cid: original.parsed_answer[cid] for cid in ids}),
            subset,
        )
    return {
        "spearman_rho": score.spearman_rho,
        "pearson_r": score.pearson_r,
        "valid": original.valid,
        "constant_reference": len(set(subset.values())) == 1,
    }


def score_plan(plan: dict, questions: list, answers: Any, specialist: dict | None = None) -> list:
    by_id = {q["question_id"]: q for q in questions}
    runs = {r["run_id"]: r for r in plan["runs"]}
    memberships = {}
    scores: dict[tuple[int, str], list] = {}
    for index, row in enumerate(plan["coverage"]):
        if row["status"] != "eligible":
            continue
        for panel in row["panels"]:
            if panel["status"] == "eligible":
                memberships.setdefault(panel["question_id"], []).append(
                    (index, panel["candidate_ids"])
                )
    seen = set()
    for answer in answers:
        rid, qid = answer["run_id"], answer["question_id"]
        if rid not in runs:
            continue
        if (
            (rid, qid) in seen
            or qid not in by_id
            or runs[rid]["task_family"] != by_id[qid]["metadata"]["task_family"]
            or answer["question_sha256"] != by_id[qid]["question_sha256"]
            or answer["response"]["status"] != "completed"
        ):
            raise ValueError("duplicate, changed, missing or failed panel answer")
        seen.add((rid, qid))
        for index, ids in memberships.get(qid, []):
            value = rescore_panel(by_id[qid], answer, ids)
            scores.setdefault((index, rid), []).append({"question_id": qid, **value})
    expected = {
        (rid, q["question_id"])
        for rid, run in runs.items()
        for q in questions
        if q["metadata"]["task_family"] == run["task_family"]
    }
    if seen != expected:
        raise ValueError("missing answers: refusing to change panel membership across models")
    if specialist is not None:
        for qid, panels in memberships.items():
            predictions = specialist["panels"].get(qid, {}).get("predictions", {})
            for index, ids in panels:
                reference = {
                    c["candidate_id"]: c["reference_score"]
                    for c in by_id[qid]["candidates"]
                    if c["candidate_id"] in ids
                }
                score = score_ranking(
                    "FINAL: " + canonical_json({cid: predictions[cid] for cid in ids}), reference
                )
                scores.setdefault((index, "specialist"), []).append(
                    {
                        "question_id": qid,
                        "spearman_rho": score.spearman_rho,
                        "pearson_r": score.pearson_r,
                        "valid": score.valid,
                        "constant_reference": len(set(reference.values())) == 1,
                    }
                )
    results = []
    for (index, rid), panels in sorted(scores.items()):
        row = plan["coverage"][index]
        if len(panels) != row["eligible_panels"]:
            raise ValueError("comparison panel membership mismatch")
        results.append(
            {
                "task_family": row["task_family"],
                "axis": row["axis"],
                "category": row["category"],
                "run_id": rid,
                "panels": sorted(panels, key=lambda p: p["question_id"]),
                "mean_spearman_rho": math.fsum(p["spearman_rho"] for p in panels) / len(panels),
                "mean_pearson_r": math.fsum(p["pearson_r"] for p in panels) / len(panels),
                "invalid_panels": sum(not p["valid"] for p in panels),
            }
        )
    return results


def main() -> None:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("mode", choices=["coverage", "score"])
    parser.add_argument("--publication", type=Path, required=True, help="Local bucket root")
    parser.add_argument("--manifest", type=Path, required=True)
    parser.add_argument("--annotations", type=Path, required=True)
    parser.add_argument(
        "--policy", type=Path, default=Path(__file__).with_name("strata-policy.json")
    )
    parser.add_argument("--plan", type=Path, required=True)
    parser.add_argument("--output", type=Path)
    parser.add_argument("--specialist", type=Path)
    args = parser.parse_args()
    manifest, questions, runs = load_inputs(args.publication, args.manifest)
    annotations = json.loads(args.annotations.read_text())
    policy = json.loads(args.policy.read_text())
    specialist = json.loads(args.specialist.read_text()) if args.specialist else None
    support = specialist_support(questions, specialist)
    selected, excluded = select_runs(questions, runs)
    coverage = coverage_plan(questions, annotations, policy, support)
    plan = {
        "schema_version": "1.0",
        "policy": policy,
        "manifest_sha256": file_sha256(args.manifest),
        "annotation_sha256": file_sha256(args.annotations),
        "annotation": annotations["annotation"],
        "question_set_sha256": manifest["question_set_sha256"],
        "question_set_size": manifest["question_set_size"],
        "questions": [
            {k: q[k] for k in ("question_id", "question_sha256", "provenance")} for q in questions
        ],
        "runs": selected,
        "configuration_selection": {
            "rule": "highest_available_reasoning_effort_per_model_and_task",
            "effort_order": EFFORT_ORDER,
            "tie_break": "latest completed_at, then greatest run_id",
        },
        "excluded_runs": excluded,
        "specialist_sha256": file_sha256(args.specialist) if args.specialist else None,
        "specialist": specialist["provenance"] if specialist else None,
        "coverage": coverage,
    }
    if args.mode == "coverage":
        if args.plan.exists():
            raise ValueError("coverage plan already exists; use a new path for a new analysis")
        args.plan.write_text(canonical_json(plan) + "\n")
        for row in coverage:
            if row["status"] == "eligible":
                print(
                    row["task_family"],
                    row["axis"],
                    row["category"],
                    row["eligible_panels"],
                    "panels",
                    row["eligible_variants"],
                    "variants",
                )
        return
    if json.loads(args.plan.read_text()) != plan:
        raise ValueError("inputs or cutoffs changed after coverage was frozen")
    if args.output is None or args.output.exists():
        raise ValueError("provide a new --output path")
    selected_ids = {r["run_id"] for r in selected}
    descriptors = [
        d for d in manifest["artifacts"]["answers"] if Path(d["path"]).parent.name in selected_ids
    ]
    results = score_plan(
        plan, questions, (read_artifact(args.publication, d) for d in descriptors), specialist
    )
    snapshot = {
        **plan,
        "results": results,
        "leaderboard": read_artifact(args.publication, manifest["artifacts"]["runs"]),
        "analysis_sha256": file_sha256(Path(__file__)),
        "scorer_sha256": file_sha256(Path(core.__file__)),
        "answer_artifacts": descriptors,
        "family_colors": json.loads(
            (Path(__file__).parents[2] / "components/model-family-colors.json").read_text()
        ),
        "plot_sha256": file_sha256(Path(__file__).parents[2] / "components/correlation-plot.js"),
    }
    payload = (canonical_json(snapshot) + "\n").encode("utf-8")
    args.output.write_bytes(
        gzip.compress(payload, mtime=0) if args.output.suffix == ".gz" else payload
    )
    statuses = dict(Counter(row["status"] for row in coverage))
    print(f"Wrote {len(results)} task/stratum/model scores; coverage statuses: {statuses}")


if __name__ == "__main__":
    main()
