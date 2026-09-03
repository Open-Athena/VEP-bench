from pathlib import Path

import pytest

from vepbench.config.model import load_model_profile
from vepbench.errors import BuildError

ROOT = Path(__file__).resolve().parents[1]


def test_luna_profile_is_valid_and_omits_temperature() -> None:
    profile = load_model_profile(ROOT / "configs/models/openai-gpt-5.6-luna-medium.yaml")

    assert profile.label == "gpt-5.6-luna-medium"
    assert profile.model_id == "openai/gpt-5.6-luna"
    assert profile.generation_parameters == {
        "reasoning": {"effort": "medium", "exclude": False},
        "seed": 20260829,
    }
    assert "temperature" not in profile.generation_parameters
    assert len(profile.content_sha256) == 64


def test_luna_flex_profile_requests_discounted_service_tier() -> None:
    profile = load_model_profile(ROOT / "configs/models/openai-gpt-5.6-luna-medium-flex.yaml")

    assert profile.label == "gpt-5.6-luna-medium-flex"
    assert profile.model_id == "openai/gpt-5.6-luna"
    assert profile.generation_parameters == {
        "reasoning": {"effort": "medium", "exclude": False},
        "seed": 20260829,
        "service_tier": "flex",
    }
    assert "temperature" not in profile.generation_parameters


def test_sol_profile_is_valid_and_reproducible() -> None:
    profile = load_model_profile(ROOT / "configs/models/openai-gpt-5.6-sol-medium.yaml")

    assert profile.label == "gpt-5.6-sol-medium"
    assert profile.model_id == "openai/gpt-5.6-sol"
    assert profile.generation_parameters == {
        "reasoning": {"effort": "medium", "exclude": False},
        "seed": 20260829,
    }
    assert "temperature" not in profile.generation_parameters


def test_fable_profile_is_valid_and_omits_unsupported_parameters() -> None:
    profile = load_model_profile(ROOT / "configs/models/anthropic-claude-fable-5.1-medium.yaml")

    assert profile.label == "claude-fable-5.1-medium"
    assert profile.model_id == "anthropic/claude-fable-5.1"
    assert profile.generation_parameters == {"reasoning": {"effort": "medium", "exclude": False}}
    assert "temperature" not in profile.generation_parameters
    assert "seed" not in profile.generation_parameters


def test_deepseek_profile_requests_low_reasoning() -> None:
    profile = load_model_profile(ROOT / "configs/models/deepseek-v4-flash-0731-low.yaml")

    assert profile.label == "deepseek-v4-flash-0731-low"
    assert profile.model_id == "deepseek/deepseek-v4-flash-0731"
    assert profile.generation_parameters == {
        "reasoning": {"effort": "low", "exclude": False},
        "seed": 20260829,
    }
    assert "temperature" not in profile.generation_parameters


@pytest.mark.parametrize("effort", ["low", "high"])
def test_luna_comparison_profiles_set_reasoning_only(effort: str) -> None:
    profile = load_model_profile(ROOT / f"configs/models/openai-gpt-5.6-luna-{effort}.yaml")

    assert profile.label == f"gpt-5.6-luna-{effort}"
    assert profile.model_id == "openai/gpt-5.6-luna"
    assert profile.generation_parameters == {
        "reasoning": {"effort": effort, "exclude": False},
        "seed": 20260829,
    }
    assert "temperature" not in profile.generation_parameters


def test_profile_rejects_unknown_fields(tmp_path: Path) -> None:
    path = tmp_path / "bad.yaml"
    path.write_text(
        """\
schema_version: "1.0"
label: example
model: example/model
generation: {}
provider: other
""",
        encoding="utf-8",
    )

    with pytest.raises(BuildError, match="unknown profile field"):
        load_model_profile(path)


def test_profile_rejects_reserved_request_fields(tmp_path: Path) -> None:
    path = tmp_path / "bad.yaml"
    path.write_text(
        """\
schema_version: "1.0"
label: example
model: example/model
generation:
  messages: []
""",
        encoding="utf-8",
    )

    with pytest.raises(BuildError, match="reserved field"):
        load_model_profile(path)
