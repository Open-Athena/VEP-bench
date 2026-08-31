import gzip
import json
from pathlib import Path

from scripts.prepare_browser_qa_fixture import prepare_fixture
from vepbench.publication import validate_version

ROOT = Path(__file__).resolve().parents[1]
QUESTIONS = ROOT / "tests/fixtures/synthetic-questions.jsonl"


def test_browser_qa_fixture_is_complete_and_offline(tmp_path: Path) -> None:
    site = tmp_path / "site"
    (site / "data").mkdir(parents=True)
    (site / "data/config.json").write_text("{}\n", encoding="utf-8")
    output = tmp_path / "publication"
    base_url = "http://127.0.0.1:4173/publication/versions/main"

    manifest = prepare_fixture(
        questions_path=QUESTIONS,
        output=output,
        selected_question_id="mc-effect-v1:synthetic-001",
        prediction="A",
        site_root=site,
        data_base_url=base_url,
    )

    assert manifest == validate_version(output, version_name="main")
    config = json.loads((site / "data/config.json").read_text(encoding="utf-8"))
    assert config["data_base_url"] == base_url
    runs = json.loads(
        (output / "versions/main/runs.json").read_text(encoding="utf-8")
    )
    assert runs["runs"][0]["coverage"]["complete"] is True
    answer_path = next(
        (output / "versions/main/answers/browser-qa").glob("*.json.gz")
    )
    answer = json.loads(gzip.decompress(answer_path.read_bytes()))
    assert answer["scoring"] == {
        "correct": False,
        "metric": "exact_match",
        "parse_error": None,
        "parsed_answer": "A",
        "value": 0,
    }
