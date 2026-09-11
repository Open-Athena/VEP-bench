"""Offline scientific checks for the post's frozen stratum analysis."""

import copy
import hashlib
import importlib.util
import json
from pathlib import Path

import pytest

from vepbench.artifacts import canonical_json, sha256_json

POST = Path(__file__).parents[1] / "projects/explorer/web/blog/introducing-vep-bench"
SPEC = importlib.util.spec_from_file_location("variant_strata", POST / "strata.py")
strata = importlib.util.module_from_spec(SPEC)
SPEC.loader.exec_module(strata)


def question(qid, scores):
    q = {
        "question_id": qid,
        "task_type": "ranking",
        "metadata": {"task_family": "sge"},
        "provenance": {"source_record_id": qid, "source_record_sha256": "source"},
        "candidates": [
            {"candidate_id": f"V{i}", "ref": "A", "alt": "T", "reference_score": score}
            for i, score in enumerate(scores)
        ],
    }
    return {**q, "question_sha256": sha256_json(q)}


def answer(q, predictions, rid="model"):
    return {
        "run_id": rid,
        "question_id": q["question_id"],
        "question_sha256": q["question_sha256"],
        "response": {"status": "completed", "content": "FINAL: " + json.dumps(predictions)},
    }


def annotations(questions):
    return {
        "annotation": {
            "provider": "Ensembl VEP",
            "software": {"release": 114},
            "assembly": "GRCh38",
            "consequence_policy": "most_severe_consequence",
            "transcripts": "Ensembl (all)",
        },
        "by_task_family": {
            "sge": {
                q["question_id"]: {
                    "source_record_sha256": "source",
                    "variants": {
                        c["candidate_id"]: {"most_severe_consequence": "missense_variant"}
                        for c in q["candidates"]
                    },
                }
                for q in questions
            }
        },
    }


@pytest.mark.parametrize(
    ("ref", "alt", "expected"),
    [
        ("ACG", "ATG", "SNV"),
        ("A", "AT", "Insertion"),
        ("AT", "A", "Deletion"),
        ("A", "TA", "Insertion"),
        ("TA", "A", "Deletion"),
        ("", "G", "Insertion"),
        ("G", "", "Deletion"),
        ("AC", "GT", "Other/complex"),
        ("AC", "GTT", "Other/complex"),
        ("A", "A", "Unknown"),
        (None, "T", "Unknown"),
        ("A", "<DEL>", "Unknown"),
    ],
)
def test_complete_edit_types(ref, alt, expected):
    assert strata.allele_type(ref, alt) == expected


def test_axes_cutoff_boundaries_and_explicit_unknowns():
    qs = [question("one", [1, 2, 3]), question("two", [1, 2]), question("three", [1])]
    ann = annotations(qs)
    ann["by_task_family"]["sge"]["three"]["variants"]["V0"]["most_severe_consequence"] = [
        "missense_variant",
        "synonymous_variant",
    ]
    policy = {"minimum_variants_per_panel": 2, "minimum_panels_per_task": 2}
    rows = strata.coverage_plan(qs, ann, policy)
    snv = next(r for r in rows if r["category"] == "SNV")
    missense = next(r for r in rows if r["category"] == "missense_variant")
    assert (snv["variant_count"], snv["eligible_panels"], snv["eligible_variants"]) == (6, 2, 5)
    assert snv["status"] == missense["status"] == "eligible"
    assert snv["below_cutoff_variants"] == 1
    assert next(r for r in rows if r["category"] == "Ambiguous")["variant_count"] == 1
    assert (
        next(r for r in rows if r["category"] == "Insertion")["status"] == "insufficient_coverage"
    )
    ann["by_task_family"]["sge"]["one"]["source_record_sha256"] = "stale"
    rows = strata.coverage_plan(qs, ann, policy)
    assert (
        next(r for r in rows if r["axis"] == "consequence" and r["category"] == "Unknown")[
            "variant_count"
        ]
        == 3
    )
    assert (
        next(r for r in rows if r["category"] == "missense_variant")["status"]
        == "insufficient_coverage"
    )


def test_original_invalid_answer_is_not_salvaged_on_subset():
    q = question("one", [1, 2, 3])
    invalid = answer(q, {"V0": 1, "V1": 2})
    score = strata.rescore_panel(q, invalid, ["V0", "V1"])
    assert score["spearman_rho"] == score["pearson_r"] == 0
    assert score["valid"] is False
    with pytest.raises(ValueError, match="API errors"):
        strata.rescore_panel(q, {**invalid, "response": {"status": "api_error"}}, ["V0", "V1"])
    with pytest.raises(ValueError, match="digest"):
        strata.rescore_panel(q, {**invalid, "question_sha256": "wrong"}, ["V0", "V1"])


def test_ties_and_constant_vectors_keep_existing_rules():
    q = question("ties", [1, 2, 2])
    score = strata.rescore_panel(q, answer(q, {"V0": 1, "V1": 3, "V2": 2}), ["V0", "V1", "V2"])
    assert score["spearman_rho"] == pytest.approx(3**0.5 / 2)
    score = strata.rescore_panel(q, answer(q, {"V0": 1, "V1": 3, "V2": 2}), ["V1", "V2"])
    assert score["constant_reference"] is True
    assert score["spearman_rho"] == score["pearson_r"] == 0
    assert score["valid"] is True


def test_panel_macro_average_and_common_membership():
    qs = [question("one", [1, 2]), question("two", [101, 102, 103])]
    policy = {"minimum_variants_per_panel": 2, "minimum_panels_per_task": 2}
    plan = {
        "coverage": strata.coverage_plan(qs, annotations(qs), policy),
        "runs": [
            {"run_id": "model", "task_family": "sge"},
            {"run_id": "invalid", "task_family": "sge"},
        ],
    }
    answers = [
        answer(qs[0], {"V0": 1, "V1": 2}),
        answer(qs[1], {"V0": 5, "V1": 4, "V2": 3}),
        answer(qs[0], {}, "invalid"),
        answer(qs[1], {"V0": 1, "V1": 2, "V2": 3}, "invalid"),
    ]
    results = strata.score_plan(plan, qs, iter(answers))
    assert next(r for r in results if r["run_id"] == "model")["mean_spearman_rho"] == 0
    invalid = next(r for r in results if r["run_id"] == "invalid")
    assert invalid["mean_spearman_rho"] == 0.5
    assert invalid["invalid_panels"] == 1
    assert all(len(r["panels"]) == 2 for r in results)
    with pytest.raises(ValueError, match="missing answers"):
        strata.score_plan(plan, qs, iter(answers[:-1]))
    with pytest.raises(ValueError, match="duplicate"):
        strata.score_plan(plan, qs, iter(answers + answers[:1]))
    plan["coverage"] = strata.coverage_plan(
        qs, annotations(qs), {**policy, "minimum_panels_per_task": 3}
    )
    assert strata.score_plan(plan, qs, iter(answers)) == []


def test_specialist_support_restricts_every_model_before_cutoffs():
    qs = [question("one", [1, 2, 3]), question("two", [1, 2])]
    specialist = {
        "provenance": dict.fromkeys(
            [
                "name",
                "version",
                "source_sha256",
                "score_definition",
                "direction",
                "input_context",
                "known_overlap",
                "variant_matching_rule",
            ],
            "offline fixture",
        ),
        "panels": {
            "one": {"question_sha256": qs[0]["question_sha256"], "predictions": {"V0": 1, "V1": 2}},
            "two": {"question_sha256": qs[1]["question_sha256"], "predictions": {"V0": 1}},
        },
    }
    support = strata.specialist_support(qs, specialist)
    rows = strata.coverage_plan(
        qs,
        annotations(qs),
        {"minimum_variants_per_panel": 2, "minimum_panels_per_task": 2},
        support,
    )
    snv = next(r for r in rows if r["category"] == "SNV")
    assert (snv["variant_count"], snv["unsupported_variants"], snv["eligible_panels"]) == (5, 2, 1)
    assert snv["status"] == "insufficient_coverage"
    assert snv["panels"][0]["candidate_ids"] == ["V0", "V1"]
    specialist["panels"]["two"]["predictions"]["V1"] = 2
    rows = strata.coverage_plan(
        qs,
        annotations(qs),
        {"minimum_variants_per_panel": 2, "minimum_panels_per_task": 2},
        strata.specialist_support(qs, specialist),
    )
    plan = {"coverage": rows, "runs": [{"run_id": "model", "task_family": "sge"}]}
    results = strata.score_plan(
        plan,
        qs,
        [answer(qs[0], {"V0": 1, "V1": 2, "V2": 3}), answer(qs[1], {"V0": 1, "V1": 2})],
        specialist,
    )
    assert {r["run_id"] for r in results} == {"model", "specialist"}
    assert all(r["mean_spearman_rho"] == 1 for r in results)
    changed = copy.deepcopy(specialist)
    changed["panels"]["one"]["question_sha256"] = "changed"
    with pytest.raises(ValueError, match="identity"):
        strata.specialist_support(qs, changed)


def test_saved_coverage_cutoffs_are_fixed():
    assert json.loads((POST / "strata-policy.json").read_text()) == {
        "minimum_variants_per_panel": 10,
        "minimum_panels_per_task": 5,
    }


def test_selects_highest_available_effort_per_model_without_using_scores():
    q = question("one", [1, 2])
    content = canonical_json({k: v for k, v in q.items() if k != "question_sha256"}) + "\n"
    common = {
        "coverage": {"complete": True},
        "evaluation_profile": "sge:fixture@2.0",
        "task_type": "ranking",
        "question_set_size": 1,
        "question_set_sha256": hashlib.sha256(content.encode()).hexdigest(),
    }

    def run(rid, model, effort, date, score):
        return {
            **common,
            "run_id": rid,
            "configuration_key": rid,
            "model": {"model_id": model},
            "generation_parameters": {"reasoning": {"effort": effort}},
            "completed_at": date,
            "metrics": {"mean_spearman_rho": score},
        }

    runs = [
        run("high-old", "model-a", "high", "2026-09-01", -1),
        run("high-new", "model-a", "high", "2026-09-02", 0),
        run("medium-newest", "model-a", "medium", "2026-09-03", 1),
        run("low-only", "model-b", "low", "2026-09-03", 1),
    ]
    selected, excluded = strata.select_runs([q], runs)
    assert [r["run_id"] for r in selected] == ["high-new", "low-only"]
    assert excluded == [
        {"run_id": "high-old", "reason": "superseded_same_effort"},
        {"run_id": "medium-newest", "reason": "lower_reasoning_effort"},
    ]
    unknown = run("implicit", "model-c", None, "2026-09-03", 1)
    with pytest.raises(ValueError, match="unrecognized reasoning effort"):
        strata.select_runs([q], [unknown])
    failed_high = run("incomplete", "model-b", "high", "2026-09-03", 1)
    failed_high["coverage"] = {"complete": False}
    selected, excluded = strata.select_runs([q], [failed_high, runs[-1]])
    assert [r["run_id"] for r in selected] == ["low-only"]
    assert excluded == [{"run_id": "incomplete", "reason": "incomplete_run"}]
