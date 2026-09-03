import json
from pathlib import Path

import pytest

from vepbench.builder import BuildError
from vepbench.site import OFFICIAL_DATA_BASE_URL, build_question_metadata, build_site

ROOT = Path(__file__).resolve().parents[1]
ASSETS = ROOT / "web"
SATMUT_SOURCE = ROOT / "data/sources/satmut-mpra-cadd-v1.7.jsonl"


def production_question_metadata() -> dict:
    return build_question_metadata(source_paths=[SATMUT_SOURCE])


def test_site_stages_only_source_assets_and_official_main_config(tmp_path: Path) -> None:
    output = tmp_path / "site"
    metadata = production_question_metadata()
    config = build_site(assets_dir=ASSETS, output=output, question_metadata=metadata)

    assert config["data_base_url"] == OFFICIAL_DATA_BASE_URL
    assert json.loads((output / "data/config.json").read_text()) == config
    assert json.loads((output / "data/question-metadata.json").read_text()) == metadata
    assert set(metadata["by_task_family"]) == {"satmut_mpra"}
    assert len(metadata["by_task_family"]["satmut_mpra"]) == 16
    assert metadata["by_task_family"]["satmut_mpra"]["GP1BA"] == {"element": "GP1BB promoter"}
    assert (output / "index.md").is_file()
    assert (output / "questions.md").is_file()
    assert (output / "tasks/satmut-mpra.md").is_file()
    assert not (output / "tasks/consequence-classification.md").exists()
    assert not (output / "tasks/clinvar.md").exists()
    assert not (output / "data/explorer.json").exists()
    assert not (output / "data/questions.jsonl").exists()
    assert not (output / "data/results").exists()
    question_source = (output / "questions.md").read_text(encoding="utf-8")
    assert "const controls = view(controlsInput);" in question_source
    assert "const selected = view(questionTable);" in question_source
    assert "Generators.input" not in question_source
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
        "https://example.test/versions/experiment",
        "https://example.test/versions/main",
        "file:///tmp/versions/main",
    ],
)
def test_site_rejects_noncanonical_data_source(tmp_path: Path, url: str) -> None:
    with pytest.raises(BuildError, match="canonical HF Bucket main"):
        build_site(assets_dir=ASSETS, output=tmp_path / "site", data_base_url=url)


def test_site_build_refuses_nonempty_output(tmp_path: Path) -> None:
    output = tmp_path / "site"
    output.mkdir()
    (output / "keep.txt").write_text("user data", encoding="utf-8")

    with pytest.raises(BuildError, match="refusing to overwrite"):
        build_site(assets_dir=ASSETS, output=output)
