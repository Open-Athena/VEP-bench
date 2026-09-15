"""Offline checks for matching, resumption, exclusions and panel/task weighting."""

import copy
import json

import pytest
from vepbench_blog_analysis import specialists as s

from vepbench.artifacts import sha256_json


def question(name, scores, family="sge"):
    q = {
        "question_id": name,
        "metadata": {"task_family": family},
        "task_type": "ranking",
        "candidates": [
            {"candidate_id": f"V{i}", "reference_score": score} for i, score in enumerate(scores)
        ],
    }
    return {**q, "question_sha256": sha256_json(q)}


def panel(q, overlap=False):
    return {
        "question_id": q["question_id"],
        "question_sha256": q["question_sha256"],
        "source_record_id": q["question_id"],
        "task_family": q["metadata"]["task_family"],
        "avi_model_selection_overlap": overlap,
        "candidates": [
            {
                "candidate_id": c["candidate_id"],
                "exclusion_reason": None,
                "request": {
                    "task_family": "sge",
                    "variant": {
                        "assembly": "GRCh38",
                        "chrom": "1",
                        "pos": i + 1,
                        "ref": "A",
                        "alt": "T",
                    },
                },
            }
            for i, c in enumerate(q["candidates"])
        ],
    }


def plan(qs):
    return {
        "schema_version": "1.0",
        "implementation": s.implementation(),
        "policy": s.read_json(s.POST / "specialist-policy.json"),
        "panels": [panel(q) for q in qs],
        "source_sha256": {"synthetic": "hash"},
        "runs": [{"run_id": "model", "task_family": "sge"}],
    }


class FakeClient:
    def __init__(self):
        self.calls = []

    def metadata(self):
        return {"dnase": [], "client_version": "offline"}

    def predict(self, request, metadata):
        self.calls.append(request)
        if request["variant"]["pos"] == 3:
            return {"status": "excluded", "reason": "atlas_score_not_found"}
        return {"status": "scored", "score": float(request["variant"]["pos"])}


def test_partial_run_cannot_be_compared_and_resume_reuses_verified_cache(tmp_path):
    p, client = plan([question("gene", [1, 2, 3])]), FakeClient()
    s.predict(p, tmp_path, client, limit=1)
    with pytest.raises(ValueError, match="incomplete"):
        s.collect(p, tmp_path)
    s.predict(p, tmp_path, client)
    assert len(client.calls) == 3
    s.predict(p, tmp_path, client)
    assert len(client.calls) == 3
    collected = s.collect(p, tmp_path)
    assert collected["panels"]["gene"]["predictions"] == {"V0": 1, "V1": 2}
    assert collected["panels"]["gene"]["exclusions"] == {"V2": "atlas_score_not_found"}
    assert collected["provenance"]["cost"]["underlying_inference_cost"] is None
    path = s.cache_path(tmp_path, client.calls[0])
    row = s.read_json(path)
    row["request"]["variant"]["ref"] = "C"
    path.write_text(json.dumps(row))
    with pytest.raises(ValueError, match="identity"):
        s.collect(p, tmp_path)


def test_transport_errors_are_not_cached_as_missing_scores_or_logged(tmp_path):
    class Failing(FakeClient):
        def predict(self, *args):
            raise ConnectionError("secret-in-request-metadata")

    p = plan([question("gene", [1, 2])])
    with pytest.raises(RuntimeError, match="ConnectionError") as exc:
        s.predict(p, tmp_path, Failing())
    assert "secret-in-request" not in str(exc.value)
    assert list(tmp_path.glob("*.json")) == [tmp_path / "session.json"]
    with pytest.raises(ValueError, match="incomplete"):
        s.collect(p, tmp_path)


def test_frozen_plan_and_outputs_cannot_be_overwritten(tmp_path):
    path = tmp_path / "plan.json.gz"
    p = plan([question("gene", [1, 2])])
    s.write_new(path, p)
    assert s.read_json(path) == p
    with pytest.raises(FileExistsError):
        s.write_new(path, {})
    p["implementation"]["scorer"] = "changed"
    with pytest.raises(ValueError, match="changed"):
        s.validate_plan(p)


def test_matched_rescoring_preserves_invalid_full_answer_penalty_and_equal_panel_weights():
    qs = [question("BRCA1", [1, 2, 3]), question("other", [3, 2, 1, 0])]
    p = plan(qs)
    p["panels"][0]["avi_model_selection_overlap"] = True
    specialist = {
        "panels": {
            "BRCA1": {"predictions": {"V0": 1.0, "V1": 2.0}, "exclusions": {"V2": "missing"}},
            "other": {
                "predictions": {"V0": 3.0, "V1": 2.0, "V2": 1.0},
                "exclusions": {"V3": "missing"},
            },
        }
    }
    answers = [
        {
            "run_id": "model",
            "question_id": q["question_id"],
            "question_sha256": q["question_sha256"],
            "response": {"status": "completed", "content": "FINAL: " + json.dumps(values)},
        }
        for q, values in zip(
            qs, [{"V0": 1, "V1": 2}, {"V0": 0, "V1": 1, "V2": 2, "V3": 3}], strict=True
        )
    ]
    matched = {"runs": p["runs"], "coverage": s.coverage(p, specialist)}
    results = s.STRATA.score_plan(matched, qs, answers, specialist)
    broader = next(r for r in results if r["run_id"] == "model" and r["category"] == "all_covered")
    assert broader["mean_spearman_rho"] == -0.5  # Equal panels, not variant-count weighting.
    assert broader["invalid_panels"] == 1  # Missing V2 stays invalid even outside the match.
    without = next(r for r in results if r["run_id"] == "model" and r["category"] != "all_covered")
    assert without["mean_spearman_rho"] == -1
    assert [r["question_id"] for r in without["panels"]] == ["other"]
    with pytest.raises(ValueError, match="missing answers"):
        s.STRATA.score_plan(matched, qs, answers[:1], specialist)


def test_overall_requires_all_tasks_and_same_configuration():
    runs = [
        {
            "run_id": family,
            "model": {"gateway": "openrouter", "model_id": "x"},
            "generation_parameters": {"reasoning": {"effort": "high"}},
        }
        for family in s.TASKS
    ]
    rows = [
        {
            "task_family": task,
            "run_id": task,
            "category": "all_covered",
            "mean_spearman_rho": value,
            "mean_pearson_r": value,
        }
        for task, value in zip(s.TASKS, [1, 0, -0.4], strict=True)
    ]
    assert s.overall_results(rows, runs)[0]["mean_spearman_rho"] == pytest.approx(0.2)
    assert not s.overall_results(rows[:2], runs)
    runs[-1]["generation_parameters"]["temperature"] = 0.2
    assert not s.overall_results(rows, runs)


def test_committed_source_mapping_defines_eligibility_without_scores():
    sources = [r for path in s.SOURCE_PATHS for r in s.read_jsonl(path)]
    qs = [
        {
            "question_id": r["source_record_id"],
            "question_sha256": "test",
            "metadata": {"task_family": r["task_family"]},
            "candidates": r["candidates"],
            "provenance": {
                "source_record_id": r["source_record_id"],
                "source_record_sha256": sha256_json(r),
            },
        }
        for r in sources
    ]
    ann = s.read_json(s.ROOT / "projects/explorer/data/variant-annotations.json")
    policy = s.read_json(s.POST / "specialist-policy.json")
    panels = s.make_panels(qs, sources, ann, policy)
    supported = {
        family: sum(
            c["exclusion_reason"] is None
            for p in panels
            if p["task_family"] == family
            for c in p["candidates"]
        )
        for family in s.TASKS
    }
    assert supported == {"opensplice_snv": 1000, "satmut_mpra": 700, "sge": 487}
    assert sum(p["avi_model_selection_overlap"] for p in panels) == 3
    # Request artifacts must not expose measured effects or include mutable annotation consequences.
    assert "reference_score" not in json.dumps(panels)
    altered = copy.deepcopy(ann)
    altered["by_task_family"]["sge"]["BRCA1"]["source_record_sha256"] = "stale"
    with pytest.raises(ValueError, match="does not match"):
        s.make_panels(qs, sources, altered, policy)


def test_committed_plan_and_blog_status_match_the_reviewed_implementation():
    p = s.read_json(s.POST / "specialist-plan.json.gz")
    s.validate_plan(p)
    assert p["policy"] == s.read_json(s.POST / "specialist-policy.json")
    assert p["source_sha256"] == {path.name: s.STRATA.file_sha256(path) for path in s.SOURCE_PATHS}
    assert p["annotation_sha256"] == s.STRATA.file_sha256(
        s.ROOT / "projects/explorer/data/variant-annotations.json"
    )
    snapshot = s.read_json(s.POST / "specialist-comparison.json")
    assert snapshot["plan_sha256"] == sha256_json(p)
    if snapshot["status"] == "awaiting_inference":
        assert snapshot == s.planned_summary(p)
