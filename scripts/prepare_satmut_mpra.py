"""Prepare the canonical CADD v1.7 satMutMPRA ranking task."""

from __future__ import annotations

import argparse
import gzip
import hashlib
import io
import json
import os
import tempfile
import urllib.parse
import urllib.request
from pathlib import Path
from typing import Any

from huggingface_hub import HfApi, get_hf_file_metadata, get_token

from vepbench.builder import canonical_json, is_finite_number, sha256_file
from vepbench.genome import Genome
from vepbench.satmut_mpra import (
    ELEMENT_SPECS,
    EXPECTED_FILTER_COUNTS,
    KNOWN_TARGET_SEQUENCE_MISMATCHES,
    SPEC_BY_LABEL,
    TARGET_SEQUENCE_MISMATCH_TREATMENT,
    ElementMetadata,
    PreparedElement,
    SatMutPreparationError,
    Variant,
    eligible_cache_rows,
    parse_cadd_vcf,
    parse_mavedb_metadata,
    validate_mavedb_crosswalk,
    validate_reference,
    write_prepared_dataset,
)

ROOT = Path(__file__).resolve().parents[1]
CADD_BASE_URL = "https://kircherlab.bihealth.org/download/CADD-development/v1.7/validation/regseq"
CADD_MD5_URL = f"{CADD_BASE_URL}/MD5SUMs"
MAVEDB_API_BASE = "https://api.mavedb.org/api/v1/score-sets"
REFERENCE_REVISION = "11b9433582981bb929af333bc6422f10a8fd71b4"
REFERENCE_URL = (
    "https://huggingface.co/datasets/marin-dna/human-genome/resolve/"
    f"{REFERENCE_REVISION}/Homo_sapiens.GRCh38.dna_sm.primary_assembly.fa"
)
OUTPUT = ROOT / "data/sources/satmut-mpra-cadd-v1.7.jsonl"
MANIFEST_OUTPUT = ROOT / "data/sources/satmut-mpra-cadd-v1.7.manifest.json"
PINNED_INPUTS_PATH = ROOT / "configs/sources/satmut-mpra-v1.json"
CACHE_BUCKET = "open-athena/VEP-bench"
CACHE_ROOT = "data_prep/satmut-mpra/v1"
LEGACY_PREPARATION_IMPLEMENTATION_SHA256 = (
    "08b759f9fce8b0826fac1b827d04cd5c201f9ef704745ab3f0f07c2fa435f3ff"
)
PREPARATION_IMPLEMENTATION_SHA256 = (
    "66cd049858dc9bc5d9bb516a0ae3c8bb293254c55b9ecc64dfbfd4dff6795afe"
)
EXPECTED_CADD_MD5 = {
    "SatMut.all.F9.vcf.gz": "fb70753825d20d5ee61f1e5b40d7389e",
    "SatMut.all.GP1BA.vcf.gz": "d7a9d0348c94880970ddebaedc4d51b5",
    "SatMut.all.HBB.vcf.gz": "2a58aaddbfbe279a23f8c1dd4cd496eb",
    "SatMut.all.HBG1.vcf.gz": "906150f39bff154a873cc0adf4783551",
    "SatMut.all.HNF4A.vcf.gz": "02fecf3021e8c20c44a111453bf0a777",
    "SatMut.all.IRF4.vcf.gz": "703fe90b319f20e923ff045e8e9c578a",
    "SatMut.all.IRF6.vcf.gz": "f2a14a386cabbe047169b9f8fad21312",
    "SatMut.all.LDLR.vcf.gz": "0e1133e28f070182080b440b9ad4a20c",
    "SatMut.all.MSMB.vcf.gz": "f041a4a532533986dc97ebad525465a4",
    "SatMut.all.MYCrs6983267.vcf.gz": "1ec96765f2f9ce58b53a8b18ffb4217c",
    "SatMut.all.PKLR.vcf.gz": "cfca28c9992ac6ef4b57853c0b737e2d",
    "SatMut.all.SORT1.vcf.gz": "9749f32730210516167a3d756f0984ad",
    "SatMut.all.TCF7L2.vcf.gz": "07d641f75dcfdedde2fa6ab0d5a34b57",
    "SatMut.all.TERT.vcf.gz": "5a0011f2c9800f50072649932a667117",
    "SatMut.all.ZFAND3.vcf.gz": "32b0be2745782293bb90f49d880945f8",
    "SatMut.all.ZRSh13.vcf.gz": "014b48c5fa39754001e34cc660988935",
}
CACHE_DATA_FILES = ("eligible-variants.jsonl.gz", "elements.json")
PINNED_INPUTS = json.loads(PINNED_INPUTS_PATH.read_text(encoding="utf-8"))
RETRIEVAL_DATE = PINNED_INPUTS["retrieval_date"]


def _download(url: str) -> bytes:
    request = urllib.request.Request(url, headers={"User-Agent": "VEP-bench/0.1"})
    with urllib.request.urlopen(request, timeout=900) as response:
        return response.read()


def _digest_record(url: str, payload: bytes) -> dict[str, Any]:
    return {
        "url": url,
        "retrieval_date": RETRIEVAL_DATE,
        "bytes": len(payload),
        "sha256": hashlib.sha256(payload).hexdigest(),
    }


def _verify_pinned_payload(label: str, payload: bytes, expected: dict[str, Any]) -> None:
    observed = {"bytes": len(payload), "sha256": hashlib.sha256(payload).hexdigest()}
    if observed != expected:
        raise RuntimeError(f"{label}: payload does not match the pinned size and SHA-256")


def _verify_reference_metadata(token: str | None, expected: dict[str, Any]) -> None:
    metadata = get_hf_file_metadata(
        REFERENCE_URL,
        token=token,
        timeout=30,
        user_agent="VEP-bench/0.1",
        retry_on_errors=True,
    )
    observed = {"bytes": metadata.size, "sha256": metadata.etag}
    if metadata.commit_hash != REFERENCE_REVISION or observed != expected:
        raise RuntimeError("pinned GRCh38 remote metadata does not match size and SHA-256")


def _mavedb_url(urn: str, suffix: str = "") -> str:
    encoded = urllib.parse.quote(urn, safe="")
    return f"{MAVEDB_API_BASE}/{encoded}{suffix}"


def _cache_key(configuration: dict[str, Any]) -> str:
    return hashlib.sha256(canonical_json(configuration).encode()).hexdigest()


def _cache_configuration(
    *, implementation_sha256: str = PREPARATION_IMPLEMENTATION_SHA256
) -> dict[str, Any]:
    cadd_pins = PINNED_INPUTS["cadd"]
    mavedb_pins = PINNED_INPUTS["mavedb"]
    reference_pin = PINNED_INPUTS["reference"]
    return {
        "schema_version": "1.0",
        "implementation_sha256": implementation_sha256,
        "cadd": {
            filename: {
                "url": f"{CADD_BASE_URL}/{filename}",
                **pin,
                "md5": EXPECTED_CADD_MD5[filename],
            }
            for filename, pin in cadd_pins.items()
        },
        "mavedb": {
            urn: {
                "metadata": {"url": _mavedb_url(urn), **pin["metadata"]},
                "scores": {"url": _mavedb_url(urn, "/scores"), **pin["scores"]},
            }
            for urn, pin in mavedb_pins.items()
        },
        "reference": {
            "dataset": "marin-dna/human-genome",
            "revision": REFERENCE_REVISION,
            "url": REFERENCE_URL,
            "assembly": "GRCh38",
            **reference_pin,
        },
        "eligibility": "FILTER == SIGN",
    }


def _write_cache(
    destination: Path,
    *,
    elements: list[PreparedElement],
    configuration: dict[str, Any],
) -> tuple[str, dict[str, Any]]:
    destination.mkdir(parents=True, exist_ok=False)
    rows = eligible_cache_rows(elements)
    content = "".join(f"{canonical_json(row)}\n" for row in rows).encode()
    compressed_buffer = io.BytesIO()
    with gzip.GzipFile(fileobj=compressed_buffer, mode="wb", mtime=0) as compressed:
        compressed.write(content)
    (destination / CACHE_DATA_FILES[0]).write_bytes(compressed_buffer.getvalue())
    element_document = {
        "schema_version": "1.0",
        "elements": [
            {
                "cadd_label": element.spec.cadd_label,
                "cadd_filename": element.spec.cadd_filename,
                "mavedb_score_set_urn": element.spec.mavedb_urn,
                "model_visible_name": element.spec.model_name,
                "source_study_element_label": element.spec.source_study_label,
                "element_class": element.spec.element_class,
                "cell_line": element.spec.cell_line,
                "experimental_context": element.spec.experimental_context,
                "chrom": element.metadata.chrom,
                "start": element.metadata.start,
                "end": element.metadata.end,
                "modification_date": element.metadata.modification_date,
                "reference_sequence": element.metadata.sequence,
                "mavedb_reference_sequence": element.metadata.mavedb_sequence,
                "reference_discrepancies": list(element.metadata.reference_discrepancies),
                "filter_counts": element.filter_counts,
                "reference_records_validated": element.reference_records_validated,
                "mavedb_records_validated": element.mavedb_records_validated,
            }
            for element in elements
        ],
    }
    (destination / CACHE_DATA_FILES[1]).write_text(
        f"{canonical_json(element_document)}\n", encoding="utf-8", newline="\n"
    )
    key = _cache_key(configuration)
    manifest = {
        "schema_version": "1.0",
        "kind": "vepbench_satmut_mpra_processed_cache",
        "cache_key": key,
        "configuration": configuration,
        "files": {
            filename: {
                "bytes": (destination / filename).stat().st_size,
                "sha256": sha256_file(destination / filename),
                "records": len(rows) if filename == CACHE_DATA_FILES[0] else len(elements),
            }
            for filename in CACHE_DATA_FILES
        },
    }
    (destination / "manifest.json").write_text(
        f"{canonical_json(manifest)}\n", encoding="utf-8", newline="\n"
    )
    _load_cache(
        destination,
        expected_key=key,
        expected_configuration=configuration,
    )
    return key, manifest


def _normalize_cached_reference_discrepancies(
    discrepancies: list[dict[str, Any]],
) -> tuple[dict[str, Any], ...]:
    normalized = []
    for cached_discrepancy in discrepancies:
        discrepancy = dict(cached_discrepancy)
        target_mismatch = (
            discrepancy.get("chrom"),
            discrepancy.get("pos"),
            discrepancy.get("mavedb_base"),
            discrepancy.get("grch38_base"),
        )
        if target_mismatch in KNOWN_TARGET_SEQUENCE_MISMATCHES:
            discrepancy["treatment"] = TARGET_SEQUENCE_MISMATCH_TREATMENT
        normalized.append(discrepancy)
    return tuple(normalized)


def _load_cache(
    destination: Path,
    *,
    expected_key: str,
    expected_configuration: dict[str, Any] | None = None,
) -> list[PreparedElement]:
    try:
        manifest = json.loads((destination / "manifest.json").read_text(encoding="utf-8"))
    except (OSError, json.JSONDecodeError) as exc:
        raise SatMutPreparationError("processed cache manifest is not valid JSON") from exc
    configuration = manifest.get("configuration")
    if expected_configuration is None:
        expected_configuration = _cache_configuration()
    if (
        manifest.get("schema_version") != "1.0"
        or manifest.get("kind") != "vepbench_satmut_mpra_processed_cache"
        or not isinstance(configuration, dict)
        or manifest.get("cache_key") != expected_key
        or _cache_key(configuration) != expected_key
        or configuration != expected_configuration
    ):
        raise SatMutPreparationError("processed cache identity or configuration mismatch")
    files = manifest.get("files")
    if not isinstance(files, dict) or set(files) != set(CACHE_DATA_FILES):
        raise SatMutPreparationError("processed cache manifest has an unexpected file set")
    for filename in CACHE_DATA_FILES:
        metadata = files[filename]
        path = destination / filename
        if not isinstance(metadata, dict) or not path.is_file():
            raise SatMutPreparationError(f"processed cache file is missing: {filename}")
        if path.stat().st_size != metadata.get("bytes") or sha256_file(path) != metadata.get(
            "sha256"
        ):
            raise SatMutPreparationError(f"processed cache digest or size mismatch: {filename}")

    try:
        row_payload = gzip.decompress((destination / CACHE_DATA_FILES[0]).read_bytes()).decode()
        rows = [json.loads(line) for line in row_payload.splitlines()]
        element_document = json.loads(
            (destination / CACHE_DATA_FILES[1]).read_text(encoding="utf-8")
        )
    except (OSError, UnicodeDecodeError, gzip.BadGzipFile, json.JSONDecodeError) as exc:
        raise SatMutPreparationError("processed cache data is malformed") from exc
    if (
        len(rows) != files[CACHE_DATA_FILES[0]].get("records")
        or not isinstance(element_document, dict)
        or element_document.get("schema_version") != "1.0"
        or not isinstance(element_document.get("elements"), list)
        or len(element_document["elements"]) != files[CACHE_DATA_FILES[1]].get("records")
    ):
        raise SatMutPreparationError("processed cache record counts do not match")

    variants_by_element: dict[str, list[Variant]] = {label: [] for label in SPEC_BY_LABEL}
    expected_row_fields = {
        "element",
        "chrom",
        "pos",
        "ref",
        "alt",
        "effect",
        "p_value",
        "barcode_count",
        "source_filter",
    }
    for row in rows:
        if not isinstance(row, dict) or set(row) != expected_row_fields:
            raise SatMutPreparationError("processed cache has an invalid eligible variant row")
        label = row["element"]
        if (
            label not in variants_by_element
            or not isinstance(row["chrom"], str)
            or isinstance(row["pos"], bool)
            or not isinstance(row["pos"], int)
            or row["pos"] < 1
            or not isinstance(row["ref"], str)
            or not isinstance(row["alt"], str)
            or not row["ref"]
            or not row["alt"]
            or any(base not in "ACGT" for base in row["ref"] + row["alt"])
            or row["ref"] == row["alt"]
            or not is_finite_number(row["effect"])
            or not is_finite_number(row["p_value"])
            or row["p_value"] < 0
            or isinstance(row["barcode_count"], bool)
            or not isinstance(row["barcode_count"], int)
            or row["barcode_count"] < 1
            or row["source_filter"] != "SIGN"
        ):
            raise SatMutPreparationError("processed cache has invalid eligible variant values")
        variants_by_element[label].append(
            Variant(
                row["chrom"],
                row["pos"],
                row["ref"],
                row["alt"],
                float(row["effect"]),
                float(row["p_value"]),
                row["barcode_count"],
                row["source_filter"],
            )
        )

    expected_element_fields = {
        "cadd_label",
        "cadd_filename",
        "mavedb_score_set_urn",
        "model_visible_name",
        "source_study_element_label",
        "element_class",
        "cell_line",
        "experimental_context",
        "chrom",
        "start",
        "end",
        "modification_date",
        "reference_sequence",
        "mavedb_reference_sequence",
        "reference_discrepancies",
        "filter_counts",
        "reference_records_validated",
        "mavedb_records_validated",
    }
    elements = []
    seen_labels = set()
    for record in element_document["elements"]:
        if not isinstance(record, dict) or set(record) != expected_element_fields:
            raise SatMutPreparationError("processed cache has invalid element metadata fields")
        label = record["cadd_label"]
        if label not in SPEC_BY_LABEL or label in seen_labels:
            raise SatMutPreparationError("processed cache has unknown or duplicate elements")
        seen_labels.add(label)
        spec = SPEC_BY_LABEL[label]
        expected_identity = (
            spec.cadd_filename,
            spec.mavedb_urn,
            spec.model_name,
            spec.source_study_label,
            spec.element_class,
            spec.cell_line,
        )
        observed_identity = tuple(
            record[field]
            for field in (
                "cadd_filename",
                "mavedb_score_set_urn",
                "model_visible_name",
                "source_study_element_label",
                "element_class",
                "cell_line",
            )
        )
        sequence = record["reference_sequence"]
        mavedb_sequence = record["mavedb_reference_sequence"]
        counts = record["filter_counts"]
        variants = variants_by_element[label]
        reference_records = record["reference_records_validated"]
        mavedb_records = record["mavedb_records_validated"]
        if (
            observed_identity != expected_identity
            or not isinstance(record["chrom"], str)
            or isinstance(record["start"], bool)
            or not isinstance(record["start"], int)
            or isinstance(record["end"], bool)
            or not isinstance(record["end"], int)
            or not isinstance(sequence, str)
            or not isinstance(mavedb_sequence, str)
            or record["end"] - record["start"] + 1 != len(sequence)
            or len(mavedb_sequence) != len(sequence)
            or any(base not in "ACGT" for base in sequence + mavedb_sequence)
            or not isinstance(record["modification_date"], str)
            or not isinstance(record["reference_discrepancies"], list)
            or not all(isinstance(item, dict) for item in record["reference_discrepancies"])
            or not isinstance(counts, dict)
            or set(counts) != set(EXPECTED_FILTER_COUNTS)
            or any(
                isinstance(value, bool) or not isinstance(value, int) or value < 0
                for value in counts.values()
            )
            or counts["SIGN"] != len(variants)
            or isinstance(reference_records, bool)
            or not isinstance(reference_records, int)
            or isinstance(mavedb_records, bool)
            or not isinstance(mavedb_records, int)
            or reference_records != sum(counts.values())
            or mavedb_records != reference_records
            or len({variant.key for variant in variants}) != len(variants)
        ):
            raise SatMutPreparationError("processed cache has invalid element metadata values")
        reference_discrepancies = _normalize_cached_reference_discrepancies(
            record["reference_discrepancies"]
        )
        elements.append(
            PreparedElement(
                spec,
                ElementMetadata(
                    sequence,
                    mavedb_sequence,
                    record["chrom"],
                    record["start"],
                    record["end"],
                    record["modification_date"],
                    reference_discrepancies,
                ),
                tuple(variants),
                counts,
                reference_records,
                mavedb_records,
            )
        )
    if seen_labels != set(SPEC_BY_LABEL):
        raise SatMutPreparationError("processed cache does not contain the canonical element set")
    filter_totals = {
        name: sum(element.filter_counts[name] for element in elements)
        for name in EXPECTED_FILTER_COUNTS
    }
    if filter_totals != EXPECTED_FILTER_COUNTS:
        raise SatMutPreparationError("processed cache FILTER totals do not match")
    return elements


def _remote_cache_state(api: HfApi, prefix: str, token: str | None) -> str:
    paths = {
        item.path
        for item in api.list_bucket_tree(CACHE_BUCKET, prefix=prefix, recursive=True, token=token)
        if getattr(item, "type", None) == "file"
    }
    if not paths:
        return "absent"
    return "complete" if f"{prefix}/manifest.json" in paths else "incomplete"


def _download_cache(
    api: HfApi,
    destination: Path,
    prefix: str,
    token: str | None,
    *,
    expected_key: str,
    expected_configuration: dict[str, Any] | None = None,
) -> list[PreparedElement]:
    destination.mkdir(parents=True, exist_ok=False)
    api.download_bucket_files(
        CACHE_BUCKET,
        files=[
            (f"{prefix}/{filename}", destination / filename)
            for filename in (*CACHE_DATA_FILES, "manifest.json")
        ],
        raise_on_missing_files=True,
        token=token,
    )
    return _load_cache(
        destination,
        expected_key=expected_key,
        expected_configuration=expected_configuration,
    )


def _publish_cache(api: HfApi, cache_dir: Path, prefix: str, token: str) -> None:
    state = _remote_cache_state(api, prefix, token)
    if state == "complete":
        print(f"processed cache already exists at {prefix}", flush=True)
        return
    if state == "incomplete":
        raise RuntimeError(f"refusing to overwrite incomplete cache prefix {prefix}")
    api.batch_bucket_files(
        CACHE_BUCKET,
        add=[(cache_dir / filename, f"{prefix}/{filename}") for filename in CACHE_DATA_FILES],
        token=token,
    )
    api.batch_bucket_files(
        CACHE_BUCKET,
        add=[(cache_dir / "manifest.json", f"{prefix}/manifest.json")],
        token=token,
    )
    if _remote_cache_state(api, prefix, token) != "complete":
        raise RuntimeError(f"processed cache marker was not installed at {prefix}")


def prepare(*, upload_cache: bool) -> tuple[int, str]:
    expected_cadd = PINNED_INPUTS.get("cadd")
    expected_mavedb = PINNED_INPUTS.get("mavedb")
    expected_cadd_files = {spec.cadd_filename for spec in ELEMENT_SPECS}
    expected_mavedb_urns = {spec.mavedb_urn for spec in ELEMENT_SPECS}
    if not isinstance(expected_cadd, dict) or set(expected_cadd) != expected_cadd_files:
        raise RuntimeError("pinned CADD inputs do not cover the canonical file set")
    if not isinstance(expected_mavedb, dict) or set(expected_mavedb) != expected_mavedb_urns:
        raise RuntimeError("pinned MaveDB inputs do not cover the canonical score sets")
    reference_pin = PINNED_INPUTS.get("reference")
    if not isinstance(reference_pin, dict):
        raise RuntimeError("pinned GRCh38 input metadata is missing")
    token = os.environ.get("HF_TOKEN") or get_token()
    if upload_cache and not token:
        raise RuntimeError("HF_TOKEN is required to upload the processed cache")
    _verify_reference_metadata(token, reference_pin)
    storage_options = {"headers": {"Authorization": f"Bearer {token}"}} if token else None
    cache_configuration = _cache_configuration()
    cache_key = _cache_key(cache_configuration)
    cache_prefix = f"{CACHE_ROOT}/{cache_key}"
    legacy_cache_configuration = _cache_configuration(
        implementation_sha256=LEGACY_PREPARATION_IMPLEMENTATION_SHA256
    )
    legacy_cache_key = _cache_key(legacy_cache_configuration)
    legacy_cache_prefix = f"{CACHE_ROOT}/{legacy_cache_key}"
    api = HfApi(token=token)
    cache_state = _remote_cache_state(api, cache_prefix, token)
    if cache_state == "incomplete":
        raise RuntimeError(f"refusing to use incomplete cache prefix {cache_prefix}")
    legacy_cache_state = (
        _remote_cache_state(api, legacy_cache_prefix, token)
        if cache_state == "absent"
        else "absent"
    )
    if legacy_cache_state == "incomplete":
        raise RuntimeError(f"refusing to use incomplete cache prefix {legacy_cache_prefix}")

    with tempfile.TemporaryDirectory(prefix="vepbench-satmut-mpra-") as temporary:
        cache_dir = Path(temporary) / "processed-cache"
        if cache_state == "complete":
            print(f"downloading processed cache {cache_prefix}", flush=True)
            elements = _download_cache(
                api,
                cache_dir,
                cache_prefix,
                token,
                expected_key=cache_key,
            )
        elif legacy_cache_state == "complete":
            legacy_cache_dir = Path(temporary) / "legacy-processed-cache"
            print(f"migrating processed cache {legacy_cache_prefix}", flush=True)
            elements = _download_cache(
                api,
                legacy_cache_dir,
                legacy_cache_prefix,
                token,
                expected_key=legacy_cache_key,
                expected_configuration=legacy_cache_configuration,
            )
            written_key, _ = _write_cache(
                cache_dir,
                elements=elements,
                configuration=cache_configuration,
            )
            if written_key != cache_key:
                raise RuntimeError("processed cache key changed while migrating")
            if upload_cache:
                assert token is not None
                _publish_cache(api, cache_dir, cache_prefix, token)
        else:
            md5_payload = _download(CADD_MD5_URL)
            md5_manifest_pin = PINNED_INPUTS.get("cadd_md5_manifest")
            if not isinstance(md5_manifest_pin, dict):
                raise RuntimeError("pinned CADD MD5 manifest metadata is missing")
            _verify_pinned_payload("CADD MD5 manifest", md5_payload, md5_manifest_pin)
            observed_md5 = {}
            for line in md5_payload.decode("utf-8").splitlines():
                digest, filename = line.split()
                observed_md5[filename] = digest
            if observed_md5 != EXPECTED_CADD_MD5:
                raise RuntimeError("CADD MD5 manifest does not match the canonical pinned file set")

            cadd_payloads = {}
            mavedb_payloads = {}
            for spec in ELEMENT_SPECS:
                cadd_url = f"{CADD_BASE_URL}/{spec.cadd_filename}"
                cadd_payload = _download(cadd_url)
                _verify_pinned_payload(
                    spec.cadd_filename,
                    cadd_payload,
                    expected_cadd[spec.cadd_filename],
                )
                if (
                    hashlib.md5(cadd_payload, usedforsecurity=False).hexdigest()
                    != EXPECTED_CADD_MD5[spec.cadd_filename]
                ):
                    raise RuntimeError(f"{spec.cadd_filename}: MD5 does not match CADD manifest")
                metadata_url = _mavedb_url(spec.mavedb_urn)
                scores_url = _mavedb_url(spec.mavedb_urn, "/scores")
                metadata_payload = _download(metadata_url)
                scores_payload = _download(scores_url)
                pins = expected_mavedb[spec.mavedb_urn]
                _verify_pinned_payload(
                    f"{spec.mavedb_urn} metadata", metadata_payload, pins["metadata"]
                )
                _verify_pinned_payload(f"{spec.mavedb_urn} scores", scores_payload, pins["scores"])
                cadd_payloads[spec.cadd_label] = cadd_payload
                mavedb_payloads[spec.cadd_label] = (metadata_payload, scores_payload)
                print(f"downloaded {spec.cadd_label} upstream records", flush=True)

            chromosomes = {
                parse_mavedb_metadata(mavedb_payloads[spec.cadd_label][0], spec).chrom
                for spec in ELEMENT_SPECS
            }
            elements = []
            with Genome(
                REFERENCE_URL,
                subset_chroms=chromosomes,
                storage_options=storage_options,
            ) as genome:
                for spec in ELEMENT_SPECS:
                    cadd_payload = cadd_payloads[spec.cadd_label]
                    metadata_payload, scores_payload = mavedb_payloads[spec.cadd_label]
                    variants, filter_counts = parse_cadd_vcf(cadd_payload, label=spec.cadd_filename)
                    metadata = parse_mavedb_metadata(metadata_payload, spec)
                    metadata, reference_records = validate_reference(variants, metadata, genome)
                    mavedb_records = validate_mavedb_crosswalk(
                        variants,
                        metadata,
                        scores_payload,
                        spec=spec,
                        genome=genome,
                    )
                    elements.append(
                        PreparedElement(
                            spec,
                            metadata,
                            variants,
                            filter_counts,
                            reference_records,
                            mavedb_records,
                        )
                    )
                    print(
                        f"validated {spec.cadd_label}: {len(variants):,} records, "
                        f"{filter_counts['SIGN']:,} eligible",
                        flush=True,
                    )
            written_key, _ = _write_cache(
                cache_dir,
                elements=elements,
                configuration=cache_configuration,
            )
            if written_key != cache_key:
                raise RuntimeError("processed cache key changed while writing")
            if upload_cache:
                assert token is not None
                _publish_cache(api, cache_dir, cache_prefix, token)

    filter_totals = {
        name: sum(element.filter_counts[name] for element in elements)
        for name in EXPECTED_FILTER_COUNTS
    }
    if filter_totals != EXPECTED_FILTER_COUNTS:
        raise RuntimeError(f"canonical FILTER totals do not match: {filter_totals}")
    cadd_sources = {
        spec.cadd_filename: {
            "url": f"{CADD_BASE_URL}/{spec.cadd_filename}",
            "retrieval_date": RETRIEVAL_DATE,
            **expected_cadd[spec.cadd_filename],
            "md5": EXPECTED_CADD_MD5[spec.cadd_filename],
        }
        for spec in ELEMENT_SPECS
    }
    mavedb_sources = {
        spec.mavedb_urn: {
            "metadata": {
                "url": _mavedb_url(spec.mavedb_urn),
                "retrieval_date": RETRIEVAL_DATE,
                **expected_mavedb[spec.mavedb_urn]["metadata"],
            },
            "scores": {
                "url": _mavedb_url(spec.mavedb_urn, "/scores"),
                "retrieval_date": RETRIEVAL_DATE,
                **expected_mavedb[spec.mavedb_urn]["scores"],
            },
            "modification_date": next(
                element.metadata.modification_date for element in elements if element.spec == spec
            ),
        }
        for spec in ELEMENT_SPECS
    }
    md5_manifest_pin = PINNED_INPUTS["cadd_md5_manifest"]
    source_provenance = {
        "cadd": {
            "release": "v1.7",
            "validation_set": "RegSeq",
            "md5_manifest": {
                "url": CADD_MD5_URL,
                "retrieval_date": RETRIEVAL_DATE,
                **md5_manifest_pin,
            },
            "files": cadd_sources,
        },
        "mavedb": {
            "api": "MaveDB REST API v1",
            "records": mavedb_sources,
            "crosswalk_validation": (
                "all CADD rows matched to MaveDB by the source-study coordinates and alleles, "
                "rounded effect, p-value, and barcode count"
            ),
        },
        "reference": {
            "dataset": "marin-dna/human-genome",
            "revision": REFERENCE_REVISION,
            "url": REFERENCE_URL,
            "assembly": "GRCh38",
            "retrieval_date": RETRIEVAL_DATE,
            **reference_pin,
            "vcf_records_checked": sum(element.reference_records_validated for element in elements),
            "reference_mismatch_records": sum(
                "vcf_key" in discrepancy
                for element in elements
                for discrepancy in element.metadata.reference_discrepancies
            ),
            "target_sequences_checked": len(elements),
            "target_sequence_base_mismatches": sum(
                "chrom" in discrepancy
                for element in elements
                for discrepancy in element.metadata.reference_discrepancies
            ),
        },
        "processed_cache": {
            "bucket": CACHE_BUCKET,
            "prefix": cache_prefix,
            "cache_key": cache_key,
            "records": EXPECTED_FILTER_COUNTS["SIGN"],
        },
    }
    return write_prepared_dataset(
        elements,
        source_provenance=source_provenance,
        output=OUTPUT,
        manifest_output=MANIFEST_OUTPUT,
        output_relpath="data/sources/satmut-mpra-cadd-v1.7.jsonl",
    )


def main() -> None:
    parser = argparse.ArgumentParser()
    parser.add_argument(
        "--skip-cache-upload",
        action="store_true",
        help="prepare locally without publishing the reusable eligible pool",
    )
    args = parser.parse_args()
    count, digest = prepare(upload_cache=not args.skip_cache_upload)
    print(f"wrote {count} source records to {OUTPUT} (sha256 {digest})")
    print(f"wrote manifest to {MANIFEST_OUTPUT}")


if __name__ == "__main__":
    main()
