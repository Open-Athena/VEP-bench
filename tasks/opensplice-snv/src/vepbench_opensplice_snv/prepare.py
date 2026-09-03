"""Remote-friendly, bounded-memory OpenSplice source preparation."""

from __future__ import annotations

import csv
import gzip
import hashlib
import io
import json
import os
import tempfile
import urllib.request
import zipfile
from collections import Counter, OrderedDict, defaultdict
from collections.abc import Iterator, Mapping, Sequence
from dataclasses import asdict
from pathlib import Path
from typing import Any, TextIO

from huggingface_hub import HfApi, get_token

from vepbench.artifacts import canonical_json, sha256_file

from .configuration import CONFIG, cache_configuration, cache_key
from .task import (
    REQUIRED_MASTER_COLUMNS,
    REQUIRED_VARIANT_METADATA_COLUMNS,
    ExonMetadata,
    ExonSummary,
    OpenSplicePreparationError,
    Variant,
    build_source_record,
    eligible_variant_from_row,
    parse_exon_metadata,
    parse_integral,
    select_exon_summaries,
    summarize_exon,
    validate_required_columns,
    validate_unique_variants,
    variants_from_cache_records,
    write_prepared_dataset,
)

OUTPUT = CONFIG.resolve_path("output")
MANIFEST_OUTPUT = CONFIG.resolve_path("manifest_output")
PINS = CONFIG.pins
CACHE_BUCKET = CONFIG.values["cache"]["bucket"]
CACHE_ROOT = CONFIG.values["cache"]["root"]
CACHE_DATA_FILES = tuple(CONFIG.values["cache"]["data_files"])
DOWNLOAD_CHUNK_SIZE = 1024 * 1024
MAX_OPEN_EXON_FILES = 32


def _figshare_url(pin: Mapping[str, Any]) -> str:
    return f"https://ndownloader.figshare.com/files/{pin['file_id']}"


def _download_and_verify(label: str, pin: Mapping[str, Any], destination: Path) -> None:
    """Download one immutable Figshare object and verify all three pinned properties."""

    md5 = hashlib.md5()
    sha256 = hashlib.sha256()
    size = 0
    print(f"downloading pinned {label} ({pin['bytes']} bytes)", flush=True)
    request = urllib.request.Request(_figshare_url(pin), headers={"User-Agent": "VEP-bench/0.1"})
    with urllib.request.urlopen(request, timeout=900) as response, destination.open("wb") as output:
        while chunk := response.read(DOWNLOAD_CHUNK_SIZE):
            output.write(chunk)
            size += len(chunk)
            md5.update(chunk)
            sha256.update(chunk)
    observed = {"bytes": size, "md5": md5.hexdigest(), "sha256": sha256.hexdigest()}
    expected = {key: pin[key] for key in observed}
    if observed != expected:
        raise OpenSplicePreparationError(
            f"{label}: pinned Figshare object mismatch: expected {expected}, got {observed}"
        )


def _cache_configuration() -> dict[str, Any]:
    return cache_configuration()


def _cache_key(configuration: Mapping[str, Any]) -> str:
    return cache_key(dict(configuration))


def _cache_state(api: HfApi, prefix: str, token: str | None) -> str:
    paths = {
        item.path
        for item in api.list_bucket_tree(CACHE_BUCKET, prefix=prefix, recursive=True, token=token)
        if getattr(item, "type", None) == "file"
    }
    if not paths:
        return "absent"
    return "complete" if f"{prefix}/manifest.json" in paths else "incomplete"


def _validate_cache(
    directory: Path,
    *,
    expected_key: str,
    expected_configuration: Mapping[str, Any],
) -> dict[str, Any]:
    try:
        manifest = json.loads((directory / "manifest.json").read_text(encoding="utf-8"))
    except (OSError, json.JSONDecodeError) as exc:
        raise OpenSplicePreparationError(f"invalid processed-cache manifest: {exc}") from exc
    if (
        manifest.get("schema_version") != "1.0"
        or manifest.get("kind") != "vepbench_opensplice_snv_processed_cache"
        or manifest.get("cache_key") != expected_key
        or manifest.get("configuration") != expected_configuration
    ):
        raise OpenSplicePreparationError("processed-cache identity does not match")
    files = manifest.get("files")
    if not isinstance(files, dict) or set(files) != set(CACHE_DATA_FILES):
        raise OpenSplicePreparationError("processed-cache file manifest is incomplete")
    for filename in CACHE_DATA_FILES:
        path = directory / filename
        expected = files[filename]
        if path.stat().st_size != expected.get("bytes") or sha256_file(path) != expected.get(
            "sha256"
        ):
            raise OpenSplicePreparationError(f"processed-cache {filename} digest mismatch")
    return manifest


def _download_cache(
    api: HfApi,
    destination: Path,
    prefix: str,
    token: str | None,
    *,
    expected_key: str,
    expected_configuration: Mapping[str, Any],
) -> dict[str, Any]:
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
    return _validate_cache(
        destination,
        expected_key=expected_key,
        expected_configuration=expected_configuration,
    )


def _publish_cache(api: HfApi, directory: Path, prefix: str, token: str) -> None:
    state = _cache_state(api, prefix, token)
    if state == "complete":
        print(f"processed cache already exists at {prefix}", flush=True)
        return
    if state == "incomplete":
        raise OpenSplicePreparationError(f"refusing to overwrite incomplete cache {prefix}")
    api.batch_bucket_files(
        CACHE_BUCKET,
        add=[(directory / filename, f"{prefix}/{filename}") for filename in CACHE_DATA_FILES],
        token=token,
    )
    api.batch_bucket_files(
        CACHE_BUCKET,
        add=[(directory / "manifest.json", f"{prefix}/manifest.json")],
        token=token,
    )
    if _cache_state(api, prefix, token) != "complete":
        raise OpenSplicePreparationError(f"processed-cache marker missing at {prefix}")


class _ExonFilePool:
    """Bound the number of file descriptors while partitioning eligible rows by exon."""

    def __init__(self, directory: Path) -> None:
        self.directory = directory
        self.handles: OrderedDict[str, TextIO] = OrderedDict()

    def write(self, exon_id: str, payload: str) -> None:
        handle = self.handles.pop(exon_id, None)
        if handle is None:
            if len(self.handles) >= MAX_OPEN_EXON_FILES:
                _, oldest = self.handles.popitem(last=False)
                oldest.close()
            handle = (self.directory / f"{exon_id}.jsonl").open("a", encoding="utf-8", newline="\n")
        self.handles[exon_id] = handle
        handle.write(payload)
        handle.write("\n")

    def close(self) -> None:
        for handle in self.handles.values():
            handle.close()
        self.handles.clear()


def _prepare_cache(
    destination: Path,
    *,
    master_path: Path,
    exon_path: Path,
    configuration: Mapping[str, Any],
) -> dict[str, Any]:
    destination.mkdir(parents=True, exist_ok=False)
    partitions = destination / "partitions"
    partitions.mkdir()
    with exon_path.open(encoding="utf-8", newline="") as stream:
        exons = parse_exon_metadata(stream, label=exon_path.name)
    if len(exons) != CONFIG.values["population"]["exon_metadata_records"]:
        raise OpenSplicePreparationError("exon metadata record count does not match")

    pool = _ExonFilePool(partitions)
    master_rows = 0
    eligible_rows = 0
    exclusions: Counter[str] = Counter()
    mutation_types: Counter[str] = Counter()
    measured_values: Counter[str] = Counter()
    genes_by_exon: dict[str, set[str]] = defaultdict(set)
    try:
        with gzip.open(master_path, mode="rt", encoding="utf-8", newline="") as stream:
            reader = csv.DictReader(stream, delimiter="\t")
            validate_required_columns(
                reader.fieldnames, REQUIRED_MASTER_COLUMNS, label=master_path.name
            )
            for line_number, row in enumerate(reader, start=2):
                master_rows += 1
                measured_values[row["measured"]] += 1
                mutation_types[f"{row['mut_type']}|{row['length']}"] += 1
                exon_id = row["ensembl_exon_id"].strip()
                gene = row["gene"].strip()
                if exon_id in exons and gene:
                    genes_by_exon[exon_id].add(gene)
                variant, reasons = eligible_variant_from_row(
                    row,
                    exons,
                    label=f"{master_path.name}:{line_number}",
                )
                if variant is None:
                    exclusions.update(reasons)
                    continue
                pool.write(exon_id, canonical_json(asdict(variant)))
                eligible_rows += 1
                if master_rows % 100_000 == 0:
                    print(f"processed {master_rows} master rows", flush=True)
    finally:
        pool.close()

    conflicts = {
        exon_id: sorted(genes) for exon_id, genes in genes_by_exon.items() if len(genes) != 1
    }
    if conflicts:
        raise OpenSplicePreparationError(f"conflicting gene assignments: {conflicts}")
    expected = CONFIG.values["population"]
    if master_rows != expected["master_rows"] or eligible_rows != expected["eligible_rows"]:
        raise OpenSplicePreparationError(
            f"population drift: master_rows={master_rows}, eligible_rows={eligible_rows}"
        )

    eligible_path = destination / "eligible-variants.jsonl.gz"
    cached_records = 0
    with (
        eligible_path.open("wb") as raw,
        gzip.GzipFile(fileobj=raw, mode="wb", mtime=0) as compressed,
        io.TextIOWrapper(compressed, encoding="utf-8", newline="\n") as output,
    ):
        for exon_id in sorted(exons):
            partition = partitions / f"{exon_id}.jsonl"
            if not partition.exists():
                continue
            raw_records = [
                json.loads(line)
                for line in partition.read_text(encoding="utf-8").splitlines()
                if line
            ]
            variants = variants_from_cache_records(raw_records)
            validate_unique_variants(variants, exon_id=exon_id)
            variants.sort(
                key=lambda variant: (
                    variant.ensembl_exon_id,
                    variant.start,
                    variant.wt,
                    variant.mut,
                    variant.variant_id,
                )
            )
            for variant in variants:
                output.write(canonical_json(asdict(variant)))
                output.write("\n")
                cached_records += 1
    if cached_records != eligible_rows:
        raise AssertionError("processed-cache record count changed while sorting")

    exon_document = {
        "schema_version": "1.0",
        "exons": [asdict(exons[exon_id]) for exon_id in sorted(exons)],
    }
    (destination / "exons.json").write_text(
        f"{canonical_json(exon_document)}\n", encoding="utf-8", newline="\n"
    )
    population = {
        "schema_version": "1.0",
        "master_rows": master_rows,
        "eligible_rows": eligible_rows,
        "exclusions": dict(sorted(exclusions.items())),
        "mutation_types": dict(sorted(mutation_types.items())),
        "measured_values": dict(sorted(measured_values.items())),
        "genes_by_exon": {
            exon_id: next(iter(genes_by_exon[exon_id]))
            for exon_id in sorted(exons)
            if genes_by_exon[exon_id]
        },
    }
    (destination / "population.json").write_text(
        f"{canonical_json(population)}\n", encoding="utf-8", newline="\n"
    )
    key = _cache_key(configuration)
    manifest = {
        "schema_version": "1.0",
        "kind": "vepbench_opensplice_snv_processed_cache",
        "cache_key": key,
        "configuration": dict(configuration),
        "files": {
            filename: {
                "bytes": (destination / filename).stat().st_size,
                "sha256": sha256_file(destination / filename),
                "records": (
                    eligible_rows
                    if filename == "eligible-variants.jsonl.gz"
                    else len(exons)
                    if filename == "exons.json"
                    else 1
                ),
            }
            for filename in CACHE_DATA_FILES
        },
    }
    (destination / "manifest.json").write_text(
        f"{canonical_json(manifest)}\n", encoding="utf-8", newline="\n"
    )
    _validate_cache(
        destination,
        expected_key=key,
        expected_configuration=configuration,
    )
    return manifest


def _load_exons(cache_dir: Path) -> dict[str, ExonMetadata]:
    document = json.loads((cache_dir / "exons.json").read_text(encoding="utf-8"))
    records = document.get("exons")
    if not isinstance(records, list):
        raise OpenSplicePreparationError("processed-cache exon metadata is missing")
    exons = {record["ensembl_exon_id"]: ExonMetadata(**record) for record in records}
    if len(exons) != CONFIG.values["population"]["exon_metadata_records"]:
        raise OpenSplicePreparationError("processed-cache exon count does not match")
    return exons


def _iter_variant_groups(path: Path) -> Iterator[tuple[str, list[Variant]]]:
    current_id: str | None = None
    records: list[dict[str, Any]] = []
    with gzip.open(path, mode="rt", encoding="utf-8", newline="") as stream:
        for line_number, line in enumerate(stream, start=1):
            try:
                record = json.loads(line)
            except json.JSONDecodeError as exc:
                raise OpenSplicePreparationError(
                    f"processed cache eligible row {line_number} is invalid JSON"
                ) from exc
            exon_id = record.get("ensembl_exon_id")
            if not isinstance(exon_id, str):
                raise OpenSplicePreparationError("processed-cache exon identity is invalid")
            if current_id is not None and exon_id != current_id:
                if exon_id < current_id:
                    raise OpenSplicePreparationError("processed-cache variants are not sorted")
                yield current_id, variants_from_cache_records(records)
                records = []
            current_id = exon_id
            records.append(record)
    if current_id is not None:
        yield current_id, variants_from_cache_records(records)


def _selection_from_cache(
    cache_dir: Path,
    exons: Mapping[str, ExonMetadata],
) -> tuple[list[ExonSummary], list[ExonSummary], dict[str, list[Variant]]]:
    population = json.loads((cache_dir / "population.json").read_text(encoding="utf-8"))
    genes_by_exon = population.get("genes_by_exon", {})
    provisional: dict[str, ExonSummary] = {}
    eligible_path = cache_dir / "eligible-variants.jsonl.gz"
    for exon_id, variants in _iter_variant_groups(eligible_path):
        provisional[exon_id] = summarize_exon(variants, exon_id=exon_id)
    for exon_id in exons:
        if exon_id not in provisional:
            gene = genes_by_exon.get(exon_id)
            missing_gene = not isinstance(gene, str) or not gene
            provisional[exon_id] = ExonSummary(
                exon_id,
                None if missing_gene else gene,
                0,
                None,
                None,
                None,
                None,
                None,
                exclusion_reasons=(
                    "fewer_than_50_eligible_snvs",
                    *(("missing_source_gene_assignment",) if missing_gene else ()),
                ),
            )
    summaries, selected = select_exon_summaries(list(provisional.values()))
    expected = CONFIG.values["population"]
    eligible_exons = sum(summary.robust_range is not None for summary in summaries)
    eligible_genes = len({summary.gene for summary in summaries if summary.gene_winner})
    if eligible_exons != expected["eligible_exons"] or eligible_genes != expected["eligible_genes"]:
        raise OpenSplicePreparationError(
            f"eligible exon/gene counts drifted: {eligible_exons}/{eligible_genes}"
        )

    selected_ids = {summary.ensembl_exon_id for summary in selected}
    selected_variants = {}
    for exon_id, variants in _iter_variant_groups(eligible_path):
        if exon_id in selected_ids:
            selected_variants[exon_id] = variants
    if set(selected_variants) != selected_ids:
        raise OpenSplicePreparationError("selected exon variants are incomplete")
    return summaries, selected, selected_variants


def _validate_variant_metadata(
    path: Path,
    selected_variants: Mapping[str, Sequence[Variant]],
    exons: Mapping[str, ExonMetadata],
) -> None:
    selected = {
        (exon_id, variant.nt_seq): variant
        for exon_id, variants in selected_variants.items()
        for variant in variants
    }
    found: set[tuple[str, str]] = set()
    wt_found: set[str] = set()
    with path.open(encoding="utf-8", newline="") as stream:
        reader = csv.DictReader(stream, delimiter="\t")
        validate_required_columns(
            reader.fieldnames, REQUIRED_VARIANT_METADATA_COLUMNS, label=path.name
        )
        for line_number, row in enumerate(reader, start=2):
            exon_id = row["ensembl_exon_id"].strip()
            if exon_id not in selected_variants:
                continue
            variant_id = row["variant_id"].strip()
            nt_seq = row["nt_seq"].strip().upper().replace("U", "T")
            key = (exon_id, nt_seq)
            if key in selected:
                if key in found:
                    raise OpenSplicePreparationError(
                        f"{path.name}:{line_number}: duplicate variant"
                    )
                variant = selected[key]
                prediction_identifier = variant.alphagenome_minigene["identifier"]
                if (
                    not variant_id
                    or (prediction_identifier is not None and variant_id != prediction_identifier)
                    or parse_integral(row["start"], label=f"{path.name}:{line_number}:start")
                    != variant.start
                    or parse_integral(row["length"], label=f"{path.name}:{line_number}:length") != 1
                ):
                    raise OpenSplicePreparationError(
                        f"{path.name}:{line_number}: selected minigene input mismatch"
                    )
                found.add(key)
            if variant_id.endswith("_wt"):
                if exon_id in wt_found:
                    raise OpenSplicePreparationError(
                        f"{path.name}:{line_number}: duplicate WT input"
                    )
                exon = exons[exon_id]
                if (
                    row["nt_seq"].strip().upper().replace("U", "T") != exon.wt_seq
                    or parse_integral(
                        row["exon_length"],
                        label=f"{path.name}:{line_number}:exon_length",
                    )
                    != exon.exon_length
                ):
                    raise OpenSplicePreparationError(
                        f"{path.name}:{line_number}: wild-type minigene input mismatch"
                    )
                wt_found.add(exon_id)
    if found != set(selected) or wt_found != set(selected_variants):
        raise OpenSplicePreparationError("selected AlphaGenome minigene inputs are incomplete")


def _validate_genome_inputs(
    path: Path,
    selected_variants: Mapping[str, Sequence[Variant]],
) -> dict[str, str]:
    paths: dict[str, str] = {}
    with zipfile.ZipFile(path) as archive:
        for exon_id, variants in selected_variants.items():
            matches = [
                name
                for name in archive.namelist()
                if Path(name).name.startswith(f"{exon_id}_") and name.endswith(".vcf")
            ]
            if len(matches) != 1:
                raise OpenSplicePreparationError(
                    f"{exon_id}: expected one AlphaGenome genome-mode VCF, found {len(matches)}"
                )
            archive_path = matches[0]
            vcf_by_key: dict[tuple[str, int, str, str], str] = {}
            for line in archive.read(archive_path).decode("utf-8").splitlines():
                if not line or line.startswith("#"):
                    continue
                fields = line.split("\t")
                if len(fields) < 5:
                    raise OpenSplicePreparationError(f"{archive_path}: malformed VCF row")
                chrom, pos, variant_id, ref, alt = fields[:5]
                key = (chrom, int(pos), ref, alt)
                if key in vcf_by_key:
                    raise OpenSplicePreparationError(f"{archive_path}: duplicate VCF key")
                vcf_by_key[key] = variant_id
            for variant in variants:
                vcf_id = vcf_by_key.get(variant.genomic_key)
                if vcf_id is None:
                    raise OpenSplicePreparationError(
                        f"{archive_path}: missing selected genomic variant {variant.genomic_key}"
                    )
                prediction_id = variant.alphagenome_genome["variant_id"]
                if prediction_id is not None and prediction_id != vcf_id:
                    raise OpenSplicePreparationError(
                        f"{archive_path}: AlphaGenome variant ID does not match VCF"
                    )
            paths[exon_id] = archive_path
    return paths


def _source_provenance(cache_prefix: str, cache_key: str) -> dict[str, Any]:
    return {
        "figshare": {
            **PINS["dataset"],
            "retrieval_date": PINS["retrieval_date"],
            "files": {
                label: {**pin, "url": _figshare_url(pin)} for label, pin in PINS["files"].items()
            },
        },
        "opensplice_repository": {
            "url": CONFIG.values["alphagenome"]["repository"],
            "commit": CONFIG.values["alphagenome"]["commit"],
            "scripts": {
                "minigene": CONFIG.values["alphagenome"]["minigene_script"],
                "genome": CONFIG.values["alphagenome"]["genome_script"],
                "library_design": CONFIG.values["alphagenome"]["library_design_script"],
            },
        },
        "processed_cache": {
            "bucket": CACHE_BUCKET,
            "prefix": cache_prefix,
            "cache_key": cache_key,
        },
    }


def prepare(*, upload_cache: bool) -> tuple[int, str]:
    """Prepare the canonical OpenSplice source and optionally publish its immutable cache."""

    token = os.environ.get("HF_TOKEN") or get_token()
    if upload_cache and not token:
        raise OpenSplicePreparationError("HF_TOKEN is required to upload the processed cache")
    api = HfApi()
    configuration = _cache_configuration()
    cache_key = _cache_key(configuration)
    cache_prefix = f"{CACHE_ROOT}/{cache_key}"
    with tempfile.TemporaryDirectory(prefix="vepbench-opensplice-") as temporary:
        work = Path(temporary)
        cache_dir = work / "cache"
        state = _cache_state(api, cache_prefix, token)
        if state == "incomplete":
            raise OpenSplicePreparationError(
                f"refusing to use incomplete processed cache {cache_prefix}"
            )
        if state == "complete":
            print(f"using processed cache {cache_prefix}", flush=True)
            cache_manifest = _download_cache(
                api,
                cache_dir,
                cache_prefix,
                token,
                expected_key=cache_key,
                expected_configuration=configuration,
            )
        else:
            master_pin = PINS["files"]["master"]
            exon_pin = PINS["files"]["exon_metadata"]
            master_path = work / master_pin["filename"]
            exon_path = work / exon_pin["filename"]
            _download_and_verify("master", master_pin, master_path)
            _download_and_verify("exon_metadata", exon_pin, exon_path)
            cache_manifest = _prepare_cache(
                cache_dir,
                master_path=master_path,
                exon_path=exon_path,
                configuration=configuration,
            )
            if upload_cache:
                assert token is not None
                _publish_cache(api, cache_dir, cache_prefix, token)

        exons = _load_exons(cache_dir)
        summaries, selected, selected_variants = _selection_from_cache(cache_dir, exons)
        variant_pin = PINS["files"]["variant_metadata"]
        genome_pin = PINS["files"]["alphagenome_genome_inputs"]
        variant_path = work / variant_pin["filename"]
        genome_path = work / genome_pin["filename"]
        _download_and_verify("variant_metadata", variant_pin, variant_path)
        _download_and_verify("alphagenome_genome_inputs", genome_pin, genome_path)
        _validate_variant_metadata(variant_path, selected_variants, exons)
        genome_vcf_paths = _validate_genome_inputs(genome_path, selected_variants)

        records = []
        for summary in selected:
            rank = summary.selected_rank
            assert rank is not None
            exon_id = summary.ensembl_exon_id
            records.append(
                build_source_record(
                    exons[exon_id],
                    summary,
                    selected_variants[exon_id],
                    source_record_id=f"E{rank:02d}",
                    genome_vcf_path=genome_vcf_paths[exon_id],
                )
            )
        population_cache = json.loads((cache_dir / "population.json").read_text(encoding="utf-8"))
        population = {
            "master_rows": population_cache["master_rows"],
            "exon_metadata_records": len(exons),
            "eligible_rows": cache_manifest["files"]["eligible-variants.jsonl.gz"]["records"],
            "eligible_exons": sum(summary.robust_range is not None for summary in summaries),
            "eligible_genes": len({summary.gene for summary in summaries if summary.gene_winner}),
            "exclusions": population_cache["exclusions"],
            "mutation_types": population_cache["mutation_types"],
            "measured_values": population_cache["measured_values"],
            "short_upstream_flank_exons": sum(
                exon.native_upstream_length != 70 for exon in exons.values()
            ),
        }
        return write_prepared_dataset(
            records,
            summaries,
            source_provenance=_source_provenance(cache_prefix, cache_key),
            output=OUTPUT,
            manifest_output=MANIFEST_OUTPUT,
            output_relpath="data/sources/opensplice-snv-figshare-v5.jsonl",
            population=population,
        )


__all__ = [
    "_cache_configuration",
    "_cache_key",
    "_prepare_cache",
    "_validate_cache",
    "prepare",
]
