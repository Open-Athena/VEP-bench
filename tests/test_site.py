import json
from pathlib import Path

import pytest

from vepbench.builder import BuildError
from vepbench.site import OFFICIAL_DATA_BASE_URL, build_question_metadata, build_site

ROOT = Path(__file__).resolve().parents[1]
ASSETS = ROOT / "web"
CONSEQUENCE_SOURCE = ROOT / "data/sources/chr17-vep-consequences.jsonl"
CONSEQUENCE_MANIFEST = ROOT / "data/sources/chr17-vep-consequences.manifest.json"
CLINVAR_SOURCE = ROOT / "data/sources/clinvar-july-2026.jsonl"


def production_question_metadata() -> dict:
    consequence_manifest = json.loads(CONSEQUENCE_MANIFEST.read_text(encoding="utf-8"))
    return build_question_metadata(
        source_paths=[CONSEQUENCE_SOURCE, CLINVAR_SOURCE],
        consequence_overrides={
            "vep_most_severe_consequence": consequence_manifest["record_source_consequences"]
        },
    )


def test_site_stages_only_source_assets_and_official_main_config(tmp_path: Path) -> None:
    output = tmp_path / "site"
    metadata = production_question_metadata()
    config = build_site(assets_dir=ASSETS, output=output, question_metadata=metadata)

    assert config["data_base_url"] == OFFICIAL_DATA_BASE_URL
    assert json.loads((output / "data/config.json").read_text()) == config
    assert json.loads((output / "data/question-metadata.json").read_text()) == metadata
    assert len(metadata["by_task_family"]["clinvar"]) == 42
    assert len(metadata["by_task_family"]["vep_most_severe_consequence"]) == 51
    assert metadata["by_task_family"]["clinvar"]["VCV004857040.1"] == {
        "consequence": "missense_variant"
    }
    assert metadata["by_task_family"]["vep_most_severe_consequence"]["17:38786886:A:T"] == {
        "consequence": "start_lost"
    }
    assert (output / "index.md").is_file()
    assert (output / "questions.md").is_file()
    assert (output / "tasks/consequence-classification.md").is_file()
    assert (output / "tasks/clinvar.md").is_file()
    assert not (output / "data/explorer.json").exists()
    assert not (output / "data/questions.jsonl").exists()
    assert not (output / "data/results").exists()
    question_source = (output / "questions.md").read_text(encoding="utf-8")
    assert "const controls = view(controlsInput);" in question_source
    assert "const selected = view(questionTable);" in question_source
    assert "Generators.input" not in question_source


def test_question_metadata_requires_consequence_for_every_task_record(tmp_path: Path) -> None:
    source = tmp_path / "future-task.jsonl"
    source.write_text(
        '{"source_record_id":"future-1","task_family":"future_task"}\n',
        encoding="utf-8",
        newline="\n",
    )

    with pytest.raises(BuildError, match="missing consequence metadata"):
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
