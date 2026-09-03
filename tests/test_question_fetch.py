import hashlib
import json
from compression import zstd
from pathlib import Path

import pytest

from vepbench.errors import BuildError
from vepbench.questions.fetch import fetch_questions

ROOT = Path(__file__).resolve().parents[1]
QUESTIONS = ROOT / "tests/fixtures/synthetic-questions.jsonl"


class Response:
    def __init__(self, payload: bytes):
        self.payload = payload

    def __enter__(self) -> Response:
        return self

    def __exit__(self, *args: object) -> None:
        return None

    def read(self) -> bytes:
        return self.payload


def published_payloads() -> tuple[bytes, bytes]:
    content = QUESTIONS.read_bytes()
    archive = zstd.compress(content)
    manifest = {
        "version_name": "main",
        "artifacts": {
            "questions": {
                "path": "versions/main/questions.jsonl.zst",
                "records": 1,
                "content_sha256": hashlib.sha256(content).hexdigest(),
                "content_bytes": len(content),
                "artifact_sha256": hashlib.sha256(archive).hexdigest(),
                "artifact_bytes": len(archive),
            }
        },
    }
    return json.dumps(manifest).encode(), archive


def test_fetch_questions_verifies_and_reuses_digest_cache(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    manifest, archive = published_payloads()
    requests: list[str] = []

    def urlopen(request: object, timeout: int) -> Response:
        del timeout
        url = request.full_url  # type: ignore[attr-defined]
        requests.append(url)
        return Response(manifest if url.endswith("manifest.json") else archive)

    monkeypatch.setattr("vepbench.questions.fetch.urllib.request.urlopen", urlopen)
    first = fetch_questions(cache_dir=tmp_path, base_url="https://example.test")
    second = fetch_questions(cache_dir=tmp_path, base_url="https://example.test")

    assert first.output.read_bytes() == QUESTIONS.read_bytes()
    assert not first.from_cache
    assert second.from_cache
    assert requests == [
        "https://example.test/versions/main/manifest.json",
        "https://example.test/versions/main/questions.jsonl.zst",
        "https://example.test/versions/main/manifest.json",
    ]


def test_fetch_questions_rejects_archive_digest_mismatch(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    manifest, _ = published_payloads()

    def urlopen(request: object, timeout: int) -> Response:
        del timeout
        url = request.full_url  # type: ignore[attr-defined]
        return Response(manifest if url.endswith("manifest.json") else b"corrupt")

    monkeypatch.setattr("vepbench.questions.fetch.urllib.request.urlopen", urlopen)

    with pytest.raises(BuildError, match="does not match its manifest"):
        fetch_questions(cache_dir=tmp_path, base_url="https://example.test")
