import json
from pathlib import Path

import pytest

from vepbench.builder import BuildError
from vepbench.site import OFFICIAL_DATA_BASE_URL, build_site

ROOT = Path(__file__).resolve().parents[1]
ASSETS = ROOT / "web"


def test_site_stages_only_source_assets_and_official_main_config(tmp_path: Path) -> None:
    output = tmp_path / "site"
    config = build_site(assets_dir=ASSETS, output=output)

    assert config["data_base_url"] == OFFICIAL_DATA_BASE_URL
    assert json.loads((output / "data/config.json").read_text()) == config
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
