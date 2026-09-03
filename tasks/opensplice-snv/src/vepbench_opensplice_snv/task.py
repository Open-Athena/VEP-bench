"""Deterministic selection and artifact validation for the OpenSplice SNV task."""

from __future__ import annotations

import csv
import hashlib
import json
import math
from collections import Counter, defaultdict
from collections.abc import Iterable, Mapping, Sequence
from dataclasses import asdict, dataclass
from pathlib import Path
from typing import Any, TextIO, cast

from vepbench.artifacts import canonical_json, read_jsonl, sha256_file
from vepbench.errors import BuildError

from .configuration import CONFIG, cache_configuration, cache_key

TASK_FAMILY = CONFIG.values["task_family"]
SOURCE_DATASET = CONFIG.values["source_dataset"]
REFERENCE_CONTIG = CONFIG.values["reference_contig"]
EXON_COUNT = CONFIG.values["sampling"]["exon_count"]
PANEL_SIZE = CONFIG.values["sampling"]["panel_size"]
QUANTILE_BINS = CONFIG.values["sampling"]["quantile_bins"]
SAMPLES_PER_BIN = CONFIG.values["sampling"]["samples_per_bin"]
SAMPLING_SEED = CONFIG.values["sampling"]["seed"]
SAMPLING_ALGORITHM = CONFIG.values["sampling"]["algorithm"]
FAS_E5 = CONFIG.values["reporter"]["fas_e5"]
FAS_I5 = CONFIG.values["reporter"]["fas_i5"]
FAS_I6 = CONFIG.values["reporter"]["fas_i6"]
FAS_E7 = CONFIG.values["reporter"]["fas_e7"]
FIXED_PREFIX = FAS_E5 + FAS_I5
FIXED_SUFFIX = FAS_I6 + FAS_E7
DOWNSTREAM_NATIVE_FLANK = CONFIG.values["reporter"]["downstream_native_flank"]
ALPHAGENOME = CONFIG.values["alphagenome"]
MISSING_NUMBERS = {"", "NA", "N/A", "null", "None"}

GENOME_PREDICTION_COLUMNS = {
    "variant_id": "alphagenome_genome__variant_id",
    "acceptor_ref_at_canonical": "alphagenome_genome__acceptor_ref_at_canonical",
    "acceptor_alt_at_canonical": "alphagenome_genome__acceptor_alt_at_canonical",
    "donor_ref_at_canonical": "alphagenome_genome__donor_ref_at_canonical",
    "donor_alt_at_canonical": "alphagenome_genome__donor_alt_at_canonical",
    "delta_acceptor": "alphagenome_genome__delta_acceptor",
    "delta_donor": "alphagenome_genome__delta_donor",
    "mean_delta_splice": "alphagenome_genome__mean_delta_splice",
}
MINIGENE_PREDICTION_COLUMNS = {
    "identifier": "alphagenome_minigene__Identifier",
    "acceptor_wt": "alphagenome_minigene__alphagenome_minigene_acceptor_wt",
    "donor_wt": "alphagenome_minigene__alphagenome_minigene_donor_wt",
    "acceptor_mut": "alphagenome_minigene__alphagenome_minigene_acceptor_mut",
    "donor_mut": "alphagenome_minigene__alphagenome_minigene_donor_mut",
    "delta_acceptor": "alphagenome_minigene__alphagenome_minigene_delta_acceptor",
    "delta_donor": "alphagenome_minigene__alphagenome_minigene_delta_donor",
    "delta_mean": "alphagenome_minigene__alphagenome_minigene_delta_mean",
}
REQUIRED_MASTER_COLUMNS = {
    "CHROM",
    "POS",
    "REF",
    "ALT",
    "gene",
    "exon_id",
    "ensembl_exon_id",
    "variant_id",
    "nt_seq",
    "start",
    "end",
    "length",
    "wt",
    "mut",
    "mut_type",
    "region",
    "exon_length",
    "psi_r1",
    "psi_r2",
    "psi_r3",
    "wt_psi",
    "se_wt_psi",
    "psi",
    "delta_psi",
    "se_psi",
    "se",
    "se_wt",
    "se_d",
    "significant",
    "measured",
    *GENOME_PREDICTION_COLUMNS.values(),
    *MINIGENE_PREDICTION_COLUMNS.values(),
}
REQUIRED_EXON_COLUMNS = {
    "ensembl_exon_id",
    "strand",
    "start_exon",
    "end_exon",
    "up_5k",
    "wt_seq",
    "down_5k",
    "exon_length",
}
REQUIRED_VARIANT_METADATA_COLUMNS = {
    "ensembl_exon_id",
    "variant_id",
    "nt_seq",
    "exon_length",
    "start",
    "length",
}


class OpenSplicePreparationError(BuildError):
    """Raised when OpenSplice source data violates the task contract."""


@dataclass(frozen=True)
class ExonMetadata:
    """Construct and native-coordinate metadata for one assayed exon."""

    ensembl_exon_id: str
    strand: int
    start_exon: int
    end_exon: int
    wt_seq: str
    exon_length: int
    native_upstream_length: int
    native_downstream_length: int


@dataclass(frozen=True)
class Variant:
    """One fully validated eligible OpenSplice substitution."""

    chrom: str
    pos: int
    genomic_ref: str
    genomic_alt: str
    gene: str
    exon_id: str
    ensembl_exon_id: str
    variant_id: str
    nt_seq: str
    start: int
    wt: str
    mut: str
    region: str
    exon_length: int
    psi_r1: float
    psi_r2: float
    psi_r3: float
    wt_psi: float | None
    psi: float | None
    delta_psi: float
    se_wt_psi: float | None
    se_psi: float | None
    se: float | None
    se_wt: float | None
    se_d: float | None
    significant: str
    alphagenome_genome: dict[str, Any]
    alphagenome_minigene: dict[str, Any]

    @property
    def stable_key(self) -> str:
        return f"{self.start}:{self.wt}:{self.mut}:{self.variant_id}"

    @property
    def construct_key(self) -> tuple[int, str, str]:
        return (self.start, self.wt, self.mut)

    @property
    def genomic_key(self) -> tuple[str, int, str, str]:
        return (self.chrom, self.pos, self.genomic_ref, self.genomic_alt)


@dataclass(frozen=True)
class ExonSummary:
    """Selection statistics for one exon with at least one eligible SNV."""

    ensembl_exon_id: str
    gene: str | None
    eligible_count: int
    q05: float | None
    q95: float | None
    robust_range: float | None
    minimum: float | None
    maximum: float | None
    gene_winner: bool = False
    gene_winner_rank: int | None = None
    selected_rank: int | None = None
    exclusion_reasons: tuple[str, ...] = ()


def validate_required_columns(
    actual: Sequence[str] | None,
    required: set[str],
    *,
    label: str,
) -> None:
    """Reject a source table that is missing any pinned contract column."""

    if actual is None:
        raise OpenSplicePreparationError(f"{label}: missing header")
    missing = required - set(actual)
    if missing:
        raise OpenSplicePreparationError(f"{label}: missing required columns {sorted(missing)}")


def normalize_dna(value: str, *, label: str, allow_empty: bool = False) -> str:
    """Normalize construct-oriented RNA spelling to uppercase DNA."""

    normalized = value.strip().upper().replace("U", "T")
    if (not normalized and not allow_empty) or set(normalized) - set("ACGT"):
        raise OpenSplicePreparationError(f"{label}: expected an uppercase DNA sequence")
    return normalized


def parse_boolean(value: str, *, label: str) -> bool:
    """Parse only the two booleans used by the pinned table."""

    if value == "True":
        return True
    if value == "False":
        return False
    raise OpenSplicePreparationError(f"{label}: malformed boolean {value!r}")


def parse_integral(value: str, *, label: str, positive: bool = True) -> int:
    """Parse integer-like TSV values without silently rounding."""

    try:
        number = float(value)
    except ValueError as exc:
        raise OpenSplicePreparationError(f"{label}: malformed integer {value!r}") from exc
    if not math.isfinite(number) or number != int(number) or (positive and number < 1):
        raise OpenSplicePreparationError(f"{label}: malformed integer {value!r}")
    return int(number)


def parse_optional_number(value: str, *, label: str) -> float | None:
    """Parse a finite number, preserving canonical missing values as null."""

    if value.strip() in MISSING_NUMBERS:
        return None
    try:
        number = float(value)
    except ValueError as exc:
        raise OpenSplicePreparationError(f"{label}: malformed number {value!r}") from exc
    if not math.isfinite(number):
        raise OpenSplicePreparationError(f"{label}: non-finite number {value!r}")
    return number


def parse_exon_metadata(stream: TextIO, *, label: str) -> dict[str, ExonMetadata]:
    """Parse and validate the complete pinned exon metadata table."""

    reader = csv.DictReader(stream, delimiter="\t")
    validate_required_columns(reader.fieldnames, REQUIRED_EXON_COLUMNS, label=label)
    exons: dict[str, ExonMetadata] = {}
    for line_number, row in enumerate(reader, start=2):
        exon_id = row["ensembl_exon_id"].strip()
        if not exon_id or exon_id in exons:
            raise OpenSplicePreparationError(f"{label}:{line_number}: duplicate or empty exon ID")
        strand = parse_integral(
            row["strand"], label=f"{label}:{line_number}:strand", positive=False
        )
        if strand not in {-1, 1}:
            raise OpenSplicePreparationError(f"{label}:{line_number}: strand must be -1 or 1")
        start_exon = parse_integral(row["start_exon"], label=f"{label}:{line_number}:start_exon")
        end_exon = parse_integral(row["end_exon"], label=f"{label}:{line_number}:end_exon")
        exon_length = parse_integral(row["exon_length"], label=f"{label}:{line_number}:exon_length")
        if end_exon - start_exon + 1 != exon_length:
            raise OpenSplicePreparationError(
                f"{label}:{line_number}: exon coordinates disagree with exon_length"
            )
        wt_seq = normalize_dna(row["wt_seq"], label=f"{label}:{line_number}:wt_seq")
        upstream = len(wt_seq) - exon_length - DOWNSTREAM_NATIVE_FLANK
        if upstream < 1:
            raise OpenSplicePreparationError(f"{label}:{line_number}: invalid insert geometry")
        exons[exon_id] = ExonMetadata(
            exon_id,
            strand,
            start_exon,
            end_exon,
            wt_seq,
            exon_length,
            upstream,
            DOWNSTREAM_NATIVE_FLANK,
        )
    if not exons:
        raise OpenSplicePreparationError(f"{label}: no exon records")
    return exons


def _prediction_object(
    row: Mapping[str, str],
    columns: Mapping[str, str],
    *,
    label: str,
) -> dict[str, Any]:
    values: dict[str, Any] = {}
    for output_name, source_name in columns.items():
        value = row[source_name].strip()
        if output_name in {"variant_id", "identifier"}:
            values[output_name] = value or None
        else:
            values[output_name] = parse_optional_number(value, label=f"{label}:{source_name}")
    return values


def eligible_variant_from_row(
    row: Mapping[str, str],
    exons: Mapping[str, ExonMetadata],
    *,
    label: str,
) -> tuple[Variant | None, tuple[str, ...]]:
    """Validate one master row and return an eligible SNV or exclusion reasons."""

    measured = parse_boolean(row["measured"], label=f"{label}:measured")
    length = parse_integral(row["length"], label=f"{label}:length")
    if row["mut_type"] != "sub" or length != 1:
        return None, ("not_single_nucleotide_substitution",)
    if not measured:
        return None, ("not_measured",)

    required_text = ("gene", "ensembl_exon_id", "variant_id", "nt_seq")
    missing = tuple(f"missing_{field}" for field in required_text if not row[field].strip())
    if missing:
        return None, missing
    exon_id = row["ensembl_exon_id"].strip()
    exon = exons.get(exon_id)
    if exon is None:
        return None, ("missing_exon_metadata",)

    required_numeric: dict[str, float] = {}
    missing_numeric = []
    for field in ("delta_psi", "psi_r1", "psi_r2", "psi_r3"):
        parsed = parse_optional_number(row[field], label=f"{label}:{field}")
        if parsed is None:
            missing_numeric.append(f"nonfinite_{field}")
        else:
            required_numeric[field] = parsed
    if missing_numeric:
        return None, tuple(missing_numeric)

    start = parse_integral(row["start"], label=f"{label}:start")
    end = parse_integral(row["end"], label=f"{label}:end")
    row_exon_length = parse_integral(row["exon_length"], label=f"{label}:exon_length")
    if end != start or row_exon_length != exon.exon_length:
        raise OpenSplicePreparationError(f"{label}: SNV or exon geometry disagrees")
    if start > len(exon.wt_seq):
        raise OpenSplicePreparationError(f"{label}: local position is outside wt_seq")
    wt = normalize_dna(row["wt"], label=f"{label}:wt")
    mut = normalize_dna(row["mut"], label=f"{label}:mut")
    if len(wt) != 1 or len(mut) != 1 or wt == mut:
        raise OpenSplicePreparationError(f"{label}: expected distinct single-base alleles")
    if exon.wt_seq[start - 1] != wt:
        raise OpenSplicePreparationError(f"{label}: wt allele disagrees with exon metadata")
    nt_seq = normalize_dna(row["nt_seq"], label=f"{label}:nt_seq")
    expected_mutant = exon.wt_seq[: start - 1] + mut + exon.wt_seq[start:]
    if nt_seq != expected_mutant:
        raise OpenSplicePreparationError(f"{label}: nt_seq is not the expected substitution")

    chrom = row["CHROM"].strip()
    pos = parse_integral(row["POS"], label=f"{label}:POS")
    genomic_ref = normalize_dna(row["REF"], label=f"{label}:REF")
    genomic_alt = normalize_dna(row["ALT"], label=f"{label}:ALT")
    if not chrom or len(genomic_ref) != 1 or len(genomic_alt) != 1 or genomic_ref == genomic_alt:
        raise OpenSplicePreparationError(f"{label}: malformed genomic SNV")
    optional = {
        field: parse_optional_number(row[field], label=f"{label}:{field}")
        for field in ("wt_psi", "psi", "se_wt_psi", "se_psi", "se", "se_wt", "se_d")
    }
    return (
        Variant(
            chrom=chrom,
            pos=pos,
            genomic_ref=genomic_ref,
            genomic_alt=genomic_alt,
            gene=row["gene"].strip(),
            exon_id=row["exon_id"].strip(),
            ensembl_exon_id=exon_id,
            variant_id=row["variant_id"].strip(),
            nt_seq=nt_seq,
            start=start,
            wt=wt,
            mut=mut,
            region=row["region"].strip(),
            exon_length=row_exon_length,
            psi_r1=required_numeric["psi_r1"],
            psi_r2=required_numeric["psi_r2"],
            psi_r3=required_numeric["psi_r3"],
            wt_psi=optional["wt_psi"],
            psi=optional["psi"],
            delta_psi=required_numeric["delta_psi"],
            se_wt_psi=optional["se_wt_psi"],
            se_psi=optional["se_psi"],
            se=optional["se"],
            se_wt=optional["se_wt"],
            se_d=optional["se_d"],
            significant=row["significant"].strip(),
            alphagenome_genome=_prediction_object(row, GENOME_PREDICTION_COLUMNS, label=label),
            alphagenome_minigene=_prediction_object(row, MINIGENE_PREDICTION_COLUMNS, label=label),
        ),
        (),
    )


def validate_unique_variants(variants: Sequence[Variant], *, exon_id: str) -> None:
    """Fail closed on duplicate stable keys or duplicate mutant constructs."""

    keys = [variant.construct_key for variant in variants]
    sequences = [variant.nt_seq for variant in variants]
    if len(keys) != len(set(keys)):
        raise OpenSplicePreparationError(f"{exon_id}: duplicate construct variant key")
    if len(sequences) != len(set(sequences)):
        raise OpenSplicePreparationError(f"{exon_id}: duplicate mutant sequence")
    genes = {variant.gene for variant in variants}
    if len(genes) != 1:
        raise OpenSplicePreparationError(f"{exon_id}: conflicting gene assignments")


def type7_quantile(values: Sequence[float], probability: float) -> float:
    """Compute a Hyndman-Fan type 7 quantile by the issue's explicit formula."""

    if not values or not 0 <= probability <= 1:
        raise OpenSplicePreparationError("type 7 quantile requires values and 0 <= p <= 1")
    ordered = sorted(values)
    h = (len(ordered) - 1) * probability
    lower = math.floor(h)
    upper = math.ceil(h)
    return ordered[lower] + (h - lower) * (ordered[upper] - ordered[lower])


def summarize_and_select_exons(
    variants_by_exon: Mapping[str, Sequence[Variant]],
) -> tuple[list[ExonSummary], list[ExonSummary]]:
    """Select one per-gene exon winner, then the 20 largest robust ranges."""

    provisional = []
    for exon_id, variants in sorted(variants_by_exon.items()):
        if not variants:
            continue
        provisional.append(summarize_exon(variants, exon_id=exon_id))
    return select_exon_summaries(provisional)


def summarize_exon(variants: Sequence[Variant], *, exon_id: str) -> ExonSummary:
    """Calculate the pinned effect-range summary for one exon."""

    if not variants:
        raise OpenSplicePreparationError(f"{exon_id}: cannot summarize an empty exon")
    validate_unique_variants(variants, exon_id=exon_id)
    effects = [variant.delta_psi for variant in variants]
    eligible = len(effects) >= PANEL_SIZE
    q05 = type7_quantile(effects, 0.05) if eligible else None
    q95 = type7_quantile(effects, 0.95) if eligible else None
    return ExonSummary(
        ensembl_exon_id=exon_id,
        gene=variants[0].gene,
        eligible_count=len(variants),
        q05=q05,
        q95=q95,
        robust_range=q95 - q05 if q05 is not None and q95 is not None else None,
        minimum=min(effects),
        maximum=max(effects),
        exclusion_reasons=() if eligible else ("fewer_than_50_eligible_snvs",),
    )


def _eligible_summary_key(summary: ExonSummary) -> tuple[float, int, str]:
    robust_range = summary.robust_range
    if robust_range is None:
        raise AssertionError("eligible exon summary is missing its robust range")
    return (-robust_range, -summary.eligible_count, summary.ensembl_exon_id)


def select_exon_summaries(
    provisional: Sequence[ExonSummary],
) -> tuple[list[ExonSummary], list[ExonSummary]]:
    """Apply per-gene winner selection and the stable global ranking."""

    eligible_summaries = [summary for summary in provisional if summary.robust_range is not None]
    by_gene: dict[str, list[ExonSummary]] = defaultdict(list)
    for summary in eligible_summaries:
        if summary.gene is None:
            raise OpenSplicePreparationError(
                f"{summary.ensembl_exon_id}: eligible exon is missing its gene"
            )
        by_gene[summary.gene].append(summary)
    winner_ids = {
        min(summaries, key=_eligible_summary_key).ensembl_exon_id for summaries in by_gene.values()
    }

    def winner_key(summary: ExonSummary) -> tuple[float, int, str, str]:
        robust_range = summary.robust_range
        gene = summary.gene
        if robust_range is None or gene is None:
            raise AssertionError("gene winner is missing its range or gene")
        return (-robust_range, -summary.eligible_count, gene, summary.ensembl_exon_id)

    winners = sorted(
        (summary for summary in eligible_summaries if summary.ensembl_exon_id in winner_ids),
        key=winner_key,
    )
    winner_ranks = {summary.ensembl_exon_id: rank for rank, summary in enumerate(winners, start=1)}
    selected_ids = {summary.ensembl_exon_id for summary in winners[:EXON_COUNT]}
    final = []
    for summary in provisional:
        reasons = list(summary.exclusion_reasons)
        winner_rank = winner_ranks.get(summary.ensembl_exon_id)
        if summary.robust_range is not None and winner_rank is None:
            reasons.append("not_gene_winner")
        elif winner_rank is not None and summary.ensembl_exon_id not in selected_ids:
            reasons.append("below_selection_cutoff")
        final.append(
            ExonSummary(
                **{
                    **asdict(summary),
                    "gene_winner": winner_rank is not None,
                    "gene_winner_rank": winner_rank,
                    "selected_rank": winner_rank
                    if summary.ensembl_exon_id in selected_ids
                    else None,
                    "exclusion_reasons": tuple(reasons),
                }
            )
        )
    selected = sorted(
        (summary for summary in final if summary.selected_rank is not None),
        key=lambda summary: int(summary.selected_rank or 0),
    )
    if len(selected) != EXON_COUNT or len({summary.gene for summary in selected}) != EXON_COUNT:
        raise OpenSplicePreparationError("exon selection did not produce 20 distinct genes")
    return sorted(final, key=lambda summary: summary.ensembl_exon_id), selected


def _sample_digest(seed: str, exon_id: str, bin_index: int, variant: Variant) -> str:
    return _sample_digest_from_stable_key(seed, exon_id, bin_index, variant.stable_key)


def _sample_digest_from_stable_key(
    seed: str,
    exon_id: str,
    bin_index: int,
    stable_key: str,
) -> str:
    payload = "\0".join((SAMPLING_ALGORITHM, seed, exon_id, str(bin_index), stable_key)).encode()
    return hashlib.sha256(payload).hexdigest()


def select_panel(
    variants: Sequence[Variant],
    *,
    exon_id: str,
    seed: str = SAMPLING_SEED,
) -> tuple[tuple[Variant, int, str], ...]:
    """Select five variants from each of ten equal-population effect-rank bins."""

    validate_unique_variants(variants, exon_id=exon_id)
    ordered = sorted(
        variants,
        key=lambda variant: (
            variant.delta_psi,
            variant.start,
            variant.wt,
            variant.mut,
            variant.variant_id,
        ),
    )
    if len(ordered) < PANEL_SIZE:
        raise OpenSplicePreparationError(
            f"{exon_id}: only {len(ordered)} eligible SNVs; at least {PANEL_SIZE} required"
        )
    base, remainder = divmod(len(ordered), QUANTILE_BINS)
    selected: list[tuple[Variant, int, str]] = []
    offset = 0
    for bin_index in range(1, QUANTILE_BINS + 1):
        bin_size = base + (bin_index <= remainder)
        values = ordered[offset : offset + bin_size]
        offset += bin_size
        ranked = sorted(
            values, key=lambda variant: _sample_digest(seed, exon_id, bin_index, variant)
        )
        selected.extend(
            (variant, bin_index, _sample_digest(seed, exon_id, bin_index, variant))
            for variant in ranked[:SAMPLES_PER_BIN]
        )
    if offset != len(ordered) or len(selected) != PANEL_SIZE:
        raise AssertionError("rank-quantile sampling did not consume the eligible population")
    return tuple(sorted(selected, key=lambda item: item[0].construct_key))


def cassette_segments(exon: ExonMetadata) -> list[dict[str, Any]]:
    """Return contiguous 1-based inclusive intervals for the displayed cassette."""

    upstream_start = len(FIXED_PREFIX) + 1
    upstream_end = len(FIXED_PREFIX) + exon.native_upstream_length
    exon_start = upstream_end + 1
    exon_end = upstream_end + exon.exon_length
    downstream_start = exon_end + 1
    downstream_end = len(FIXED_PREFIX) + len(exon.wt_seq)
    fixed_downstream_start = downstream_end + 1
    segments = [
        ("FAS exon 5", 1, len(FAS_E5)),
        ("FAS intron 5", len(FAS_E5) + 1, len(FIXED_PREFIX)),
        ("native upstream intron", upstream_start, upstream_end),
        ("tested alternative exon", exon_start, exon_end),
        ("native downstream intron", downstream_start, downstream_end),
        ("FAS intron 6", fixed_downstream_start, downstream_end + len(FAS_I6)),
        (
            "FAS exon 7",
            downstream_end + len(FAS_I6) + 1,
            downstream_end + len(FIXED_SUFFIX),
        ),
    ]
    return [
        {"segment": name, "start": start, "end": end, "length": end - start + 1}
        for name, start, end in segments
    ]


def complete_cassette(exon: ExonMetadata) -> str:
    """Construct the complete three-exon reporter cassette."""

    return FIXED_PREFIX + exon.wt_seq + FIXED_SUFFIX


def _alphagenome_provenance() -> dict[str, Any]:
    return {
        "repository": ALPHAGENOME["repository"],
        "commit": ALPHAGENOME["commit"],
        "minigene_script": ALPHAGENOME["minigene_script"],
        "genome_script": ALPHAGENOME["genome_script"],
        "library_design_script": ALPHAGENOME["library_design_script"],
        "ontology_term": ALPHAGENOME["ontology_term"],
        "ontology_label": ALPHAGENOME["ontology_label"],
    }


def _format_segment_table(segments: Sequence[Mapping[str, Any]]) -> str:
    lines = [
        "| Segment | 1-based inclusive interval | Length |",
        "| --- | ---: | ---: |",
    ]
    lines.extend(
        f"| {segment['segment']} | {segment['start']}--{segment['end']} | {segment['length']} nt |"
        for segment in segments
    )
    return "\n".join(lines)


def _assay_context() -> str:
    return "\n".join(
        (
            "- The displayed sequence is the complete wild-type three-exon splicing cassette "
            "in reporter-construct orientation. It includes the fixed FAS reporter exons and "
            "introns plus the mutagenized native insert; the unreported plasmid backbone is "
            "not included.",
            f"- The minigene library was assayed in {CONFIG.values['assay']['cellular_context']}.",
            f"- Exon inclusion was quantified as {CONFIG.values['assay']['measurement']}.",
        )
    )


def _reporter_context(cassette: str, exon: ExonMetadata) -> str:
    return "\n".join(
        (
            f"**Cassette geometry:** The cassette is {len(cassette)} nt long. The mutagenized "
            f"native insert occupies positions {len(FIXED_PREFIX) + 1}--"
            f"{len(FIXED_PREFIX) + len(exon.wt_seq)}, inclusive.",
            "",
            _format_segment_table(cassette_segments(exon)),
        )
    )


def _genome_interval(exon: ExonMetadata) -> dict[str, int]:
    center_pos1 = (exon.start_exon + exon.end_exon) // 2
    center0 = center_pos1 - 1
    start0 = center0 - ALPHAGENOME["input_length"] // 2
    return {
        "center_pos1": center_pos1,
        "start0": start0,
        "end0": start0 + ALPHAGENOME["input_length"],
    }


def build_source_record(
    exon: ExonMetadata,
    summary: ExonSummary,
    variants: Sequence[Variant],
    *,
    source_record_id: str,
    genome_vcf_path: str,
) -> dict[str, Any]:
    """Build one compact public source record with model-private provenance."""

    cassette = complete_cassette(exon)
    segments = cassette_segments(exon)
    panel = select_panel(variants, exon_id=exon.ensembl_exon_id)
    interval = _genome_interval(exon)
    actual_acceptor_pos0 = len(FIXED_PREFIX) + exon.native_upstream_length
    deposited_acceptor_pos0 = ALPHAGENOME["deposited_minigene_acceptor_pos0"]
    minigene_sites_verified = actual_acceptor_pos0 == deposited_acceptor_pos0
    total_padding = ALPHAGENOME["input_length"] - len(cassette)
    left_padding = total_padding // 2
    right_padding = total_padding - left_padding
    candidates = []
    private_candidates = []
    for display_index, (variant, quantile_bin, sampling_digest) in enumerate(panel, start=1):
        candidate_id = f"V{display_index:02d}"
        cassette_pos = len(FIXED_PREFIX) + variant.start
        if cassette[cassette_pos - 1] != variant.wt:
            raise OpenSplicePreparationError(
                f"{exon.ensembl_exon_id}: displayed REF does not match cassette"
            )
        candidates.append(
            {
                "candidate_id": candidate_id,
                "chrom": REFERENCE_CONTIG,
                "pos": cassette_pos,
                "ref": variant.wt,
                "alt": variant.mut,
                "reference_score": variant.delta_psi,
            }
        )
        genome_prediction = dict(variant.alphagenome_genome)
        minigene_prediction = dict(variant.alphagenome_minigene)
        private_candidates.append(
            {
                "candidate_id": candidate_id,
                "source": {
                    "gene": variant.gene,
                    "exon_id": variant.exon_id,
                    "ensembl_exon_id": variant.ensembl_exon_id,
                    "variant_id": variant.variant_id,
                    "genomic_variant": {
                        "chrom": variant.chrom,
                        "pos": variant.pos,
                        "ref": variant.genomic_ref,
                        "alt": variant.genomic_alt,
                        "strand": exon.strand,
                    },
                    "construct_variant": {
                        "insert_position": variant.start,
                        "cassette_position": cassette_pos,
                        "ref": variant.wt,
                        "alt": variant.mut,
                        "region": variant.region,
                    },
                },
                "measurements": {
                    "psi_r1": variant.psi_r1,
                    "psi_r2": variant.psi_r2,
                    "psi_r3": variant.psi_r3,
                    "wt_psi": variant.wt_psi,
                    "psi": variant.psi,
                    "delta_psi": variant.delta_psi,
                    "se_wt_psi": variant.se_wt_psi,
                    "se_psi": variant.se_psi,
                    "se": variant.se,
                    "se_wt": variant.se_wt,
                    "se_d": variant.se_d,
                    "measured": True,
                    "significant": variant.significant,
                },
                "selection": {
                    "quantile_bin": quantile_bin,
                    "sampling_digest": sampling_digest,
                },
                "alphagenome": {
                    "genome": {
                        "input": {
                            "vcf_path": genome_vcf_path,
                            "vcf_variant_id": genome_prediction["variant_id"],
                            "chrom": variant.chrom,
                            "position_1based": variant.pos,
                            "reference_bases": variant.genomic_ref,
                            "alternate_bases": variant.genomic_alt,
                            "interval_start0": interval["start0"],
                            "interval_end0": interval["end0"],
                            "interval_center_pos1": interval["center_pos1"],
                            "strand": "+" if exon.strand == 1 else "-",
                            "canonical_sites": [
                                {
                                    "boundary": "start_exon",
                                    "site_pos1": exon.start_exon,
                                    "role": "acceptor" if exon.strand == 1 else "donor",
                                },
                                {
                                    "boundary": "end_exon",
                                    "site_pos1": exon.end_exon,
                                    "role": "donor" if exon.strand == 1 else "acceptor",
                                },
                            ],
                            "ontology_term": ALPHAGENOME["ontology_term"],
                            "organism": ALPHAGENOME["organism"],
                            "requested_output": ALPHAGENOME["requested_output"],
                        },
                        "prediction": genome_prediction,
                        "quality": {
                            "available": all(
                                value is not None for value in genome_prediction.values()
                            ),
                            "vcf_input_verified": True,
                        },
                    },
                    "minigene": {
                        "input": {
                            "variant_metadata_identifier": minigene_prediction["identifier"],
                            "mutagenized_insert_sequence": variant.nt_seq,
                            "complete_variant_cassette_sha256": hashlib.sha256(
                                (FIXED_PREFIX + variant.nt_seq + FIXED_SUFFIX).encode()
                            ).hexdigest(),
                            "core_length": len(cassette),
                            "target_length": ALPHAGENOME["input_length"],
                            "left_n_padding": left_padding,
                            "right_n_padding": right_padding,
                            "actual_acceptor_construct_pos0": actual_acceptor_pos0,
                            "deposited_acceptor_construct_pos0": deposited_acceptor_pos0,
                            "actual_acceptor_padded_pos0": left_padding + actual_acceptor_pos0,
                            "deposited_acceptor_padded_pos0": left_padding
                            + deposited_acceptor_pos0,
                            "actual_donor_construct_pos0": actual_acceptor_pos0
                            + exon.exon_length
                            - 1,
                            "deposited_donor_construct_pos0": deposited_acceptor_pos0
                            + exon.exon_length
                            - 1,
                            "actual_donor_padded_pos0": left_padding
                            + actual_acceptor_pos0
                            + exon.exon_length
                            - 1,
                            "deposited_donor_padded_pos0": left_padding
                            + deposited_acceptor_pos0
                            + exon.exon_length
                            - 1,
                            "ontology_term": ALPHAGENOME["ontology_term"],
                            "organism": ALPHAGENOME["organism"],
                            "requested_output": ALPHAGENOME["requested_output"],
                            "model_loading_identifier": ALPHAGENOME["model_loading_identifier"],
                            "interval": None,
                        },
                        "prediction": minigene_prediction,
                        "quality": {
                            "available": all(
                                value is not None for value in minigene_prediction.values()
                            ),
                            "variant_metadata_verified": True,
                            "canonical_sites_verified": minigene_sites_verified,
                            "baseline_eligible": minigene_sites_verified,
                            "short_upstream_flank": exon.native_upstream_length != 70,
                        },
                    },
                },
            }
        )

    return {
        "source_dataset": SOURCE_DATASET,
        "source_record_id": source_record_id,
        "assay_context": _assay_context(),
        "reference_sequence": cassette,
        "reporter_context": _reporter_context(cassette, exon),
        "candidates": candidates,
        "task_family": TASK_FAMILY,
        "tags": ["minigene", "quantitative", "ranking", "snv", "splicing"],
        "source_metadata": {
            "display_name": f"OpenSplice exon {source_record_id}",
            "gene": summary.gene,
            "ensembl_exon_id": exon.ensembl_exon_id,
            "selected_rank": summary.selected_rank,
            "selection_summary": {
                "eligible_count": summary.eligible_count,
                "q05": summary.q05,
                "q95": summary.q95,
                "robust_range": summary.robust_range,
                "minimum": summary.minimum,
                "maximum": summary.maximum,
            },
            "construct": {
                "components": {
                    "fas_e5": FAS_E5,
                    "fas_i5": FAS_I5,
                    "wt_seq": exon.wt_seq,
                    "fas_i6": FAS_I6,
                    "fas_e7": FAS_E7,
                },
                "component_lengths": {
                    "fas_e5": len(FAS_E5),
                    "fas_i5": len(FAS_I5),
                    "wt_seq": len(exon.wt_seq),
                    "fas_i6": len(FAS_I6),
                    "fas_e7": len(FAS_E7),
                },
                "complete_wild_type_cassette": cassette,
                "cassette_sha256": hashlib.sha256(cassette.encode()).hexdigest(),
                "cassette_length": len(cassette),
                "segments": segments,
                "variable_insert_interval": {
                    "start": len(FIXED_PREFIX) + 1,
                    "end": len(FIXED_PREFIX) + len(exon.wt_seq),
                },
                "tested_exon_interval": {
                    "start": segments[3]["start"],
                    "end": segments[3]["end"],
                },
                "native_upstream_length": exon.native_upstream_length,
                "native_downstream_length": exon.native_downstream_length,
            },
            "native_exon": {
                "strand": exon.strand,
                "start_exon": exon.start_exon,
                "end_exon": exon.end_exon,
                "exon_length": exon.exon_length,
            },
            "alphagenome_provenance": _alphagenome_provenance(),
            "selected_candidates": private_candidates,
        },
    }


def _summary_document(summary: ExonSummary) -> dict[str, Any]:
    result = asdict(summary)
    result["exclusion_reasons"] = list(summary.exclusion_reasons)
    return result


def _manifest_configuration() -> dict[str, Any]:
    return {
        "exon_count": EXON_COUNT,
        "distinct_gene_count": EXON_COUNT,
        "panel_size": PANEL_SIZE,
        "quantile_bins": QUANTILE_BINS,
        "samples_per_bin": SAMPLES_PER_BIN,
        "sampling_seed": SAMPLING_SEED,
        "sampling_algorithm": SAMPLING_ALGORITHM,
        "quantile_algorithm": "Hyndman-Fan type 7 with linear interpolation",
        "exon_rank_tiebreakers": (
            "descending robust range, descending eligible count, ascending gene, "
            "ascending Ensembl exon ID"
        ),
        "variant_rank_tiebreakers": (
            "ascending delta_psi, local position, REF, ALT, source variant_id"
        ),
        "bin_allocation": "first N mod 10 bins receive one extra row",
        "candidate_display_order": "local position, REF, ALT",
        "candidate_ids": "V01 through V50",
        "model_visible_coordinates": (
            "synthetic contig element; 1-based positions in the complete displayed cassette"
        ),
    }


def _selected_panel_manifest(record: Mapping[str, Any]) -> dict[str, Any]:
    metadata = record["source_metadata"]
    return {
        "source_record_id": record["source_record_id"],
        "ensembl_exon_id": metadata["ensembl_exon_id"],
        "members": [
            {
                "candidate_id": candidate["candidate_id"],
                "source_variant_id": candidate["source"]["variant_id"],
                "stable_variant_key": "{insert_position}:{ref}:{alt}:{variant_id}".format(
                    insert_position=candidate["source"]["construct_variant"]["insert_position"],
                    ref=candidate["source"]["construct_variant"]["ref"],
                    alt=candidate["source"]["construct_variant"]["alt"],
                    variant_id=candidate["source"]["variant_id"],
                ),
                "quantile_bin": candidate["selection"]["quantile_bin"],
                "sampling_digest": candidate["selection"]["sampling_digest"],
            }
            for candidate in metadata["selected_candidates"]
        ],
    }


def write_prepared_dataset(
    records: Sequence[Mapping[str, Any]],
    summaries: Sequence[ExonSummary],
    *,
    source_provenance: Mapping[str, Any],
    output: str | Path,
    manifest_output: str | Path,
    output_relpath: str,
    population: Mapping[str, Any],
) -> tuple[int, str]:
    """Write canonical source JSONL and its complete selection manifest."""

    ordered = sorted(
        (dict(record) for record in records), key=lambda item: item["source_record_id"]
    )
    output_path = Path(output)
    output_path.parent.mkdir(parents=True, exist_ok=True)
    output_path.write_text(
        "".join(f"{canonical_json(record)}\n" for record in ordered),
        encoding="utf-8",
        newline="\n",
    )
    digest = sha256_file(output_path)
    manifest = {
        "schema_version": "1.0",
        "kind": "vepbench_opensplice_snv_prepared_source",
        "task_family": TASK_FAMILY,
        "configuration": _manifest_configuration(),
        "sources": dict(source_provenance),
        "population": {
            **dict(population),
            "exon_selection": [_summary_document(summary) for summary in summaries],
        },
        "selected_panels": [_selected_panel_manifest(record) for record in ordered],
        "output": {
            "path": output_relpath,
            "records": len(ordered),
            "candidates": sum(len(record["candidates"]) for record in ordered),
            "bytes": output_path.stat().st_size,
            "sha256": digest,
        },
    }
    manifest_path = Path(manifest_output)
    manifest_path.parent.mkdir(parents=True, exist_ok=True)
    manifest_path.write_text(f"{canonical_json(manifest)}\n", encoding="utf-8", newline="\n")
    validate_prepared_artifacts(output_path, manifest_path)
    return len(ordered), digest


def validate_prepared_artifacts(
    source_path: str | Path,
    manifest_path: str | Path,
) -> dict[str, Any]:
    """Validate committed OpenSplice source and manifest without network access."""

    source = Path(source_path)
    manifest_file = Path(manifest_path)
    try:
        manifest = json.loads(manifest_file.read_text(encoding="utf-8"))
    except (OSError, json.JSONDecodeError) as exc:
        raise OpenSplicePreparationError(f"could not read {manifest_file}: {exc}") from exc
    if (
        manifest.get("schema_version") != "1.0"
        or manifest.get("kind") != "vepbench_opensplice_snv_prepared_source"
        or manifest.get("task_family") != TASK_FAMILY
    ):
        raise OpenSplicePreparationError("OpenSplice manifest identity does not match")
    if manifest.get("configuration") != _manifest_configuration():
        raise OpenSplicePreparationError("OpenSplice manifest configuration does not match")
    sources = manifest.get("sources")
    expected_figshare = {
        **CONFIG.pins["dataset"],
        "retrieval_date": CONFIG.pins["retrieval_date"],
        "files": {
            label: {
                **pin,
                "url": f"https://ndownloader.figshare.com/files/{pin['file_id']}",
            }
            for label, pin in CONFIG.pins["files"].items()
        },
    }
    if not isinstance(sources, dict) or sources.get("figshare") != expected_figshare:
        raise OpenSplicePreparationError("OpenSplice Figshare provenance does not match")
    repository = sources.get("opensplice_repository")
    if (
        not isinstance(repository, dict)
        or repository.get("url") != ALPHAGENOME["repository"]
        or repository.get("commit") != ALPHAGENOME["commit"]
    ):
        raise OpenSplicePreparationError("OpenSplice repository provenance does not match")
    processed_cache = sources.get("processed_cache")
    expected_cache_key = cache_key(cache_configuration())
    if (
        not isinstance(processed_cache, dict)
        or processed_cache.get("bucket") != CONFIG.values["cache"]["bucket"]
        or processed_cache.get("cache_key") != expected_cache_key
        or processed_cache.get("prefix") != f"{CONFIG.values['cache']['root']}/{expected_cache_key}"
    ):
        raise OpenSplicePreparationError("OpenSplice processed-cache provenance does not match")
    output = manifest.get("output")
    if not isinstance(output, dict):
        raise OpenSplicePreparationError("OpenSplice manifest output must be an object")
    if source.stat().st_size != output.get("bytes") or sha256_file(source) != output.get("sha256"):
        raise OpenSplicePreparationError("OpenSplice source digest or size mismatch")
    records = read_jsonl(source)
    if len(records) != EXON_COUNT or output.get("records") != EXON_COUNT:
        raise OpenSplicePreparationError("OpenSplice source must contain exactly 20 records")
    if output.get("candidates") != EXON_COUNT * PANEL_SIZE:
        raise OpenSplicePreparationError("OpenSplice source must contain exactly 1,000 candidates")
    population = manifest.get("population")
    expected_population = CONFIG.values["population"]
    if not isinstance(population, dict) or any(
        population.get(field) != expected for field, expected in expected_population.items()
    ):
        raise OpenSplicePreparationError("OpenSplice population counts do not match")
    summaries = population.get("exon_selection")
    if (
        not isinstance(summaries, list)
        or len(summaries) != expected_population["exon_metadata_records"]
    ):
        raise OpenSplicePreparationError("OpenSplice manifest must cover every metadata exon")
    provisional = []
    for summary in summaries:
        if not isinstance(summary, dict):
            raise OpenSplicePreparationError("OpenSplice exon summary must be an object")
        count = summary.get("eligible_count")
        gene = summary.get("gene")
        exon_id = summary.get("ensembl_exon_id")
        if (
            not isinstance(exon_id, str)
            or not exon_id
            or isinstance(count, bool)
            or not isinstance(count, int)
            or count < 0
            or (gene is not None and (not isinstance(gene, str) or not gene))
        ):
            raise OpenSplicePreparationError("OpenSplice exon summary identity is invalid")
        if count >= PANEL_SIZE:
            q05 = summary.get("q05")
            q95 = summary.get("q95")
            robust_range = summary.get("robust_range")
            if (
                gene is None
                or isinstance(q05, bool)
                or not isinstance(q05, (int, float))
                or not math.isfinite(q05)
                or isinstance(q95, bool)
                or not isinstance(q95, (int, float))
                or not math.isfinite(q95)
                or isinstance(robust_range, bool)
                or not isinstance(robust_range, (int, float))
                or not math.isfinite(robust_range)
            ):
                raise OpenSplicePreparationError("eligible exon summary is incomplete")
            if robust_range != q95 - q05:
                raise OpenSplicePreparationError("eligible exon robust range is inconsistent")
            reasons: tuple[str, ...] = ()
        else:
            if any(summary.get(field) is not None for field in ("q05", "q95", "robust_range")):
                raise OpenSplicePreparationError("ineligible exon has quantile statistics")
            reasons = (
                "fewer_than_50_eligible_snvs",
                *(("missing_source_gene_assignment",) if gene is None else ()),
            )
        provisional.append(
            ExonSummary(
                ensembl_exon_id=exon_id,
                gene=gene,
                eligible_count=count,
                q05=summary.get("q05"),
                q95=summary.get("q95"),
                robust_range=summary.get("robust_range"),
                minimum=summary.get("minimum"),
                maximum=summary.get("maximum"),
                exclusion_reasons=reasons,
            )
        )
    recomputed_summaries, _ = select_exon_summaries(provisional)
    if summaries != [_summary_document(summary) for summary in recomputed_summaries]:
        raise OpenSplicePreparationError("OpenSplice exon selection does not reproduce")
    selected_summaries = sorted(
        (summary for summary in summaries if summary.get("selected_rank") is not None),
        key=lambda summary: summary["selected_rank"],
    )
    if [summary["selected_rank"] for summary in selected_summaries] != list(
        range(1, EXON_COUNT + 1)
    ):
        raise OpenSplicePreparationError("OpenSplice selected ranks are incomplete")
    if len({summary.get("gene") for summary in selected_summaries}) != EXON_COUNT:
        raise OpenSplicePreparationError("OpenSplice selected genes are not distinct")
    selected_by_exon = {summary["ensembl_exon_id"]: summary for summary in selected_summaries}

    expected_ids = [f"E{index:02d}" for index in range(1, EXON_COUNT + 1)]
    if [record.get("source_record_id") for record in records] != expected_ids:
        raise OpenSplicePreparationError("OpenSplice records must be canonical E01--E20")
    manifest_panels = manifest.get("selected_panels")
    if not isinstance(manifest_panels, list) or len(manifest_panels) != EXON_COUNT:
        raise OpenSplicePreparationError("OpenSplice selected-panel manifest is incomplete")
    expected_manifest_panels = []
    genes = set()
    for record in records:
        label = record["source_record_id"]
        if record.get("task_family") != TASK_FAMILY:
            raise OpenSplicePreparationError(f"{label}: task family mismatch")
        candidates = record.get("candidates")
        metadata = record.get("source_metadata")
        if not isinstance(candidates, list) or len(candidates) != PANEL_SIZE:
            raise OpenSplicePreparationError(f"{label}: expected 50 candidates")
        if not isinstance(metadata, dict):
            raise OpenSplicePreparationError(f"{label}: missing source metadata")
        exon_id = metadata.get("ensembl_exon_id")
        if not isinstance(exon_id, str) or not exon_id:
            raise OpenSplicePreparationError(f"{label}: invalid Ensembl exon identity")
        selected_summary = selected_by_exon.get(exon_id)
        if (
            selected_summary is None
            or metadata.get("gene") != selected_summary["gene"]
            or metadata.get("selected_rank") != selected_summary["selected_rank"]
            or label != f"E{selected_summary['selected_rank']:02d}"
        ):
            raise OpenSplicePreparationError(f"{label}: selected exon provenance mismatch")
        genes.add(metadata.get("gene"))
        private = metadata.get("selected_candidates")
        construct = metadata.get("construct")
        if not isinstance(private, list) or len(private) != PANEL_SIZE:
            raise OpenSplicePreparationError(f"{label}: private candidates are incomplete")
        if not isinstance(construct, dict):
            raise OpenSplicePreparationError(f"{label}: construct metadata is missing")
        cassette = construct.get("complete_wild_type_cassette")
        components = construct.get("components")
        native_exon = metadata.get("native_exon")
        if (
            not isinstance(cassette, str)
            or cassette != record.get("reference_sequence")
            or not isinstance(components, dict)
            or set(components) != {"fas_e5", "fas_i5", "wt_seq", "fas_i6", "fas_e7"}
            or not isinstance(native_exon, dict)
        ):
            raise OpenSplicePreparationError(f"{label}: cassette reconstruction mismatch")
        wt_insert = components.get("wt_seq")
        strand = native_exon.get("strand")
        start_exon = native_exon.get("start_exon")
        end_exon = native_exon.get("end_exon")
        exon_length = native_exon.get("exon_length")
        upstream_length = construct.get("native_upstream_length")
        downstream_length = construct.get("native_downstream_length")
        integral_values = (
            strand,
            start_exon,
            end_exon,
            exon_length,
            upstream_length,
            downstream_length,
        )
        if (
            not isinstance(wt_insert, str)
            or not wt_insert
            or set(wt_insert) - set("ACGT")
            or any(
                isinstance(value, bool) or not isinstance(value, int) for value in integral_values
            )
        ):
            raise OpenSplicePreparationError(f"{label}: native exon geometry is invalid")
        strand = cast(int, strand)
        start_exon = cast(int, start_exon)
        end_exon = cast(int, end_exon)
        exon_length = cast(int, exon_length)
        upstream_length = cast(int, upstream_length)
        downstream_length = cast(int, downstream_length)
        if (
            strand not in {-1, 1}
            or min(start_exon, end_exon, exon_length, upstream_length, downstream_length) < 1
            or end_exon - start_exon + 1 != exon_length
            or len(wt_insert) != upstream_length + exon_length + downstream_length
        ):
            raise OpenSplicePreparationError(f"{label}: native exon geometry is invalid")
        exon = ExonMetadata(
            exon_id,
            strand,
            start_exon,
            end_exon,
            wt_insert,
            exon_length,
            upstream_length,
            downstream_length,
        )
        expected_components = {
            "fas_e5": FAS_E5,
            "fas_i5": FAS_I5,
            "wt_seq": wt_insert,
            "fas_i6": FAS_I6,
            "fas_e7": FAS_E7,
        }
        expected_lengths = {name: len(sequence) for name, sequence in expected_components.items()}
        expected_cassette = complete_cassette(exon)
        expected_segments = cassette_segments(exon)
        if (
            components != expected_components
            or construct.get("component_lengths") != expected_lengths
            or cassette != expected_cassette
            or construct.get("cassette_length") != len(expected_cassette)
            or hashlib.sha256(cassette.encode()).hexdigest() != construct.get("cassette_sha256")
            or construct.get("segments") != expected_segments
            or construct.get("variable_insert_interval")
            != {
                "start": len(FIXED_PREFIX) + 1,
                "end": len(FIXED_PREFIX) + len(wt_insert),
            }
            or construct.get("tested_exon_interval")
            != {"start": expected_segments[3]["start"], "end": expected_segments[3]["end"]}
            or metadata.get("alphagenome_provenance") != _alphagenome_provenance()
            or record.get("assay_context") != _assay_context()
            or record.get("reporter_context") != _reporter_context(cassette, exon)
        ):
            raise OpenSplicePreparationError(f"{label}: cassette provenance does not reproduce")
        expected_candidate_ids = [f"V{index:02d}" for index in range(1, PANEL_SIZE + 1)]
        if (
            not all(isinstance(candidate, dict) for candidate in candidates)
            or [candidate.get("candidate_id") for candidate in candidates] != expected_candidate_ids
        ):
            raise OpenSplicePreparationError(f"{label}: candidate IDs or order mismatch")
        if (
            not all(
                isinstance(item, dict) and isinstance(item.get("selection"), dict)
                for item in private
            )
            or [item.get("candidate_id") for item in private] != expected_candidate_ids
        ):
            raise OpenSplicePreparationError(f"{label}: private candidate IDs mismatch")
        bins = Counter(item.get("selection", {}).get("quantile_bin") for item in private)
        if bins != Counter(dict.fromkeys(range(1, QUANTILE_BINS + 1), SAMPLES_PER_BIN)):
            raise OpenSplicePreparationError(f"{label}: rank bins are unbalanced")
        keys = []
        for candidate, private_candidate in zip(candidates, private, strict=True):
            position = candidate.get("pos")
            ref = candidate.get("ref")
            alt = candidate.get("alt")
            measurements = private_candidate.get("measurements", {})
            if not isinstance(measurements, dict):
                raise OpenSplicePreparationError(f"{label}: private measurements are invalid")
            if (
                candidate.get("chrom") != REFERENCE_CONTIG
                or isinstance(position, bool)
                or not isinstance(position, int)
                or not isinstance(ref, str)
                or not isinstance(alt, str)
                or len(ref) != 1
                or len(alt) != 1
                or ref == alt
                or cassette[position - 1 : position] != ref
                or candidate.get("reference_score") != measurements.get("delta_psi")
                or isinstance(candidate.get("reference_score"), bool)
                or not isinstance(candidate.get("reference_score"), (int, float))
                or not math.isfinite(candidate["reference_score"])
            ):
                raise OpenSplicePreparationError(f"{label}: invalid displayed candidate")
            keys.append((position, ref, alt))
            source = private_candidate.get("source", {})
            if not isinstance(source, dict):
                raise OpenSplicePreparationError(f"{label}: private source metadata is invalid")
            construct_variant = source.get("construct_variant", {})
            genomic_variant = source.get("genomic_variant", {})
            measurements = private_candidate.get("measurements", {})
            selection = private_candidate.get("selection", {})
            if not all(
                isinstance(value, dict)
                for value in (construct_variant, genomic_variant, measurements, selection)
            ):
                raise OpenSplicePreparationError(f"{label}: private candidate structure is invalid")
            variant_id = source.get("variant_id")
            insert_position = construct_variant.get("insert_position")
            quantile_bin = selection.get("quantile_bin")
            stable_key = f"{insert_position}:{ref}:{alt}:{variant_id}"
            expected_digest = (
                _sample_digest_from_stable_key(
                    SAMPLING_SEED,
                    exon_id,
                    quantile_bin,
                    stable_key,
                )
                if isinstance(quantile_bin, int) and not isinstance(quantile_bin, bool)
                else None
            )
            if (
                set(source)
                != {
                    "gene",
                    "exon_id",
                    "ensembl_exon_id",
                    "variant_id",
                    "genomic_variant",
                    "construct_variant",
                }
                or source.get("gene") != metadata.get("gene")
                or source.get("ensembl_exon_id") != exon_id
                or not isinstance(source.get("exon_id"), str)
                or not source["exon_id"]
                or not isinstance(variant_id, str)
                or not variant_id
                or set(construct_variant)
                != {"insert_position", "cassette_position", "ref", "alt", "region"}
                or construct_variant.get("cassette_position") != position
                or construct_variant.get("ref") != ref
                or construct_variant.get("alt") != alt
                or construct_variant.get("insert_position") != position - len(FIXED_PREFIX)
                or not isinstance(construct_variant.get("region"), str)
                or set(genomic_variant) != {"chrom", "pos", "ref", "alt", "strand"}
                or not isinstance(genomic_variant.get("chrom"), str)
                or not genomic_variant["chrom"]
                or isinstance(genomic_variant.get("pos"), bool)
                or not isinstance(genomic_variant.get("pos"), int)
                or genomic_variant["pos"] < 1
                or not isinstance(genomic_variant.get("ref"), str)
                or not isinstance(genomic_variant.get("alt"), str)
                or len(genomic_variant["ref"]) != 1
                or len(genomic_variant["alt"]) != 1
                or genomic_variant["ref"] == genomic_variant["alt"]
                or genomic_variant.get("strand") != strand
                or measurements.get("measured") is not True
                or any(
                    isinstance(measurements.get(field), bool)
                    or not isinstance(measurements.get(field), (int, float))
                    or not math.isfinite(measurements[field])
                    for field in ("psi_r1", "psi_r2", "psi_r3", "delta_psi")
                )
                or set(selection) != {"quantile_bin", "sampling_digest"}
                or not isinstance(quantile_bin, int)
                or isinstance(quantile_bin, bool)
                or not 1 <= quantile_bin <= QUANTILE_BINS
                or selection.get("sampling_digest") != expected_digest
            ):
                raise OpenSplicePreparationError(f"{label}: private candidate provenance mismatch")
            alphagenome = private_candidate.get("alphagenome", {})
            if not isinstance(alphagenome, dict) or set(alphagenome) != {
                "genome",
                "minigene",
            }:
                raise OpenSplicePreparationError(f"{label}: AlphaGenome metadata is incomplete")
            for mode in ("genome", "minigene"):
                member = alphagenome[mode]
                if not isinstance(member, dict) or set(member) != {
                    "input",
                    "prediction",
                    "quality",
                }:
                    raise OpenSplicePreparationError(
                        f"{label}: AlphaGenome {mode} metadata is incomplete"
                    )
                prediction = member["prediction"]
                quality = member["quality"]
                expected_prediction_fields = (
                    set(GENOME_PREDICTION_COLUMNS)
                    if mode == "genome"
                    else set(MINIGENE_PREDICTION_COLUMNS)
                )
                identifier_field = "variant_id" if mode == "genome" else "identifier"
                if (
                    not isinstance(prediction, dict)
                    or set(prediction) != expected_prediction_fields
                    or not isinstance(quality, dict)
                    or quality.get("available")
                    is not all(value is not None for value in prediction.values())
                ):
                    raise OpenSplicePreparationError(
                        f"{label}: AlphaGenome {mode} prediction metadata is invalid"
                    )
                for field, value in prediction.items():
                    if value is None:
                        continue
                    if field == identifier_field:
                        valid = isinstance(value, str) and bool(value)
                    else:
                        valid = (
                            isinstance(value, (int, float))
                            and not isinstance(value, bool)
                            and math.isfinite(value)
                        )
                    if not valid:
                        raise OpenSplicePreparationError(
                            f"{label}: AlphaGenome {mode} prediction value is invalid"
                        )
            genome_prediction = alphagenome["genome"]["prediction"]
            minigene_prediction = alphagenome["minigene"]["prediction"]
            minigene_input = alphagenome["minigene"]["input"]
            genome_input = alphagenome["genome"]["input"]
            if not isinstance(minigene_input, dict) or not isinstance(genome_input, dict):
                raise OpenSplicePreparationError(f"{label}: AlphaGenome inputs are invalid")
            expected_mutant = wt_insert[: insert_position - 1] + alt + wt_insert[insert_position:]
            total_padding = ALPHAGENOME["input_length"] - len(cassette)
            left_padding = total_padding // 2
            right_padding = total_padding - left_padding
            actual_acceptor_pos0 = len(FIXED_PREFIX) + upstream_length
            deposited_acceptor_pos0 = ALPHAGENOME["deposited_minigene_acceptor_pos0"]
            actual_donor_pos0 = actual_acceptor_pos0 + exon_length - 1
            deposited_donor_pos0 = deposited_acceptor_pos0 + exon_length - 1
            expected_minigene_input = {
                "variant_metadata_identifier": minigene_prediction["identifier"],
                "mutagenized_insert_sequence": expected_mutant,
                "complete_variant_cassette_sha256": hashlib.sha256(
                    (FIXED_PREFIX + expected_mutant + FIXED_SUFFIX).encode()
                ).hexdigest(),
                "core_length": len(cassette),
                "target_length": ALPHAGENOME["input_length"],
                "left_n_padding": left_padding,
                "right_n_padding": right_padding,
                "actual_acceptor_construct_pos0": actual_acceptor_pos0,
                "deposited_acceptor_construct_pos0": deposited_acceptor_pos0,
                "actual_acceptor_padded_pos0": left_padding + actual_acceptor_pos0,
                "deposited_acceptor_padded_pos0": left_padding + deposited_acceptor_pos0,
                "actual_donor_construct_pos0": actual_donor_pos0,
                "deposited_donor_construct_pos0": deposited_donor_pos0,
                "actual_donor_padded_pos0": left_padding + actual_donor_pos0,
                "deposited_donor_padded_pos0": left_padding + deposited_donor_pos0,
                "ontology_term": ALPHAGENOME["ontology_term"],
                "organism": ALPHAGENOME["organism"],
                "requested_output": ALPHAGENOME["requested_output"],
                "model_loading_identifier": ALPHAGENOME["model_loading_identifier"],
                "interval": None,
            }
            sites_verified = actual_acceptor_pos0 == deposited_acceptor_pos0
            expected_minigene_quality = {
                "available": all(value is not None for value in minigene_prediction.values()),
                "variant_metadata_verified": True,
                "canonical_sites_verified": sites_verified,
                "baseline_eligible": sites_verified,
                "short_upstream_flank": not sites_verified,
            }
            if (
                minigene_input != expected_minigene_input
                or alphagenome["minigene"]["quality"] != expected_minigene_quality
            ):
                raise OpenSplicePreparationError(
                    f"{label}: AlphaGenome minigene input reconstruction failed"
                )
            interval = _genome_interval(exon)
            expected_genome_input = {
                "vcf_path": (
                    f"alphagenome_genome_input_per_exon/{exon_id}_vcf_spliceai_pangolin.vcf"
                ),
                "vcf_variant_id": genome_prediction["variant_id"],
                "chrom": genomic_variant["chrom"],
                "position_1based": genomic_variant["pos"],
                "reference_bases": genomic_variant["ref"],
                "alternate_bases": genomic_variant["alt"],
                "interval_start0": interval["start0"],
                "interval_end0": interval["end0"],
                "interval_center_pos1": interval["center_pos1"],
                "strand": "+" if strand == 1 else "-",
                "canonical_sites": [
                    {
                        "boundary": "start_exon",
                        "site_pos1": start_exon,
                        "role": "acceptor" if strand == 1 else "donor",
                    },
                    {
                        "boundary": "end_exon",
                        "site_pos1": end_exon,
                        "role": "donor" if strand == 1 else "acceptor",
                    },
                ],
                "ontology_term": ALPHAGENOME["ontology_term"],
                "organism": ALPHAGENOME["organism"],
                "requested_output": ALPHAGENOME["requested_output"],
            }
            expected_genome_quality = {
                "available": all(value is not None for value in genome_prediction.values()),
                "vcf_input_verified": True,
            }
            if (
                genome_input != expected_genome_input
                or alphagenome["genome"]["quality"] != expected_genome_quality
            ):
                raise OpenSplicePreparationError(
                    f"{label}: AlphaGenome genome input reconstruction failed"
                )
        if keys != sorted(keys) or len(keys) != len(set(keys)):
            raise OpenSplicePreparationError(f"{label}: candidates are not canonically ordered")
        expected_manifest_panels.append(_selected_panel_manifest(record))
        prompt_fields = record.get("assay_context", "") + record.get("reporter_context", "")
        for forbidden in (
            metadata.get("gene"),
            metadata.get("ensembl_exon_id"),
            "AlphaGenome",
            "delta_psi",
            "quantile_bin",
        ):
            if isinstance(forbidden, str) and forbidden and forbidden in prompt_fields:
                raise OpenSplicePreparationError(f"{label}: private metadata leaked into context")
    if len(genes) != EXON_COUNT:
        raise OpenSplicePreparationError("OpenSplice records must represent 20 distinct genes")
    if manifest_panels != expected_manifest_panels:
        raise OpenSplicePreparationError("OpenSplice selected-panel manifest does not match source")
    return manifest


def variants_from_cache_records(records: Iterable[Mapping[str, Any]]) -> list[Variant]:
    """Reconstruct variants from canonical processed-cache records."""

    return [Variant(**dict(record)) for record in records]
