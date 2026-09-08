"""Deterministic curation and validation for the continuous SGE task."""

from __future__ import annotations

import csv
import io
import json
import math
import re
from collections import Counter
from collections.abc import Callable, Mapping, Sequence
from dataclasses import dataclass
from pathlib import Path
from typing import Any

from vepbench.alleles import normalize_allele
from vepbench.artifacts import canonical_json, read_jsonl, sha256_file
from vepbench.errors import BuildError
from vepbench.sampling import ScoredAllele, ScorePanel, quantile, sample_score_bins, sampling_digest

from .configuration import CONFIG

TASK_FAMILY = CONFIG.values["task_family"]
SOURCE_DATASET = CONFIG.values["source_dataset"]
REFERENCE_CONTIG = CONFIG.values["reference_contig"]
PANEL_SIZE = CONFIG.values["sampling"]["panel_size"]
SAMPLING_SEED = CONFIG.values["sampling"]["seed"]
SAMPLING_ALGORITHM = CONFIG.values["sampling"]["algorithm"]
FLANK_BASES = CONFIG.values["sequence"]["flank_bases"]
NUCLEOTIDES = frozenset("ACGT")


class SGEPreparationError(BuildError):
    """Raised when SGE inputs or generated artifacts violate the task contract."""


@dataclass(frozen=True)
class GeneSpec:
    gene: str
    mavedb_urn: str
    expected_target_name: str
    transcript: str
    transcript_policy: str
    coordinate_mode: str
    expected_chrom: str
    score_direction: int
    score_direction_evidence: str
    qc_field: str | None
    qc_pass_values: tuple[str, ...]
    qc_fail_values: tuple[str, ...]
    assay_context: str


def _gene_spec(record: Mapping[str, Any]) -> GeneSpec:
    qc = record["qc"]
    return GeneSpec(
        gene=record["gene"],
        mavedb_urn=record["mavedb_urn"],
        expected_target_name=record["expected_target_name"],
        transcript=record["transcript"],
        transcript_policy=record["transcript_policy"],
        coordinate_mode=record["coordinate_mode"],
        expected_chrom=record["expected_chrom"],
        score_direction=record["score_direction"],
        score_direction_evidence=record["score_direction_evidence"],
        qc_field=qc["field"],
        qc_pass_values=tuple(qc["pass_values"]),
        qc_fail_values=tuple(qc["fail_values"]),
        assay_context=record["assay_context"],
    )


GENE_SPECS = tuple(_gene_spec(record) for record in CONFIG.values["genes"])
SPEC_BY_GENE = {spec.gene: spec for spec in GENE_SPECS}
SPEC_BY_URN = {spec.mavedb_urn: spec for spec in GENE_SPECS}


@dataclass(frozen=True, order=True)
class Exon:
    """One GRCh38 exon interval using 1-based inclusive coordinates."""

    start: int
    end: int

    @property
    def length(self) -> int:
        return self.end - self.start + 1


@dataclass(frozen=True)
class Transcript:
    accession: str
    gene: str
    chrom: str
    strand: str
    exons: tuple[Exon, ...]
    cds_start0: int | None = None
    cds_end0: int | None = None


@dataclass(frozen=True)
class Variant:
    gene: str
    source_accession: str
    source_hgvs: str
    chrom: str
    pos: int
    ref: str
    alt: str
    source_score: float
    damage_score: float
    source_fields: dict[str, str]

    @property
    def key(self) -> tuple[str, int, str, str]:
        return (self.chrom, self.pos, self.ref, self.alt)

    @property
    def key_text(self) -> str:
        return f"{self.chrom}:{self.pos}:{self.ref}:{self.alt}"


@dataclass(frozen=True)
class PanelCandidate:
    variant: Variant
    candidate_id: str
    local_pos: int
    visible_ref: str
    visible_alt: str
    score_bin: int


@dataclass(frozen=True)
class Panel:
    exon: Exon
    window_start: int
    window_end: int
    sequence: str
    candidates: tuple[PanelCandidate, ...]
    sampling: ScorePanel
    robust_score_spread: float


def transcript_from_cdot(payload: bytes, spec: GeneSpec) -> Transcript:
    """Validate a pinned cdot transcript and expose its GRCh38 exon geometry."""

    try:
        record = json.loads(payload)
    except (UnicodeDecodeError, json.JSONDecodeError) as exc:
        raise SGEPreparationError(f"{spec.transcript}: invalid cdot JSON") from exc
    build = record.get("genome_builds", {}).get("GRCh38") if isinstance(record, dict) else None
    if (
        not isinstance(build, dict)
        or record.get("id") != spec.transcript
        or record.get("gene_name") != spec.gene
        or record.get("cdot_data_version") != CONFIG.values["upstream"]["cdot"]["data_version"]
    ):
        raise SGEPreparationError(f"{spec.transcript}: cdot transcript identity mismatch")
    chrom = _nc_contig_to_chrom(build.get("contig"))
    strand = build.get("strand")
    raw_exons = build.get("exons")
    if chrom != spec.expected_chrom or strand not in {"+", "-"} or not isinstance(raw_exons, list):
        raise SGEPreparationError(f"{spec.transcript}: invalid GRCh38 transcript geometry")
    exons = []
    for raw in raw_exons:
        if (
            not isinstance(raw, list)
            or len(raw) < 2
            or isinstance(raw[0], bool)
            or not isinstance(raw[0], int)
            or isinstance(raw[1], bool)
            or not isinstance(raw[1], int)
            or raw[0] < 0
            or raw[1] <= raw[0]
        ):
            raise SGEPreparationError(f"{spec.transcript}: malformed cdot exon")
        exons.append(Exon(raw[0] + 1, raw[1]))
    exons.sort()
    if len(exons) != len(set(exons)):
        raise SGEPreparationError(f"{spec.transcript}: duplicate cdot exon")
    cds_start = build.get("cds_start")
    cds_end = build.get("cds_end")
    if cds_start is not None and (isinstance(cds_start, bool) or not isinstance(cds_start, int)):
        raise SGEPreparationError(f"{spec.transcript}: invalid CDS start")
    if cds_end is not None and (isinstance(cds_end, bool) or not isinstance(cds_end, int)):
        raise SGEPreparationError(f"{spec.transcript}: invalid CDS end")
    return Transcript(spec.transcript, spec.gene, chrom, strand, tuple(exons), cds_start, cds_end)


def validate_mavedb_metadata(payload: bytes, spec: GeneSpec) -> dict[str, Any]:
    """Validate canonical score-set identity and retain private source provenance."""

    try:
        record = json.loads(payload)
    except (UnicodeDecodeError, json.JSONDecodeError) as exc:
        raise SGEPreparationError(f"{spec.mavedb_urn}: invalid metadata JSON") from exc
    if not isinstance(record, dict) or record.get("urn") != spec.mavedb_urn:
        raise SGEPreparationError(f"{spec.mavedb_urn}: metadata identity mismatch")
    targets = record.get("targetGenes")
    experiment = record.get("experiment")
    license_record = record.get("license")
    if (
        not isinstance(targets, list)
        or len(targets) != 1
        or not isinstance(targets[0], dict)
        or targets[0].get("name") != spec.expected_target_name
        or not isinstance(experiment, dict)
        or not isinstance(license_record, dict)
    ):
        raise SGEPreparationError(f"{spec.mavedb_urn}: invalid target, experiment, or license")
    target_accession = targets[0].get("targetAccession")
    accession = target_accession.get("accession") if isinstance(target_accession, dict) else None
    if spec.transcript_policy == "declared" and accession != spec.transcript:
        raise SGEPreparationError(f"{spec.mavedb_urn}: declared target transcript mismatch")
    searchable = " ".join(
        str(value)
        for value in (
            record.get("title"),
            record.get("shortDescription"),
            experiment.get("title"),
            experiment.get("abstractText"),
        )
    ).lower()
    if "saturation genome" not in searchable and "saturation genome essentiality" not in searchable:
        raise SGEPreparationError(f"{spec.mavedb_urn}: score set is not described as SGE")
    keywords: dict[str, str] = {}
    for item in experiment.get("keywords") or []:
        keyword = item.get("keyword") if isinstance(item, dict) else None
        if (
            isinstance(keyword, dict)
            and isinstance(keyword.get("key"), str)
            and isinstance(keyword.get("label"), str)
        ):
            keywords[keyword["key"]] = keyword["label"]
    if keywords.get("Phenotypic Assay Mechanism") != "Loss of function":
        raise SGEPreparationError(f"{spec.mavedb_urn}: assay is not a loss-of-function screen")
    if keywords.get("Variant Library Creation Method") not in {
        None,
        "Endogenous locus library method",
    }:
        raise SGEPreparationError(f"{spec.mavedb_urn}: assay is not endogenous-locus SGE")
    for field in ("title", "modificationDate"):
        if not isinstance(record.get(field), str) or not record[field]:
            raise SGEPreparationError(f"{spec.mavedb_urn}: missing {field}")
    experiment_urn = experiment.get("urn")
    if not isinstance(experiment_urn, str) or not experiment_urn:
        raise SGEPreparationError(f"{spec.mavedb_urn}: missing experiment URN")
    publications = []
    for item in record.get("primaryPublicationIdentifiers") or []:
        if isinstance(item, dict):
            publications.append(
                {
                    key: item.get(key)
                    for key in ("dbName", "identifier", "doi", "url")
                    if item.get(key) is not None
                }
            )
    target_sequence = targets[0].get("targetSequence")
    return {
        "score_set_urn": spec.mavedb_urn,
        "experiment_urn": experiment_urn,
        "title": record["title"],
        "creation_date": record.get("creationDate"),
        "modification_date": record["modificationDate"],
        "published_date": record.get("publishedDate"),
        "license": {
            "short_name": license_record.get("shortName"),
            "url": license_record.get("link"),
        },
        "target": {
            "name": targets[0].get("name"),
            "mapped_hgnc_name": targets[0].get("mappedHgncName"),
            "accession": accession,
            "sequence": target_sequence.get("sequence")
            if isinstance(target_sequence, dict)
            else None,
        },
        "controlled_vocabulary": dict(sorted(keywords.items())),
        "publications": publications,
    }


def build_catalog_audit(payload: bytes, *, expected_records: int | None = None) -> dict[str, Any]:
    """Record every pinned MaveDB search hit and its deterministic curation decision."""

    try:
        document = json.loads(payload)
    except (UnicodeDecodeError, json.JSONDecodeError) as exc:
        raise SGEPreparationError("MaveDB catalog audit is not valid JSON") from exc
    rows = document.get("scoreSets") if isinstance(document, dict) else None
    if (
        not isinstance(rows, list)
        or document.get("numScoreSets") != len(rows)
        or len(rows)
        != (
            CONFIG.pins["catalog_audit"]["records"]
            if expected_records is None
            else expected_records
        )
    ):
        raise SGEPreparationError("MaveDB catalog audit count mismatch")
    selected: set[str] = set()
    records = []
    for row in rows:
        if not isinstance(row, dict) or not isinstance(row.get("urn"), str):
            raise SGEPreparationError("MaveDB catalog audit contains an invalid score set")
        targets = row.get("targetGenes") or []
        gene_names: set[str] = set()
        for target in targets:
            if isinstance(target, dict):
                name = target.get("mappedHgncName") or target.get("name")
                if isinstance(name, str):
                    gene_names.add(name)
        genes = sorted(gene_names)
        urn = row["urn"]
        if urn in SPEC_BY_URN:
            spec = SPEC_BY_URN[urn]
            if spec.gene not in genes and spec.expected_target_name not in genes:
                raise SGEPreparationError(f"catalog target mismatch for {urn}")
            decision = "selected"
            reason = "canonical score set selected by the reviewed SGE policy"
            selected.add(urn)
        elif set(genes) & set(SPEC_BY_GENE):
            decision = "excluded"
            reason = (
                "noncanonical duplicate, component, replicate, treatment, or superseded score set"
            )
        else:
            decision = "excluded"
            reason = "outside the reviewed provisional protein-coding SGE gene catalog"
        experiment = row.get("experiment")
        records.append(
            {
                "urn": urn,
                "title": row.get("title"),
                "genes": genes,
                "experiment_urn": experiment.get("urn") if isinstance(experiment, dict) else None,
                "published_date": row.get("publishedDate"),
                "modification_date": row.get("modificationDate"),
                "decision": decision,
                "reason": reason,
            }
        )
    if selected != set(SPEC_BY_URN):
        missing = sorted(set(SPEC_BY_URN) - selected)
        raise SGEPreparationError(f"catalog audit is missing canonical score sets: {missing}")
    return {
        "query": CONFIG.values["upstream"]["mavedb"]["catalog_query"],
        "records": sorted(records, key=lambda record: record["urn"]),
        "selected_score_sets": sorted(selected),
    }


def parse_score_csv(
    payload: bytes,
    spec: GeneSpec,
    *,
    mapper: Callable[[str], tuple[str, int, str, str] | None],
) -> tuple[tuple[Variant, ...], dict[str, Any]]:
    """Parse primary MaveDB scores, map complete alleles to GRCh38, and apply source-level QC."""

    try:
        text = payload.decode("utf-8-sig")
    except UnicodeDecodeError as exc:
        raise SGEPreparationError(f"{spec.mavedb_urn}: scores are not UTF-8") from exc
    reader = csv.DictReader(io.StringIO(text, newline=""))
    columns = reader.fieldnames
    if (
        not columns
        or len(columns) != len(set(columns))
        or not {"accession", "hgvs_nt", "score"} <= set(columns)
    ):
        raise SGEPreparationError(f"{spec.mavedb_urn}: invalid score column inventory")
    if spec.qc_field is not None and spec.qc_field not in columns:
        raise SGEPreparationError(f"{spec.mavedb_urn}: configured QC field is absent")
    excluded: Counter[str] = Counter()
    mapped: list[Variant] = []
    for row_number, row in enumerate(reader, start=2):
        if None in row:
            raise SGEPreparationError(f"{spec.mavedb_urn}:{row_number}: malformed CSV row")
        accession = row.get("accession", "")
        hgvs = row.get("hgvs_nt", "")
        if not accession.startswith(f"{spec.mavedb_urn}#"):
            raise SGEPreparationError(f"{spec.mavedb_urn}:{row_number}: accession mismatch")
        score = _finite_float(row.get("score"))
        if score is None:
            excluded["non_finite_score"] += 1
            continue
        if spec.qc_field is not None:
            qc_value = row.get(spec.qc_field, "")
            if qc_value in spec.qc_fail_values:
                excluded["source_qc_failure"] += 1
                continue
            if spec.qc_pass_values and qc_value not in spec.qc_pass_values:
                raise SGEPreparationError(
                    f"{spec.mavedb_urn}:{row_number}: unknown QC value {qc_value!r}"
                )
        mapped_key = _map_hgvs(hgvs, spec, mapper)
        if mapped_key is None:
            excluded["unsupported_or_unmapped_variant"] += 1
            continue
        chrom, pos, ref, alt = mapped_key
        if chrom != spec.expected_chrom:
            excluded["unexpected_chromosome"] += 1
            continue
        if pos < 1 or not ref or not alt or set(ref + alt) - NUCLEOTIDES or ref == alt:
            excluded["invalid_allele"] += 1
            continue
        mapped.append(
            Variant(
                spec.gene,
                accession,
                hgvs,
                chrom,
                pos,
                ref,
                alt,
                score,
                spec.score_direction * score,
                dict(row),
            )
        )
    by_key = Counter(variant.key for variant in mapped)
    unique = tuple(variant for variant in mapped if by_key[variant.key] == 1)
    if duplicate_count := len(mapped) - len(unique):
        excluded["duplicate_variant"] += duplicate_count
    return unique, {
        "source_records": sum(excluded.values()) + len(unique),
        "mapped_unique_allele_records": len(unique),
        "excluded": dict(sorted(excluded.items())),
        "source_columns": columns,
    }


def validate_reference_variants(
    variants: Sequence[Variant], *, genome: Callable[[str, int, int], str]
) -> tuple[Variant, ...]:
    """Verify the complete normalized REF of every mapped assay allele."""
    for variant in variants:
        observed = genome(variant.chrom, variant.pos - 1, variant.pos - 1 + len(variant.ref))
        if observed.upper() != variant.ref:
            raise SGEPreparationError(f"{variant.key_text}: genomic REF mismatch")
    return tuple(sorted(variants, key=lambda variant: variant.key))


def choose_panel(
    variants: Sequence[Variant],
    transcript: Transcript,
    *,
    genome: Callable[[str, int, int], str],
    seed: str = SAMPLING_SEED,
) -> tuple[Panel | None, list[dict[str, Any]]]:
    """Choose a complete-allele window by robust score range, then sample score bins."""
    windows = []
    summaries: list[dict[str, Any]] = []
    for exon in sorted(transcript.exons):
        start, end = exon.start - FLANK_BASES, exon.end + FLANK_BASES
        pool = [v for v in variants if start <= v.pos and v.pos + len(v.ref) - 1 <= end]
        scores = sorted(v.damage_score for v in pool)
        spread = quantile(scores, 0.95) - quantile(scores, 0.05) if scores else 0.0
        reason = None
        if start < 1:
            reason = "window_outside_reference"
        elif len(pool) < PANEL_SIZE:
            reason = "insufficient_alleles"
        elif quantile(scores, 0.01) >= quantile(scores, 0.99):
            reason = "collapsed_score_anchors"
        summaries.append(
            {
                "exon_start": exon.start,
                "exon_end": exon.end,
                "eligible_records": len(pool),
                "robust_score_spread_p95_minus_p05": spread,
                "exclusion_reason": reason,
            }
        )
        if reason is None:
            windows.append((exon, pool, spread))
    if not windows:
        return None, summaries
    exon, pool, spread = min(windows, key=lambda w: (-w[2], -len(w[1]), w[0]))
    start, end = exon.start - FLANK_BASES, exon.end + FLANK_BASES
    sequence = genome(transcript.chrom, start - 1, end).upper()
    if len(sequence) != end - start + 1 or set(sequence) - NUCLEOTIDES:
        raise SGEPreparationError(f"{transcript.gene}: invalid exon-window sequence")
    if transcript.strand == "-":
        sequence = reverse_complement(sequence)
    sampled = sample_score_bins(
        [ScoredAllele(v.key_text, v.damage_score) for v in pool],
        question_key=f"sge:{transcript.gene}:{exon.start}-{exon.end}:all_alleles",
        seed=seed,
    )
    by_key = {v.key_text: v for v in pool}
    display = []
    for allele, bin_index in sampled.selected:
        variant = by_key[allele.key]
        pos, ref, alt = variant.pos - start + 1, variant.ref, variant.alt
        if transcript.strand == "-":
            pos = end - (variant.pos + len(variant.ref) - 1) + 1
            ref, alt = reverse_complement(ref), reverse_complement(alt)
        pos, ref, alt = normalize_allele(sequence, pos, ref, alt)
        display.append((pos, ref, alt, variant, bin_index))
    display.sort(key=lambda item: item[:3])
    if len({item[:3] for item in display}) != PANEL_SIZE:
        raise SGEPreparationError(f"{transcript.gene}: duplicate displayed allele")
    candidates = tuple(
        PanelCandidate(v, f"V{i:02d}", pos, ref, alt, b)
        for i, (pos, ref, alt, v, b) in enumerate(display, 1)
    )
    return Panel(exon, start, end, sequence, candidates, sampled, spread), summaries


def build_source_record(
    spec: GeneSpec,
    transcript: Transcript,
    panel: Panel,
    *,
    source_provenance: Mapping[str, Any],
    population_summary: Mapping[str, Any],
    exon_summaries: Sequence[Mapping[str, Any]],
) -> dict[str, Any]:
    """Build one compact ranking source while keeping all selection fields private."""

    candidates = []
    selected_provenance = []
    for candidate in panel.candidates:
        variant = candidate.variant
        candidates.append(
            {
                "candidate_id": candidate.candidate_id,
                "chrom": REFERENCE_CONTIG,
                "pos": candidate.local_pos,
                "ref": candidate.visible_ref,
                "alt": candidate.visible_alt,
                "reference_score": variant.damage_score,
            }
        )
        selected_provenance.append(
            {
                "candidate_id": candidate.candidate_id,
                "source_accession": variant.source_accession,
                "source_hgvs": variant.source_hgvs,
                "genomic_key": variant.key_text,
                "source_score": variant.source_score,
                "damage_score": variant.damage_score,
                "score_bin": candidate.score_bin,
                "sampling_digest": sampling_digest(
                    SAMPLING_SEED,
                    f"sge:{spec.gene}:{panel.exon.start}-{panel.exon.end}:all_alleles",
                    candidate.score_bin,
                    variant.key_text,
                ),
            }
        )
    return {
        "source_dataset": SOURCE_DATASET,
        "source_record_id": spec.gene,
        "assay_context": spec.assay_context,
        "reference_sequence": panel.sequence,
        "reporter_context": (
            "The local endogenous sequence is displayed in transcript 5-prime to 3-prime "
            "orientation."
        ),
        "candidates": candidates,
        "task_family": TASK_FAMILY,
        "tags": ["coding", "grch38", "quantitative", "ranking", "sge", "splicing"],
        "source_metadata": {
            "display_name": spec.gene,
            "score_set": dict(source_provenance),
            "transcript": {
                "accession": transcript.accession,
                "policy": spec.transcript_policy,
                "chrom": transcript.chrom,
                "strand": transcript.strand,
            },
            "score_direction": spec.score_direction,
            "score_direction_evidence": spec.score_direction_evidence,
            "population": dict(population_summary),
            "exon_selection": {
                "flank_bases": FLANK_BASES,
                "selected_exon": {"start": panel.exon.start, "end": panel.exon.end},
                "selected_window": {"start": panel.window_start, "end": panel.window_end},
                "robust_score_spread_p95_minus_p05": panel.robust_score_spread,
                "all_exon_windows": [dict(summary) for summary in exon_summaries],
            },
            "sampling": {
                "algorithm": SAMPLING_ALGORITHM,
                "seed": SAMPLING_SEED,
                **panel.sampling.provenance(),
            },
            "selected_candidates": selected_provenance,
        },
    }


def write_prepared_dataset(
    records: Sequence[Mapping[str, Any]],
    *,
    output: str | Path,
    manifest_output: str | Path,
    manifest: Mapping[str, Any],
) -> tuple[int, str]:
    """Write deterministic compact source JSONL and its completion manifest."""

    values = sorted(
        (dict(record) for record in records), key=lambda record: record["source_record_id"]
    )
    output_path = Path(output)
    output_path.parent.mkdir(parents=True, exist_ok=True)
    output_path.write_text(
        "".join(f"{canonical_json(record)}\n" for record in values),
        encoding="utf-8",
        newline="\n",
    )
    digest = sha256_file(output_path)
    document = {
        **dict(manifest),
        "output": {
            "path": output_path.name,
            "records": len(values),
            "bytes": output_path.stat().st_size,
            "sha256": digest,
        },
    }
    manifest_path = Path(manifest_output)
    manifest_path.parent.mkdir(parents=True, exist_ok=True)
    manifest_path.write_text(f"{canonical_json(document)}\n", encoding="utf-8", newline="\n")
    validate_prepared_artifacts(output_path, manifest_path)
    return len(values), digest


def validate_prepared_artifacts(
    source: str | Path,
    manifest: str | Path,
) -> dict[str, Any]:
    """Validate a generated compact SGE source and its digest manifest offline."""

    source_path = Path(source)
    manifest_path = Path(manifest)
    try:
        document = json.loads(manifest_path.read_text(encoding="utf-8"))
    except (OSError, json.JSONDecodeError) as exc:
        raise SGEPreparationError(f"{manifest_path}: invalid SGE manifest") from exc
    if (
        not isinstance(document, dict)
        or document.get("schema_version") != "1.0"
        or document.get("kind") != "vepbench_sge_prepared_source"
        or document.get("task_family") != TASK_FAMILY
    ):
        raise SGEPreparationError(f"{manifest_path}: invalid SGE manifest identity")
    output = document.get("output")
    if (
        not isinstance(output, dict)
        or output.get("path") != source_path.name
        or output.get("bytes") != source_path.stat().st_size
        or output.get("sha256") != sha256_file(source_path)
    ):
        raise SGEPreparationError("SGE source digest or size mismatch")
    records = read_jsonl(source_path)
    if output.get("records") != len(records) or not records:
        raise SGEPreparationError("SGE source record count mismatch")
    population = document.get("population")
    catalog = document.get("catalog_audit")
    if not isinstance(population, dict) or not isinstance(catalog, dict):
        raise SGEPreparationError("SGE manifest is missing population or catalog audit")
    included = sorted(
        gene for gene, summary in population.items() if summary.get("status") == "included"
    )
    if included != [record.get("source_record_id") for record in records]:
        raise SGEPreparationError("SGE included-gene manifest does not match source records")
    if len(catalog.get("selected_score_sets", [])) != len(GENE_SPECS):
        raise SGEPreparationError("SGE catalog audit does not contain the canonical score sets")
    for record in records:
        _validate_source_record(record)
    return document


def eligible_cache_rows(variants_by_gene: Mapping[str, Sequence[Variant]]) -> list[dict[str, Any]]:
    """Serialize the complete post-eligibility population for immutable caching."""

    rows = []
    for gene, variants in variants_by_gene.items():
        for variant in variants:
            rows.append(
                {
                    "gene": gene,
                    "source_accession": variant.source_accession,
                    "source_hgvs": variant.source_hgvs,
                    "chrom": variant.chrom,
                    "pos": variant.pos,
                    "ref": variant.ref,
                    "alt": variant.alt,
                    "source_score": variant.source_score,
                    "damage_score": variant.damage_score,
                    "source_fields": variant.source_fields,
                }
            )
    return sorted(rows, key=lambda row: (row["gene"], row["pos"], row["ref"], row["alt"]))


def transcript_coding_sequence(
    transcript: Transcript, genome: Callable[[str, int, int], str]
) -> str:
    """Reconstruct the transcript-oriented CDS for target-sequence validation."""

    if transcript.cds_start0 is None or transcript.cds_end0 is None:
        raise SGEPreparationError(f"{transcript.accession}: transcript has no CDS")
    pieces = []
    for exon in transcript.exons:
        start0 = max(exon.start - 1, transcript.cds_start0)
        end0 = min(exon.end, transcript.cds_end0)
        if start0 < end0:
            pieces.append(genome(transcript.chrom, start0, end0).upper())
    sequence = "".join(pieces)
    return sequence if transcript.strand == "+" else reverse_complement(sequence)


def reverse_complement(sequence: str) -> str:
    return sequence.translate(str.maketrans("ACGT", "TGCA"))[::-1]


def _map_hgvs(
    hgvs: str,
    spec: GeneSpec,
    mapper: Callable[[str], tuple[str, int, str, str] | None],
) -> tuple[str, int, str, str] | None:
    if spec.coordinate_mode == "target_coding_hgvs":
        if not hgvs.startswith("n."):
            return None
        hgvs = f"{spec.transcript}:c.{hgvs[2:]}"
    elif (
        spec.coordinate_mode == "hgvs_transcript" and not hgvs.startswith(f"{spec.transcript}:c.")
    ) or (spec.coordinate_mode == "hgvs_genomic" and ":g." not in hgvs):
        return None
    return mapper(hgvs)


def _nc_contig_to_chrom(contig: Any) -> str | None:
    if not isinstance(contig, str):
        return None
    match = re.match(r"^NC_0*(\d+)\.", contig)
    if match is None:
        return None
    value = int(match.group(1))
    return "X" if value == 23 else "Y" if value == 24 else str(value)


def _finite_float(value: Any) -> float | None:
    try:
        number = float(value)
    except TypeError, ValueError, OverflowError:
        return None
    return number if math.isfinite(number) else None


def _validate_source_record(record: Mapping[str, Any]) -> None:
    label = record.get("source_record_id")
    spec = SPEC_BY_GENE.get(label) if isinstance(label, str) else None
    if spec is None or record.get("task_family") != TASK_FAMILY:
        raise SGEPreparationError(f"invalid SGE source identity {label!r}")
    sequence = record.get("reference_sequence")
    candidates = record.get("candidates")
    metadata = record.get("source_metadata")
    if (
        not isinstance(sequence, str)
        or not sequence
        or sequence != sequence.upper()
        or set(sequence) - NUCLEOTIDES
        or not isinstance(candidates, list)
        or len(candidates) != PANEL_SIZE
        or not isinstance(metadata, dict)
    ):
        raise SGEPreparationError(f"{label}: invalid sequence, candidates, or metadata")
    exon_selection = metadata.get("exon_selection")
    selected = metadata.get("selected_candidates")
    transcript = metadata.get("transcript")
    if (
        not isinstance(exon_selection, dict)
        or not isinstance(selected, list)
        or not isinstance(transcript, dict)
    ):
        raise SGEPreparationError(f"{label}: missing private provenance")
    exon = exon_selection.get("selected_exon")
    window = exon_selection.get("selected_window")
    if (
        not isinstance(exon, dict)
        or not isinstance(window, dict)
        or len(sequence) != exon.get("end", 0) - exon.get("start", 0) + 1 + 2 * FLANK_BASES
        or window.get("start") != exon.get("start", 0) - FLANK_BASES
        or window.get("end") != exon.get("end", 0) + FLANK_BASES
    ):
        raise SGEPreparationError(f"{label}: displayed exon window is invalid")
    expected_ids = [f"V{index:02d}" for index in range(1, PANEL_SIZE + 1)]
    if [candidate.get("candidate_id") for candidate in candidates] != expected_ids:
        raise SGEPreparationError(f"{label}: candidate IDs or display order are invalid")
    if [candidate.get("candidate_id") for candidate in selected] != expected_ids:
        raise SGEPreparationError(f"{label}: private candidate provenance is misaligned")
    visible_keys = []
    for candidate, private in zip(candidates, selected, strict=True):
        pos = candidate.get("pos")
        ref = candidate.get("ref")
        alt = candidate.get("alt")
        if (
            candidate.get("chrom") != REFERENCE_CONTIG
            or isinstance(pos, bool)
            or not isinstance(pos, int)
            or pos < 1
            or not isinstance(ref, str)
            or not ref
            or not isinstance(alt, str)
            or not alt
            or set(ref + alt) - NUCLEOTIDES
            or ref == alt
            or sequence[pos - 1 : pos - 1 + len(ref)] != ref
            or normalize_allele(sequence, pos, ref, alt) != (pos, ref, alt)
            or candidate.get("reference_score") != private.get("damage_score")
            or private.get("damage_score") != spec.score_direction * private.get("source_score")
        ):
            raise SGEPreparationError(f"{label}: invalid selected candidate {candidate!r}")
        expected_digest = sampling_digest(
            SAMPLING_SEED,
            f"sge:{spec.gene}:{exon['start']}-{exon['end']}:all_alleles",
            private["score_bin"],
            private["genomic_key"],
        )
        if private.get("sampling_digest") != expected_digest:
            raise SGEPreparationError(f"{label}: sampling digest mismatch")
        chrom, genomic_pos, genomic_ref, genomic_alt = private["genomic_key"].split(":")
        original_pos = int(genomic_pos)
        projected_pos = original_pos - window["start"] + 1
        if transcript["strand"] == "-":
            projected_pos = window["end"] - (original_pos + len(genomic_ref) - 1) + 1
            genomic_ref, genomic_alt = (
                reverse_complement(genomic_ref),
                reverse_complement(genomic_alt),
            )
        if (
            chrom != transcript["chrom"]
            or transcript["strand"] not in {"+", "-"}
            or normalize_allele(sequence, projected_pos, genomic_ref, genomic_alt)
            != (pos, ref, alt)
        ):
            raise SGEPreparationError(f"{label}: genomic allele projection mismatch")
        visible_keys.append((pos, ref, alt))
    if visible_keys != sorted(visible_keys) or len(set(visible_keys)) != PANEL_SIZE:
        raise SGEPreparationError(f"{label}: candidates are not unique in display order")
    sampling = metadata.get("sampling", {})
    from vepbench.sampling import validate_sampling_provenance

    validate_sampling_provenance(
        sampling,
        [(item["damage_score"], item["score_bin"]) for item in selected],
        seed=SAMPLING_SEED,
        algorithm=SAMPLING_ALGORITHM,
    )
    prompt_fields = f"{record.get('assay_context', '')}\n{record.get('reporter_context', '')}"
    forbidden = [spec.mavedb_urn, spec.transcript, "urn:mavedb", "reference_score", "quantile"]
    if any(token in prompt_fields for token in forbidden) or re.search(
        r"\bexons?\s+\d", prompt_fields, flags=re.IGNORECASE
    ):
        raise SGEPreparationError(f"{label}: model-visible source contains private leakage")
