from pathlib import Path

import pytest

from vepbench.builder import BuildError
from vepbench.task_profile import load_task_profile

ROOT = Path(__file__).resolve().parents[1]


def test_consequence_task_owns_completion_ceiling() -> None:
    profile = load_task_profile(
        ROOT / "configs/tasks/vep-most-severe-consequence.yaml"
    )

    assert profile.task_family == "vep_most_severe_consequence"
    assert profile.generation_parameters == {"max_tokens": 128000}
    assert len(profile.content_sha256) == 64


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
