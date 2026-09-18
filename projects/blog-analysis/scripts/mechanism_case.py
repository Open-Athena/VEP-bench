"""Freeze and replay the two-panel explanation experiment for issue #97.

Preparation, comparison, and evidence export are offline. Inference uses the
existing evaluator; full provider payloads remain outside Git.
"""

import argparse
import copy
import hashlib
import json
import math
import os
from datetime import UTC, datetime
from pathlib import Path

from vepbench.artifacts import canonical_json, read_jsonl, sha256_file, sha256_json
from vepbench.evaluation.batch import submit_batch_file
from vepbench.evaluation.core import (
    OpenRouterTransport,
    ProviderError,
    evaluate_file,
    score_ranking,
)
from vepbench.evaluation.streaming import OpenRouterStreamingTransport
from vepbench.questions.builder import build_questions, load_template, write_questions
from vepbench.resources import QUESTION_SCHEMA, RESULT_SCHEMA

ROOT = Path(__file__).resolve().parents[3]
POST = ROOT / "projects/explorer/web/blog/introducing-vep-bench"
INSTRUCTION = ROOT / "projects/blog-analysis/config/mechanism-explanation.txt"
SELECTION = ROOT / "projects/blog-analysis/config/mechanism-selection.json"
PAIR_SELECTION = ROOT / "projects/blog-analysis/config/mechanism-pair-selection.json"
LDLR_ELEMENTS = ROOT / "projects/blog-analysis/config/ldlr-elements.json"
MSH6_ELEMENTS = ROOT / "projects/blog-analysis/config/msh6-elements.json"
QUESTION_ID = "opensplice-snv-ranking-v2:E01"
QUESTION_SHA256 = "fc2923445b96c5d5ee56932daf3f1dd81361d60b5fd14e639b979b087654738d"
PANELS = {
    "msh6": ("opensplice-snv-figshare-v5", "opensplice-snv", "E01", QUESTION_SHA256),
    "ldlr": (
        "satmut-mpra-cadd-v1.7",
        "satmut-mpra",
        "LDLR",
        "a554c0a44437a7a3a069214518e8d8c5ca2ec13f3721ceaf136efcf3fd4c9267",
    ),
}
MODEL_ID = "openai/gpt-6-astra"
PARAMETERS = {
    "max_tokens": 128000,
    "reasoning": {"effort": "max", "exclude": False},
    "seed": 20260829,
}


def write_json(path, value):
    path.write_text(canonical_json(value) + "\n", encoding="utf-8", newline="\n")


def make_questions(panel="msh6"):
    source_name, task, source_id, digest = PANELS[panel]
    sources = read_jsonl(ROOT / f"data/sources/{source_name}.jsonl")
    source = next(s for s in sources if s["source_record_id"] == source_id)
    template = load_template(ROOT / f"configs/tasks/{task}/prompt.yaml")
    schema = json.loads(QUESTION_SCHEMA.read_text())
    original = build_questions([source], template, schema)[0]
    if sha256_json(original) != digest:
        raise ValueError("The selected question differs from the frozen September 15 panel")
    optional = "You may explain your reasoning."
    if template["prompt"].count(optional) != 1:
        raise ValueError("Unexpected baseline explanation instruction")
    modified = dict(template)
    modified["template_version"] = "2.0-explanation-2"
    modified["prompt"] = template["prompt"].replace(optional, INSTRUCTION.read_text().strip())
    explained = build_questions([source], modified, schema)[0]
    return original, explained


def prepare(output):
    original, explained = make_questions()
    manifest_path = POST / "specialist-2026-09-15.manifest.json"
    manifest = json.loads(manifest_path.read_text())
    frozen = json.loads(SELECTION.read_text())
    selection = {
        "schema_version": "1.0",
        # Replay the original pre-inspection freeze, not a new selection date.
        "frozen_at": frozen["frozen_at"],
        "prompt_revision": frozen["prompt_revision"],
        "issue": "https://github.com/Open-Athena/VEP-bench/issues/97",
        "selection": "One deliberately chosen mechanistic case study; not representative",
        "rationale": "Paper-interpreted exon with localized splice control and tolerant interior",
        "prior_inspection": {
            "measurements_and_paper": True,
            "selected_model_traces": False,
            "deliberate_model_failure_selection": False,
        },
        "primary_panel": {
            "question_id": QUESTION_ID,
            "question_sha256": QUESTION_SHA256,
            "source_record_id": "E01",
            "exon_id": "ENSE00003677325",
            "gene_exon": "MSH6 exon 7",
            "candidates": [c["candidate_id"] for c in original["candidates"]],
            "native_insert_cassette_interval": [147, 331],
            "exon_cassette_interval": [217, 306],
        },
        "secondary_panels": [],
        "publication_manifest_sha256": sha256_file(manifest_path),
        "publication_question_set_sha256": manifest["question_set_sha256"],
        "publication_question_set_size": manifest["question_set_size"],
        # Later publication refreshes must not retroactively reselect this case study.
        "strongest_frozen_configuration": frozen["strongest_frozen_configuration"],
        "matched_baseline": "User's Astra Max run is pending; attach when supplied",
        "model_cutoff": "2026-04-30",
        "first_verified_study_release": "2026-05-23",
        "release_evidence": (
            "https://api.biorxiv.org/details/biorxiv/10.64898/2026.05.22.727141/na/json"
        ),
        "release_limit": "Study-level evidence; earliest supplement/v5 byte identity unverified",
        "instruction_sha256": sha256_file(INSTRUCTION),
        "experiment_question_sha256": sha256_json(explained),
        "planned_completions": 1,
        "planned_retries": 0,
        "model_id": MODEL_ID,
        "generation_parameters": PARAMETERS,
    }
    selection["experiment_question_set_sha256"] = hashlib.sha256(
        (canonical_json(explained) + "\n").encode()
    ).hexdigest()
    if selection != frozen:
        raise ValueError("Preparation no longer reproduces the frozen selection")
    output.mkdir(parents=True, exist_ok=False)
    write_questions([original], output / "baseline-question.jsonl")
    write_questions([explained], output / "explanation-question.jsonl")
    write_json(output / "selection.json", selection)
    write_json(
        output / "request.json",
        {
            "model": MODEL_ID,
            "messages": [{"role": "user", "content": explained["prompt"]}],
            **PARAMETERS,
        },
    )
    (output / "prompt.txt").write_text(explained["prompt"] + "\n", encoding="utf-8")
    return selection


def prepare_pair(output):
    frozen = json.loads(PAIR_SELECTION.read_text())
    originals, explanations = zip(*(make_questions(panel) for panel in PANELS), strict=True)
    expected = frozen["panels"]
    for original, explained in zip(originals, explanations, strict=True):
        identity = expected[original["question_id"]]
        if (sha256_json(original), sha256_json(explained)) != (
            identity["original_question_sha256"],
            identity["explanation_question_sha256"],
        ):
            raise ValueError("Pair does not reproduce the frozen questions")
    if frozen["instruction_sha256"] != sha256_file(INSTRUCTION):
        raise ValueError("Pair instruction differs from its freeze")
    output.mkdir(parents=True, exist_ok=False)
    write_questions(originals, output / "baseline-question.jsonl")
    write_questions(explanations, output / "explanation-question.jsonl")
    if sha256_file(output / "explanation-question.jsonl") != frozen["question_set_sha256"]:
        raise ValueError("Paired question-set digest differs")
    write_json(output / "selection.json", frozen)
    requests = [
        {"model": MODEL_ID, "messages": [{"role": "user", "content": q["prompt"]}], **PARAMETERS}
        for q in explanations
    ]
    write_json(output / "requests.json", requests)
    for panel, question in zip(PANELS, explanations, strict=True):
        (output / f"{panel}-prompt.txt").write_text(question["prompt"] + "\n", encoding="utf-8")
    return frozen


def selected_result(path, expected_question):
    matches = [r for r in read_jsonl(path) if r["question_id"] == expected_question["question_id"]]
    if len(matches) != 1:
        raise ValueError("Expected exactly one result for the selected panel")
    result = matches[0]
    if result["question"] != expected_question or result["question_sha256"] != sha256_json(
        expected_question
    ):
        raise ValueError("Result question does not match the frozen experiment")
    if result["model"]["model_id"] != MODEL_ID:
        raise ValueError("Expected GPT-6 Astra")
    if result["response"]["status"] != "completed":
        raise ValueError("An API failure cannot provide a completed comparison")
    return result


def paired_requests(experiment):
    """Reproduce the frozen requests and conservative undiscounted cost bound."""
    originals, explanations = zip(*(make_questions(panel) for panel in PANELS), strict=True)
    if read_jsonl(experiment / "explanation-question.jsonl") != list(explanations):
        raise ValueError("Explanation question differs from the frozen prompt")
    if read_jsonl(experiment / "baseline-question.jsonl") != list(originals):
        raise ValueError("Original panel differs from the frozen selection")
    if json.loads((experiment / "selection.json").read_text()) != json.loads(
        PAIR_SELECTION.read_text()
    ):
        raise ValueError("Selection differs from the original freeze")
    requests = json.loads((experiment / "requests.json").read_text())
    expected = [
        {"model": MODEL_ID, "messages": [{"role": "user", "content": q["prompt"]}], **PARAMETERS}
        for q in explanations
    ]
    if requests != expected:
        raise ValueError("Prepared request differs from the frozen prompt and settings")
    # Dollar rates per million tokens, as specified by OpenRouter's provider routing API.
    # This execution-only restriction bounds spend and pins the original upstream provider.
    parameters = copy.deepcopy(PARAMETERS)
    parameters["provider"] = {
        "only": ["openai"],
        "allow_fallbacks": False,
        "require_parameters": True,
        "max_price": {"prompt": 10, "completion": 50, "request": 0},
    }
    for request in requests:
        request.update(parameters)
    prompt_token_allowance = sum(len(q["prompt"].encode("utf-8")) + 1024 for q in explanations)
    maximum_cost = prompt_token_allowance * 10 / 1_000_000 + 2 * 128000 * 50 / 1_000_000
    if maximum_cost > 15:
        raise ValueError("Requests exceed the separately authorized $15 budget")
    return requests, parameters, prompt_token_allowance, maximum_cost


def run(experiment, *, api_key, transport=None):
    """Submit one completion per frozen panel; never retry a submitted batch."""
    requests, parameters, prompt_token_allowance, maximum_cost = paired_requests(experiment)
    if not api_key:
        raise ValueError("OPENROUTER_API_KEY is not set")
    output = experiment / "explanation-results.jsonl"
    if output.exists():
        raise ValueError("Refusing to submit again: a result file already exists")
    plan = {
        "authorized_budget_usd": 15,
        "budget_scope": "User-authorized September 17; separate from issue #72",
        "planned_requests": 2,
        "automatic_retries": 0,
        "prepared_at": datetime.now(UTC).isoformat(),
        "prompt_token_allowance": prompt_token_allowance,
        "maximum_standard_token_cost_usd": maximum_cost,
        "estimated_batch_ceiling_usd": maximum_cost / 2,
        "scenario_batch_cost_usd": {
            "20000_output_tokens_each": 1.03,
            "40000_output_tokens_each": 2.03,
        },
        "scenario_assumption": (
            "3000 input tokens each, 50% batch discount; not calibrated forecasts"
        ),
        "requests": requests,
        "requests_sha256": sha256_json(requests),
        "selection_sha256": sha256_file(experiment / "selection.json"),
        "model_route_source": "https://openrouter.ai/api/v1/models/openai/gpt-6-astra/endpoints",
        "routing_departure": "OpenAI only, no fallbacks, required parameters, capped token prices",
        "transport": "OpenRouter batch chat completions",
        "matched_baseline_status": "User's Astra Max run pending; no duplicate baseline requested",
    }
    # Reserve the attempt before contacting the provider. Unknown outcomes require review,
    # not automatic replay of an apparently missing result.
    with (experiment / "execution-plan.json").open("x", encoding="utf-8") as destination:
        destination.write(canonical_json(plan) + "\n")
    return submit_batch_file(
        questions_path=experiment / "explanation-question.jsonl",
        question_schema_path=QUESTION_SCHEMA,
        state_path=experiment / "batch-state.json",
        result_output=output,
        run_id="issue97-msh6-ldlr-astra-max-explanation-20260917",
        model_id=MODEL_ID,
        api_key=api_key,
        generation_parameters=parameters,
        transport=transport,
    )


def recover_direct(experiment, *, api_key, transport=None):
    """Explicitly recover the reviewed, unbilled batch failure exactly once."""
    requests, parameters, _, maximum_cost = paired_requests(experiment)
    original_plan = json.loads((experiment / "execution-plan.json").read_text())
    state = json.loads((experiment / "batch-state.json").read_text())
    receipt_path = experiment / "batch-failure-receipt.json"
    receipt = json.loads(receipt_path.read_text())
    failures_path = experiment / "explanation-results.jsonl"
    failures = read_jsonl(failures_path)
    questions = read_jsonl(experiment / "explanation-question.jsonl")
    if (
        original_plan["requests"] != requests
        or original_plan["requests_sha256"] != sha256_json(requests)
        or state["generation_parameters"] != parameters
        or state["question_set_sha256"] != sha256_file(experiment / "explanation-question.jsonl")
        or receipt.get("id") != state["batch_id"]
    ):
        raise ValueError("Recovery does not match the submitted batch")
    entries = receipt.get("results", [])
    if (
        receipt.get("status") != "completed"
        or receipt.get("request_counts") != {"total": 2, "completed": 0, "failed": 2}
        or receipt.get("usage", {}).get("cost") != 0
        or len(entries) != 2
        or {entry.get("custom_id") for entry in entries} != set(state["submitted_custom_ids"])
        or any(entry.get("response") is not None or not entry.get("error") for entry in entries)
        or len(failures) != 2
    ):
        raise ValueError("Recovery requires confirmed unbilled failures for both requests")
    for failure, question in zip(failures, questions, strict=True):
        if (
            failure["question"] != question
            or failure["response"]["status"] != "api_error"
            or failure["response"]["raw"] not in entries
        ):
            raise ValueError("Preserved failure records do not match the receipt")
    if not api_key:
        raise ValueError("OPENROUTER_API_KEY is not set")
    output = experiment / "direct-results.jsonl"
    if output.exists():
        raise ValueError("Refusing to repeat direct recovery: results already exist")
    plan = {
        "prepared_at": datetime.now(UTC).isoformat(),
        "reason": "Both batch requests failed upstream with malformed results and no charge",
        "original_batch_id": state["batch_id"],
        "original_receipt_sha256": sha256_file(receipt_path),
        "original_failure_results_sha256": sha256_file(failures_path),
        "authorized_budget_usd": 15,
        "previous_cost_usd": 0,
        "maximum_new_token_cost_usd": maximum_cost,
        "automatic_retries": 0,
        "transport": "OpenRouter direct chat completions, sequential, 3600-second timeout",
        "requests": requests,
        "requests_sha256": sha256_json(requests),
    }
    # An unknown direct outcome must never be replayed just because no result was saved.
    with (experiment / "direct-recovery-plan.json").open("x", encoding="utf-8") as destination:
        destination.write(canonical_json(plan) + "\n")
    print("Starting direct recovery of MSH6 and LDLR; one request at a time", flush=True)
    return evaluate_file(
        questions_path=experiment / "explanation-question.jsonl",
        question_schema_path=QUESTION_SCHEMA,
        result_schema_path=RESULT_SCHEMA,
        output=output,
        run_id="issue97-msh6-ldlr-astra-max-explanation-direct-20260918",
        model_id=MODEL_ID,
        api_key=api_key,
        generation_parameters=parameters,
        transport=transport or OpenRouterTransport(timeout=3600),
        concurrency=1,
        progress=lambda done, total, errors: print(
            f"Saved {done}/{total} responses ({errors} API errors)", flush=True
        ),
    )


def request_cost_ceiling(request):
    prompt_bytes = sum(len(m["content"].encode("utf-8")) for m in request["messages"])
    prices = request["provider"]["max_price"]
    return (
        (prompt_bytes + 1024) * prices["prompt"] + request["max_tokens"] * prices["completion"]
    ) / 1_000_000


class BudgetedDirectTransport:
    """Reserve every attempt before sending; release only receipt-confirmed savings."""

    def __init__(self, *, transport, experiment, reserved_cost):
        self.transport = transport
        self.experiment = experiment
        self.reserved_cost = reserved_cost
        self.attempts = 0

    def complete(self, request_body, api_key):
        ceiling = request_cost_ceiling(request_body)
        if self.reserved_cost + ceiling > 15:
            raise ProviderError("Direct request not submitted: remaining $15 budget insufficient")
        self.attempts += 1
        receipt_path = self.experiment / f"direct-resumed-request-{self.attempts}.json"
        record = {
            "started_at": datetime.now(UTC).isoformat(),
            "request_sha256": sha256_json(request_body),
            "maximum_cost_usd": ceiling,
            "status": "started",
        }
        with receipt_path.open("x", encoding="utf-8") as destination:
            destination.write(canonical_json(record) + "\n")
        self.reserved_cost += ceiling
        print(f"Submitting direct request {self.attempts}", flush=True)
        try:
            raw = self.transport.complete(request_body, api_key)
        except ProviderError as error:
            raw = error.raw_response or {}
            record.update(status="api_error", error=str(error), raw=raw)
            raise
        else:
            record.update(status="completed", raw=raw)
            return raw
        finally:
            cost = record.get("raw", {}).get("usage", {}).get("cost")
            if (
                isinstance(cost, int | float)
                and not isinstance(cost, bool)
                and math.isfinite(cost)
                and cost >= 0
            ):
                self.reserved_cost += cost - ceiling
                record["reported_cost_usd"] = cost
            record["finished_at"] = datetime.now(UTC).isoformat()
            write_json(receipt_path, record)


def resume_interrupted_direct(experiment, *, api_key, transport=None):
    """User-authorized restart, reserving the interrupted first request's full cost."""
    requests, parameters, _, _ = paired_requests(experiment)
    previous_plan = experiment / "direct-recovery-plan.json"
    if json.loads(previous_plan.read_text())["requests"] != requests:
        raise ValueError("Interrupted direct plan differs from the frozen requests")
    if (experiment / "direct-results.jsonl").read_bytes():
        raise ValueError("Interrupted run already has results; review before restarting")
    if not api_key:
        raise ValueError("OPENROUTER_API_KEY is not set")
    reserve = request_cost_ceiling(requests[0])
    plan = {
        "prepared_at": datetime.now(UTC).isoformat(),
        "authorization": "User clarified on September 18: launch both directly, not in batch",
        "previous_plan_sha256": sha256_file(previous_plan),
        "interruption": "Local process stopped during first MSH6 request; no response saved",
        "previous_request_cost_reserve_usd": reserve,
        "authorized_budget_usd": 15,
        "budget_policy": "Reserve full attempt ceiling; reconcile only with reported receipt cost",
        "transport": "Direct streaming chat completions; one request at a time",
        "requests": requests,
        "requests_sha256": sha256_json(requests),
    }
    with (experiment / "direct-resumed-plan.json").open("x", encoding="utf-8") as destination:
        destination.write(canonical_json(plan) + "\n")
    return evaluate_file(
        questions_path=experiment / "explanation-question.jsonl",
        question_schema_path=QUESTION_SCHEMA,
        result_schema_path=RESULT_SCHEMA,
        output=experiment / "direct-resumed-results.jsonl",
        run_id="issue97-msh6-ldlr-astra-max-explanation-direct-resumed-20260918",
        model_id=MODEL_ID,
        api_key=api_key,
        generation_parameters=parameters,
        transport=BudgetedDirectTransport(
            transport=transport or OpenRouterStreamingTransport(timeout=3600),
            experiment=experiment,
            reserved_cost=reserve,
        ),
        concurrency=1,
        progress=lambda done, total, errors: print(
            f"Saved {done}/{total} responses ({errors} API errors)", flush=True
        ),
    )


def finish_ldlr_direct(experiment, *, api_key, transport=None):
    """Use the cheaper OpenAI direct route for the still-unsubmitted LDLR panel."""
    requests, parameters, _, _ = paired_requests(experiment)
    results_path = experiment / "direct-resumed-results.jsonl"
    results = read_jsonl(results_path)
    msh6 = selected_result(results_path, make_questions("msh6")[1])
    if (
        len(results) != 2
        or results[1]["question"] != make_questions("ldlr")[1]
        or results[1]["response"]["status"] != "api_error"
        or results[1]["error"]["message"]
        != "Direct request not submitted: remaining $15 budget insufficient"
        or (experiment / "direct-resumed-request-2.json").exists()
    ):
        raise ValueError("LDLR must have been withheld locally, not already submitted")
    completed_cost = msh6["usage"].get("cost")
    if (
        not isinstance(completed_cost, int | float)
        or not math.isfinite(completed_cost)
        or completed_cost < 0
    ):
        raise ValueError("Completed MSH6 cost must be known")
    reserved = request_cost_ceiling(requests[0]) + completed_cost
    parameters["provider"]["only"] = ["openai/flex"]
    parameters["provider"]["max_price"] = {"prompt": 5, "completion": 25, "request": 0}
    request = {**requests[1], **parameters}
    maximum_total = reserved + request_cost_ceiling(request)
    if maximum_total > 15 or not api_key:
        raise ValueError("LDLR requires an API key and room in the authorized $15 budget")
    output = experiment / "ldlr-direct-flex"
    output.mkdir(exist_ok=False)
    write_questions([make_questions("ldlr")[1]], output / "questions.jsonl")
    write_json(
        output / "execution-plan.json",
        {
            "prepared_at": datetime.now(UTC).isoformat(),
            "reason": "Standard-price ceiling exceeded remaining budget; LDLR was not sent",
            "previous_results_sha256": sha256_file(results_path),
            "reserved_prior_cost_usd": reserved,
            "maximum_total_cost_usd": maximum_total,
            "authorized_budget_usd": 15,
            "departure": "OpenAI Flex direct endpoint; narrower routing and price restrictions",
            "request": request,
            "request_sha256": sha256_json(request),
        },
    )
    return evaluate_file(
        questions_path=output / "questions.jsonl",
        question_schema_path=QUESTION_SCHEMA,
        result_schema_path=RESULT_SCHEMA,
        output=output / "results.jsonl",
        run_id="issue97-ldlr-astra-max-explanation-direct-flex-20260918",
        model_id=MODEL_ID,
        api_key=api_key,
        generation_parameters=parameters,
        transport=BudgetedDirectTransport(
            transport=transport or OpenRouterStreamingTransport(timeout=3600),
            experiment=output,
            reserved_cost=reserved,
        ),
        concurrency=1,
    )


def compare(experiment, baseline, results, output, panel="msh6"):
    original, explained = make_questions(panel)
    if original not in read_jsonl(experiment / "baseline-question.jsonl") or (
        explained not in read_jsonl(experiment / "explanation-question.jsonl")
    ):
        raise ValueError("Experiment snapshots differ from the frozen questions")
    if json.loads((experiment / "selection.json").read_text()) not in [
        json.loads(SELECTION.read_text()),
        json.loads(PAIR_SELECTION.read_text()),
    ]:
        raise ValueError("Experiment selection differs from the original freeze")
    before = selected_result(baseline, original)
    after = selected_result(results, explained)
    reference = {c["candidate_id"]: c["reference_score"] for c in original["candidates"]}
    records = []
    for result in [before, after]:
        score = score_ranking(result["response"]["content"], reference)
        records.append(
            {
                "run_id": result["run_id"],
                "model": result["model"],
                "question_sha256": result["question_sha256"],
                "question_set_sha256": result["question_set_sha256"],
                "question_set_size": result["question_set_size"],
                "generation_parameters": result["generation_parameters"],
                "usage": result["usage"],
                "spearman_rho": score.spearman_rho,
                "pearson_r": score.pearson_r,
                "valid": score.valid,
                "parse_error": score.parse_error,
                "predictions": score.parsed_answer,
            }
        )
    summary = {
        "baseline_file_sha256": sha256_file(baseline),
        "experiment_file_sha256": sha256_file(results),
        "selection_file_sha256": sha256_file(experiment / "selection.json"),
        "panel_count": 1,
        "variant_count": len(reference),
        "baseline": records[0],
        "explanation": records[1],
        "same_generation_parameters": (
            before["generation_parameters"] == after["generation_parameters"]
        ),
        "same_upstream_provider": (
            before["model"]["upstream_provider"] == after["model"]["upstream_provider"]
        ),
        "candidates": copy.deepcopy(original["candidates"]),
        "limitation": (
            "One completion per condition cannot distinguish prompt effects "
            "from sampling variability"
        ),
    }
    if output.exists():
        raise ValueError("Refusing to overwrite comparison")
    write_json(output, summary)
    return summary


def ldlr_elements(comparison):
    """Describe every selected allele using explicit, post-response intervals."""
    annotation = json.loads(LDLR_ELEMENTS.read_text())
    source = next(
        s
        for s in read_jsonl(ROOT / "data/sources/satmut-mpra-cadd-v1.7.jsonl")
        if s["source_record_id"] == "LDLR"
    )
    sequence = source["reference_sequence"]
    source_alleles = {
        c["candidate_id"]: c for c in source["source_metadata"]["selected_candidates"]
    }
    for feature in annotation["elements"]:
        if sequence[feature["start"] - 1 : feature["end"]] != feature["sequence"]:
            raise ValueError("LDLR element sequence differs from the submitted insert")
    rows = []
    for candidate in comparison["candidates"]:
        cid, pos = candidate["candidate_id"], candidate["pos"]
        ref, alt = candidate["ref"], candidate["alt"]
        if len(ref) == len(alt) == 1:
            changed = pos
        elif len(ref) == 2 and alt == ref[0]:
            changed = pos + 1  # Do not classify a deletion by its retained VCF anchor.
        else:
            raise ValueError("Expected a substitution or anchored single-base deletion")
        genomic_pos = pos + annotation["genomic_position_offset"]
        if source_alleles[cid]["vcf_key"] != f"19:{genomic_pos}:{ref}:{alt}":
            raise ValueError("LDLR genomic and reporter coordinates disagree")
        matches = [f["id"] for f in annotation["elements"] if f["start"] <= changed <= f["end"]]
        if len(matches) > 1:
            raise ValueError("Overlapping analysis intervals")
        rows.append(
            {
                **candidate,
                "changed_position": changed,
                "genomic_position": genomic_pos,
                "transcript_position": changed + annotation["transcript_position_offset"],
                "element": matches[0] if matches else "other",
                "baseline": comparison["baseline"]["predictions"][cid],
                "explanation": comparison["explanation"]["predictions"][cid],
            }
        )
    groups = []
    for name in [f["id"] for f in annotation["elements"]] + ["other"]:
        members = [r for r in rows if r["element"] == name]
        if not members:
            continue
        groups.append(
            {
                "element": name,
                "candidate_ids": [r["candidate_id"] for r in members],
                "n": len(members),
                "mean_measured": sum(r["reference_score"] for r in members) / len(members),
                **{
                    f"mean_{condition}": sum(r[condition] for r in members) / len(members)
                    for condition in ["baseline", "explanation"]
                },
            }
        )
    return {"annotation": annotation, "groups": groups, "variants": rows}


def msh6_elements(comparison):
    """Audit sequence features and all allele footprints without inferring binding."""
    annotation = json.loads(MSH6_ELEMENTS.read_text())
    source = next(
        s
        for s in read_jsonl(ROOT / "data/sources/opensplice-snv-figshare-v5.jsonl")
        if s["source_record_id"] == "E01"
    )
    sequence = source["source_metadata"]["construct"]["complete_wild_type_cassette"]
    for feature in annotation["features"]:
        if sequence[feature["start"] - 1 : feature["end"]] != feature["sequence"]:
            raise ValueError("MSH6 feature differs from the submitted cassette")
    rows = []
    rescue_pairs = {"V16": (214, 215), "V17": (215, 217), "V18": (215, 237), "V19": (216, 217)}
    for candidate in comparison["candidates"]:
        cid, pos = candidate["candidate_id"], candidate["pos"]
        ref, alt = candidate["ref"], candidate["alt"]
        if sequence[pos - 1 : pos - 1 + len(ref)] != ref:
            raise ValueError("MSH6 allele REF disagrees with the cassette")
        if len(ref) == len(alt) == 1:
            start = end = pos
        elif len(alt) == 1 and ref.startswith(alt):
            start, end = pos + 1, pos + len(ref) - 1
        else:
            raise ValueError("Expected a substitution or anchored deletion")
        mutant = sequence[: pos - 1] + alt + sequence[pos - 1 + len(ref) :]
        # Retain original coordinates through a deletion to check claimed new junctions.
        positions = [*range(1, pos), pos, *range(pos + len(ref), len(sequence) + 1)]
        if len(positions) != len(mutant):
            raise ValueError("Mutant coordinate reconstruction failed")
        overlaps = [
            f["id"] for f in annotation["features"] if start <= f["end"] and end >= f["start"]
        ]
        if "acceptor" in overlaps:
            region = "acceptor"
        elif "donor" in overlaps:
            region = "donor"
        elif end < annotation["exon_start"]:
            region = "upstream"
        elif start > annotation["exon_end"]:
            region = "downstream"
        else:
            region = "interior"
        new_acceptor = None
        if cid in rescue_pairs:
            pair = rescue_pairs[cid]
            index = positions.index(pair[0])
            if positions[index + 1] != pair[1] or mutant[index : index + 2] != "AG":
                raise ValueError("Proposed reconstructed acceptor is absent")
            new_acceptor = list(pair)
        donor = None
        if 307 in positions:
            index = positions.index(307)
            donor = mutant[index - 3 : index] + "|" + mutant[index : index + 6]
        rows.append(
            {
                **candidate,
                "changed_start": start,
                "changed_end": end,
                "region": region,
                "overlapping_features": overlaps,
                "reconstructed_acceptor_original_positions": new_acceptor,
                "donor_context_at_original_307": donor,
                "baseline": comparison["baseline"]["predictions"][cid],
                "explanation": comparison["explanation"]["predictions"][cid],
            }
        )
    groups = []
    for region in ["upstream", "acceptor", "interior", "donor", "downstream"]:
        members = [r for r in rows if r["region"] == region]
        groups.append(
            {
                "region": region,
                "candidate_ids": [r["candidate_id"] for r in members],
                "n": len(members),
                "mean_measured": sum(r["reference_score"] for r in members) / len(members),
                **{
                    f"mean_{condition}": sum(r[condition] for r in members) / len(members)
                    for condition in ["baseline", "explanation"]
                },
            }
        )
    return {"annotation": annotation, "groups": groups, "variants": rows}


def export_evidence(experiment, baseline, output):
    """Export exact readable responses and sufficient data for offline reanalysis."""
    output.mkdir(parents=True, exist_ok=False)
    result_paths = {
        "msh6": experiment / "direct-resumed-results.jsonl",
        "ldlr": experiment / "ldlr-direct-flex/results.jsonl",
    }
    for panel, results in result_paths.items():
        original, explained = make_questions(panel)
        summary = compare(
            experiment, baseline, results, output / f"{panel}-comparison.json", panel=panel
        )
        after = selected_result(results, explained)
        before = selected_result(baseline, original)
        for name, content in {
            "prompt": explained["prompt"],
            "response": after["response"]["content"],
            "reasoning-summary": after["response"]["reasoning"],
            "baseline-response": before["response"]["content"],
        }.items():
            if content is not None:
                (output / f"{panel}-{name}.txt").write_bytes(content.encode("utf-8"))
        if panel == "ldlr":
            write_json(output / "ldlr-elements.json", ldlr_elements(summary))
        else:
            write_json(output / "msh6-elements.json", msh6_elements(summary))
    write_json(
        output / "manifest.json",
        {
            "schema_version": "1.0",
            "scope": (
                "Exact final responses, nullable provider-exposed reasoning summaries, "
                "prompts, baseline final responses, and deterministic comparisons. "
                "Full provider payloads and failed-attempt receipts remain in the experiment."
            ),
            "selection_sha256": sha256_file(experiment / "selection.json"),
            "ldlr_annotation_sha256": sha256_file(LDLR_ELEMENTS),
            "msh6_annotation_sha256": sha256_file(MSH6_ELEMENTS),
            "files": [
                {"path": p.name, "bytes": p.stat().st_size, "sha256": sha256_file(p)}
                for p in sorted(output.iterdir())
            ],
        },
    )


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    commands = parser.add_subparsers(dest="command", required=True)
    freeze = commands.add_parser("prepare")
    freeze.add_argument("--output", type=Path, required=True)
    paired = commands.add_parser("prepare-pair")
    paired.add_argument("--output", type=Path, required=True)
    submit = commands.add_parser("run")
    submit.add_argument("--experiment", type=Path, required=True)
    recovery = commands.add_parser("recover-direct")
    recovery.add_argument("--experiment", type=Path, required=True)
    resumed = commands.add_parser("resume-interrupted-direct")
    resumed.add_argument("--experiment", type=Path, required=True)
    ldlr = commands.add_parser("finish-ldlr-direct")
    ldlr.add_argument("--experiment", type=Path, required=True)
    comparison = commands.add_parser("compare")
    comparison.add_argument("--panel", choices=PANELS, default="msh6")
    for name in ["experiment", "baseline", "results", "output"]:
        comparison.add_argument(f"--{name}", type=Path, required=True)
    evidence = commands.add_parser("export-evidence")
    for name in ["experiment", "baseline", "output"]:
        evidence.add_argument(f"--{name}", type=Path, required=True)
    args = parser.parse_args()
    if args.command == "prepare-pair":
        prepare_pair(args.output)
        print(f"Frozen MSH6 and LDLR: {args.output}")
    elif args.command == "prepare":
        result = prepare(args.output)
        print(f"Frozen {result['primary_panel']['question_id']}: {args.output}")
    elif args.command == "run":
        result = run(args.experiment, api_key=os.environ.get("OPENROUTER_API_KEY"))
        print(f"Submitted {result.requests} requests: {result.batch_id} ({result.status})")
    elif args.command == "recover-direct":
        result = recover_direct(args.experiment, api_key=os.environ.get("OPENROUTER_API_KEY"))
        print(f"Direct recovery: {result.completed} completed, {result.api_errors} API errors")
    elif args.command == "resume-interrupted-direct":
        result = resume_interrupted_direct(
            args.experiment, api_key=os.environ.get("OPENROUTER_API_KEY")
        )
        print(f"Direct run: {result.completed} completed, {result.api_errors} API errors")
    elif args.command == "finish-ldlr-direct":
        result = finish_ldlr_direct(args.experiment, api_key=os.environ.get("OPENROUTER_API_KEY"))
        print(f"LDLR direct: {result.completed} completed, {result.api_errors} API errors")
    elif args.command == "export-evidence":
        export_evidence(args.experiment, args.baseline, args.output)
        print(args.output)
    else:
        compare(args.experiment, args.baseline, args.results, args.output, panel=args.panel)
        print(args.output)


if __name__ == "__main__":
    main()
