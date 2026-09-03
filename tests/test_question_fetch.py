import hashlib
import json
from compression import zstd
from pathlib import Path

import pytest

from vepbench.errors import BuildError
from vepbench.questions.fetch import (
    MAX_MANIFEST_BYTES,
    MAX_QUESTION_ARCHIVE_BYTES,
    fetch_questions,
)

ROOT = Path(__file__).resolve().parents[1]
QUESTIONS = ROOT / "tests/fixtures/synthetic-questions.jsonl"


class Response:
    def __init__(self, payload: bytes):
        self.payload = payload
        self.offset = 0

    def __enter__(self) -> Response:
        return self

    def __exit__(self, *args: object) -> None:
        return None

    def read(self, size: int = -1) -> bytes:
        if size < 0:
            size = len(self.payload) - self.offset
        chunk = self.payload[self.offset : self.offset + size]
        self.offset += len(chunk)
        return chunk


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


def test_fetch_questions_rejects_oversized_manifest(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    def urlopen(request: object, timeout: int) -> Response:
        del request, timeout
        return Response(b" " * (MAX_MANIFEST_BYTES + 1))

    monkeypatch.setattr("vepbench.questions.fetch.urllib.request.urlopen", urlopen)

    with pytest.raises(BuildError, match="download exceeds"):
        fetch_questions(cache_dir=tmp_path, base_url="https://example.test")


def test_fetch_questions_rejects_noncanonical_crlf(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    content = QUESTIONS.read_bytes().replace(b"\n", b"\r\n")
    archive = zstd.compress(content)
    manifest = json.loads(published_payloads()[0])
    descriptor = manifest["artifacts"]["questions"]
    descriptor.update(
        content_sha256=hashlib.sha256(content).hexdigest(),
        content_bytes=len(content),
        artifact_sha256=hashlib.sha256(archive).hexdigest(),
        artifact_bytes=len(archive),
    )

    def urlopen(request: object, timeout: int) -> Response:
        del timeout
        url = request.full_url  # type: ignore[attr-defined]
        return Response(json.dumps(manifest).encode() if url.endswith("manifest.json") else archive)

    monkeypatch.setattr("vepbench.questions.fetch.urllib.request.urlopen", urlopen)

    with pytest.raises(BuildError, match="canonical JSONL"):
        fetch_questions(cache_dir=tmp_path, base_url="https://example.test")


def test_fetch_questions_rejects_oversized_descriptor_before_archive_download(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    manifest_payload, _ = published_payloads()
    manifest = json.loads(manifest_payload)
    manifest["artifacts"]["questions"]["artifact_bytes"] = MAX_QUESTION_ARCHIVE_BYTES + 1
    requests: list[str] = []

    def urlopen(request: object, timeout: int) -> Response:
        del timeout
        requests.append(request.full_url)  # type: ignore[attr-defined]
        return Response(json.dumps(manifest).encode())

    monkeypatch.setattr("vepbench.questions.fetch.urllib.request.urlopen", urlopen)

    with pytest.raises(BuildError, match="configured size limit"):
        fetch_questions(cache_dir=tmp_path, base_url="https://example.test")
    assert requests == ["https://example.test/versions/main/manifest.json"]


def test_fetch_questions_stops_decompression_at_declared_size(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    manifest_payload, archive = published_payloads()
    manifest = json.loads(manifest_payload)
    manifest["artifacts"]["questions"]["content_bytes"] = 1

    def urlopen(request: object, timeout: int) -> Response:
        del timeout
        url = request.full_url  # type: ignore[attr-defined]
        return Response(json.dumps(manifest).encode() if url.endswith("manifest.json") else archive)

    monkeypatch.setattr("vepbench.questions.fetch.urllib.request.urlopen", urlopen)

    with pytest.raises(BuildError, match="decompressed questions do not match"):
        fetch_questions(cache_dir=tmp_path, base_url="https://example.test")
