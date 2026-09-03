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

from ..artifacts import canonical_json, read_jsonl, sha256_file
from ..errors import BuildError
from ..resources import QUESTION_SCHEMA
from .validation import validate_question

PUBLIC_BUCKET_RESOLVE_URL = "https://huggingface.co/buckets/open-athena/VEP-bench/resolve"
VERSION_NAME = re.compile(r"^[a-z0-9](?:[a-z0-9-]{0,62})?$")
SHA256 = re.compile(r"^[0-9a-f]{64}$")


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
    manifest_payload = _download(manifest_url)
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

    artifact_path = descriptor["path"]
    archive = _download(f"{resolve_url}/{artifact_path}")
    if (
        len(archive) != descriptor["artifact_bytes"]
        or _sha256(archive) != descriptor["artifact_sha256"]
    ):
        raise BuildError("published question archive does not match its manifest")
    try:
        content = zstd.decompress(archive)
    except zstd.ZstdError as exc:
        raise BuildError("published question archive is not valid zstd data") from exc
    if len(content) != descriptor["content_bytes"] or _sha256(content) != content_digest:
        raise BuildError("decompressed questions do not match the published manifest")

    output_path.parent.mkdir(parents=True, exist_ok=True)
    temporary_path: Path | None = None
    try:
        with tempfile.NamedTemporaryFile(
            prefix=f".{output_path.name}.", dir=output_path.parent, delete=False
        ) as temporary:
            temporary.write(content)
            temporary.flush()
            os.fsync(temporary.fileno())
            temporary_path = Path(temporary.name)
        _validate_question_file(temporary_path, expected_records=descriptor["records"])
        os.replace(temporary_path, output_path)
    finally:
        if temporary_path is not None:
            temporary_path.unlink(missing_ok=True)
    return FetchedQuestions(
        output=output_path,
        version=version,
        records=descriptor["records"],
        content_sha256=content_digest,
        from_cache=False,
    )


def _download(url: str) -> bytes:
    request = urllib.request.Request(url, headers={"User-Agent": "VEP-bench/0.1"})
    try:
        with urllib.request.urlopen(request, timeout=60) as response:
            return response.read()
    except (OSError, urllib.error.URLError) as exc:
        raise BuildError(f"could not download published questions from {url}: {exc}") from exc


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
    for field in ("content_sha256", "artifact_sha256"):
        if not isinstance(descriptor[field], str) or not SHA256.fullmatch(descriptor[field]):
            raise BuildError(f"published question descriptor has invalid {field}")
    return descriptor


def _validate_question_file(path: Path, *, expected_records: int) -> None:
    records = read_jsonl(path)
    if len(records) != expected_records:
        raise BuildError(
            f"{path}: expected {expected_records} published questions, found {len(records)}"
        )
    schema = json.loads(QUESTION_SCHEMA.read_text(encoding="utf-8"))
    validator = Draft202012Validator(schema)
    for question in records:
        validate_question(question, validator)
    question_ids = [question["question_id"] for question in records]
    if question_ids != sorted(question_ids) or len(question_ids) != len(set(question_ids)):
        raise BuildError(f"{path}: published question IDs must be unique and sorted")
    expected_payload = "".join(f"{canonical_json(question)}\n" for question in records)
    if path.read_text(encoding="utf-8") != expected_payload:
        raise BuildError(f"{path}: published questions must use canonical JSONL")


def _matches_file(path: Path, *, expected_size: int, expected_digest: str) -> bool:
    return path.stat().st_size == expected_size and sha256_file(path) == expected_digest


def _sha256(payload: bytes) -> str:
    return hashlib.sha256(payload).hexdigest()
