"""Fetch and verify published benchmark question sets."""

from __future__ import annotations

import hashlib
import json
import os
import re
import tempfile
import urllib.error
import urllib.request
from compression import zstd
from dataclasses import dataclass
from pathlib import Path
from typing import Any

from jsonschema import Draft202012Validator

from ..artifacts import canonical_json, sha256_file
from ..errors import BuildError
from ..resources import QUESTION_SCHEMA
from .validation import validate_question

PUBLIC_BUCKET_RESOLVE_URL = "https://huggingface.co/buckets/open-athena/VEP-bench/resolve"
VERSION_NAME = re.compile(r"^[a-z0-9](?:[a-z0-9-]{0,62})?$")
SHA256 = re.compile(r"^[0-9a-f]{64}$")
DOWNLOAD_CHUNK_BYTES = 64 * 1024
MAX_MANIFEST_BYTES = 1024 * 1024
MAX_QUESTION_ARCHIVE_BYTES = 128 * 1024 * 1024
MAX_QUESTION_CONTENT_BYTES = 128 * 1024 * 1024
MAX_ZSTD_WINDOW_LOG = 26


@dataclass(frozen=True)
class FetchedQuestions:
    """A locally cached, digest-pinned published question set."""

    output: Path
    version: str
    records: int
    content_sha256: str
    from_cache: bool


def fetch_questions(
    *,
    version: str = "main",
    output: str | Path | None = None,
    cache_dir: str | Path = ".vepbench/questions",
    base_url: str = PUBLIC_BUCKET_RESOLVE_URL,
) -> FetchedQuestions:
    """Download, verify, decompress, and cache a published question archive."""

    if not VERSION_NAME.fullmatch(version):
        raise BuildError("question version must be a lowercase URL-safe slug")
    resolve_url = base_url.rstrip("/")
    manifest_url = f"{resolve_url}/versions/{version}/manifest.json"
    manifest_payload = _download_bytes(manifest_url, max_bytes=MAX_MANIFEST_BYTES)
    try:
        manifest = json.loads(manifest_payload)
    except json.JSONDecodeError as exc:
        raise BuildError(f"published question manifest is invalid JSON: {exc.msg}") from exc
    descriptor = _question_descriptor(manifest, version=version)

    content_digest = descriptor["content_sha256"]
    output_path = (
        Path(output)
        if output is not None
        else Path(cache_dir) / f"{version}-{content_digest[:12]}.jsonl"
    )
    if output_path.is_file() and _matches_file(
        output_path,
        expected_size=descriptor["content_bytes"],
        expected_digest=content_digest,
    ):
        _validate_question_file(output_path, expected_records=descriptor["records"])
        return FetchedQuestions(
            output=output_path,
            version=version,
            records=descriptor["records"],
            content_sha256=content_digest,
            from_cache=True,
        )
    if output_path.exists():
        raise BuildError(f"refusing to overwrite non-matching question cache {output_path}")

    artifact_url = f"{resolve_url}/{descriptor['path']}"
    output_path.parent.mkdir(parents=True, exist_ok=True)
    with tempfile.TemporaryDirectory(
        prefix=f".{output_path.name}.", dir=output_path.parent
    ) as temporary_dir:
        temporary_root = Path(temporary_dir)
        archive_path = temporary_root / "questions.jsonl.zst"
        content_path = temporary_root / "questions.jsonl"
        _download_file(
            artifact_url,
            archive_path,
            expected_size=descriptor["artifact_bytes"],
            expected_digest=descriptor["artifact_sha256"],
        )
        _decompress_file(
            archive_path,
            content_path,
            expected_size=descriptor["content_bytes"],
            expected_digest=content_digest,
        )
        _validate_question_file(content_path, expected_records=descriptor["records"])
        os.replace(content_path, output_path)
    return FetchedQuestions(
        output=output_path,
        version=version,
        records=descriptor["records"],
        content_sha256=content_digest,
        from_cache=False,
    )


def _open_url(url: str) -> Any:
    request = urllib.request.Request(url, headers={"User-Agent": "VEP-bench/0.1"})
    try:
        return urllib.request.urlopen(request, timeout=60)
    except (OSError, urllib.error.URLError) as exc:
        raise BuildError(f"could not download published questions from {url}: {exc}") from exc


def _download_bytes(url: str, *, max_bytes: int) -> bytes:
    payload = bytearray()
    try:
        with _open_url(url) as response:
            while chunk := response.read(min(DOWNLOAD_CHUNK_BYTES, max_bytes - len(payload) + 1)):
                payload.extend(chunk)
                if len(payload) > max_bytes:
                    raise BuildError(
                        f"published question download exceeds {max_bytes} bytes: {url}"
                    )
    except (OSError, urllib.error.URLError) as exc:
        raise BuildError(f"could not download published questions from {url}: {exc}") from exc
    return bytes(payload)


def _download_file(
    url: str,
    output: Path,
    *,
    expected_size: int,
    expected_digest: str,
) -> None:
    digest = hashlib.sha256()
    size = 0
    try:
        with _open_url(url) as response, output.open("xb") as destination:
            while chunk := response.read(min(DOWNLOAD_CHUNK_BYTES, expected_size - size + 1)):
                size += len(chunk)
                if size > expected_size:
                    raise BuildError("published question archive does not match its manifest")
                digest.update(chunk)
                destination.write(chunk)
    except (OSError, urllib.error.URLError) as exc:
        raise BuildError(f"could not download published questions from {url}: {exc}") from exc
    if size != expected_size or digest.hexdigest() != expected_digest:
        raise BuildError("published question archive does not match its manifest")


def _decompress_file(
    archive: Path,
    output: Path,
    *,
    expected_size: int,
    expected_digest: str,
) -> None:
    digest = hashlib.sha256()
    size = 0
    try:
        with (
            zstd.open(
                archive,
                "rb",
                options={zstd.DecompressionParameter.window_log_max: MAX_ZSTD_WINDOW_LOG},
            ) as source,
            output.open("xb") as destination,
        ):
            while chunk := source.read(min(DOWNLOAD_CHUNK_BYTES, expected_size - size + 1)):
                size += len(chunk)
                if size > expected_size:
                    raise BuildError("decompressed questions do not match the published manifest")
                digest.update(chunk)
                destination.write(chunk)
            destination.flush()
            os.fsync(destination.fileno())
    except zstd.ZstdError as exc:
        raise BuildError("published question archive is not valid zstd data") from exc
    if size != expected_size or digest.hexdigest() != expected_digest:
        raise BuildError("decompressed questions do not match the published manifest")


def _question_descriptor(manifest: Any, *, version: str) -> dict[str, Any]:
    if not isinstance(manifest, dict) or manifest.get("version_name") != version:
        raise BuildError("published question manifest has the wrong version")
    artifacts = manifest.get("artifacts")
    descriptor = artifacts.get("questions") if isinstance(artifacts, dict) else None
    if not isinstance(descriptor, dict):
        raise BuildError("published question manifest is missing its question artifact")
    required = {
        "path",
        "records",
        "content_sha256",
        "content_bytes",
        "artifact_sha256",
        "artifact_bytes",
    }
    if set(descriptor) != required:
        raise BuildError("published question descriptor has an invalid shape")
    if descriptor["path"] != f"versions/{version}/questions.jsonl.zst":
        raise BuildError("published question descriptor has an unexpected path")
    for field in ("records", "content_bytes", "artifact_bytes"):
        if (
            isinstance(descriptor[field], bool)
            or not isinstance(descriptor[field], int)
            or descriptor[field] < 1
        ):
            raise BuildError(f"published question descriptor has invalid {field}")
    if descriptor["artifact_bytes"] > MAX_QUESTION_ARCHIVE_BYTES:
        raise BuildError("published question archive exceeds the configured size limit")
    if descriptor["content_bytes"] > MAX_QUESTION_CONTENT_BYTES:
        raise BuildError("published question content exceeds the configured size limit")
    for field in ("content_sha256", "artifact_sha256"):
        if not isinstance(descriptor[field], str) or not SHA256.fullmatch(descriptor[field]):
            raise BuildError(f"published question descriptor has invalid {field}")
    return descriptor


def _validate_question_file(path: Path, *, expected_records: int) -> None:
    schema = json.loads(QUESTION_SCHEMA.read_text(encoding="utf-8"))
    validator = Draft202012Validator(schema)
    records = 0
    previous_question_id: str | None = None
    with path.open("rb") as source:
        for line_number, raw_line in enumerate(source, start=1):
            try:
                question = json.loads(raw_line)
            except (json.JSONDecodeError, UnicodeDecodeError) as exc:
                raise BuildError(f"{path}:{line_number}: invalid JSON") from exc
            if not isinstance(question, dict):
                raise BuildError(f"{path}:{line_number}: each record must be an object")
            validate_question(question, validator)
            try:
                canonical_line = f"{canonical_json(question)}\n".encode()
            except (TypeError, ValueError) as exc:
                raise BuildError(f"{path}: published questions must use canonical JSONL") from exc
            if raw_line != canonical_line:
                raise BuildError(f"{path}: published questions must use canonical JSONL")
            question_id = question["question_id"]
            if previous_question_id is not None and question_id <= previous_question_id:
                raise BuildError(f"{path}: published question IDs must be unique and sorted")
            previous_question_id = question_id
            records += 1
    if records != expected_records:
        raise BuildError(
            f"{path}: expected {expected_records} published questions, found {records}"
        )


def _matches_file(path: Path, *, expected_size: int, expected_digest: str) -> bool:
    return path.stat().st_size == expected_size and sha256_file(path) == expected_digest
