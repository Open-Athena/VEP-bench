from pathlib import Path

import pytest

from vepbench.cli import main
from vepbench.config.task import load_task_profile
from vepbench.errors import BuildError

ROOT = Path(__file__).resolve().parents[1]


def test_ranking_task_uses_shared_completion_ceiling() -> None:
    profile = load_task_profile(ROOT / "configs/tasks/satmut-mpra/task.yaml")

    assert profile.task_family == "satmut_mpra"
    assert profile.generation_parameters == {"max_tokens": 128_000}


def test_task_identity_uses_semantic_yaml_values(tmp_path: Path) -> None:
    source = ROOT / "configs/tasks/satmut-mpra/task.yaml"
    original = load_task_profile(source)
    reformatted = tmp_path / "task.yaml"
    reformatted.write_text(
        f"# A comment does not affect identity.\n{source.read_text(encoding='utf-8')}\n",
        encoding="utf-8",
    )

    assert load_task_profile(reformatted).content_sha256 == original.content_sha256


def test_task_profile_rejects_model_specific_generation_settings(
    tmp_path: Path,
) -> None:
    path = tmp_path / "bad.yaml"
    path.write_text(
        """\
schema_version: "1.0"
task_family: synthetic_effect
question:
  type: multiple_choice
  source: source.jsonl
  prompt: prompt.yaml
evaluation:
  generation:
    max_tokens: 4096
    reasoning:
      effort: low
""",
        encoding="utf-8",
    )

    with pytest.raises(BuildError, match="exactly max_tokens"):
        load_task_profile(path)


def test_task_profile_rejects_duplicate_yaml_keys(tmp_path: Path) -> None:
    path = tmp_path / "duplicate.yaml"
    path.write_text('schema_version: "1.0"\nschema_version: "2.0"\n', encoding="utf-8")

    with pytest.raises(BuildError, match="duplicate key"):
        load_task_profile(path)


def test_model_profile_requires_task_profile(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setenv("OPENROUTER_API_KEY", "test-key")

    status = main(
        [
            "evaluate",
            "--model-profile",
            str(ROOT / "configs/models/openai-gpt-5.6-luna-low.yaml"),
        ]
    )

    assert status == 2


def test_task_profile_rejects_run_level_completion_override(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    monkeypatch.setenv("OPENROUTER_API_KEY", "test-key")

    status = main(
        [
            "evaluate",
            "--model-profile",
            str(ROOT / "configs/models/openai-gpt-5.6-luna-low.yaml"),
            "--task",
            str(ROOT / "configs/tasks/satmut-mpra/task.yaml"),
            "--max-tokens",
            "4096",
        ]
    )

    assert status == 2
