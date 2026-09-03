"""Deterministic curation and validation for the continuous SGE task."""

from __future__ import annotations

import bisect
import csv
import gzip
import hashlib
import io
import json
import math
import re
from collections import Counter
from collections.abc import Callable, Iterable, Mapping, Sequence
from dataclasses import dataclass, replace
from pathlib import Path
from typing import Any

from vepbench.artifacts import canonical_json, read_jsonl, sha256_file
from vepbench.errors import BuildError

from .configuration import CONFIG

TASK_FAMILY = CONFIG.values["task_family"]
SOURCE_DATASET = CONFIG.values["source_dataset"]
REFERENCE_CONTIG = CONFIG.values["reference_contig"]
PANEL_SIZE = CONFIG.values["sampling"]["panel_size"]
PREFERRED_PER_CLASS = CONFIG.values["sampling"]["preferred_per_class"]
QUANTILE_BINS = CONFIG.values["sampling"]["quantile_bins"]
SAMPLING_SEED = CONFIG.values["sampling"]["seed"]
SAMPLING_ALGORITHM = CONFIG.values["sampling"]["algorithm"]
FLANK_BASES = CONFIG.values["sequence"]["flank_bases"]
EXON_PROXIMAL_DISTANCE = CONFIG.values["eligibility"]["exon_proximal_distance"]
EXCLUDED_CONSEQUENCES = frozenset(CONFIG.values["eligibility"]["excluded_consequences"])
SPLICING_CONSEQUENCES = frozenset(CONFIG.values["eligibility"]["splicing_consequences"])
RETAINED_GROUPS = frozenset(CONFIG.values["eligibility"]["retained_groups"])
NUCLEOTIDES = frozenset("ACGT")

GENOMIC_SNV = re.compile(r"^(NC_0*(\d+)\.\d+):g\.(\d+)([ACGT])>([ACGT])$")
TRANSCRIPT_SNV = re.compile(r"^[^:]+:c\..*([ACGT])>([ACGT])$")
TARGET_SNV = re.compile(r"^n\.(.+)([ACGT])>([ACGT])$")


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

    @property
    def stable_key(self) -> str:
        return f"{self.accession}:{self.chrom}:{self.strand}"


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
    consequence: str | None = None
    consequence_final: str | None = None
    consequence_group: str | None = None
    nearest_exon_distance: int | None = None

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
    quantile_bin: int


@dataclass(frozen=True)
class Panel:
    exon: Exon
    window_start: int
    window_end: int
    sequence: str
    candidates: tuple[PanelCandidate, ...]
    missense_allocation: int
    splicing_allocation: int
    robust_score_spread: float


class ExonIndex:
    """Bounded nearest-exon lookup over merged 1-based inclusive intervals."""

    def __init__(self, intervals_by_chrom: Mapping[str, Sequence[Exon]]) -> None:
        merged: dict[str, tuple[Exon, ...]] = {}
        starts: dict[str, tuple[int, ...]] = {}
        for chrom, intervals in intervals_by_chrom.items():
            values: list[Exon] = []
            for exon in sorted(intervals):
                if not values or exon.start > values[-1].end + 1:
                    values.append(exon)
                else:
                    values[-1] = Exon(values[-1].start, max(values[-1].end, exon.end))
            merged[chrom] = tuple(values)
            starts[chrom] = tuple(exon.start for exon in values)
        self._intervals = merged
        self._starts = starts

    def distance(self, chrom: str, pos: int) -> int:
        intervals = self._intervals.get(chrom)
        if not intervals:
            raise SGEPreparationError(f"{chrom}:{pos}: no exon annotation for chromosome")
        index = bisect.bisect_right(self._starts[chrom], pos)
        candidates = []
        if index:
            previous = intervals[index - 1]
            candidates.append(0 if pos <= previous.end else pos - previous.end)
        if index < len(intervals):
            candidates.append(intervals[index].start - pos)
        return min(candidates)


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
        if isinstance(keyword, dict) and isinstance(keyword.get("key"), str) and isinstance(
            keyword.get("label"), str
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
        elif "CARD11" in genes:
            decision = "excluded"
            reason = "CARD11 policy exclusion: predominantly multi-base codon substitutions"
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
    """Parse primary MaveDB scores, map SNVs to GRCh38, and apply source-level QC."""

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
        if pos < 1 or ref not in NUCLEOTIDES or alt not in NUCLEOTIDES or ref == alt:
            excluded["invalid_snv"] += 1
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
        "mapped_unique_snv_records": len(unique),
        "excluded": dict(sorted(excluded.items())),
        "source_columns": columns,
    }


def annotate_and_filter_variants(
    variants: Sequence[Variant],
    *,
    consequences: Mapping[tuple[str, int, str, str], str],
    exon_index: ExonIndex,
    genome: Callable[[str, int, int], str],
) -> tuple[tuple[Variant, ...], dict[str, int]]:
    """Validate REF, apply the pinned consequence policy, and retain two groups."""

    eligible = []
    excluded: Counter[str] = Counter()
    for variant in variants:
        observed_ref = genome(variant.chrom, variant.pos - 1, variant.pos).upper()
        if observed_ref != variant.ref:
            excluded["reference_mismatch"] += 1
            continue
        consequence = consequences.get(variant.key)
        if consequence is None:
            excluded["missing_consequence"] += 1
            continue
        if consequence in EXCLUDED_CONSEQUENCES:
            excluded["excluded_high_impact_consequence"] += 1
            continue
        distance = exon_index.distance(variant.chrom, variant.pos)
        consequence_final = (
            "exon_proximal"
            if consequence == "intron_variant" and distance <= EXON_PROXIMAL_DISTANCE
            else consequence
        )
        if consequence_final == "missense_variant":
            group = "missense_variant"
        elif consequence_final in SPLICING_CONSEQUENCES:
            group = "splicing"
        else:
            excluded["unretained_consequence"] += 1
            continue
        eligible.append(
            replace(
                variant,
                consequence=consequence,
                consequence_final=consequence_final,
                consequence_group=group,
                nearest_exon_distance=distance,
            )
        )
    return tuple(sorted(eligible, key=lambda variant: variant.key)), dict(sorted(excluded.items()))


def choose_panel(
    variants: Sequence[Variant],
    transcript: Transcript,
    *,
    genome: Callable[[str, int, int], str],
    seed: str = SAMPLING_SEED,
) -> tuple[Panel | None, list[dict[str, Any]]]:
    """Choose one exon and a class-balanced, score-quantile-covered 50-SNV panel."""

    windows = []
    summaries = []
    for exon in transcript.exons:
        window_start = exon.start - FLANK_BASES
        window_end = exon.end + FLANK_BASES
        pool = tuple(
            variant
            for variant in variants
            if variant.chrom == transcript.chrom and window_start <= variant.pos <= window_end
        )
        counts = Counter(variant.consequence_group for variant in pool)
        missense = counts["missense_variant"]
        splicing = counts["splicing"]
        allocation = _class_allocation(missense, splicing)
        spread = _robust_spread([variant.damage_score for variant in pool]) if pool else 0.0
        qualifies = allocation is not None
        summary = {
            "exon_start": exon.start,
            "exon_end": exon.end,
            "eligible_records": len(pool),
            "missense_records": missense,
            "splicing_records": splicing,
            "can_supply_preferred_balance": missense >= PREFERRED_PER_CLASS
            and splicing >= PREFERRED_PER_CLASS,
            "achievable_smaller_class": min(PREFERRED_PER_CLASS, missense, splicing),
            "robust_score_spread_p95_minus_p05": spread,
            "qualifies": qualifies,
        }
        summaries.append(summary)
        if qualifies:
            windows.append((exon, pool, allocation, spread, summary))
    if not windows:
        return None, summaries
    windows.sort(
        key=lambda item: (
            -int(item[4]["can_supply_preferred_balance"]),
            -item[4]["achievable_smaller_class"],
            -item[3],
            transcript.stable_key,
            item[0].start,
            item[0].end,
        )
    )
    exon, pool, allocation, spread, chosen_summary = windows[0]
    chosen_summary["selected"] = True
    assert allocation is not None
    missense_allocation, splicing_allocation = allocation
    selected: list[tuple[Variant, int]] = []
    for group, count in (
        ("missense_variant", missense_allocation),
        ("splicing", splicing_allocation),
    ):
        class_pool = [variant for variant in pool if variant.consequence_group == group]
        selected.extend(
            _sample_score_quantiles(
                class_pool,
                count,
                seed=seed,
                gene=transcript.gene,
                exon=exon,
                consequence_group=group,
            )
        )
    window_start = exon.start - FLANK_BASES
    window_end = exon.end + FLANK_BASES
    genomic_sequence = genome(transcript.chrom, window_start - 1, window_end).upper()
    if (
        len(genomic_sequence) != exon.length + 2 * FLANK_BASES
        or set(genomic_sequence) - NUCLEOTIDES
    ):
        raise SGEPreparationError(f"{transcript.gene}: invalid selected exon sequence")
    display_sequence = (
        genomic_sequence if transcript.strand == "+" else reverse_complement(genomic_sequence)
    )
    visible = []
    for variant, quantile_bin in selected:
        if transcript.strand == "+":
            local_pos = variant.pos - window_start + 1
            ref, alt = variant.ref, variant.alt
        else:
            local_pos = window_end - variant.pos + 1
            ref, alt = reverse_complement(variant.ref), reverse_complement(variant.alt)
        if display_sequence[local_pos - 1] != ref:
            raise SGEPreparationError(f"{variant.key_text}: visible REF does not match sequence")
        visible.append((local_pos, ref, alt, variant, quantile_bin))
    visible.sort(key=lambda item: (item[0], item[1], item[2]))
    candidates = tuple(
        PanelCandidate(variant, f"V{index:02d}", local_pos, ref, alt, quantile_bin)
        for index, (local_pos, ref, alt, variant, quantile_bin) in enumerate(visible, start=1)
    )
    if len(candidates) != PANEL_SIZE:
        raise AssertionError("SGE panel selection did not produce 50 variants")
    return (
        Panel(
            exon,
            window_start,
            window_end,
            display_sequence,
            candidates,
            missense_allocation,
            splicing_allocation,
            spread,
        ),
        summaries,
    )


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
                "consequence": variant.consequence,
                "consequence_final": variant.consequence_final,
                "consequence_group": variant.consequence_group,
                "nearest_exon_distance": variant.nearest_exon_distance,
                "quantile_bin": candidate.quantile_bin,
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
                "class_allocation": {
                    "missense_variant": panel.missense_allocation,
                    "splicing": panel.splicing_allocation,
                },
                "robust_score_spread_p95_minus_p05": panel.robust_score_spread,
                "all_exon_windows": [dict(summary) for summary in exon_summaries],
            },
            "sampling": {
                "algorithm": SAMPLING_ALGORITHM,
                "seed": SAMPLING_SEED,
                "quantile_bins": QUANTILE_BINS,
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
                    "consequence": variant.consequence,
                    "consequence_final": variant.consequence_final,
                    "consequence_group": variant.consequence_group,
                    "nearest_exon_distance": variant.nearest_exon_distance,
                    "source_fields": variant.source_fields,
                }
            )
    return sorted(rows, key=lambda row: (row["gene"], row["pos"], row["ref"], row["alt"]))


def parse_gtf_exons(payload: bytes, chromosomes: set[str]) -> ExonIndex:
    """Parse and merge exon intervals from the pinned compressed Ensembl GTF."""

    try:
        text = gzip.decompress(payload).decode("utf-8")
    except (gzip.BadGzipFile, UnicodeDecodeError) as exc:
        raise SGEPreparationError("invalid Ensembl GTF payload") from exc
    return _exon_index_from_gtf_lines(text.splitlines(), chromosomes)


def parse_gtf_exon_file(path: str | Path, chromosomes: set[str]) -> ExonIndex:
    """Stream a pinned Ensembl GTF instead of expanding it in memory."""

    try:
        with gzip.open(path, mode="rt", encoding="utf-8") as source:
            return _exon_index_from_gtf_lines(source, chromosomes)
    except (OSError, UnicodeDecodeError) as exc:
        raise SGEPreparationError("invalid Ensembl GTF file") from exc


def _exon_index_from_gtf_lines(lines: Iterable[str], chromosomes: set[str]) -> ExonIndex:
    intervals: dict[str, list[Exon]] = {chrom: [] for chrom in chromosomes}
    for line in lines:
        if not line or line.startswith("#"):
            continue
        fields = line.split("\t", 8)
        if len(fields) != 9 or fields[2] != "exon":
            continue
        chrom = fields[0]
        if chrom not in intervals:
            continue
        start, end = int(fields[3]), int(fields[4])
        intervals[chrom].append(Exon(start, end))
    if any(not values for values in intervals.values()):
        raise SGEPreparationError("Ensembl GTF is missing a required chromosome")
    return ExonIndex(intervals)


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
    if spec.coordinate_mode == "hgvs_genomic":
        match = GENOMIC_SNV.fullmatch(hgvs)
        if match is None:
            return None
        chrom = _nc_contig_to_chrom(match.group(1))
        return (
            (chrom, int(match.group(3)), match.group(4), match.group(5))
            if chrom is not None
            else None
        )
    if spec.coordinate_mode == "hgvs_transcript":
        if TRANSCRIPT_SNV.fullmatch(hgvs) is None or not hgvs.startswith(f"{spec.transcript}:c."):
            return None
        return mapper(hgvs)
    match = TARGET_SNV.fullmatch(hgvs)
    if match is None:
        return None
    return mapper(f"{spec.transcript}:c.{match.group(1)}{match.group(2)}>{match.group(3)}")


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
    except (TypeError, ValueError, OverflowError):
        return None
    return number if math.isfinite(number) else None


def _class_allocation(missense: int, splicing: int) -> tuple[int, int] | None:
    if missense + splicing < PANEL_SIZE:
        return None
    if missense >= PREFERRED_PER_CLASS and splicing >= PREFERRED_PER_CLASS:
        return (PREFERRED_PER_CLASS, PREFERRED_PER_CLASS)
    smaller = min(missense, splicing)
    smaller_allocation = min(PREFERRED_PER_CLASS, smaller)
    if missense <= splicing:
        allocation = (smaller_allocation, PANEL_SIZE - smaller_allocation)
    else:
        allocation = (PANEL_SIZE - smaller_allocation, smaller_allocation)
    return allocation if allocation[0] <= missense and allocation[1] <= splicing else None


def _sample_score_quantiles(
    variants: Sequence[Variant],
    count: int,
    *,
    seed: str,
    gene: str,
    exon: Exon,
    consequence_group: str,
) -> list[tuple[Variant, int]]:
    if count == 0:
        return []
    if len(variants) < count:
        raise SGEPreparationError(f"{gene}: class allocation exceeds eligible pool")
    ordered = sorted(variants, key=lambda variant: (variant.damage_score, variant.key))
    bins = min(QUANTILE_BINS, count)
    pool_base, pool_remainder = divmod(len(ordered), bins)
    take_base, take_remainder = divmod(count, bins)
    selected: list[tuple[Variant, int]] = []
    offset = 0
    for bin_index in range(bins):
        size = pool_base + (bin_index < pool_remainder)
        take = take_base + (bin_index < take_remainder)
        values = ordered[offset : offset + size]
        offset += size
        values.sort(
            key=lambda variant: _sample_digest(
                seed,
                gene,
                str(exon.start),
                str(exon.end),
                consequence_group,
                str(bin_index + 1),
                variant.key_text,
            )
        )
        selected.extend((variant, bin_index + 1) for variant in values[:take])
    if offset != len(ordered) or len(selected) != count:
        raise AssertionError("rank-quantile sampling did not consume the class pool")
    return selected


def _sample_digest(seed: str, *parts: str) -> bytes:
    return hashlib.sha256("\0".join((SAMPLING_ALGORITHM, seed, *parts)).encode()).digest()


def _robust_spread(values: Sequence[float]) -> float:
    ordered = sorted(values)
    return _percentile(ordered, 0.95) - _percentile(ordered, 0.05)


def _percentile(ordered: Sequence[float], fraction: float) -> float:
    """R type-7 linear percentile, specified here to avoid library-dependent behavior."""

    if not ordered:
        raise ValueError("percentile requires a non-empty sequence")
    position = (len(ordered) - 1) * fraction
    lower = math.floor(position)
    upper = math.ceil(position)
    if lower == upper:
        return ordered[lower]
    weight = position - lower
    return ordered[lower] * (1 - weight) + ordered[upper] * weight


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
    if not isinstance(exon_selection, dict) or not isinstance(selected, list) or not isinstance(
        transcript, dict
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
            or ref not in NUCLEOTIDES
            or alt not in NUCLEOTIDES
            or ref == alt
            or sequence[pos - 1] != ref
            or private.get("consequence") in EXCLUDED_CONSEQUENCES
            or private.get("consequence_group") not in RETAINED_GROUPS
            or candidate.get("reference_score") != private.get("damage_score")
            or private.get("damage_score") != spec.score_direction * private.get("source_score")
        ):
            raise SGEPreparationError(f"{label}: invalid selected candidate {candidate!r}")
        visible_keys.append((pos, ref, alt))
    if visible_keys != sorted(visible_keys) or len(set(visible_keys)) != PANEL_SIZE:
        raise SGEPreparationError(f"{label}: candidates are not unique in display order")
    allocations = dict(sorted(Counter(item["consequence_group"] for item in selected).items()))
    if allocations != exon_selection.get("class_allocation"):
        raise SGEPreparationError(f"{label}: class allocation provenance mismatch")
    prompt_fields = f"{record.get('assay_context', '')}\n{record.get('reporter_context', '')}"
    forbidden = [spec.mavedb_urn, spec.transcript, "urn:mavedb", "reference_score", "quantile"]
    if any(token in prompt_fields for token in forbidden) or re.search(
        r"\bexons?\s+\d", prompt_fields, flags=re.IGNORECASE
    ):
        raise SGEPreparationError(f"{label}: model-visible source contains private leakage")


__all__ = [
    "EXCLUDED_CONSEQUENCES",
    "EXON_PROXIMAL_DISTANCE",
    "GENE_SPECS",
    "PANEL_SIZE",
    "Exon",
    "ExonIndex",
    "GeneSpec",
    "Panel",
    "PanelCandidate",
    "SGEPreparationError",
    "Transcript",
    "Variant",
    "annotate_and_filter_variants",
    "build_catalog_audit",
    "build_source_record",
    "choose_panel",
    "eligible_cache_rows",
    "parse_gtf_exon_file",
    "parse_gtf_exons",
    "parse_score_csv",
    "reverse_complement",
    "transcript_coding_sequence",
    "transcript_from_cdot",
    "validate_mavedb_metadata",
    "validate_prepared_artifacts",
    "write_prepared_dataset",
]
