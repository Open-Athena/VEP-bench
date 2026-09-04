import json
from pathlib import Path

import pytest
from vepbench_explorer.site import build_question_metadata, build_site

from vepbench.errors import BuildError

ROOT = Path(__file__).resolve().parents[1]
ASSETS = ROOT / "projects/explorer/web"
SATMUT_SOURCE = ROOT / "data/sources/satmut-mpra-cadd-v1.7.jsonl"
SGE_SOURCE = ROOT / "data/sources/sge-mavedb-2026-09-03.jsonl"
OPENSPLICE_SOURCE = ROOT / "data/sources/opensplice-snv-figshare-v5.jsonl"
OFFICIAL_DATA_BASE_URL = (
    "https://huggingface.co/buckets/open-athena/VEP-bench/resolve/versions/main"
)


def production_question_metadata() -> dict:
    return build_question_metadata(source_paths=[OPENSPLICE_SOURCE, SATMUT_SOURCE, SGE_SOURCE])


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
    assert set(metadata["by_task_family"]) == {"opensplice_snv", "satmut_mpra", "sge"}
    assert len(metadata["by_task_family"]["opensplice_snv"]) == 20
    assert len(metadata["by_task_family"]["satmut_mpra"]) == 16
    assert len(metadata["by_task_family"]["sge"]) == 15
    assert metadata["by_task_family"]["satmut_mpra"]["GP1BA"] == {"element": "GP1BB promoter"}
    assert metadata["by_task_family"]["sge"]["BAP1"] == {"element": "BAP1"}
    assert (output / "index.md").is_file()
    assert not (output / "questions.md").exists()
    assert (output / "tasks/satmut-mpra.md").is_file()
    assert (output / "tasks/sge.md").is_file()
    assert (output / "tasks/opensplice-snv.md").is_file()
    assert not (output / "tasks/consequence-classification.md").exists()
    assert not (output / "tasks/clinvar.md").exists()
    assert not (output / "data/explorer.json").exists()
    assert not (output / "data/questions.jsonl").exists()
    assert not (output / "data/results").exists()
    leaderboard_source = (output / "index.md").read_text(encoding="utf-8")
    assert 'label: "Task"' in leaderboard_source
    assert "supportsOverallLeaderboard(aggregation)" in leaderboard_source
    assert "? [null]" in leaderboard_source
    assert "value: allTasksAvailable ? null : taskOptions[0]" in leaderboard_source
    assert "macro average of each task's primary Score" in leaderboard_source
    assert 'columns: ["model", "score", "release_date", "tokens", "cost"]' in (leaderboard_source)
    assert "Unscored model attempts" in leaderboard_source
    assert 'status: "Content filtered"' in leaderboard_source
    assert "Claude Fable 5.1 (medium)" in leaderboard_source
    assert "Claude Opus 5 (medium)" in leaderboard_source
    assert "8/8 panels; zero output tokens; not ranked" in leaderboard_source
    assert "5/8 panels; run stopped and not ranked" in leaderboard_source
    assert "visibleUnscoredAttempts" not in leaderboard_source
    assert "visible regardless of the task selected" in leaderboard_source
    assert leaderboard_source.index("## Unscored model attempts") > leaderboard_source.index(
        "## Score by cost and token usage"
    )
    assert 'y: {\n      label: "Score"' in leaderboard_source
    assert "displayScore," in leaderboard_source
    assert "score: displayScore(row.score)" in leaderboard_source
    assert "const width = Math.max(0, Math.min(1, value)) * 100;" in leaderboard_source
    assert "domain: [0, 1]" not in leaderboard_source
    assert "(value + 1) / 2" not in leaderboard_source
    assert "Pearson r" not in leaderboard_source
    assert "Valid outputs" not in leaderboard_source
    task_source = (output / "tasks/satmut-mpra.md").read_text(encoding="utf-8")
    assert "# Expression (satMutMPRA)" in task_source
    assert "## Task description" in task_source
    assert "https://doi.org/10.1038/s41467-019-11526-w" in task_source
    assert "const controls = view(controlsInput);" in task_source
    assert "const selected = view(questionTable);" in task_source
    assert "Generators.input" not in task_source
    opensplice_source = (output / "tasks/opensplice-snv.md").read_text(encoding="utf-8")
    assert "# Splicing (OpenSplice)" in opensplice_source
    assert "https://doi.org/10.6084/m9.figshare.32337414.v5" in opensplice_source
    assert "large measured 5th-to-95th-percentile" in opensplice_source
    assert "not direct estimates of native-tissue splicing" in opensplice_source
    component_source = (output / "components/vepbench.js").read_text(encoding="utf-8")
    assert 'element("h2", null, "Prompt given to model")' in component_source
    assert "markdownNode(question.prompt)" in component_source
    assert "Reference effects" not in component_source
    assert "npm:@observablehq/plot@0.6.17" in component_source
    assert 'label: "Measured effect"' in component_source
    assert "ariaLabel: (row)" in component_source
    assert 'element("h3", null, "Predictions vs. measurements")' in component_source
    assert "predictionComparisonPlot(comparisonRows)" in component_source
    sge_source = (output / "tasks/sge.md").read_text(encoding="utf-8")
    assert "# Fitness (SGE)" in sge_source
    assert "https://www.mavedb.org/" in sge_source
    assert 'const taskFamily = "sge";' in sge_source
    assert "gene: Inputs.select([" in sge_source
    assert "Reference effects" not in sge_source
    for source in (task_source, opensplice_source, sge_source):
        description = source.split("## Questions", maxsplit=1)[0]
        assert "## Task design" not in description
        assert "## Interpretation" not in description
        assert '<div class="card">' not in description

    tasks_source = (output / "tasks.md").read_text(encoding="utf-8")
    assert (
        tasks_source.index("## Fitness (SGE)")
        < tasks_source.index("## Expression (satMutMPRA)")
        < tasks_source.index("## Splicing (OpenSplice)")
    )


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
