import json
from pathlib import Path

import pytest

from vepbench.cli import main

ROOT = Path(__file__).resolve().parents[1]


def test_build_command_writes_questions_and_manifest(
    tmp_path: Path, capsys: pytest.CaptureFixture[str]
) -> None:
    output = tmp_path / "questions.jsonl"

    status = main(
        [
            "build",
            "--source",
            str(ROOT / "tests/fixtures/synthetic-source.jsonl"),
            "--template",
            str(ROOT / "tests/fixtures/synthetic-template.json"),
            "--schema",
            str(ROOT / "schemas/question.schema.json"),
            "--output",
            str(output),
        ]
    )

    assert status == 0
    assert "wrote 1 question(s)" in capsys.readouterr().out
    manifest = json.loads(
        (tmp_path / "questions.manifest.json").read_text(encoding="utf-8")
    )
    assert manifest["records"] == 1
    assert manifest["path"] == "questions.jsonl"
