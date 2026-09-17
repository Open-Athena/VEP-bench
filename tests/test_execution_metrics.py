import pytest
from vepbench_publishing.execution import execution_metrics


def test_execution_usage_counts_reasoning_and_truncation_with_a_valid_final_answer() -> None:
    records = [
        {
            "response": {"status": "completed", "finish_reason": "length"},
            "scoring": {"valid": True},
            "usage": {
                "completion_tokens": 100,
                "completion_tokens_details": {"reasoning_tokens": 90},
            },
        },
        {
            "response": {"status": "completed", "finish_reason": "stop"},
            "usage": {"completion_tokens": 30},
        },
        {"response": {"status": "api_error", "finish_reason": None}, "usage": {}},
    ]
    assert execution_metrics(records) == {
        "total_output_tokens": 130,
        "max_output_tokens_used": 100,
        "truncated_outputs": 1,
        "completed_response_cost_usd": None,
        "completed_response_total_tokens": None,
    }


@pytest.mark.parametrize("missing", [None, True, -1, "100"])
def test_partial_output_usage_does_not_become_a_complete_total_or_maximum(missing: object) -> None:
    records = [
        {
            "response": {"status": "completed", "finish_reason": "stop"},
            "usage": {"completion_tokens": 30},
        },
        {
            "response": {"status": "completed", "finish_reason": "length"},
            "usage": {"completion_tokens": missing},
        },
    ]
    assert execution_metrics(records) == {
        "total_output_tokens": None,
        "max_output_tokens_used": None,
        "truncated_outputs": 1,
        "completed_response_cost_usd": None,
        "completed_response_total_tokens": None,
    }


def test_zero_usage_is_distinct_from_no_completed_responses() -> None:
    assert execution_metrics([])["total_output_tokens"] is None
    assert execution_metrics(
        [
            {
                "response": {"status": "completed", "finish_reason": "stop"},
                "usage": {"completion_tokens": 0},
            },
        ]
    ) == {
        "total_output_tokens": 0,
        "max_output_tokens_used": 0,
        "truncated_outputs": 0,
        "completed_response_cost_usd": None,
        "completed_response_total_tokens": None,
    }


@pytest.mark.parametrize("prior_status, expected", [("api_error", 0.5), ("completed", 1.2)])
def test_benchmark_cost_excludes_serving_errors_but_counts_completed_truncations(
    prior_status: str, expected: float
) -> None:
    records = [
        {
            "response": {"status": "completed", "finish_reason": "stop"},
            "usage": {
                "cost": 0.2,
                "vepbench": {
                    "retry": {
                        "prior_attempt": {
                            "response": {"status": prior_status},
                            "usage": {"cost": 0.7},
                        },
                        "source_run_id": "retry",
                        "source_record_sha256": "0" * 64,
                    }
                },
            },
        },
        {"response": {"status": "completed", "finish_reason": "stop"}, "usage": {"cost": 0.3}},
        {"response": {"status": "api_error", "finish_reason": None}, "usage": {"cost": 0.7}},
    ]
    assert execution_metrics(records)["completed_response_cost_usd"] == expected
    del records[1]["usage"]["cost"]
    assert execution_metrics(records)["completed_response_cost_usd"] is None


@pytest.mark.parametrize("prior_status, expected", [("api_error", 50), ("completed", 120)])
@pytest.mark.parametrize("failed_usage", [{}, {"total_tokens": 900}])
def test_benchmark_tokens_exclude_serving_errors_but_count_completed_truncations(
    prior_status: str, expected: int, failed_usage: dict[str, int]
) -> None:
    records = [
        {
            "response": {"status": "completed", "finish_reason": "stop"},
            "usage": {
                "total_tokens": 20,
                "vepbench": {
                    "retry": {
                        "prior_attempt": {
                            "response": {"status": prior_status, "finish_reason": "length"},
                            "usage": {"total_tokens": 70},
                        },
                        "source_run_id": "retry",
                        "source_record_sha256": "0" * 64,
                    }
                },
            },
        },
        {
            "response": {"status": "completed", "finish_reason": "stop"},
            "usage": {
                "prompt_tokens": 10,
                "completion_tokens": 20,
                "completion_tokens_details": {"reasoning_tokens": 15},
            },
        },
        {"response": {"status": "api_error", "finish_reason": None}, "usage": failed_usage},
    ]
    assert execution_metrics(records)["completed_response_total_tokens"] == expected
    del records[1]["usage"]["prompt_tokens"]
    assert execution_metrics(records)["completed_response_total_tokens"] is None


@pytest.mark.parametrize("missing", [None, True, -1, "100", 10.5])
def test_invalid_completed_token_usage_is_unknown(missing: object) -> None:
    record = {
        "response": {"status": "completed", "finish_reason": "stop"},
        "usage": {"total_tokens": missing},
    }
    assert execution_metrics([record])["completed_response_total_tokens"] is None
    record["usage"] = {"prompt_tokens": missing, "completion_tokens": 100}
    assert execution_metrics([record])["completed_response_total_tokens"] is None


def test_zero_completed_tokens_are_distinct_from_no_completed_usage() -> None:
    assert execution_metrics([])["completed_response_total_tokens"] is None
    record = {
        "response": {"status": "completed", "finish_reason": "stop"},
        "usage": {"prompt_tokens": 0, "completion_tokens": 0},
    }
    assert execution_metrics([record])["completed_response_total_tokens"] == 0
