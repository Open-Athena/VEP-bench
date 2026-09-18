"""Offline safeguards for the one-time explanation prompt and matched comparison."""

import copy
import importlib.util
import json
from datetime import UTC, datetime
from pathlib import Path

import pytest

from vepbench.artifacts import canonical_json, sha256_json
from vepbench.evaluation.batch import collect_batch_file
from vepbench.evaluation.core import completed_result

SCRIPT = Path(__file__).resolve().parents[1] / "scripts/mechanism_case.py"
SPEC = importlib.util.spec_from_file_location("mechanism_case", SCRIPT)
case = importlib.util.module_from_spec(SPEC)
SPEC.loader.exec_module(case)


def fake_result(question, *, effort="max", invalid=False):
    predictions = {c["candidate_id"]: c["reference_score"] for c in question["candidates"]}
    parameters = copy.deepcopy(case.PARAMETERS)
    parameters["reasoning"]["effort"] = effort
    return completed_result(
        raw={
            "provider": "OpenAI",
            "choices": [
                {
                    "message": {
                        "content": (
                            "No final answer"
                            if invalid
                            else f"FINAL: {canonical_json(predictions)}"
                        ),
                        "reasoning": "Offline synthetic reasoning",
                    },
                    "finish_reason": "stop",
                }
            ],
            "usage": {"prompt_tokens": 10, "completion_tokens": 10, "total_tokens": 20},
        },
        question=question,
        question_set_sha256="a" * 64,
        question_set_size=1,
        run_id="offline-test",
        model_id=case.MODEL_ID,
        generation_parameters=parameters,
        evaluated_at=datetime(2026, 9, 17, tzinfo=UTC),
        latency_seconds=0.1,
    )


def test_frozen_prompt_preserves_inputs_and_excludes_reference_information(tmp_path):
    output = tmp_path / "experiment"
    selection = case.prepare(output)
    original, explained = case.make_questions()
    repeated = case.make_questions()
    assert repeated == (original, explained)
    assert sha256_json(original) == case.QUESTION_SHA256
    assert explained["question_id"] == original["question_id"]
    assert explained["candidates"] == original["candidates"]
    assert len(explained["candidates"]) == 50
    recovered = copy.deepcopy(explained)
    recovered["prompt"] = recovered["prompt"].replace(
        case.INSTRUCTION.read_text().strip(), "You may explain your reasoning."
    )
    recovered["provenance"]["template_version"] = "2.0"
    assert recovered == original
    request = json.loads((output / "request.json").read_text())
    assert request["messages"] == [{"role": "user", "content": explained["prompt"]}]
    for hidden in ["MSH6", "97.121602", "87.466071", "78.4%", "Fig. 2f"]:
        assert hidden not in request["messages"][0]["content"]
    assert selection["primary_panel"]["question_sha256"] == case.QUESTION_SHA256
    assert selection["strongest_frozen_configuration"]["effort"] == "high"
    assert selection["prior_inspection"]["selected_model_traces"] is False
    assert not list(output.glob("*result*"))
    with pytest.raises(FileExistsError):
        case.prepare(output)


def test_comparison_scores_all_candidates_and_reports_setting_departures(tmp_path):
    experiment = tmp_path / "experiment"
    case.prepare(experiment)
    original, explained = case.make_questions()
    baseline = tmp_path / "baseline.jsonl"
    results = tmp_path / "explanation.jsonl"
    case.write_json(baseline, fake_result(original, effort="high"))
    case.write_json(results, fake_result(explained, invalid=True))
    output = tmp_path / "comparison.json"
    summary = case.compare(experiment, baseline, results, output)
    assert summary["variant_count"] == 50
    assert summary["baseline"]["spearman_rho"] == pytest.approx(1)
    assert summary["explanation"]["spearman_rho"] == 0
    assert summary["explanation"]["pearson_r"] == 0
    assert not summary["explanation"]["valid"]
    assert not summary["same_generation_parameters"]
    assert summary["same_upstream_provider"]
    with pytest.raises(ValueError, match="overwrite"):
        case.compare(experiment, baseline, results, output)


def test_comparison_rejects_wrong_panel_even_with_reused_identifier(tmp_path):
    original, _ = case.make_questions()
    wrong = copy.deepcopy(original)
    wrong["candidates"][0]["reference_score"] += 1
    result_path = tmp_path / "wrong.jsonl"
    case.write_json(result_path, fake_result(wrong))
    with pytest.raises(ValueError, match="does not match"):
        case.selected_result(result_path, original)


def test_paired_batch_caps_spend_and_cannot_repeat(tmp_path):
    experiment = tmp_path / "experiment"
    case.prepare_pair(experiment)
    explanations = [case.make_questions(panel)[1] for panel in case.PANELS]
    requests = []

    class FakeTransport:
        def create(self, request, api_key):
            assert api_key == "offline-fake-key"
            requests.append(copy.deepcopy(request))
            return {
                "id": "offline-test-batch",
                "status": "validating",
                "request_counts": {"total": 2, "completed": 0, "failed": 0},
            }

    summary = case.run(experiment, api_key="offline-fake-key", transport=FakeTransport())
    assert summary.requests == 2
    assert len(requests) == 1
    plan = json.loads((experiment / "execution-plan.json").read_text())
    assert plan["maximum_standard_token_cost_usd"] < plan["authorized_budget_usd"] == 15
    assert plan["requests"] == [r["body"] for r in requests[0]["requests"]]
    for request, explained in zip(plan["requests"], explanations, strict=True):
        assert request["provider"]["max_price"] == {"prompt": 10, "completion": 50, "request": 0}
        assert request["messages"] == [{"role": "user", "content": explained["prompt"]}]
    assert not summary.result_output.exists()
    assert "offline-fake-key" not in summary.state_path.read_text()
    assert "offline-fake-key" not in (experiment / "execution-plan.json").read_text()
    with pytest.raises(FileExistsError):
        case.run(experiment, api_key="offline-fake-key", transport=FakeTransport())
    assert len(requests) == 1


def test_secondary_panel_preserves_all_alleles_and_only_changes_explanation():
    original, explained = case.make_questions("ldlr")
    assert len(explained["candidates"]) == 50
    recovered = copy.deepcopy(explained)
    recovered["prompt"] = recovered["prompt"].replace(
        case.INSTRUCTION.read_text().strip(), "You may explain your reasoning."
    )
    recovered["provenance"]["template_version"] = "2.0"
    assert recovered == original
    assert sha256_json(original) == case.PANELS["ldlr"][3]
    for hidden in ["LDLR", "SREBP", "SP1", "-2.07003379293319", "Fig. 1d"]:
        assert hidden not in explained["prompt"]


@pytest.fixture
def failed_batch(tmp_path):
    experiment = tmp_path / "experiment"
    case.prepare_pair(experiment)
    receipt = {
        "id": "offline-failed-batch",
        "status": "completed",
        "request_counts": {"total": 2, "completed": 0, "failed": 2},
        "usage": {"cost": 0},
        "results": [
            {
                "custom_id": f"request_{i:06d}",
                "response": None,
                "error": {"type": "provider_error", "message": "Malformed result; not billed"},
            }
            for i in range(2)
        ],
    }

    class FakeBatch:
        def create(self, request, api_key):
            return {"id": receipt["id"], "status": "validating"}

        def retrieve(self, batch_id, api_key):
            return receipt

    case.run(experiment, api_key="offline-fake-key", transport=FakeBatch())
    case.write_json(experiment / "batch-failure-receipt.json", receipt)
    collect_batch_file(
        state_path=experiment / "batch-state.json",
        questions_path=experiment / "explanation-question.jsonl",
        question_schema_path=case.QUESTION_SCHEMA,
        result_schema_path=case.RESULT_SCHEMA,
        api_key="offline-fake-key",
        transport=FakeBatch(),
    )
    return experiment


def test_direct_recovery_preserves_requests_failures_and_prevents_resubmission(failed_batch):
    experiment = failed_batch
    requests = []
    failures_before = (experiment / "explanation-results.jsonl").read_bytes()
    original_plan = json.loads((experiment / "execution-plan.json").read_text())

    class FakeDirect:
        def complete(self, request_body, api_key):
            requests.append(copy.deepcopy(request_body))
            question = case.make_questions(list(case.PANELS)[len(requests) - 1])[1]
            return fake_result(question)["response"]["raw"]

    result = case.recover_direct(experiment, api_key="offline-fake-key", transport=FakeDirect())
    assert result.completed == 2
    assert result.api_errors == 0
    assert requests == original_plan["requests"]
    assert (experiment / "explanation-results.jsonl").read_bytes() == failures_before
    plan = json.loads((experiment / "direct-recovery-plan.json").read_text())
    assert plan["maximum_new_token_cost_usd"] < plan["authorized_budget_usd"] == 15
    assert "offline-fake-key" not in (experiment / "direct-recovery-plan.json").read_text()
    with pytest.raises(ValueError, match="repeat direct recovery"):
        case.recover_direct(experiment, api_key="offline-fake-key", transport=FakeDirect())
    result.output.unlink()
    with pytest.raises(FileExistsError):
        case.recover_direct(experiment, api_key="offline-fake-key", transport=FakeDirect())
    assert len(requests) == 2


@pytest.mark.parametrize("cost", [None, 0.1])
def test_direct_recovery_rejects_unknown_or_billed_failures(failed_batch, cost):
    path = failed_batch / "batch-failure-receipt.json"
    receipt = json.loads(path.read_text())
    receipt["usage"]["cost"] = cost
    case.write_json(path, receipt)
    with pytest.raises(ValueError, match="confirmed unbilled failures"):
        case.recover_direct(failed_batch, api_key="offline-fake-key")
    assert not (failed_batch / "direct-recovery-plan.json").exists()


@pytest.mark.parametrize("first_cost,expected_calls", [(0.5, 2), (None, 1), (2.5, 1)])
def test_direct_budget_includes_interrupted_attempt(tmp_path, first_cost, expected_calls):
    experiment = tmp_path / "experiment"
    case.prepare_pair(experiment)
    requests, _, _, _ = case.paired_requests(experiment)
    calls = []

    class FakeDirect:
        def complete(self, request_body, api_key):
            calls.append(request_body)
            return {"usage": {"cost": first_cost}}

    client = case.BudgetedDirectTransport(
        transport=FakeDirect(),
        experiment=experiment,
        reserved_cost=case.request_cost_ceiling(requests[0]),
    )
    client.complete(requests[0], "offline-key")
    if expected_calls == 2:
        client.complete(requests[1], "offline-key")
    else:
        with pytest.raises(case.ProviderError, match="not submitted"):
            client.complete(requests[1], "offline-key")
    assert len(calls) == expected_calls
    assert client.reserved_cost <= 15


def test_ldlr_flex_preserves_prediction_settings_and_reserves_prior_cost(tmp_path):
    experiment = tmp_path / "experiment"
    case.prepare_pair(experiment)
    msh6 = fake_result(case.make_questions("msh6")[1])
    msh6["usage"]["cost"] = 2.4
    ldlr = fake_result(case.make_questions("ldlr")[1])
    ldlr["response"]["status"] = "api_error"
    ldlr["error"] = {"message": "Direct request not submitted: remaining $15 budget insufficient"}
    (experiment / "direct-resumed-results.jsonl").write_text(
        canonical_json(msh6) + "\n" + canonical_json(ldlr) + "\n"
    )
    requests = []

    class FakeDirect:
        def complete(self, request_body, api_key):
            requests.append(request_body)
            return fake_result(case.make_questions("ldlr")[1])["response"]["raw"]

    result = case.finish_ldlr_direct(experiment, api_key="offline-key", transport=FakeDirect())
    assert result.completed == 1
    assert len(requests) == 1
    expected = case.paired_requests(experiment)[0][1]
    assert {k: v for k, v in requests[0].items() if k != "provider"} == {
        k: v for k, v in expected.items() if k != "provider"
    }
    assert requests[0]["provider"]["only"] == ["openai/flex"]
    plan = json.loads((experiment / "ldlr-direct-flex/execution-plan.json").read_text())
    assert plan["reserved_prior_cost_usd"] > 2.4
    assert plan["maximum_total_cost_usd"] < 15
    with pytest.raises(FileExistsError):
        case.finish_ldlr_direct(experiment, api_key="offline-key", transport=FakeDirect())
    (experiment / "direct-resumed-request-2.json").write_text("{}")
    with pytest.raises(ValueError, match="not already submitted"):
        case.finish_ldlr_direct(experiment, api_key="offline-key", transport=FakeDirect())
    assert len(requests) == 1
