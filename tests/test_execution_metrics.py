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
    ) == {"total_output_tokens": 0, "max_output_tokens_used": 0, "truncated_outputs": 0}
