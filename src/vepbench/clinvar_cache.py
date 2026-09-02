"""Immutable processed-data caches for ClinVar preparation."""

from __future__ import annotations

import hashlib
import json
from collections.abc import Mapping, Sequence
from pathlib import Path, PurePosixPath
from typing import Any

import polars as pl
from huggingface_hub import HfApi

from .builder import canonical_json, sha256_file
from .clinvar import (
    ClinVarCandidate,
    ClinVarPreparationError,
    ParsedCohort,
    ReferenceValidation,
    VepCandidate,
    VepJoinResult,
    WindowCandidate,
)

CACHE_SCHEMA_VERSION = "1"
CACHE_ROOT = "data_prep/clinvar"
CACHE_FILENAMES = (
    "clinvar-eligible.parquet",
    "vep-matched.parquet",
    "reference-valid.parquet",
)

_ELIGIBLE_SCHEMA = {
    "accession": pl.String,
    "version": pl.Int64,
    "variation_id": pl.Int64,
    "date_created": pl.String,
    "label": pl.String,
    "review_status": pl.String,
    "review_stars": pl.Int64,
    "chrom": pl.String,
    "pos": pl.Int64,
    "ref": pl.String,
    "alt": pl.String,
    "genes_json": pl.String,
    "transcripts_json": pl.String,
    "conditions_json": pl.String,
}
_MATCHED_SCHEMA = {
    "chrom": pl.String,
    "pos": pl.Int64,
    "ref": pl.String,
    "alt": pl.String,
    "consequence": pl.String,
}
_VALID_SCHEMA = {
    "chrom": pl.String,
    "pos": pl.Int64,
    "ref": pl.String,
    "alt": pl.String,
    "sequence": pl.String,
}


def processed_cache_key(configuration: Mapping[str, Any]) -> str:
    """Return the content-addressed key for pre-sampling inputs and algorithms."""

    payload = canonical_json(dict(configuration)).encode()
    return hashlib.sha256(payload).hexdigest()


def processed_cache_prefix(release_month: str, cache_key: str) -> str:
    """Return the isolated HF Bucket prefix for one immutable cache."""

    if not _safe_segment(release_month):
        raise ClinVarPreparationError(f"invalid cache release month {release_month!r}")
    if len(cache_key) != 64 or any(character not in "0123456789abcdef" for character in cache_key):
        raise ClinVarPreparationError("cache key must be a lowercase SHA-256 digest")
    return f"{CACHE_ROOT}/v{CACHE_SCHEMA_VERSION}/{release_month}/{cache_key}"


def implementation_digest(
    paths: Sequence[str | Path],
    *,
    root: str | Path,
) -> str:
    """Hash the named preparation files, including their paths and contents."""

    root_path = Path(root).resolve()
    resolved: list[tuple[Path, Path]] = []
    for path in paths:
        item = Path(path).resolve()
        if not item.is_file():
            raise ClinVarPreparationError(f"implementation file does not exist: {item}")
        try:
            relative = item.relative_to(root_path)
        except ValueError as exc:
            raise ClinVarPreparationError(
                f"implementation file is outside the digest root: {item}"
            ) from exc
        resolved.append((item, relative))
    digest = hashlib.sha256()
    for item, relative in sorted(resolved, key=lambda pair: pair[1].as_posix()):
        digest.update(relative.as_posix().encode())
        digest.update(b"\0")
        with item.open("rb") as stream:
            while chunk := stream.read(1024 * 1024):
                digest.update(chunk)
        digest.update(b"\0")
    return digest.hexdigest()


def write_processed_cache(
    cache_dir: str | Path,
    *,
    configuration: Mapping[str, Any],
    parsed: ParsedCohort,
    joined: VepJoinResult,
    reference_validation: ReferenceValidation,
) -> dict[str, Any]:
    """Write the complete processed pre-sampling state as compact Parquet."""

    destination = Path(cache_dir)
    destination.mkdir(parents=True, exist_ok=False)
    key = processed_cache_key(configuration)

    eligible_rows = [_candidate_row(candidate) for candidate in parsed.candidates]
    matched_rows = [
        {
            **_key_row(candidate.key),
            "consequence": candidate.consequence,
        }
        for candidate in joined.matched
    ]
    valid_rows = [
        {
            **_key_row(candidate.joined.key),
            "sequence": candidate.sequence,
        }
        for candidate in reference_validation.valid
    ]
    tables = {
        CACHE_FILENAMES[0]: pl.DataFrame(eligible_rows, schema=_ELIGIBLE_SCHEMA),
        CACHE_FILENAMES[1]: pl.DataFrame(matched_rows, schema=_MATCHED_SCHEMA),
        CACHE_FILENAMES[2]: pl.DataFrame(valid_rows, schema=_VALID_SCHEMA),
    }
    for filename, frame in tables.items():
        frame.write_parquet(destination / filename, compression="zstd", statistics=True)

    files = {
        filename: {
            "records": tables[filename].height,
            "bytes": (destination / filename).stat().st_size,
            "sha256": sha256_file(destination / filename),
        }
        for filename in CACHE_FILENAMES
    }
    manifest = {
        "schema_version": CACHE_SCHEMA_VERSION,
        "kind": "vepbench_clinvar_processed_cache",
        "cache_key": key,
        "configuration": dict(configuration),
        "parsed": {
            "filter_stages": list(parsed.filter_stages),
            "duplicate_allele_keys": parsed.duplicate_allele_keys,
            "duplicate_records_rejected": parsed.duplicate_records_rejected,
        },
        "files": files,
    }
    (destination / "manifest.json").write_text(
        f"{canonical_json(manifest)}\n",
        encoding="utf-8",
        newline="\n",
    )
    load_processed_cache(destination, expected_key=key)
    return manifest


def load_processed_cache(
    cache_dir: str | Path,
    *,
    expected_key: str | None = None,
) -> tuple[ParsedCohort, VepJoinResult, ReferenceValidation, dict[str, Any]]:
    """Validate and reconstruct a processed pre-sampling cache."""

    source = Path(cache_dir)
    manifest_path = source / "manifest.json"
    try:
        manifest = json.loads(manifest_path.read_text(encoding="utf-8"))
    except (OSError, json.JSONDecodeError) as exc:
        raise ClinVarPreparationError(
            f"could not read cache manifest {manifest_path}: {exc}"
        ) from exc
    if manifest.get("schema_version") != CACHE_SCHEMA_VERSION:
        raise ClinVarPreparationError("processed cache schema version does not match")
    if manifest.get("kind") != "vepbench_clinvar_processed_cache":
        raise ClinVarPreparationError("processed cache kind does not match")
    configuration = manifest.get("configuration")
    if not isinstance(configuration, dict):
        raise ClinVarPreparationError("processed cache configuration must be an object")
    actual_key = processed_cache_key(configuration)
    if manifest.get("cache_key") != actual_key:
        raise ClinVarPreparationError("processed cache key does not match its configuration")
    if expected_key is not None and actual_key != expected_key:
        raise ClinVarPreparationError("processed cache key does not match the requested cache")

    files = manifest.get("files")
    if not isinstance(files, dict) or set(files) != set(CACHE_FILENAMES):
        raise ClinVarPreparationError("processed cache manifest has an unexpected file set")
    for filename in CACHE_FILENAMES:
        metadata = files[filename]
        path = source / filename
        if not isinstance(metadata, dict) or not path.is_file():
            raise ClinVarPreparationError(f"processed cache file is missing: {filename}")
        if path.stat().st_size != metadata.get("bytes"):
            raise ClinVarPreparationError(f"processed cache size mismatch: {filename}")
        if sha256_file(path) != metadata.get("sha256"):
            raise ClinVarPreparationError(f"processed cache digest mismatch: {filename}")

    eligible_frame = _read_cache_frame(
        source / CACHE_FILENAMES[0], _ELIGIBLE_SCHEMA, files[CACHE_FILENAMES[0]]
    )
    matched_frame = _read_cache_frame(
        source / CACHE_FILENAMES[1], _MATCHED_SCHEMA, files[CACHE_FILENAMES[1]]
    )
    valid_frame = _read_cache_frame(
        source / CACHE_FILENAMES[2], _VALID_SCHEMA, files[CACHE_FILENAMES[2]]
    )

    candidates = tuple(_candidate_from_row(row) for row in eligible_frame.iter_rows(named=True))
    candidate_by_key = _unique_by_key(candidates, "eligible ClinVar")
    matched_by_key: dict[tuple[str, int, str, str], VepCandidate] = {}
    for row in matched_frame.iter_rows(named=True):
        key = _row_key(row)
        if key in matched_by_key:
            raise ClinVarPreparationError(f"processed cache repeats VEP key {key}")
        if key not in candidate_by_key:
            raise ClinVarPreparationError(f"processed cache VEP key is not eligible: {key}")
        consequence = row["consequence"]
        if not isinstance(consequence, str) or not consequence:
            raise ClinVarPreparationError(f"processed cache has an empty consequence for {key}")
        matched_by_key[key] = VepCandidate(candidate_by_key[key], consequence)

    valid_by_key: dict[tuple[str, int, str, str], WindowCandidate] = {}
    for row in valid_frame.iter_rows(named=True):
        key = _row_key(row)
        if key in valid_by_key:
            raise ClinVarPreparationError(f"processed cache repeats reference key {key}")
        if key not in matched_by_key:
            raise ClinVarPreparationError(
                f"processed cache reference key is not VEP-matched: {key}"
            )
        sequence = row["sequence"]
        if not isinstance(sequence, str) or not sequence:
            raise ClinVarPreparationError(f"processed cache has an empty sequence for {key}")
        valid_by_key[key] = WindowCandidate(matched_by_key[key], sequence)

    parsed_metadata = manifest.get("parsed")
    if not isinstance(parsed_metadata, dict):
        raise ClinVarPreparationError("processed cache parsed metadata must be an object")
    filter_stages = parsed_metadata.get("filter_stages")
    if not isinstance(filter_stages, list) or not all(
        isinstance(item, dict) for item in filter_stages
    ):
        raise ClinVarPreparationError("processed cache filter stages must be an array of objects")
    parsed = ParsedCohort(
        candidates=tuple(sorted(candidates, key=lambda item: (item.key, item.source_record_id))),
        filter_stages=tuple(filter_stages),
        duplicate_allele_keys=_nonnegative_int(
            parsed_metadata.get("duplicate_allele_keys"), "duplicate_allele_keys"
        ),
        duplicate_records_rejected=_nonnegative_int(
            parsed_metadata.get("duplicate_records_rejected"), "duplicate_records_rejected"
        ),
    )
    joined = VepJoinResult(
        matched=tuple(matched_by_key[key] for key in sorted(matched_by_key)),
        missing=tuple(
            candidate_by_key[key] for key in sorted(set(candidate_by_key) - set(matched_by_key))
        ),
    )
    reference_validation = ReferenceValidation(
        valid=tuple(valid_by_key[key] for key in sorted(valid_by_key)),
        invalid=tuple(
            matched_by_key[key] for key in sorted(set(matched_by_key) - set(valid_by_key))
        ),
    )
    return parsed, joined, reference_validation, manifest


def remote_cache_state(
    api: HfApi,
    *,
    bucket_id: str,
    prefix: str,
    token: str,
) -> str:
    """Return absent, complete, or incomplete for an immutable remote prefix."""

    paths = {
        item.path
        for item in api.list_bucket_tree(bucket_id, prefix=prefix, recursive=True, token=token)
        if getattr(item, "type", None) == "file"
    }
    if not paths:
        return "absent"
    marker = f"{prefix}/manifest.json"
    return "complete" if marker in paths else "incomplete"


def download_processed_cache(
    api: HfApi,
    *,
    bucket_id: str,
    prefix: str,
    destination: str | Path,
    token: str,
    expected_key: str,
) -> tuple[ParsedCohort, VepJoinResult, ReferenceValidation, dict[str, Any]]:
    """Download a complete cache and verify every file before use."""

    output = Path(destination)
    output.mkdir(parents=True, exist_ok=False)
    remote_files = [*CACHE_FILENAMES, "manifest.json"]
    api.download_bucket_files(
        bucket_id,
        files=[(f"{prefix}/{filename}", output / filename) for filename in remote_files],
        raise_on_missing_files=True,
        token=token,
    )
    return load_processed_cache(output, expected_key=expected_key)


def publish_processed_cache(
    api: HfApi,
    *,
    bucket_id: str,
    prefix: str,
    cache_dir: str | Path,
    token: str,
) -> None:
    """Publish immutable cache data first and its completion manifest last."""

    source = Path(cache_dir)
    load_processed_cache(source)
    state = remote_cache_state(api, bucket_id=bucket_id, prefix=prefix, token=token)
    if state != "absent":
        raise ClinVarPreparationError(
            f"refusing to overwrite {state} processed cache prefix {prefix}"
        )
    api.batch_bucket_files(
        bucket_id,
        add=[(source / filename, f"{prefix}/{filename}") for filename in CACHE_FILENAMES],
        token=token,
    )
    api.batch_bucket_files(
        bucket_id,
        add=[(source / "manifest.json", f"{prefix}/manifest.json")],
        token=token,
    )
    if remote_cache_state(api, bucket_id=bucket_id, prefix=prefix, token=token) != "complete":
        raise ClinVarPreparationError(f"processed cache marker was not installed at {prefix}")


def _candidate_row(candidate: ClinVarCandidate) -> dict[str, Any]:
    return {
        "accession": candidate.accession,
        "version": candidate.version,
        "variation_id": candidate.variation_id,
        "date_created": candidate.date_created,
        "label": candidate.label,
        "review_status": candidate.review_status,
        "review_stars": candidate.review_stars,
        **_key_row(candidate.key),
        "genes_json": canonical_json(list(candidate.genes)),
        "transcripts_json": canonical_json(list(candidate.transcripts)),
        "conditions_json": canonical_json([list(condition) for condition in candidate.conditions]),
    }


def _candidate_from_row(row: Mapping[str, Any]) -> ClinVarCandidate:
    try:
        genes = json.loads(row["genes_json"])
        transcripts = json.loads(row["transcripts_json"])
        conditions = json.loads(row["conditions_json"])
    except (KeyError, TypeError, json.JSONDecodeError) as exc:
        raise ClinVarPreparationError("processed cache contains invalid nested metadata") from exc
    if not isinstance(genes, list) or not all(isinstance(item, str) for item in genes):
        raise ClinVarPreparationError("processed cache genes must be an array of strings")
    if not isinstance(transcripts, list) or not all(isinstance(item, str) for item in transcripts):
        raise ClinVarPreparationError("processed cache transcripts must be an array of strings")
    if not isinstance(conditions, list) or not all(
        isinstance(item, list)
        and len(item) == 3
        and isinstance(item[0], str)
        and (item[1] is None or isinstance(item[1], str))
        and (item[2] is None or isinstance(item[2], str))
        for item in conditions
    ):
        raise ClinVarPreparationError("processed cache conditions have an invalid shape")
    return ClinVarCandidate(
        accession=str(row["accession"]),
        version=int(row["version"]),
        variation_id=int(row["variation_id"]),
        date_created=str(row["date_created"]),
        label=str(row["label"]),
        review_status=str(row["review_status"]),
        review_stars=int(row["review_stars"]),
        chrom=str(row["chrom"]),
        pos=int(row["pos"]),
        ref=str(row["ref"]),
        alt=str(row["alt"]),
        genes=tuple(genes),
        transcripts=tuple(transcripts),
        conditions=tuple((item[0], item[1], item[2]) for item in conditions),
    )


def _read_cache_frame(
    path: Path,
    schema: Mapping[str, Any],
    metadata: Mapping[str, Any],
) -> pl.DataFrame:
    try:
        frame = pl.read_parquet(path)
    except Exception as exc:
        raise ClinVarPreparationError(f"could not read processed cache file {path}: {exc}") from exc
    if frame.schema != schema:
        raise ClinVarPreparationError(f"processed cache schema mismatch: {path.name}")
    if frame.height != metadata.get("records"):
        raise ClinVarPreparationError(f"processed cache record count mismatch: {path.name}")
    return frame


def _unique_by_key(
    candidates: Sequence[ClinVarCandidate],
    label: str,
) -> dict[tuple[str, int, str, str], ClinVarCandidate]:
    result: dict[tuple[str, int, str, str], ClinVarCandidate] = {}
    for candidate in candidates:
        if candidate.key in result:
            raise ClinVarPreparationError(f"processed cache repeats {label} key {candidate.key}")
        result[candidate.key] = candidate
    return result


def _key_row(key: tuple[str, int, str, str]) -> dict[str, Any]:
    return dict(zip(("chrom", "pos", "ref", "alt"), key, strict=True))


def _row_key(row: Mapping[str, Any]) -> tuple[str, int, str, str]:
    return (str(row["chrom"]), int(row["pos"]), str(row["ref"]), str(row["alt"]))


def _nonnegative_int(value: Any, name: str) -> int:
    if not isinstance(value, int) or isinstance(value, bool) or value < 0:
        raise ClinVarPreparationError(f"processed cache {name} must be a non-negative integer")
    return value


def _safe_segment(value: str) -> bool:
    candidate = PurePosixPath(value)
    return (
        value != ""
        and not candidate.is_absolute()
        and len(candidate.parts) == 1
        and candidate.name not in {".", ".."}
        and "\\" not in value
        and "\x00" not in value
    )
