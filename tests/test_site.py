import json
from pathlib import Path

import pytest
from vepbench_explorer.site import build_question_metadata, build_site

from vepbench.errors import BuildError

ROOT = Path(__file__).resolve().parents[1]
ASSETS = ROOT / "projects/explorer/web"
SATMUT_SOURCE = ROOT / "data/sources/satmut-mpra-cadd-v1.7.jsonl"
OFFICIAL_DATA_BASE_URL = (
    "https://huggingface.co/buckets/open-athena/VEP-bench/resolve/versions/main"
)


def production_question_metadata() -> dict:
    return build_question_metadata(source_paths=[SATMUT_SOURCE])


def test_site_stages_only_source_assets_and_official_main_config(tmp_path: Path) -> None:
    output = tmp_path / "site"
    metadata = production_question_metadata()
    config = build_site(
        assets_dir=ASSETS,
        output=output,
        data_base_url=OFFICIAL_DATA_BASE_URL,
        question_metadata=metadata,
    )

    assert config["data_base_url"] == OFFICIAL_DATA_BASE_URL
    assert json.loads((output / "data/config.json").read_text()) == config
    assert json.loads((output / "data/question-metadata.json").read_text()) == metadata
    assert set(metadata["by_task_family"]) == {"satmut_mpra"}
    assert len(metadata["by_task_family"]["satmut_mpra"]) == 16
    assert metadata["by_task_family"]["satmut_mpra"]["GP1BA"] == {"element": "GP1BB promoter"}
    assert (output / "index.md").is_file()
    assert not (output / "questions.md").exists()
    assert (output / "tasks/satmut-mpra.md").is_file()
    assert not (output / "tasks/consequence-classification.md").exists()
    assert not (output / "tasks/clinvar.md").exists()
    assert not (output / "data/explorer.json").exists()
    assert not (output / "data/questions.jsonl").exists()
    assert not (output / "data/results").exists()
    leaderboard_source = (output / "index.md").read_text(encoding="utf-8")
    assert 'label: "Task"' in leaderboard_source
    assert '{task_family: null, label: "All tasks"}' in leaderboard_source
    assert "value: taskOptions[0]" in leaderboard_source
    assert 'columns: ["model", "score", "release_date", "tokens", "cost"]' in (leaderboard_source)
    assert 'y: {\n      label: "Score"' in leaderboard_source
    assert "displayScore," in leaderboard_source
    assert "score: displayScore(row.score)" in leaderboard_source
    assert "const width = Math.max(0, Math.min(1, value)) * 100;" in leaderboard_source
    assert "domain: [0, 1]" not in leaderboard_source
    assert "(value + 1) / 2" not in leaderboard_source
    assert "Pearson r" not in leaderboard_source
    assert "Valid outputs" not in leaderboard_source
    task_source = (output / "tasks/satmut-mpra.md").read_text(encoding="utf-8")
    assert "const controls = view(controlsInput);" in task_source
    assert "const selected = view(questionTable);" in task_source
    assert "Generators.input" not in task_source
    component_source = (output / "components/vepbench.js").read_text(encoding="utf-8")
    assert 'element("h2", null, "Prompt given to model")' in component_source
    assert "markdownNode(question.prompt)" in component_source
    assert "Reference effects" not in component_source
    assert "Measured effect" not in component_source


def test_question_metadata_requires_display_metadata_for_every_task_record(tmp_path: Path) -> None:
    source = tmp_path / "future-task.jsonl"
    source.write_text(
        '{"source_record_id":"future-1","task_family":"future_task"}\n',
        encoding="utf-8",
        newline="\n",
    )

    with pytest.raises(BuildError, match="missing display metadata"):
        build_question_metadata(source_paths=[source])


@pytest.mark.parametrize(
    "url",
    [
        "http://example.test/versions/main",
        "file:///tmp/versions/main",
    ],
)
def test_site_rejects_insecure_data_source(tmp_path: Path, url: str) -> None:
    with pytest.raises(BuildError, match="must use HTTPS"):
        build_site(assets_dir=ASSETS, output=tmp_path / "site", data_base_url=url)


def test_site_accepts_configured_https_data_source(tmp_path: Path) -> None:
    url = "https://example.test/versions/experiment"
    config = build_site(assets_dir=ASSETS, output=tmp_path / "site", data_base_url=url)

    assert config["data_base_url"] == url


def test_site_build_refuses_nonempty_output(tmp_path: Path) -> None:
    output = tmp_path / "site"
    output.mkdir()
    (output / "keep.txt").write_text("user data", encoding="utf-8")

    with pytest.raises(BuildError, match="refusing to overwrite"):
        build_site(assets_dir=ASSETS, output=output, data_base_url=OFFICIAL_DATA_BASE_URL)
