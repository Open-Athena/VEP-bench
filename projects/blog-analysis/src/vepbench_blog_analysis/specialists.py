"""Freeze, infer, and compare specialist predictions against saved LLM answers."""

from __future__ import annotations

import argparse
import gzip
import importlib.util
import json
import math
import os
import tempfile
import time
from collections import Counter
from datetime import UTC, datetime
from pathlib import Path
from typing import Any

from vepbench.artifacts import canonical_json, read_jsonl, sha256_json
from vepbench.evaluation import core
from vepbench_blog_analysis import alphagenome_client

ROOT = Path(__file__).resolve().parents[4]
POST = ROOT / "projects/explorer/web/blog/introducing-vep-bench"
SOURCE_PATHS = [
    ROOT / "data/sources/opensplice-snv-figshare-v5.jsonl",
    ROOT / "data/sources/satmut-mpra-cadd-v1.7.jsonl",
    ROOT / "data/sources/sge-mavedb-2026-09-03.jsonl",
]
TASKS = ("opensplice_snv", "satmut_mpra", "sge")


def strata_module() -> Any:
    # Share the blog's strict publication validation, run selection and original
    # invalid-output penalty. The evaluator and public schemas stay independent.
    spec = importlib.util.spec_from_file_location("specialist_strata", POST / "strata.py")
    assert spec is not None and spec.loader is not None
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module


STRATA = strata_module()


def read_json(path: Path) -> Any:
    opener = gzip.open if path.suffix == ".gz" else open
    with opener(path, "rb") as stream:
        data = stream.read(16 * 1024**2 + 1)
    if len(data) > 16 * 1024**2:
        raise ValueError("analysis artifact exceeds 16 MiB")
    return json.loads(data)


def write_new(path: Path, value: Any) -> None:
    payload = (canonical_json(value) + "\n").encode()
    if path.suffix == ".gz":
        payload = gzip.compress(payload, mtime=0)
    path.parent.mkdir(parents=True, exist_ok=True)
    # Atomic, exclusive publication: an interruption cannot leave a partially
    # written cache entry, and concurrent runs cannot overwrite one another.
    with tempfile.NamedTemporaryFile(dir=path.parent, delete=False) as stream:
        temporary = Path(stream.name)
        stream.write(payload)
    try:
        os.link(temporary, path)
    finally:
        temporary.unlink()


def implementation() -> dict:
    return {
        "analysis": STRATA.file_sha256(Path(__file__)),
        "client": STRATA.file_sha256(Path(alphagenome_client.__file__)),
        "strata": STRATA.file_sha256(POST / "strata.py"),
        "scorer": STRATA.file_sha256(Path(core.__file__)),
    }


def validate_variant(variant: dict) -> None:
    if (
        variant.get("assembly") != "GRCh38"
        or variant.get("chrom") not in [*(str(n) for n in range(1, 23)), "X", "Y"]
        or type(variant.get("pos")) is not int
        or variant["pos"] < 1
        or not all(isinstance(variant.get(k), str) and variant[k] for k in ("ref", "alt"))
        or variant["ref"] == variant["alt"]
        or set(variant["ref"] + variant["alt"]) - set("ACGT")
    ):
        raise ValueError("invalid complete GRCh38 allele")


def make_panels(questions: list, sources: list, annotations: dict, policy: dict) -> list:
    if policy["assembly"] != "GRCh38" or policy["minimum_variants_per_panel"] < 2:
        raise ValueError("unsupported assembly or panel cutoff")
    by_source = {(r["task_family"], r["source_record_id"]): r for r in sources}
    if len(by_source) != len(sources):
        raise ValueError("duplicate source panel")
    panels = []
    for q in sorted(questions, key=lambda q: q["question_id"]):
        family = q["metadata"]["task_family"]
        source_id = q["provenance"]["source_record_id"]
        source = by_source[family, source_id]
        annotation = annotations["by_task_family"][family][source_id]
        digest = q["provenance"]["source_record_sha256"]
        if sha256_json(source) != digest or annotation["source_record_sha256"] != digest:
            raise ValueError("source or genomic mapping does not match the frozen question")
        if source["candidates"] != q["candidates"]:
            raise ValueError("source candidate ordering or complete alleles changed")
        meta = source["source_metadata"]
        candidates = []
        for c in q["candidates"]:
            cid = c["candidate_id"]
            variant = annotation["variants"][cid]["genomic"]
            validate_variant(variant)
            request = {"task_family": family, "variant": variant}
            reason = None
            if family == "opensplice_snv":
                exon = meta["native_exon"]
                if exon["strand"] not in (-1, 1):
                    raise ValueError("unrecognized exon strand")
                start, end = exon["start_exon"] - 1, exon["end_exon"] - 1
                center = (start + end) // 2
                width = policy["splicing"]["sequence_length"]
                request.update(
                    strand="+" if exon["strand"] == 1 else "-",
                    sites={
                        "acceptor": start if exon["strand"] == 1 else end,
                        "donor": end if exon["strand"] == 1 else start,
                    },
                )
            elif family == "satmut_mpra":
                terms = policy["mpra"]["ontology_terms"].get(source_id)
                if not terms:
                    reason = "no_published_cell_type_match"
                request["ontology_terms"] = terms or []
                center = variant["pos"] - 1
                width = policy["mpra"]["sequence_length"]
            elif family == "sge":
                if len(variant["ref"]) != 1 or len(variant["alt"]) != 1:
                    reason = "outside_released_avi_snv_coverage"
            else:
                raise ValueError("unsupported specialist task")
            if family != "sge":
                interval_start = center - width // 2
                request["interval"] = {
                    "chromosome": "chr" + variant["chrom"],
                    "start": interval_start,
                    "end": interval_start + width,
                }
                if (
                    not interval_start
                    <= variant["pos"] - 1
                    < variant["pos"] - 1 + len(variant["ref"])
                    <= interval_start + width
                ):
                    raise ValueError("complete allele does not fit the fixed inference window")
            candidates.append({"candidate_id": cid, "request": request, "exclusion_reason": reason})
        panels.append(
            {
                "question_id": q["question_id"],
                "question_sha256": q["question_sha256"],
                "source_record_id": source_id,
                "source_record_sha256": digest,
                "task_family": family,
                "candidates": candidates,
                "avi_model_selection_overlap": family == "sge"
                and source_id in policy["avi"]["model_selection_genes"],
            }
        )
    return panels


def make_plan(
    publication: Path, manifest_path: Path, policy_path: Path, annotations_path: Path
) -> dict:
    manifest, questions, runs = STRATA.load_inputs(publication, manifest_path)
    selected, excluded = STRATA.select_runs(questions, runs)
    originals = {r["run_id"]: r for r in runs}
    for run in selected:
        if "retry_policy" in originals[run["run_id"]]:
            run["retry_policy"] = originals[run["run_id"]]["retry_policy"]
    policy = read_json(policy_path)
    panels = make_panels(
        questions,
        [r for p in SOURCE_PATHS for r in read_jsonl(p)],
        read_json(annotations_path),
        policy,
    )
    return {
        "schema_version": "1.0",
        "policy": policy,
        "manifest_sha256": STRATA.file_sha256(manifest_path),
        "question_set_sha256": manifest["question_set_sha256"],
        "question_set_size": manifest["question_set_size"],
        "annotation_sha256": STRATA.file_sha256(annotations_path),
        "source_sha256": {p.name: STRATA.file_sha256(p) for p in SOURCE_PATHS},
        "implementation": implementation(),
        "runs": selected,
        "excluded_runs": excluded,
        "configuration_selection": "highest effort per model/task; latest run breaks ties",
        "panels": panels,
    }


def validate_plan(plan: dict) -> None:
    if plan["schema_version"] != "1.0" or plan["implementation"] != implementation():
        raise ValueError("implementation changed since the plan was frozen; make a new plan")


def requests(plan: dict):
    for panel in plan["panels"]:
        for c in panel["candidates"]:
            if c["exclusion_reason"] is None:
                yield c["request"]


def cache_path(cache: Path, request: dict) -> Path:
    return cache / (sha256_json(request) + ".json")


def validate_prediction(row: dict, request: dict, session: dict) -> None:
    if row["request"] != request or row["session_sha256"] != sha256_json(session):
        raise ValueError("prediction cache identity mismatch")
    if row["status"] == "scored":
        if type(row["score"]) not in (int, float):
            raise ValueError("cached prediction must be a number")
        alphagenome_client.finite(row["score"])
        if row.get("reason") is not None:
            raise ValueError("scored prediction has an exclusion reason")
    elif not (
        row["status"] == "excluded"
        and request["task_family"] == "sge"
        and row.get("reason") == "atlas_score_not_found"
    ):
        raise ValueError("unexpected prediction status; failures cannot become exclusions")
    if not math.isfinite(row["runtime_seconds"]) or row["runtime_seconds"] < 0:
        raise ValueError("invalid measured runtime")


def predict(plan: dict, cache: Path, client: Any, *, limit: int | None = None) -> None:
    validate_plan(plan)
    session_path = cache / "session.json"
    if session_path.exists():
        session = read_json(session_path)
        if session["plan_sha256"] != sha256_json(plan):
            raise ValueError("cache belongs to a different frozen plan")
    else:
        session = {
            "plan_sha256": sha256_json(plan),
            "started_at": datetime.now(UTC).isoformat(),
            "metadata": client.metadata(),
        }
        # Resolve exact tracks before any variant inference or comparison.
        for request in requests(plan):
            if request["task_family"] == "satmut_mpra":
                available = {r["ontology_curie"] for r in session["metadata"]["dnase"]}
                if not set(request["ontology_terms"]) <= available:
                    raise ValueError("published DNase ontology match unavailable in this model")
        write_new(session_path, session)
    started = time.monotonic()
    inferred = reused = 0
    for request in requests(plan):
        path = cache_path(cache, request)
        if path.exists():
            validate_prediction(read_json(path), request, session)
            reused += 1
            continue
        if limit is not None and inferred >= limit:
            break
        begin = time.monotonic()
        try:
            result = client.predict(request, session["metadata"])
        except Exception as exc:
            # SDK exceptions can contain request metadata. Neither cache nor
            # console should serialize credentials or a transport traceback.
            raise RuntimeError(
                f"Inference failed ({type(exc).__name__}); cache is resumable"
            ) from None
        row = {
            **result,
            "request": request,
            "session_sha256": sha256_json(session),
            "retrieved_at": datetime.now(UTC).isoformat(),
            "runtime_seconds": time.monotonic() - begin,
            "mode": "atlas_lookup" if request["task_family"] == "sge" else "live_inference",
        }
        validate_prediction(row, request, session)
        write_new(path, row)
        inferred += 1
        if inferred % 25 == 0:
            print(f"Cached {inferred} new predictions; {reused} reused", flush=True)
    print(
        f"Saved {inferred}; reused {reused}; elapsed {time.monotonic() - started:.1f}s", flush=True
    )


def collect(plan: dict, cache: Path) -> dict:
    validate_plan(plan)
    session = read_json(cache / "session.json")
    if session["plan_sha256"] != sha256_json(plan):
        raise ValueError("cache belongs to a different frozen plan")
    panels, runtime, cache_hashes = {}, Counter(), {}
    for panel in plan["panels"]:
        predictions, exclusions, evidence = {}, {}, {}
        for c in panel["candidates"]:
            cid, reason = c["candidate_id"], c["exclusion_reason"]
            if reason:
                exclusions[cid] = reason
                continue
            path = cache_path(cache, c["request"])
            if not path.exists():
                raise ValueError("inference incomplete; missing requests cannot become exclusions")
            row = read_json(path)
            validate_prediction(row, c["request"], session)
            if path.name not in cache_hashes:
                runtime[row["mode"]] += row["runtime_seconds"]
                cache_hashes[path.name] = STRATA.file_sha256(path)
            evidence[cid] = row
            if row["status"] == "scored":
                predictions[cid] = row["score"]
            else:
                exclusions[cid] = row["reason"]
        panels[panel["question_id"]] = {
            "question_sha256": panel["question_sha256"],
            "predictions": predictions,
            "exclusions": exclusions,
            "evidence": evidence,
        }
    return {
        "schema_version": "1.0",
        "plan_sha256": sha256_json(plan),
        "session": session,
        "provenance": {
            "name": "AlphaGenome / AVI",
            "version": plan["policy"]["model_version"],
            "source_sha256": plan["source_sha256"],
            "score_definition": {k: plan["policy"][k] for k in ("splicing", "mpra", "avi")},
            "direction": "signed molecular effects; higher AVI means greater impact",
            "input_context": "native GRCh38",
            "known_overlap": plan["policy"]["known_overlap"],
            "variant_matching_rule": "exact question/source digest, candidate ID, complete allele",
            "cost": {
                "access": "free API",
                "underlying_inference_cost": None,
                "note": "Lookup runtime is not the compute cost of generating Atlas scores.",
            },
        },
        "runtime_seconds": dict(runtime),
        "cache_sha256": cache_hashes,
        "panels": panels,
    }


def coverage(plan: dict, specialist: dict) -> list:
    rows = []
    minimum = plan["policy"]["minimum_variants_per_panel"]
    for family in TASKS:
        for category in ("all_covered", "excluding_avi_model_selection"):
            memberships = []
            for panel in plan["panels"]:
                if panel["task_family"] != family:
                    continue
                qid = panel["question_id"]
                predicted = specialist["panels"][qid]
                ids = sorted(predicted["predictions"])
                overlap = (
                    category == "excluding_avi_model_selection"
                    and panel["avi_model_selection_overlap"]
                )
                status = (
                    "avi_model_selection_overlap"
                    if overlap
                    else ("eligible" if len(ids) >= minimum else "below_variant_cutoff")
                )
                memberships.append(
                    {
                        "question_id": qid,
                        "source_record_id": panel["source_record_id"],
                        "candidate_ids": ids,
                        "matched_variants": len(ids),
                        "total_variants": len(panel["candidates"]),
                        "status": status,
                        "exclusions": predicted["exclusions"],
                    }
                )
            eligible = [m for m in memberships if m["status"] == "eligible"]
            rows.append(
                {
                    "task_family": family,
                    "axis": "specialist",
                    "category": category,
                    "status": "eligible" if eligible else "insufficient_coverage",
                    "eligible_panels": len(eligible),
                    "total_panels": len(memberships),
                    "eligible_variants": sum(len(m["candidate_ids"]) for m in eligible),
                    "panels": memberships,
                }
            )
    return rows


def planned_summary(plan: dict) -> dict:
    return {
        "schema_version": "1.0",
        "status": "awaiting_inference",
        "plan_sha256": sha256_json(plan),
        "policy": plan["policy"],
        "coverage": [
            {
                "task_family": family,
                "eligible_variants": sum(
                    c["exclusion_reason"] is None
                    for p in plan["panels"]
                    if p["task_family"] == family
                    for c in p["candidates"]
                ),
                "total_variants": sum(
                    len(p["candidates"]) for p in plan["panels"] if p["task_family"] == family
                ),
            }
            for family in TASKS
        ],
        "results": [],
    }


def overall_results(results: list, runs: list) -> list:
    by_run = {r["run_id"]: r for r in runs}
    groups = {}
    for result in results:
        rid = result["run_id"]
        if rid == "specialist":
            identity = {
                "model": {"model_id": "AlphaGenome / AVI", "model_revision": None},
                "generation_parameters": {},
            }
        else:
            run = by_run[rid]
            identity = {
                "model": {
                    k: run["model"].get(k) for k in ("gateway", "model_id", "model_revision")
                },
                "generation_parameters": run["generation_parameters"],
                **({"retry_policy": run["retry_policy"]} if "retry_policy" in run else {}),
            }
        key = canonical_json(identity), result["category"]
        groups.setdefault(key, []).append(result)
    overall = []
    for (identity, category), rows in sorted(groups.items()):
        if len(rows) != len(TASKS) or {r["task_family"] for r in rows} != set(TASKS):
            continue
        overall.append(
            {
                "configuration": json.loads(identity),
                "category": category,
                "mean_spearman_rho": math.fsum(r["mean_spearman_rho"] for r in rows) / len(TASKS),
                "mean_pearson_r": math.fsum(r["mean_pearson_r"] for r in rows) / len(TASKS),
                "run_ids": [r["run_id"] for r in rows],
            }
        )
    return overall


def compare(plan: dict, specialist: dict, publication: Path, manifest_path: Path) -> dict:
    validate_plan(plan)
    if (
        specialist["plan_sha256"] != sha256_json(plan)
        or STRATA.file_sha256(manifest_path) != plan["manifest_sha256"]
    ):
        raise ValueError("comparison inputs changed after freezing the plan")
    manifest, questions, _ = STRATA.load_inputs(publication, manifest_path)
    STRATA.specialist_support(questions, specialist)
    selected_ids = {r["run_id"] for r in plan["runs"]}
    descriptors = [
        d for d in manifest["artifacts"]["answers"] if Path(d["path"]).parent.name in selected_ids
    ]
    matched = {"runs": plan["runs"], "coverage": coverage(plan, specialist)}
    scores = STRATA.score_plan(
        matched, questions, (STRATA.read_artifact(publication, d) for d in descriptors), specialist
    )
    return {
        **matched,
        "schema_version": "1.0",
        "status": "complete",
        "results": scores,
        "overall": overall_results(scores, plan["runs"]),
        "specialist": specialist["provenance"],
        "predictions": {
            qid: {k: panel[k] for k in ("question_sha256", "predictions", "exclusions")}
            for qid, panel in specialist["panels"].items()
        },
        "session": specialist["session"],
        "runtime_seconds": specialist["runtime_seconds"],
        "plan_sha256": sha256_json(plan),
        "specialist_sha256": sha256_json(specialist),
        "manifest_sha256": plan["manifest_sha256"],
        "implementation": plan["implementation"],
        "plot_sha256": STRATA.file_sha256(POST / "specialist-plots.js"),
        "display_sha256": STRATA.file_sha256(POST / "specialists.js"),
        "answer_artifacts": descriptors,
        "family_colors": read_json(
            ROOT / "projects/explorer/web/components/model-family-colors.json"
        ),
    }


def main() -> None:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("mode", choices=["plan", "predict", "collect", "compare"])
    parser.add_argument("--plan", type=Path, required=True)
    parser.add_argument("--publication", type=Path)
    parser.add_argument("--manifest", type=Path, default=POST / "strata-2026-09-12.manifest.json")
    parser.add_argument("--policy", type=Path, default=POST / "specialist-policy.json")
    parser.add_argument(
        "--annotations", type=Path, default=ROOT / "projects/explorer/data/variant-annotations.json"
    )
    parser.add_argument("--cache", type=Path)
    parser.add_argument("--predictions", type=Path)
    parser.add_argument("--output", type=Path)
    parser.add_argument(
        "--limit", type=int, help="Maximum new requests; partial runs cannot be compared"
    )
    args = parser.parse_args()
    if args.mode in {"plan", "compare"} and args.publication is None:
        parser.error("--publication is required")
    if args.mode in {"predict", "collect"} and args.cache is None:
        parser.error("--cache is required")
    if args.mode in {"collect", "compare"} and args.output is None:
        parser.error("--output is required")
    if args.limit is not None and (args.limit < 1 or args.mode != "predict"):
        parser.error("--limit must be positive and used with predict")
    if args.mode == "plan":
        plan = make_plan(args.publication, args.manifest, args.policy, args.annotations)
        write_new(args.plan, plan)
        if args.output is not None:
            write_new(args.output, planned_summary(plan))
        counts = Counter(
            (p["task_family"], c["exclusion_reason"] or "eligible")
            for p in plan["panels"]
            for c in p["candidates"]
        )
        for (task, status), count in sorted(counts.items()):
            print(task, status, count)
        return
    plan = read_json(args.plan)
    if args.mode == "predict":
        validate_plan(plan)
        key = os.environ.get("ALPHAGENOME_API_KEY")
        if not key:
            parser.error("set ALPHAGENOME_API_KEY in the evaluation process")
        try:
            client = alphagenome_client.AlphaGenomeClient(key, plan["policy"])
            predict(plan, args.cache, client, limit=args.limit)
        except Exception as exc:
            parser.exit(
                1, f"Specialist inference stopped ({type(exc).__name__}); cache retained.\n"
            )
    elif args.mode == "collect":
        write_new(args.output, collect(plan, args.cache))
    else:
        if args.predictions is None:
            parser.error("--predictions is required for compare")
        write_new(
            args.output, compare(plan, read_json(args.predictions), args.publication, args.manifest)
        )


if __name__ == "__main__":
    main()
