from pathlib import Path

import pytest

from vepbench.builder import BuildError
from vepbench.cli import main
from vepbench.task_profile import load_task_profile

ROOT = Path(__file__).resolve().parents[1]


def test_ranking_task_uses_shared_completion_ceiling() -> None:
    profile = load_task_profile(ROOT / "configs/tasks/satmut-mpra.yaml")

    assert profile.task_family == "satmut_mpra"
    assert profile.generation_parameters == {"max_tokens": 128_000}


def test_task_profile_rejects_model_specific_generation_settings(
    tmp_path: Path,
) -> None:
    path = tmp_path / "bad.yaml"
    path.write_text(
        """\
schema_version: "1.0"
task_family: synthetic_effect
generation:
  max_tokens: 4096
  reasoning:
    effort: low
""",
        encoding="utf-8",
    )

    with pytest.raises(BuildError, match="exactly max_tokens"):
        load_task_profile(path)


def test_model_profile_requires_task_profile(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setenv("OPENROUTER_API_KEY", "test-key")

    with pytest.raises(SystemExit) as exc_info:
        main(
            [
                "evaluate",
                "--model-profile",
                str(ROOT / "configs/models/openai-gpt-5.6-luna-low.yaml"),
            ]
        )

    assert exc_info.value.code == 2


def test_task_profile_rejects_run_level_completion_override(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    monkeypatch.setenv("OPENROUTER_API_KEY", "test-key")

    with pytest.raises(SystemExit) as exc_info:
        main(
            [
                "evaluate",
                "--model-profile",
                str(ROOT / "configs/models/openai-gpt-5.6-luna-low.yaml"),
                "--task-profile",
                str(ROOT / "configs/tasks/satmut-mpra.yaml"),
                "--max-tokens",
                "4096",
            ]
        )

    assert exc_info.value.code == 2
