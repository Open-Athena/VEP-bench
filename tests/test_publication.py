import gzip
import io
import json
from copy import deepcopy
from pathlib import Path

import pytest
import zstandard

import vepbench.publication as publication_module
from vepbench.builder import BuildError, canonical_json
from vepbench.publication import (
    build_version,
    promote_version,
    validate_version,
    validate_version_name,
)

ROOT = Path(__file__).resolve().parents[1]
QUESTIONS = ROOT / "tests/fixtures/synthetic-questions.jsonl"
RESULTS = ROOT / "tests/fixtures/results"
RESULT_SCHEMA = ROOT / "schemas/result.schema.json"
SCHEMAS = ROOT / "schemas"


def build_synthetic(output: Path, version_name: str = "candidate") -> dict:
    return build_version(
        questions_path=QUESTIONS,
        results_dir=RESULTS,
        result_schema_path=RESULT_SCHEMA,
        schemas_dir=SCHEMAS,
        output=output,
        version_name=version_name,
    )


def test_publication_is_deterministic_and_separates_browser_answers(tmp_path: Path) -> None:
    first = tmp_path / "first"
    second = tmp_path / "second"
    manifest = build_synthetic(first)
    build_synthetic(second)

    first_files = {
        path.relative_to(first): path.read_bytes()
        for path in first.rglob("*")
        if path.is_file()
    }
    second_files = {
        path.relative_to(second): path.read_bytes()
        for path in second.rglob("*")
        if path.is_file()
    }
    assert first_files == second_files
    assert manifest["version_name"] == "candidate"

    version = first / "versions/candidate"
    answer_path = next((version / "answers/synthetic-demo").glob("*.json.gz"))
    answer = json.loads(gzip.decompress(answer_path.read_bytes()))
    assert answer["response"]["content"].endswith("FINAL: B")
    assert "raw" not in answer["response"]
    assert "question" not in answer
    assert "model" not in answer

    raw_path = version / "raw/synthetic-demo.jsonl.zst"
    with zstandard.ZstdDecompressor().stream_reader(raw_path.open("rb")) as reader:
        raw_payload = io.BufferedReader(reader).read()
    envelope = json.loads(raw_payload)
    assert envelope["response"]["raw"]["id"] == "synthetic-generation"
    assert "question" not in envelope
    assert "scoring" not in envelope
    assert set(envelope["request"]) == {"body_sha256"}

    questions = zstandard.ZstdDecompressor().decompress(
        (version / "questions.jsonl.zst").read_bytes()
    )
    assert questions == QUESTIONS.read_bytes()


def test_publication_streams_result_input_and_raw_validation(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    original_read_jsonl = publication_module.read_jsonl
    original_verify_compressed = publication_module._verify_compressed

    def guarded_read_jsonl(path: str | Path) -> list[dict]:
        if Path(path).parent.name == "results":
            raise AssertionError("result JSONL must not be materialized")
        return original_read_jsonl(path)

    def guarded_verify_compressed(root: Path, descriptor: dict, compression: str) -> bytes:
        if "/raw/" in descriptor["path"]:
            raise AssertionError("raw archives must use the streaming validator")
        return original_verify_compressed(root, descriptor, compression)

    monkeypatch.setattr(publication_module, "read_jsonl", guarded_read_jsonl)
    monkeypatch.setattr(
        publication_module, "_verify_compressed", guarded_verify_compressed
    )

    build_synthetic(tmp_path / "publication")


def test_validate_version_rejects_tampered_answer(tmp_path: Path) -> None:
    output = tmp_path / "publication"
    build_synthetic(output)
    answer_path = next(
        (output / "versions/candidate/answers/synthetic-demo").glob("*.json.gz")
    )
    answer = json.loads(gzip.decompress(answer_path.read_bytes()))
    answer["response"]["content"] = "tampered\nFINAL: A"
    answer_path.write_bytes(gzip.compress(f"{canonical_json(answer)}\n".encode(), mtime=0))

    with pytest.raises(BuildError, match="artifact digest or size mismatch"):
        validate_version(output, version_name="candidate")


def test_named_version_promotes_to_validated_main(tmp_path: Path) -> None:
    candidate = tmp_path / "candidate"
    main = tmp_path / "main"
    build_synthetic(candidate)

    manifest = promote_version(
        source_root=candidate,
        source_version="candidate",
        output=main,
    )

    assert manifest["version_name"] == "main"
    assert (main / "versions/main/manifest.json").is_file()
    assert not (main / "versions/candidate").exists()
    assert (
        main / "versions/main/questions.jsonl.zst"
    ).read_bytes() == (
        candidate / "versions/candidate/questions.jsonl.zst"
    ).read_bytes()


def test_publication_rejects_result_from_another_question_set(tmp_path: Path) -> None:
    results = tmp_path / "results"
    results.mkdir()
    record = json.loads((RESULTS / "synthetic-demo.jsonl").read_text())
    record = deepcopy(record)
    record["question_set_sha256"] = "0" * 64
    (results / "wrong.jsonl").write_text(
        f"{canonical_json(record)}\n", encoding="utf-8", newline="\n"
    )

    with pytest.raises(BuildError, match="does not match the published question set"):
        build_version(
            questions_path=QUESTIONS,
            results_dir=results,
            result_schema_path=RESULT_SCHEMA,
            schemas_dir=SCHEMAS,
            output=tmp_path / "publication",
            version_name="candidate",
        )


def test_main_rejects_incomplete_run_but_experiment_accepts_it(tmp_path: Path) -> None:
    results = tmp_path / "results"
    results.mkdir()
    record = deepcopy(json.loads((RESULTS / "synthetic-demo.jsonl").read_text()))
    record["response"].update(
        {
            "status": "api_error",
            "content": None,
            "reasoning": None,
            "finish_reason": None,
            "raw": None,
        }
    )
    record["scoring"].update(
        {
            "parsed_answer": None,
            "value": None,
            "correct": None,
            "parse_error": None,
        }
    )
    record["error"] = {
        "type": "provider_error",
        "message": "synthetic failure",
        "status_code": 503,
    }
    (results / "incomplete.jsonl").write_text(
        f"{canonical_json(record)}\n", encoding="utf-8", newline="\n"
    )

    build_version(
        questions_path=QUESTIONS,
        results_dir=results,
        result_schema_path=RESULT_SCHEMA,
        schemas_dir=SCHEMAS,
        output=tmp_path / "experiment",
        version_name="experiment",
    )
    with pytest.raises(BuildError, match="main may contain only complete runs"):
        promote_version(
            source_root=tmp_path / "experiment",
            source_version="experiment",
            output=tmp_path / "main",
        )


def test_main_rejects_version_without_runs(tmp_path: Path) -> None:
    results = tmp_path / "results"
    results.mkdir()
    experiment = tmp_path / "experiment"
    build_version(
        questions_path=QUESTIONS,
        results_dir=results,
        result_schema_path=RESULT_SCHEMA,
        schemas_dir=SCHEMAS,
        output=experiment,
        version_name="experiment",
    )

    with pytest.raises(BuildError, match="at least one complete run"):
        promote_version(
            source_root=experiment,
            source_version="experiment",
            output=tmp_path / "main",
        )


def test_completed_response_requires_raw_provider_payload(tmp_path: Path) -> None:
    results = tmp_path / "results"
    results.mkdir()
    record = deepcopy(json.loads((RESULTS / "synthetic-demo.jsonl").read_text()))
    record["response"]["raw"] = None
    (results / "missing-raw.jsonl").write_text(
        f"{canonical_json(record)}\n", encoding="utf-8", newline="\n"
    )

    with pytest.raises(BuildError, match="raw"):
        build_version(
            questions_path=QUESTIONS,
            results_dir=results,
            result_schema_path=RESULT_SCHEMA,
            schemas_dir=SCHEMAS,
            output=tmp_path / "publication",
            version_name="candidate",
        )


@pytest.mark.parametrize("name", ["../main", "Prompt-Test", "a/b", "", "x" * 64])
def test_version_names_are_restricted(name: str) -> None:
    with pytest.raises(BuildError, match="lowercase URL-safe slug"):
        validate_version_name(name)
