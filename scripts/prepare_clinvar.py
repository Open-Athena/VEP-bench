"""Prepare the pinned July 2026 temporal ClinVar cohort."""

import hashlib
import io
import os
import tempfile
import urllib.request
from bisect import bisect_left
from collections.abc import Iterable
from pathlib import Path

import fsspec
import polars as pl
import pyarrow.parquet as pq
from huggingface_hub import HfApi, get_token

from vepbench.clinvar import (
    LABELS,
    PRIMARY_CHROMS,
    REVIEW_STARS,
    ClinVarCandidate,
    PreparationConfig,
    VepJoinResult,
    parse_clinvar_vcv,
    prepare_dataset,
    sparse_join_vep,
    validate_reference_windows,
    write_prepared_dataset,
)
from vepbench.clinvar_cache import (
    CACHE_SCHEMA_VERSION,
    download_processed_cache,
    processed_cache_key,
    processed_cache_prefix,
    publish_processed_cache,
    remote_cache_state,
    write_processed_cache,
)
from vepbench.genome import Genome

ROOT = Path(__file__).resolve().parents[1]
CLINVAR_URL = "https://ftp.ncbi.nlm.nih.gov/pub/clinvar/xml/ClinVarVCVRelease_2026-08.xml.gz"
CLINVAR_RELEASE_DATE = "2026-08-06"
CLINVAR_SCHEMA_VERSION = "2.6"
CLINVAR_EXPECTED_BYTES = 5_851_397_371
CLINVAR_EXPECTED_SHA256: str | None = (
    "95b07120541dae708ecca2bf749c12000bcd7bfa468fbe613c581c76b436dd0f"
)
VEP_REVISION = "eb3022cc6797b9369cca16af72ff3c4197df343a"
REFERENCE_REVISION = "11b9433582981bb929af333bc6422f10a8fd71b4"
REFERENCE_URL = (
    "https://huggingface.co/datasets/marin-dna/human-genome/resolve/"
    f"{REFERENCE_REVISION}/Homo_sapiens.GRCh38.dna_sm.primary_assembly.fa"
)
OUTPUT = ROOT / "data/sources/clinvar-july-2026.jsonl"
MANIFEST_OUTPUT = ROOT / "data/sources/clinvar-july-2026.manifest.json"
CACHE_BUCKET = "open-athena/VEP-bench"
CACHE_RELEASE_MONTH = "2026-08"
PRE_SAMPLING_IMPLEMENTATION_SHA256 = (
    "756ae3bdb0f1ffc8b2aad6383b025a8a3ce2f018f584b82596d0535239a06cc6"
)


def _download(url: str, output: Path) -> tuple[int, str]:
    request = urllib.request.Request(url, headers={"User-Agent": "VEP-bench/0.1"})
    digest = hashlib.sha256()
    size = 0
    with urllib.request.urlopen(request) as response, output.open("wb") as destination:
        while chunk := response.read(1024 * 1024):
            destination.write(chunk)
            digest.update(chunk)
            size += len(chunk)
    return size, digest.hexdigest()


def _cache_configuration(clinvar_sha256: str, config: PreparationConfig) -> dict:
    return {
        "cache_schema_version": CACHE_SCHEMA_VERSION,
        "clinvar": {
            "dataset_revision": "ClinVarVCVRelease_2026-08",
            "release_url": CLINVAR_URL,
            "release_date": CLINVAR_RELEASE_DATE,
            "schema_version": CLINVAR_SCHEMA_VERSION,
            "bytes": CLINVAR_EXPECTED_BYTES,
            "sha256": clinvar_sha256,
        },
        "eligibility": {
            "date_created_start_inclusive": config.start_date.isoformat(),
            "date_created_end_inclusive": config.end_date.isoformat(),
            "classifications": list(LABELS),
            "review_statuses": dict(REVIEW_STARS),
            "assembly": "GRCh38",
            "primary_chromosomes": list(PRIMARY_CHROMS),
            "variant": "one-base A/C/G/T substitution with REF != ALT",
            "duplicate_allele_policy": "exclude every eligible VCV sharing an allele key",
        },
        "vep": {
            "dataset": "songlab/hg38-variant-consequences",
            "revision": VEP_REVISION,
            "selected_column": "consequence",
            "join_keys": ["chrom", "pos", "ref", "alt"],
        },
        "reference": {
            "dataset": "marin-dna/human-genome",
            "revision": REFERENCE_REVISION,
            "assembly": "GRCh38",
            "flank_size": config.flank_size,
            "window_size": config.window_size,
            "uppercase_sequence": True,
            "allowed_bases": "ACGT",
        },
        # This is an explicit pre-sampling revision, not a digest of every file.
        # Keep it stable for sampling-only changes and replace it whenever
        # parsing, filtering, joining, or reference validation behavior changes.
        "implementation_sha256": PRE_SAMPLING_IMPLEMENTATION_SHA256,
    }


def _block_cached_sparse_join(
    candidates: Iterable[ClinVarCandidate],
    *,
    token: str,
) -> VepJoinResult:
    """Coalesce Parquet byte ranges while preserving the exact sparse join."""

    candidate_list = tuple(candidates)
    matched = []
    missing = []
    by_chrom = {
        chrom: tuple(candidate for candidate in candidate_list if candidate.chrom == chrom)
        for chrom in PRIMARY_CHROMS
    }
    for chrom in PRIMARY_CHROMS:
        chrom_candidates = by_chrom[chrom]
        if not chrom_candidates:
            continue
        print(
            f"querying pinned VEP shard {chrom} for {len(chrom_candidates):,} candidates",
            flush=True,
        )
        remote_shard = (
            f"hf://datasets/songlab/hg38-variant-consequences@{VEP_REVISION}/{chrom}.parquet"
        )
        with fsspec.open(
            remote_shard,
            "rb",
            token=token,
            block_size=8 * 1024 * 1024,
            cache_type="mmap",
        ) as shard:
            parquet = pq.ParquetFile(shard)
            positions = sorted({candidate.pos for candidate in chrom_candidates})
            try:
                position_column = parquet.schema.names.index("pos")
            except ValueError as exc:
                raise RuntimeError(f"VEP shard {chrom} has no pos column") from exc
            row_groups = []
            for index in range(parquet.metadata.num_row_groups):
                statistics = parquet.metadata.row_group(index).column(position_column).statistics
                if statistics is None or not statistics.has_min_max:
                    row_groups.append(index)
                    continue
                minimum = int(statistics.min)
                maximum = int(statistics.max)
                candidate_index = bisect_left(positions, minimum)
                if candidate_index < len(positions) and positions[candidate_index] <= maximum:
                    row_groups.append(index)
            print(
                f"selected {len(row_groups):,}/{parquet.metadata.num_row_groups:,} "
                f"VEP row groups for chromosome {chrom}",
                flush=True,
            )
            if row_groups:
                frame = pl.from_arrow(
                    parquet.read_row_groups(
                        row_groups,
                        columns=["pos", "ref", "alt", "consequence"],
                        use_threads=False,
                    )
                ).filter(pl.col("pos").is_in(positions))
                frame = frame.with_columns(pl.lit(chrom).alias("chrom")).select(
                    "chrom", "pos", "ref", "alt", "consequence"
                )
                filtered = io.BytesIO()
                frame.write_parquet(filtered)
                filtered.seek(0)
                result = sparse_join_vep(chrom_candidates, {chrom: filtered})
            else:
                result = VepJoinResult(matched=(), missing=chrom_candidates)
            requested_bytes = getattr(getattr(shard, "cache", None), "total_requested_bytes", None)
            if isinstance(requested_bytes, int):
                print(
                    f"read {requested_bytes:,} coalesced bytes from VEP shard {chrom}",
                    flush=True,
                )
            matched.extend(result.matched)
            missing.extend(result.missing)
    return VepJoinResult(
        matched=tuple(sorted(matched, key=lambda item: item.key)),
        missing=tuple(sorted(missing, key=lambda item: item.key)),
    )


def main() -> None:
    config = PreparationConfig()
    hf_token = os.environ.get("HF_TOKEN") or get_token()
    if not hf_token:
        raise RuntimeError("HF_TOKEN is required for processed-cache access")
    api = HfApi(token=hf_token)
    reference_storage_options = {"headers": {"Authorization": f"Bearer {hf_token}"}}
    with tempfile.TemporaryDirectory(prefix="vepbench-clinvar-") as temporary_dir:
        temporary_path = Path(temporary_dir)
        cache_configuration = (
            _cache_configuration(CLINVAR_EXPECTED_SHA256, config)
            if CLINVAR_EXPECTED_SHA256 is not None
            else None
        )
        cache_key = (
            processed_cache_key(cache_configuration) if cache_configuration is not None else None
        )
        cache_prefix = (
            processed_cache_prefix(CACHE_RELEASE_MONTH, cache_key)
            if cache_key is not None
            else None
        )
        state = (
            remote_cache_state(
                api,
                bucket_id=CACHE_BUCKET,
                prefix=cache_prefix,
                token=hf_token,
            )
            if cache_prefix is not None
            else "absent"
        )
        if state == "incomplete":
            raise RuntimeError(f"processed cache prefix is incomplete: {cache_prefix}")

        if state == "complete":
            assert cache_key is not None and cache_prefix is not None
            print(f"downloading processed cache {cache_prefix}", flush=True)
            parsed, joined, reference_validation, _ = download_processed_cache(
                api,
                bucket_id=CACHE_BUCKET,
                prefix=cache_prefix,
                destination=temporary_path / "processed-cache",
                token=hf_token,
                expected_key=cache_key,
            )
            clinvar_bytes = CLINVAR_EXPECTED_BYTES
            clinvar_sha256 = CLINVAR_EXPECTED_SHA256
            assert clinvar_sha256 is not None
        else:
            xml_path = temporary_path / "ClinVarVCVRelease_2026-08.xml.gz"
            print("downloading and hashing pinned ClinVar VCV release", flush=True)
            clinvar_bytes, clinvar_sha256 = _download(CLINVAR_URL, xml_path)
            print(
                f"downloaded {clinvar_bytes:,} bytes (sha256 {clinvar_sha256})",
                flush=True,
            )
            if clinvar_bytes != CLINVAR_EXPECTED_BYTES:
                raise RuntimeError(
                    f"ClinVar release size {clinvar_bytes} does not match {CLINVAR_EXPECTED_BYTES}"
                )
            if CLINVAR_EXPECTED_SHA256 is not None and clinvar_sha256 != CLINVAR_EXPECTED_SHA256:
                raise RuntimeError("ClinVar release SHA-256 does not match the pinned digest")
            cache_configuration = _cache_configuration(clinvar_sha256, config)
            cache_key = processed_cache_key(cache_configuration)
            cache_prefix = processed_cache_prefix(CACHE_RELEASE_MONTH, cache_key)
            state = remote_cache_state(
                api,
                bucket_id=CACHE_BUCKET,
                prefix=cache_prefix,
                token=hf_token,
            )
            if state == "incomplete":
                raise RuntimeError(f"processed cache prefix is incomplete: {cache_prefix}")
            if state == "complete":
                print(f"downloading processed cache {cache_prefix}", flush=True)
                parsed, joined, reference_validation, _ = download_processed_cache(
                    api,
                    bucket_id=CACHE_BUCKET,
                    prefix=cache_prefix,
                    destination=temporary_path / "processed-cache",
                    token=hf_token,
                    expected_key=cache_key,
                )
            else:
                print(f"streaming {clinvar_bytes:,} compressed ClinVar bytes", flush=True)
                parsed = parse_clinvar_vcv(
                    xml_path,
                    start_date=config.start_date,
                    end_date=config.end_date,
                )
                print(
                    f"joining {len(parsed.candidates):,} unique eligible SNVs to VEP",
                    flush=True,
                )
                joined = _block_cached_sparse_join(
                    parsed.candidates,
                    token=hf_token,
                )
                print(
                    f"matched {len(joined.matched):,}; missing {len(joined.missing):,}",
                    flush=True,
                )
                chromosomes = {candidate.clinvar.chrom for candidate in joined.matched}
                print("fetching and validating pinned GRCh38 reference windows", flush=True)
                with Genome(
                    REFERENCE_URL,
                    subset_chroms=chromosomes,
                    storage_options=reference_storage_options,
                ) as genome:
                    reference_validation = validate_reference_windows(
                        joined,
                        genome,
                        config=config,
                    )
                local_cache = temporary_path / "processed-cache"
                write_processed_cache(
                    local_cache,
                    configuration=cache_configuration,
                    parsed=parsed,
                    joined=joined,
                    reference_validation=reference_validation,
                )
                print(f"uploading processed cache {cache_prefix}", flush=True)
                publish_processed_cache(
                    api,
                    bucket_id=CACHE_BUCKET,
                    prefix=cache_prefix,
                    cache_dir=local_cache,
                    token=hf_token,
                )

        assert cache_key is not None and cache_prefix is not None
        prepared = prepare_dataset(
            parsed,
            joined,
            None,
            config=config,
            clinvar_source={
                "dataset": "NCBI ClinVar VCV",
                "dataset_revision": "ClinVarVCVRelease_2026-08",
                "release_url": CLINVAR_URL,
                "release_date": CLINVAR_RELEASE_DATE,
                "schema_version": CLINVAR_SCHEMA_VERSION,
                "bytes": clinvar_bytes,
                "sha256": clinvar_sha256,
            },
            vep_source={
                "dataset": "songlab/hg38-variant-consequences",
                "revision": VEP_REVISION,
                "vep_release": "109.1",
                "flags": ["--most_severe", "--distance", "1000"],
                "selected_column": "consequence",
                "join_keys": ["chrom", "pos", "ref", "alt"],
                "parquet_shards": "one pinned primary-chromosome shard per candidate chrom",
                "query": "row-group statistics, 8 MiB mmap-cached ranges, exact allele join",
            },
            reference={
                "dataset": "marin-dna/human-genome",
                "revision": REFERENCE_REVISION,
                "fasta_url": REFERENCE_URL,
                "assembly": "GRCh38",
                "sequence": "soft-masked primary assembly (uppercased for prompts)",
            },
            reference_validation=reference_validation,
            processed_cache={
                "bucket": CACHE_BUCKET,
                "prefix": cache_prefix,
                "cache_key": cache_key,
                "schema_version": CACHE_SCHEMA_VERSION,
            },
        )

    count, digest = write_prepared_dataset(
        prepared,
        output=OUTPUT,
        manifest_output=MANIFEST_OUTPUT,
        output_relpath="data/sources/clinvar-july-2026.jsonl",
    )
    print(f"wrote {count} source records to {OUTPUT} (sha256 {digest})")
    print(f"wrote manifest to {MANIFEST_OUTPUT}")


if __name__ == "__main__":
    main()
