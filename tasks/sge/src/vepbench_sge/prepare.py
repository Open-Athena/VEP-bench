"""Build the pinned MaveDB saturation genome editing source artifacts."""

from __future__ import annotations

import gzip
import hashlib
import json
import os
import shutil
import tempfile
import urllib.parse
import urllib.request
from collections import Counter
from pathlib import Path
from typing import Any

from huggingface_hub import HfApi, get_hf_file_metadata, get_token, hf_hub_download

from vepbench.artifacts import canonical_json, sha256_file

from .configuration import CONFIG
from .task import (
    GENE_SPECS,
    Exon,
    SGEPreparationError,
    Transcript,
    Variant,
    annotate_and_filter_variants,
    build_catalog_audit,
    build_source_record,
    choose_panel,
    eligible_cache_rows,
    parse_gtf_exon_file,
    parse_score_csv,
    reverse_complement,
    transcript_coding_sequence,
    transcript_from_cdot,
    validate_mavedb_metadata,
    write_prepared_dataset,
)

MAVEDB_API_BASE = CONFIG.values["upstream"]["mavedb"]["api_base"]
MAVEDB_CATALOG_QUERY = CONFIG.values["upstream"]["mavedb"]["catalog_query"]
CDOT_API_BASE = CONFIG.values["upstream"]["cdot"]["api_base"]
REFERENCE = CONFIG.values["upstream"]["reference"]
REFERENCE_URL = (
    f"https://huggingface.co/datasets/{REFERENCE['dataset']}/resolve/"
    f"{REFERENCE['revision']}/{REFERENCE['filename']}"
)
ANNOTATION = CONFIG.values["upstream"]["annotation"]
CONSEQUENCES = CONFIG.values["upstream"]["consequences"]
OUTPUT = CONFIG.resolve_path("output")
MANIFEST_OUTPUT = CONFIG.resolve_path("manifest_output")
CACHE_BUCKET = CONFIG.values["cache"]["bucket"]
CACHE_ROOT = CONFIG.values["cache"]["root"]
CACHE_DATA_FILES = tuple(CONFIG.values["cache"]["data_files"])
RETRIEVAL_DATE = CONFIG.pins["retrieval_date"]


class Genome:
    """Small pyfaidx adapter using 0-based half-open intervals."""

    def __init__(self, fasta: Any) -> None:
        self._fasta = fasta

    def __call__(self, chrom: str, start: int, end: int) -> str:
        if start < 0 or end <= start:
            raise SGEPreparationError(f"{chrom}:{start}-{end}: invalid genome interval")
        try:
            return str(self._fasta[chrom][start:end]).upper()
        except (KeyError, IndexError) as exc:
            raise SGEPreparationError(
                f"{chrom}:{start}-{end}: genome interval unavailable"
            ) from exc


class _NCGenome:
    """Translate cdot RefSeq contigs to the Ensembl-style FASTA labels."""

    def __init__(self, fasta: Any) -> None:
        self._fasta = fasta

    @staticmethod
    def _key(contig: str) -> str:
        prefix = "NC_"
        if not contig.startswith(prefix):
            return contig
        number = int(contig[len(prefix) :].split(".", 1)[0])
        return "X" if number == 23 else "Y" if number == 24 else str(number)

    def __getitem__(self, contig: str) -> Any:
        return self._fasta[self._key(contig)]

    def __contains__(self, contig: str) -> bool:
        return self._key(contig) in self._fasta


def _download(url: str, *, body: bytes | None = None) -> bytes:
    headers = {"User-Agent": "VEP-bench/0.1"}
    if body is not None:
        headers["Content-Type"] = "application/json"
    request = urllib.request.Request(url, data=body, headers=headers)
    with urllib.request.urlopen(request, timeout=900) as response:
        return response.read()


def _download_file(url: str, destination: Path) -> None:
    request = urllib.request.Request(url, headers={"User-Agent": "VEP-bench/0.1"})
    with urllib.request.urlopen(request, timeout=900) as response, destination.open("wb") as output:
        shutil.copyfileobj(response, output, length=1024 * 1024)


def _verify_payload(label: str, payload: bytes, expected: dict[str, Any]) -> None:
    observed = {"bytes": len(payload), "sha256": hashlib.sha256(payload).hexdigest()}
    expected_identity = {key: expected[key] for key in observed}
    if observed != expected_identity:
        raise SGEPreparationError(
            f"{label}: payload identity mismatch; "
            f"expected={expected_identity}, observed={observed}"
        )


def _verify_file(label: str, path: Path, expected: dict[str, Any]) -> None:
    observed = {"bytes": path.stat().st_size, "sha256": sha256_file(path)}
    if observed != expected:
        raise SGEPreparationError(
            f"{label}: file identity mismatch; expected={expected}, observed={observed}"
        )


def _payload_record(url: str, pin: dict[str, Any]) -> dict[str, Any]:
    return {"url": url, "retrieval_date": RETRIEVAL_DATE, **pin}


def _mavedb_url(urn: str, suffix: str = "") -> str:
    return f"{MAVEDB_API_BASE}/score-sets/{urllib.parse.quote(urn, safe='')}{suffix}"


def _cache_configuration() -> dict[str, Any]:
    genes = []
    for record in CONFIG.values["genes"]:
        genes.append(
            {
                key: record[key]
                for key in (
                    "gene",
                    "mavedb_urn",
                    "expected_target_name",
                    "transcript",
                    "transcript_policy",
                    "coordinate_mode",
                    "expected_chrom",
                    "score_direction",
                    "score_direction_evidence",
                    "qc",
                )
            }
        )
    return {
        "schema_version": "1.0",
        "implementation_sha256": CONFIG.values["cache"]["implementation_sha256"],
        "source_pins": CONFIG.pins,
        "genes": genes,
        "eligibility": CONFIG.values["eligibility"],
        "reference_assembly": REFERENCE["assembly"],
    }


def _cache_key(configuration: dict[str, Any]) -> str:
    return hashlib.sha256(canonical_json(configuration).encode()).hexdigest()


def _write_cache(
    destination: Path,
    *,
    variants_by_gene: dict[str, tuple[Variant, ...]],
    transcripts: dict[str, Transcript],
    source_provenance: dict[str, Any],
    population: dict[str, Any],
    catalog_audit: dict[str, Any],
    configuration: dict[str, Any],
) -> dict[str, Any]:
    destination.mkdir(parents=True, exist_ok=False)
    rows = eligible_cache_rows(variants_by_gene)
    with (
        (destination / CACHE_DATA_FILES[0]).open("wb") as raw_output,
        gzip.GzipFile(filename="", mode="wb", fileobj=raw_output, mtime=0) as output,
    ):
        for row in rows:
            output.write(f"{canonical_json(row)}\n".encode())
    genes_document = {
        "schema_version": "1.0",
        "transcripts": {
            gene: {
                "accession": transcript.accession,
                "gene": transcript.gene,
                "chrom": transcript.chrom,
                "strand": transcript.strand,
                "exons": [[exon.start, exon.end] for exon in transcript.exons],
                "cds_start0": transcript.cds_start0,
                "cds_end0": transcript.cds_end0,
            }
            for gene, transcript in sorted(transcripts.items())
        },
        "source_provenance": source_provenance,
        "population": population,
        "catalog_audit": catalog_audit,
    }
    (destination / CACHE_DATA_FILES[1]).write_text(
        f"{canonical_json(genes_document)}\n", encoding="utf-8", newline="\n"
    )
    key = _cache_key(configuration)
    manifest = {
        "schema_version": "1.0",
        "kind": "vepbench_sge_processed_cache",
        "cache_key": key,
        "configuration": configuration,
        "files": {
            filename: {
                "bytes": (destination / filename).stat().st_size,
                "sha256": sha256_file(destination / filename),
                "records": len(rows) if index == 0 else len(transcripts),
            }
            for index, filename in enumerate(CACHE_DATA_FILES)
        },
    }
    (destination / "manifest.json").write_text(
        f"{canonical_json(manifest)}\n", encoding="utf-8", newline="\n"
    )
    _load_cache(destination, expected_key=key, expected_configuration=configuration)
    return manifest


def _load_cache(
    destination: Path,
    *,
    expected_key: str,
    expected_configuration: dict[str, Any],
) -> tuple[
    dict[str, tuple[Variant, ...]],
    dict[str, Transcript],
    dict[str, Any],
    dict[str, Any],
    dict[str, Any],
    dict[str, Any],
]:
    try:
        manifest = json.loads((destination / "manifest.json").read_text(encoding="utf-8"))
    except (OSError, json.JSONDecodeError) as exc:
        raise SGEPreparationError("processed cache manifest is invalid") from exc
    if (
        manifest.get("schema_version") != "1.0"
        or manifest.get("kind") != "vepbench_sge_processed_cache"
        or manifest.get("cache_key") != expected_key
        or manifest.get("configuration") != expected_configuration
        or _cache_key(manifest.get("configuration", {})) != expected_key
    ):
        raise SGEPreparationError("processed cache identity mismatch")
    files = manifest.get("files")
    if not isinstance(files, dict) or set(files) != set(CACHE_DATA_FILES):
        raise SGEPreparationError("processed cache file inventory mismatch")
    for filename, metadata in files.items():
        path = destination / filename
        if (
            not isinstance(metadata, dict)
            or not path.is_file()
            or path.stat().st_size != metadata.get("bytes")
            or sha256_file(path) != metadata.get("sha256")
        ):
            raise SGEPreparationError(f"processed cache file is corrupt: {filename}")
    try:
        genes_document = json.loads(
            (destination / CACHE_DATA_FILES[1]).read_text(encoding="utf-8")
        )
        with gzip.open(destination / CACHE_DATA_FILES[0], mode="rt", encoding="utf-8") as source:
            rows = [json.loads(line) for line in source]
    except (OSError, UnicodeDecodeError, json.JSONDecodeError) as exc:
        raise SGEPreparationError("processed cache content is malformed") from exc
    if len(rows) != files[CACHE_DATA_FILES[0]].get("records"):
        raise SGEPreparationError("processed cache row count mismatch")
    transcripts = {}
    for gene, record in genes_document.get("transcripts", {}).items():
        transcripts[gene] = Transcript(
            accession=record["accession"],
            gene=record["gene"],
            chrom=record["chrom"],
            strand=record["strand"],
            exons=tuple(Exon(*exon) for exon in record["exons"]),
            cds_start0=record["cds_start0"],
            cds_end0=record["cds_end0"],
        )
    if set(transcripts) != {spec.gene for spec in GENE_SPECS}:
        raise SGEPreparationError("processed cache transcript set mismatch")
    variants_by_gene: dict[str, list[Variant]] = {spec.gene: [] for spec in GENE_SPECS}
    for row in rows:
        gene = row.get("gene")
        if gene not in variants_by_gene:
            raise SGEPreparationError("processed cache contains an unknown gene")
        variants_by_gene[gene].append(
            Variant(
                gene=gene,
                source_accession=row["source_accession"],
                source_hgvs=row["source_hgvs"],
                chrom=row["chrom"],
                pos=row["pos"],
                ref=row["ref"],
                alt=row["alt"],
                source_score=row["source_score"],
                damage_score=row["damage_score"],
                source_fields=row["source_fields"],
                consequence=row["consequence"],
                consequence_final=row["consequence_final"],
                consequence_group=row["consequence_group"],
                nearest_exon_distance=row["nearest_exon_distance"],
            )
        )
    return (
        {gene: tuple(values) for gene, values in variants_by_gene.items()},
        transcripts,
        genes_document["source_provenance"],
        genes_document["population"],
        genes_document["catalog_audit"],
        manifest,
    )


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
    expected_configuration: dict[str, Any],
) -> tuple[
    dict[str, tuple[Variant, ...]],
    dict[str, Transcript],
    dict[str, Any],
    dict[str, Any],
    dict[str, Any],
    dict[str, Any],
]:
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


def _publish_cache(api: HfApi, directory: Path, prefix: str, token: str) -> None:
    state = _remote_cache_state(api, prefix, token)
    if state == "complete":
        return
    if state == "incomplete":
        raise RuntimeError(f"refusing to overwrite incomplete cache prefix {prefix}")
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
    if _remote_cache_state(api, prefix, token) != "complete":
        raise RuntimeError(f"processed cache marker was not installed at {prefix}")


def _download_hf_file(
    *, repo_id: str, revision: str, filename: str, token: str | None, expected: dict[str, Any]
) -> Path:
    url = f"https://huggingface.co/datasets/{repo_id}/resolve/{revision}/{filename}"
    metadata = get_hf_file_metadata(
        url,
        token=token,
        timeout=60,
        user_agent="VEP-bench/0.1",
        retry_on_errors=True,
    )
    if (
        metadata.commit_hash != revision
        or metadata.size != expected["bytes"]
        or metadata.etag != expected["sha256"]
    ):
        raise SGEPreparationError(f"{repo_id}/{filename}: remote metadata does not match pin")
    path = Path(
        hf_hub_download(
            repo_id=repo_id,
            filename=filename,
            revision=revision,
            repo_type="dataset",
            token=token,
        )
    )
    _verify_file(f"{repo_id}/{filename}", path, expected)
    return path


def _pyhgvs_mapper(fasta: Any, transcript_records: dict[str, dict[str, Any]]) -> Any:
    import pyhgvs
    from pyhgvs.utils import make_transcript

    genome = _NCGenome(fasta)

    def get_transcript(transcript_id: str) -> Any:
        """Adapt pinned cdot JSON to the dictionary contract used by PyHGVS."""

        record = transcript_records.get(transcript_id)
        build = record.get("genome_builds", {}).get("GRCh38") if record else None
        if not isinstance(record, dict) or not isinstance(build, dict):
            return None
        exons = build["exons"]
        end = exons[-1][1]
        return make_transcript(
            {
                "id": record["id"],
                "chrom": build["contig"],
                "start": exons[0][0],
                "end": end,
                "strand": build["strand"],
                "cds_start": build.get("cds_start", end),
                "cds_end": build.get("cds_end", end),
                "gene_name": record["gene_name"],
                "exons": [exon[:2] for exon in exons],
            }
        )

    def mapper(hgvs: str) -> tuple[str, int, str, str] | None:
        try:
            contig, pos, ref, alt = pyhgvs.parse_hgvs_name(
                hgvs,
                genome,
                get_transcript=get_transcript,
            )
            chrom = _NCGenome._key(contig)
            transcript_id = hgvs.split(":", 1)[0]
            record = transcript_records[transcript_id]
            strand = record["genome_builds"]["GRCh38"]["strand"]
            source_ref, source_alt = hgvs.rsplit(">", 1)[0][-1], hgvs.rsplit(">", 1)[1]
            expected_ref = source_ref if strand == "+" else reverse_complement(source_ref)
            expected_alt = source_alt if strand == "+" else reverse_complement(source_alt)
        except Exception:
            return None
        if str(ref).upper() != expected_ref or str(alt).upper() != expected_alt:
            return None
        if chrom not in {spec.expected_chrom for spec in GENE_SPECS}:
            return None
        return chrom, int(pos), str(ref).upper(), str(alt).upper()

    return mapper


def _join_consequences(
    variants_by_gene: dict[str, tuple[Variant, ...]],
    paths: dict[str, Path],
) -> dict[tuple[str, int, str, str], str]:
    import polars as pl

    by_chrom: dict[str, set[tuple[str, int, str, str]]] = {}
    for variants in variants_by_gene.values():
        for variant in variants:
            by_chrom.setdefault(variant.chrom, set()).add(variant.key)
    result = {}
    for chrom, keys in sorted(by_chrom.items()):
        wanted = pl.DataFrame(
            [
                {"chrom": key[0], "pos": key[1], "ref": key[2], "alt": key[3]}
                for key in sorted(keys)
            ]
        )
        joined = (
            pl.scan_parquet(paths[chrom])
            .select("chrom", "pos", "ref", "alt", "consequence")
            .with_columns(pl.col("pos").cast(pl.Int64))
            .join(wanted.lazy(), on=["chrom", "pos", "ref", "alt"], how="inner")
            .collect(engine="streaming")
        )
        if joined.height != len(keys):
            raise SGEPreparationError(
                f"chromosome {chrom}: consequence join matched {joined.height}/{len(keys)} variants"
            )
        for row in joined.iter_rows(named=True):
            key = (row["chrom"], row["pos"], row["ref"], row["alt"])
            consequence = row["consequence"]
            if not isinstance(consequence, str) or key in result:
                raise SGEPreparationError(f"{key}: invalid or duplicate consequence")
            result[key] = consequence
    return result


def _build_processed_cache_inputs(
    temporary: Path,
    *,
    fasta: Any,
    genome: Genome,
    token: str | None,
) -> tuple[
    dict[str, tuple[Variant, ...]],
    dict[str, Transcript],
    dict[str, Any],
    dict[str, Any],
    dict[str, Any],
]:
    query_body = json.dumps(MAVEDB_CATALOG_QUERY, separators=(",", ":")).encode()
    catalog_url = f"{MAVEDB_API_BASE}/score-sets/search"
    catalog_payload = _download(catalog_url, body=query_body)
    _verify_payload("MaveDB catalog audit", catalog_payload, CONFIG.pins["catalog_audit"])
    catalog_audit = build_catalog_audit(catalog_payload)

    metadata_by_gene = {}
    scores_by_gene = {}
    transcript_payloads = {}
    transcripts = {}
    source_provenance: dict[str, Any] = {
        "retrieval_date": RETRIEVAL_DATE,
        "mavedb_catalog": _payload_record(catalog_url, CONFIG.pins["catalog_audit"]),
        "mavedb": {},
        "cdot": {},
    }
    for spec in GENE_SPECS:
        metadata_url = _mavedb_url(spec.mavedb_urn)
        scores_url = _mavedb_url(spec.mavedb_urn, "/scores")
        transcript_url = f"{CDOT_API_BASE}/{spec.transcript}"
        metadata_payload = _download(metadata_url)
        scores_payload = _download(scores_url)
        transcript_payload = _download(transcript_url)
        pins = CONFIG.pins["mavedb"][spec.mavedb_urn]
        _verify_payload(f"{spec.mavedb_urn} metadata", metadata_payload, pins["metadata"])
        _verify_payload(f"{spec.mavedb_urn} scores", scores_payload, pins["scores"])
        _verify_payload(spec.transcript, transcript_payload, CONFIG.pins["cdot"][spec.transcript])
        metadata = validate_mavedb_metadata(metadata_payload, spec)
        transcript = transcript_from_cdot(transcript_payload, spec)
        metadata_by_gene[spec.gene] = metadata
        scores_by_gene[spec.gene] = scores_payload
        transcript_payloads[spec.transcript] = json.loads(transcript_payload)
        transcripts[spec.gene] = transcript
        source_provenance["mavedb"][spec.mavedb_urn] = {
            "metadata_payload": _payload_record(metadata_url, pins["metadata"]),
            "scores_payload": _payload_record(scores_url, pins["scores"]),
            **metadata,
        }
        source_provenance["cdot"][spec.transcript] = _payload_record(
            transcript_url, CONFIG.pins["cdot"][spec.transcript]
        )

    mapper = _pyhgvs_mapper(fasta, transcript_payloads)
    mapped_by_gene = {}
    population: dict[str, dict[str, Any]] = {}
    for spec in GENE_SPECS:
        variants, report = parse_score_csv(scores_by_gene[spec.gene], spec, mapper=mapper)
        mapped_by_gene[spec.gene] = variants
        population[spec.gene] = {"source_validation": report}
        source_provenance["mavedb"][spec.mavedb_urn]["source_columns"] = report[
            "source_columns"
        ]
        if spec.coordinate_mode == "target_coding_hgvs":
            target_sequence = metadata_by_gene[spec.gene]["target"]["sequence"]
            observed = transcript_coding_sequence(transcripts[spec.gene], genome)
            if target_sequence != observed:
                raise SGEPreparationError(
                    f"{spec.gene}: target coding sequence does not match MANE transcript"
                )
            population[spec.gene]["target_sequence_validation"] = {
                "policy": "target n. coordinates interpreted as MANE CDS c. coordinates",
                "bases": len(observed),
                "sha256": hashlib.sha256(observed.encode()).hexdigest(),
            }

    annotation_path = temporary / "Homo_sapiens.GRCh38.107.chr.gtf.gz"
    _download_file(ANNOTATION["url"], annotation_path)
    _verify_file("Ensembl GTF", annotation_path, CONFIG.pins["annotation"])
    exon_index = parse_gtf_exon_file(
        annotation_path, {spec.expected_chrom for spec in GENE_SPECS}
    )
    source_provenance["annotation"] = {
        "release": ANNOTATION["release"],
        **_payload_record(ANNOTATION["url"], CONFIG.pins["annotation"]),
    }

    consequence_paths = {}
    source_provenance["consequences"] = {
        "dataset": CONSEQUENCES["dataset"],
        "revision": CONSEQUENCES["revision"],
        "vep_version": CONSEQUENCES["vep_version"],
        "vep_flags": CONSEQUENCES["vep_flags"],
        "files": {},
    }
    for chrom, pin in CONFIG.pins["consequences"].items():
        filename = f"{chrom}.parquet"
        path = _download_hf_file(
            repo_id=CONSEQUENCES["dataset"],
            revision=CONSEQUENCES["revision"],
            filename=filename,
            token=token,
            expected=pin,
        )
        consequence_paths[chrom] = path
        source_provenance["consequences"]["files"][filename] = _payload_record(
            f"https://huggingface.co/datasets/{CONSEQUENCES['dataset']}/resolve/"
            f"{CONSEQUENCES['revision']}/{filename}",
            pin,
        )
    consequences = _join_consequences(mapped_by_gene, consequence_paths)
    eligible_by_gene = {}
    for spec in GENE_SPECS:
        eligible, excluded = annotate_and_filter_variants(
            mapped_by_gene[spec.gene],
            consequences=consequences,
            exon_index=exon_index,
            genome=genome,
        )
        eligible_by_gene[spec.gene] = eligible
        class_counts: Counter[str] = Counter()
        for variant in eligible:
            if variant.consequence_group is None:
                raise AssertionError("eligible variant has no consequence group")
            class_counts[variant.consequence_group] += 1
        population[spec.gene].update(
            {
                "eligibility_excluded": excluded,
                "eligible_records": len(eligible),
                "eligible_class_counts": dict(sorted(class_counts.items())),
            }
        )
    return eligible_by_gene, transcripts, source_provenance, population, catalog_audit


def prepare(*, upload_cache: bool) -> tuple[int, str]:
    """Rebuild SGE from pinned primary sources or a complete immutable processed cache."""

    from pyfaidx import Fasta

    token = os.environ.get("HF_TOKEN") or get_token()
    if upload_cache and not token:
        raise RuntimeError("HF_TOKEN is required to upload the processed cache")
    cache_configuration = _cache_configuration()
    cache_key = _cache_key(cache_configuration)
    cache_prefix = f"{CACHE_ROOT}/{cache_key}"
    api = HfApi(token=token)
    cache_state = _remote_cache_state(api, cache_prefix, token)
    if cache_state == "incomplete":
        raise RuntimeError(f"refusing to use incomplete cache prefix {cache_prefix}")

    with tempfile.TemporaryDirectory(prefix="vepbench-sge-") as temporary_name:
        temporary = Path(temporary_name)
        reference_path = _download_hf_file(
            repo_id=REFERENCE["dataset"],
            revision=REFERENCE["revision"],
            filename=REFERENCE["filename"],
            token=token,
            expected=CONFIG.pins["reference"],
        )
        fasta = Fasta(str(reference_path), indexname=str(temporary / "reference.fa.fai"))
        genome = Genome(fasta)
        reference_provenance = {
            "dataset": REFERENCE["dataset"],
            "revision": REFERENCE["revision"],
            "assembly": REFERENCE["assembly"],
            **_payload_record(REFERENCE_URL, CONFIG.pins["reference"]),
        }
        cache_dir = temporary / "processed-cache"
        if cache_state == "complete":
            (
                variants_by_gene,
                transcripts,
                source_provenance,
                population,
                catalog_audit,
                cache_manifest,
            ) = _download_cache(
                api,
                cache_dir,
                cache_prefix,
                token,
                expected_key=cache_key,
                expected_configuration=cache_configuration,
            )
        else:
            (
                variants_by_gene,
                transcripts,
                source_provenance,
                population,
                catalog_audit,
            ) = _build_processed_cache_inputs(
                temporary,
                fasta=fasta,
                genome=genome,
                token=token,
            )
            source_provenance["reference"] = reference_provenance
            cache_manifest = _write_cache(
                cache_dir,
                variants_by_gene=variants_by_gene,
                transcripts=transcripts,
                source_provenance=source_provenance,
                population=population,
                catalog_audit=catalog_audit,
                configuration=cache_configuration,
            )
            if upload_cache:
                assert token is not None
                _publish_cache(api, cache_dir, cache_prefix, token)
        source_provenance["reference"] = reference_provenance

        records = []
        for spec in GENE_SPECS:
            panel, exon_summaries = choose_panel(
                variants_by_gene[spec.gene], transcripts[spec.gene], genome=genome
            )
            gene_population = population[spec.gene]
            if panel is None:
                gene_population["status"] = "excluded"
                gene_population["exclusion_reason"] = (
                    "no transcript exon plus 100 bp flanks contains 50 eligible variants"
                )
                gene_population["exon_windows"] = exon_summaries
                continue
            gene_population["status"] = "included"
            gene_population["selected_exon"] = {
                "start": panel.exon.start,
                "end": panel.exon.end,
            }
            gene_population["selected_class_allocation"] = {
                "missense_variant": panel.missense_allocation,
                "splicing": panel.splicing_allocation,
            }
            gene_population["exon_windows"] = exon_summaries
            records.append(
                build_source_record(
                    spec,
                    transcripts[spec.gene],
                    panel,
                    source_provenance=source_provenance["mavedb"][spec.mavedb_urn],
                    population_summary=gene_population,
                    exon_summaries=exon_summaries,
                )
            )
        manifest = {
            "schema_version": "1.0",
            "kind": "vepbench_sge_prepared_source",
            "task_family": "sge",
            "configuration": {
                "assembly": REFERENCE["assembly"],
                "panel_size": CONFIG.values["sampling"]["panel_size"],
                "preferred_class_balance": "25 missense_variant / 25 splicing",
                "sampling_seed": CONFIG.values["sampling"]["seed"],
                "sampling_algorithm": CONFIG.values["sampling"]["algorithm"],
                "score_orientation": "larger means greater functional damage; sign flip only",
                "exon_flanks": CONFIG.values["sequence"]["flank_bases"],
                "display_orientation": CONFIG.values["sequence"]["display_orientation"],
                "exon_tiebreaks": (
                    "preferred balance, achievable smaller class, P95-P05 damage-score spread, "
                    "transcript and genomic exon key"
                ),
                "percentile_method": "R type-7 linear interpolation",
                "eligibility": CONFIG.values["eligibility"],
            },
            "sources": source_provenance,
            "catalog_audit": catalog_audit,
            "population": dict(sorted(population.items())),
            "cache": {
                "bucket": CACHE_BUCKET,
                "prefix": cache_prefix,
                "cache_key": cache_key,
                "manifest": cache_manifest,
            },
        }
        return write_prepared_dataset(
            records,
            output=OUTPUT,
            manifest_output=MANIFEST_OUTPUT,
            manifest=manifest,
        )


__all__ = [
    "Genome",
    "_cache_configuration",
    "_cache_key",
    "_load_cache",
    "_write_cache",
    "prepare",
]
