"""Preparation and validation for the CADD v1.7 satMutMPRA ranking task."""

from __future__ import annotations

import csv
import gzip
import hashlib
import io
import json
import math
import re
from collections import Counter
from collections.abc import Callable, Iterable, Mapping, Sequence
from dataclasses import dataclass
from pathlib import Path
from typing import Any

from .builder import BuildError, canonical_json, read_jsonl, sha256_file

TASK_FAMILY = "satmut_mpra"
REFERENCE_CONTIG = "element"
SOURCE_DATASET = "CADD-v1.7-RegSeq"
PANEL_SIZE = 50
QUANTILE_BINS = 10
SAMPLES_PER_BIN = 5
SAMPLING_SEED = "2026090200"
SAMPLING_ALGORITHM = "sha256_rank_quantile_v1"
MODEL_VISIBLE_SEQUENCE_POLICY = "MaveDB target sequence in reporter-construct orientation"
TARGET_SEQUENCE_MISMATCH_TREATMENT = "retain_reporter_construct_base_in_model_visible_reference"
EXPECTED_FILTER_COUNTS = {"SIGN": 4_332, "MIN": 17_685, "QUAL": 1_499}
KNOWN_REFERENCE_MISMATCHES = {("7", 156_791_603, "CA", "C")}
KNOWN_TARGET_SEQUENCE_MISMATCHES = {("7", 156_791_604, "A", "T")}
HGVS_SUBSTITUTION = re.compile(r"n\.(\d+)([ACGT])>([ACGT])")
HGVS_DELETION = re.compile(r"n\.(\d+)=")


class SatMutPreparationError(BuildError):
    """Raised when upstream or prepared satMutMPRA data violates the task contract."""


@dataclass(frozen=True)
class ElementSpec:
    cadd_label: str
    mavedb_urn: str
    model_name: str
    source_study_label: str
    element_class: str
    cell_line: str
    experimental_context: str
    mavedb_target_name: str | None = None

    @property
    def cadd_filename(self) -> str:
        return f"SatMut.all.{self.cadd_label}.vcf.gz"


ELEMENT_SPECS = (
    ElementSpec(
        "F9",
        "urn:mavedb:00000015-a-1",
        "F9 promoter",
        "F9 promoter",
        "promoter",
        "HepG2",
        "No additional treatment was applied.",
    ),
    ElementSpec(
        "GP1BA",
        "urn:mavedb:00000017-a-1",
        "GP1BB promoter",
        "GP1BB promoter",
        "promoter",
        "HEL 92.1.7",
        "No additional treatment was applied.",
    ),
    ElementSpec(
        "HBB",
        "urn:mavedb:00000018-a-1",
        "HBB promoter",
        "HBB promoter",
        "promoter",
        "HEL 92.1.7",
        "No additional treatment was applied.",
    ),
    ElementSpec(
        "HBG1",
        "urn:mavedb:00000019-a-1",
        "HBG1 promoter",
        "HBG1 promoter",
        "promoter",
        "HEL 92.1.7",
        "No additional treatment was applied.",
    ),
    ElementSpec(
        "HNF4A",
        "urn:mavedb:00000020-a-1",
        "HNF4A promoter",
        "HNF4A promoter",
        "promoter",
        "HEK293T",
        "No additional treatment was applied.",
    ),
    ElementSpec(
        "IRF4",
        "urn:mavedb:00000021-a-1",
        "IRF4 enhancer",
        "IRF4 enhancer",
        "enhancer",
        "SK-MEL-28",
        "No additional treatment was applied.",
    ),
    ElementSpec(
        "IRF6",
        "urn:mavedb:00000022-a-1",
        "IRF6 enhancer",
        "IRF6 enhancer",
        "enhancer",
        "HaCaT",
        "No additional treatment was applied.",
    ),
    ElementSpec(
        "LDLR",
        "urn:mavedb:00000023-a-2",
        "LDLR promoter",
        "LDLR promoter, replicate 2",
        "promoter",
        "HepG2",
        "Biological replicate 2 of 2.",
    ),
    ElementSpec(
        "MSMB",
        "urn:mavedb:00000024-a-1",
        "MSMB promoter",
        "MSMB promoter",
        "promoter",
        "HEK293T",
        "No additional treatment was applied.",
    ),
    ElementSpec(
        "MYCrs6983267",
        "urn:mavedb:00000025-a-1",
        "MYC enhancer",
        "MYC enhancer (rs6983267)",
        "enhancer",
        "HEK293T",
        "No additional treatment was applied.",
        "MYC enhancer (rs6983267)",
    ),
    ElementSpec(
        "PKLR",
        "urn:mavedb:00000027-b-1",
        "PKLR promoter",
        "PKLR promoter, 48h",
        "promoter",
        "K562",
        "Reporter activity was measured 48 hours after transfection.",
    ),
    ElementSpec(
        "SORT1",
        "urn:mavedb:00000029-a-1",
        "SORT1 enhancer",
        "SORT1 enhancer, replicate 1",
        "enhancer",
        "HepG2",
        "Biological replicate 1 of 2.",
    ),
    ElementSpec(
        "TCF7L2",
        "urn:mavedb:00000030-a-1",
        "TCF7L2 enhancer",
        "TCF7L2 enhancer",
        "enhancer",
        "MIN6",
        "No additional treatment was applied.",
    ),
    ElementSpec(
        "TERT",
        "urn:mavedb:00000031-a-1",
        "TERT promoter",
        "TERT promoter, HEK",
        "promoter",
        "HEK293T",
        "No additional treatment or siRNA was applied.",
    ),
    ElementSpec(
        "ZFAND3",
        "urn:mavedb:00000033-a-1",
        "ZFAND3 enhancer",
        "ZFAND3 enhancer",
        "enhancer",
        "MIN6",
        "No additional treatment was applied.",
    ),
    ElementSpec(
        "ZRSh13",
        "urn:mavedb:00000034-a-1",
        "ZRS enhancer",
        "ZRS enhancer, Hoxd13",
        "enhancer",
        "NIH3T3",
        "Cells were co-transfected with Hoxd13.",
    ),
)

SPEC_BY_LABEL = {spec.cadd_label: spec for spec in ELEMENT_SPECS}


@dataclass(frozen=True)
class Variant:
    chrom: str
    pos: int
    ref: str
    alt: str
    effect: float
    p_value: float
    barcode_count: int
    source_filter: str

    @property
    def key(self) -> tuple[str, int, str, str]:
        return (self.chrom, self.pos, self.ref, self.alt)

    @property
    def key_text(self) -> str:
        return f"{self.chrom}:{self.pos}:{self.ref}:{self.alt}"


@dataclass(frozen=True)
class ElementMetadata:
    sequence: str
    mavedb_sequence: str
    chrom: str
    start: int
    end: int
    modification_date: str
    reference_discrepancies: tuple[dict[str, Any], ...] = ()


@dataclass(frozen=True)
class PreparedElement:
    spec: ElementSpec
    metadata: ElementMetadata
    variants: tuple[Variant, ...]
    filter_counts: dict[str, int]
    reference_records_validated: int
    mavedb_records_validated: int


def parse_cadd_vcf(payload: bytes, *, label: str) -> tuple[tuple[Variant, ...], dict[str, int]]:
    """Parse one canonical compressed CADD validation VCF."""

    try:
        text = gzip.decompress(payload).decode("utf-8")
    except (gzip.BadGzipFile, UnicodeDecodeError) as exc:
        raise SatMutPreparationError(f"{label}: invalid gzip VCF") from exc
    variants: list[Variant] = []
    filters: Counter[str] = Counter()
    seen: set[tuple[str, int, str, str]] = set()
    for line_number, line in enumerate(text.splitlines(), start=1):
        if not line or line.startswith("#"):
            continue
        fields = line.split("\t")
        if len(fields) < 8:
            raise SatMutPreparationError(f"{label}:{line_number}: malformed VCF row")
        chrom, pos_text, _, ref, alt, _, source_filter, info_text = fields[:8]
        if source_filter not in EXPECTED_FILTER_COUNTS:
            raise SatMutPreparationError(
                f"{label}:{line_number}: unexpected FILTER {source_filter!r}"
            )
        try:
            pos = int(pos_text)
            info = dict(item.split("=", 1) for item in info_text.split(";"))
            effect = float(info["EF"])
            p_value = float(info["PV"])
            barcode_count = int(info["BC"])
        except (KeyError, ValueError) as exc:
            raise SatMutPreparationError(
                f"{label}:{line_number}: invalid EF, PV, or BC value"
            ) from exc
        if (
            pos < 1
            or not ref
            or not alt
            or any(base not in "ACGT" for base in ref + alt)
            or ref == alt
            or not math.isfinite(effect)
            or not math.isfinite(p_value)
            or p_value < 0
            or barcode_count < 1
        ):
            raise SatMutPreparationError(f"{label}:{line_number}: invalid variant values")
        if len(ref) == 2:
            if len(alt) != 1 or ref[0] != alt:
                raise SatMutPreparationError(
                    f"{label}:{line_number}: deletion is not normalized and anchored"
                )
        elif len(ref) != 1 or len(alt) != 1:
            raise SatMutPreparationError(
                f"{label}:{line_number}: only substitutions and one-base deletions are allowed"
            )
        variant = Variant(chrom, pos, ref, alt, effect, p_value, barcode_count, source_filter)
        if variant.key in seen:
            raise SatMutPreparationError(f"{label}:{line_number}: duplicate VCF key {variant.key}")
        seen.add(variant.key)
        variants.append(variant)
        filters[source_filter] += 1
    if not variants:
        raise SatMutPreparationError(f"{label}: VCF contains no variants")
    if set(filters) != set(EXPECTED_FILTER_COUNTS):
        raise SatMutPreparationError(f"{label}: VCF does not contain all three FILTER classes")
    return tuple(variants), dict(filters)


def parse_mavedb_metadata(payload: bytes, spec: ElementSpec) -> ElementMetadata:
    try:
        record = json.loads(payload)
    except (UnicodeDecodeError, json.JSONDecodeError) as exc:
        raise SatMutPreparationError(f"{spec.mavedb_urn}: invalid metadata JSON") from exc
    if not isinstance(record, dict) or record.get("urn") != spec.mavedb_urn:
        raise SatMutPreparationError(f"{spec.mavedb_urn}: metadata record identity mismatch")
    targets = record.get("targetGenes")
    extra = record.get("extraMetadata")
    if not isinstance(targets, list) or len(targets) != 1 or not isinstance(extra, dict):
        raise SatMutPreparationError(f"{spec.mavedb_urn}: expected exactly one genomic target")
    target = targets[0]
    sequence_record = target.get("targetSequence") if isinstance(target, dict) else None
    sequence = sequence_record.get("sequence") if isinstance(sequence_record, dict) else None
    chrom = extra.get("chr")
    start = extra.get("start")
    end = extra.get("end")
    modification_date = record.get("modificationDate")
    if (
        not isinstance(sequence, str)
        or not sequence
        or sequence != sequence.upper()
        or any(base not in "ACGT" for base in sequence)
        or not isinstance(chrom, str)
        or isinstance(start, bool)
        or not isinstance(start, int)
        or isinstance(end, bool)
        or not isinstance(end, int)
        or end - start + 1 != len(sequence)
        or not isinstance(modification_date, str)
    ):
        raise SatMutPreparationError(f"{spec.mavedb_urn}: invalid target sequence metadata")
    target_name = target.get("name") if isinstance(target, dict) else None
    expected_target_name = spec.mavedb_target_name or spec.model_name
    if target_name != expected_target_name:
        raise SatMutPreparationError(
            f"{spec.mavedb_urn}: target name {target_name!r} does not match "
            f"{expected_target_name!r}"
        )
    return ElementMetadata(sequence, sequence, chrom, start, end, modification_date)


def validate_reference(
    variants: Sequence[Variant],
    metadata: ElementMetadata,
    genome: Callable[[str, int, int], str],
) -> tuple[ElementMetadata, int]:
    """Validate the target orientation and every CADD REF allele against GRCh38."""

    target = genome(metadata.chrom, metadata.start - 1, metadata.end).upper()
    discrepancies = []
    for offset, (mavedb_base, reference_base) in enumerate(
        zip(metadata.mavedb_sequence, target, strict=True)
    ):
        if mavedb_base == reference_base:
            continue
        position = metadata.start + offset
        mismatch = (metadata.chrom, position, mavedb_base, reference_base)
        if mismatch not in KNOWN_TARGET_SEQUENCE_MISMATCHES:
            raise SatMutPreparationError(
                f"{metadata.chrom}:{position}: MaveDB target base {mavedb_base!r} does not "
                f"match pinned GRCh38 base {reference_base!r}"
            )
        discrepancies.append(
            {
                "chrom": metadata.chrom,
                "pos": position,
                "mavedb_base": mavedb_base,
                "grch38_base": reference_base,
                "treatment": TARGET_SEQUENCE_MISMATCH_TREATMENT,
            }
        )
    minimum = min(variant.pos for variant in variants)
    maximum = max(variant.pos + len(variant.ref) - 1 for variant in variants)
    interval = genome(metadata.chrom, minimum - 1, maximum).upper()
    for variant in variants:
        offset = variant.pos - minimum
        observed_ref = interval[offset : offset + len(variant.ref)]
        if observed_ref != variant.ref and variant.key not in KNOWN_REFERENCE_MISMATCHES:
            raise SatMutPreparationError(
                f"{variant.key_text}: REF does not match the pinned GRCh38 reference"
            )
        if observed_ref != variant.ref:
            if variant.source_filter != "QUAL":
                raise SatMutPreparationError(
                    f"{variant.key_text}: an eligible or barcode-qualified record cannot use "
                    "the pinned reference-mismatch exception"
                )
            discrepancies.append(
                {
                    "vcf_key": variant.key_text,
                    "source_filter": variant.source_filter,
                    "cadd_ref": variant.ref,
                    "grch38_ref": observed_ref,
                    "treatment": "exclude_via_source_filter_and_retain_in_provenance",
                }
            )
    validated_metadata = ElementMetadata(
        target,
        metadata.mavedb_sequence,
        metadata.chrom,
        metadata.start,
        metadata.end,
        metadata.modification_date,
        tuple(discrepancies),
    )
    return validated_metadata, len(variants)


def validate_mavedb_crosswalk(
    variants: Sequence[Variant],
    metadata: ElementMetadata,
    score_csv: bytes,
    *,
    spec: ElementSpec,
    genome: Callable[[str, int, int], str],
) -> int:
    """Match every CADD record to MaveDB by alleles, rounded score, and barcode count."""

    try:
        rows = csv.DictReader(io.StringIO(score_csv.decode("utf-8-sig")))
    except UnicodeDecodeError as exc:
        raise SatMutPreparationError(f"{spec.mavedb_urn}: invalid score CSV encoding") from exc
    preceding = genome(metadata.chrom, metadata.start - 2, metadata.start - 1).upper()
    by_key: dict[tuple[str, int, str, str], dict[str, str]] = {}
    for row_number, row in enumerate(rows, start=2):
        hgvs = row.get("hgvs_nt", "")
        substitution = HGVS_SUBSTITUTION.fullmatch(hgvs)
        deletion = HGVS_DELETION.fullmatch(hgvs)
        if substitution is not None:
            index = int(substitution.group(1))
            key = (
                metadata.chrom,
                metadata.start + index - 1,
                substitution.group(2),
                substitution.group(3),
            )
        elif deletion is not None:
            index = int(deletion.group(1))
            if index < 1 or index > len(metadata.mavedb_sequence):
                raise SatMutPreparationError(
                    f"{spec.mavedb_urn}:{row_number}: deletion is outside the target"
                )
            anchor = preceding if index == 1 else metadata.mavedb_sequence[index - 2]
            deleted = metadata.mavedb_sequence[index - 1]
            key = (metadata.chrom, metadata.start + index - 2, anchor + deleted, anchor)
        else:
            raise SatMutPreparationError(
                f"{spec.mavedb_urn}:{row_number}: unsupported HGVS notation {hgvs!r}"
            )
        if key in by_key:
            raise SatMutPreparationError(f"{spec.mavedb_urn}: duplicate mapped VCF key {key}")
        by_key[key] = row

    variant_by_key = {variant.key: variant for variant in variants}
    if set(by_key) != set(variant_by_key):
        missing = len(set(variant_by_key) - set(by_key))
        extra = len(set(by_key) - set(variant_by_key))
        raise SatMutPreparationError(
            f"{spec.mavedb_urn}: crosswalk allele mismatch (missing={missing}, extra={extra})"
        )
    for key, variant in variant_by_key.items():
        row = by_key[key]
        try:
            score = float(row["score"])
            p_value = float(row["p-value"])
            barcode_count = float(row["unique_tags"])
        except (KeyError, TypeError, ValueError) as exc:
            raise SatMutPreparationError(
                f"{spec.mavedb_urn}: invalid score fields for {variant.key_text}"
            ) from exc
        if (
            abs(score - variant.effect) > 0.0050000001
            or abs(p_value - variant.p_value) > 0.0000050001
            or not barcode_count.is_integer()
            or int(barcode_count) != variant.barcode_count
        ):
            raise SatMutPreparationError(
                f"{spec.mavedb_urn}: score or barcode crosswalk mismatch for {variant.key_text}"
            )
    return len(variants)


def select_panel(
    variants: Sequence[Variant],
    *,
    element_label: str,
    seed: str = SAMPLING_SEED,
) -> tuple[tuple[Variant, int], ...]:
    """Select five records per rank quantile and return them in VCF coordinate order."""

    eligible = sorted(
        (variant for variant in variants if variant.source_filter == "SIGN"),
        key=lambda variant: (variant.effect, variant.key),
    )
    if len(eligible) < PANEL_SIZE:
        raise SatMutPreparationError(
            f"{element_label}: only {len(eligible)} SIGN records; at least {PANEL_SIZE} required"
        )
    base, remainder = divmod(len(eligible), QUANTILE_BINS)
    selected: list[tuple[Variant, int]] = []
    offset = 0
    for bin_index in range(QUANTILE_BINS):
        size = base + (bin_index < remainder)
        values = eligible[offset : offset + size]
        offset += size
        ranked = sorted(
            values,
            key=lambda variant: _sample_digest(
                seed, element_label, "select", str(bin_index + 1), variant.key_text
            ),
        )
        selected.extend((variant, bin_index + 1) for variant in ranked[:SAMPLES_PER_BIN])
    if offset != len(eligible) or len(selected) != PANEL_SIZE:
        raise AssertionError("rank quantile construction did not consume the eligible pool")
    return tuple(sorted(selected, key=lambda item: item[0].key))


def build_source_record(element: PreparedElement) -> dict[str, Any]:
    panel = select_panel(element.variants, element_label=element.spec.cadd_label)
    candidates = []
    private_candidates = []
    for display_index, (variant, quantile_bin) in enumerate(panel, start=1):
        candidate_id = f"V{display_index:02d}"
        local_pos = variant.pos - element.metadata.start + 1
        reference_start = local_pos - 1
        reference_end = reference_start + len(variant.ref)
        if (
            variant.chrom != element.metadata.chrom
            or local_pos < 1
            or element.metadata.mavedb_sequence[reference_start:reference_end] != variant.ref
        ):
            raise SatMutPreparationError(
                f"{element.spec.cadd_label}: selected variant {variant.key_text} does not map "
                "to the reference element"
            )
        candidates.append(
            {
                "candidate_id": candidate_id,
                "chrom": REFERENCE_CONTIG,
                "pos": local_pos,
                "ref": variant.ref,
                "alt": variant.alt,
                "reference_score": variant.effect,
            }
        )
        private_candidates.append(
            {
                "candidate_id": candidate_id,
                "vcf_key": variant.key_text,
                "effect": variant.effect,
                "p_value": variant.p_value,
                "barcode_count": variant.barcode_count,
                "source_filter": variant.source_filter,
                "quantile_bin": quantile_bin,
            }
        )
    assay_context = "\n".join(
        (
            f"- Regulatory element: {element.spec.model_name}",
            f"- Element class: {element.spec.element_class}",
            f"- Cell line: {element.spec.cell_line}",
            "- Assay: saturation-mutagenesis MPRA with barcode-linked reporter "
            "quantification; activity effects are signed log2-scale regression coefficients.",
            f"- Experimental context: {element.spec.experimental_context}",
        )
    )
    return {
        "source_dataset": SOURCE_DATASET,
        "source_record_id": element.spec.cadd_label,
        "assay_context": assay_context,
        "reference_sequence": element.metadata.mavedb_sequence,
        "candidates": candidates,
        "task_family": TASK_FAMILY,
        "tags": ["grch38", "mpra", "noncoding", "quantitative", "ranking", "satmut"],
        "source_metadata": {
            "cadd_filename": element.spec.cadd_filename,
            "cadd_element_label": element.spec.cadd_label,
            "mavedb_score_set_urn": element.spec.mavedb_urn,
            "source_study_element_label": element.spec.source_study_label,
            "model_visible_name": element.spec.model_name,
            "assembly": "GRCh38",
            "target": {
                "chrom": element.metadata.chrom,
                "start": element.metadata.start,
                "end": element.metadata.end,
                "sequence_basis": "reporter_construct",
                "genomic_mapping_orientation": "forward",
                "reference_discrepancies": list(element.metadata.reference_discrepancies),
            },
            "eligible_records": element.filter_counts["SIGN"],
            "selected_candidates": private_candidates,
        },
    }


def write_prepared_dataset(
    elements: Sequence[PreparedElement],
    *,
    source_provenance: Mapping[str, Any],
    output: str | Path,
    manifest_output: str | Path,
    output_relpath: str,
) -> tuple[int, str]:
    if {element.spec.cadd_label for element in elements} != set(SPEC_BY_LABEL):
        raise SatMutPreparationError("prepared elements do not match the canonical 16-element set")
    records = [build_source_record(element) for element in elements]
    records.sort(key=lambda record: record["source_record_id"])
    output_path = Path(output)
    output_path.parent.mkdir(parents=True, exist_ok=True)
    output_path.write_text(
        "".join(f"{canonical_json(record)}\n" for record in records),
        encoding="utf-8",
        newline="\n",
    )
    digest = sha256_file(output_path)
    filter_totals = Counter[str]()
    per_element = {}
    for element in sorted(elements, key=lambda item: item.spec.cadd_label):
        filter_totals.update(element.filter_counts)
        per_element[element.spec.cadd_label] = {
            "filter_counts": element.filter_counts,
            "reference_records_checked": element.reference_records_validated,
            "mavedb_records_validated": element.mavedb_records_validated,
            "target_sequence_sha256": hashlib.sha256(
                element.metadata.mavedb_sequence.encode()
            ).hexdigest(),
            "mavedb_target_sequence_sha256": hashlib.sha256(
                element.metadata.mavedb_sequence.encode()
            ).hexdigest(),
            "grch38_sequence_sha256": hashlib.sha256(
                element.metadata.sequence.encode()
            ).hexdigest(),
            "reference_discrepancies": list(element.metadata.reference_discrepancies),
        }
    if dict(filter_totals) != EXPECTED_FILTER_COUNTS:
        raise SatMutPreparationError(
            f"canonical FILTER totals {dict(filter_totals)} do not match {EXPECTED_FILTER_COUNTS}"
        )
    manifest = {
        "schema_version": "1.0",
        "kind": "vepbench_satmut_mpra_prepared_source",
        "task_family": TASK_FAMILY,
        "configuration": {
            "assembly": "GRCh38",
            "eligible_filter": "SIGN",
            "panel_size": PANEL_SIZE,
            "quantile_bins": QUANTILE_BINS,
            "samples_per_bin": SAMPLES_PER_BIN,
            "sampling_seed": SAMPLING_SEED,
            "sampling_algorithm": SAMPLING_ALGORITHM,
            "quantile_allocation": "ascending EF; first N mod 10 bins receive one extra row",
            "rank_tiebreaker": "CHROM, POS, REF, ALT",
            "candidate_ids": "V01 through V50 after coordinate sorting",
            "display_order": "CHROM, POS, REF, ALT",
            "model_visible_coordinates": (
                "synthetic contig element; 1-based POS relative to displayed reference sequence"
            ),
            "model_visible_sequence": MODEL_VISIBLE_SEQUENCE_POLICY,
        },
        "sources": dict(source_provenance),
        "crosswalk": [
            {
                "cadd_filename": spec.cadd_filename,
                "cadd_element_label": spec.cadd_label,
                "mavedb_score_set_urn": spec.mavedb_urn,
                "source_study_element_label": spec.source_study_label,
                "model_visible_name": spec.model_name,
                "element_class": spec.element_class,
                "cell_line": spec.cell_line,
                "experimental_context": spec.experimental_context,
            }
            for spec in ELEMENT_SPECS
        ],
        "population": {
            "records": sum(filter_totals.values()),
            "filter_counts": dict(filter_totals),
            "elements": per_element,
        },
        "output": {
            "path": output_relpath,
            "records": len(records),
            "bytes": output_path.stat().st_size,
            "sha256": digest,
        },
    }
    manifest_path = Path(manifest_output)
    manifest_path.parent.mkdir(parents=True, exist_ok=True)
    manifest_path.write_text(f"{canonical_json(manifest)}\n", encoding="utf-8", newline="\n")
    validate_prepared_artifacts(output_path, manifest_path)
    return len(records), digest


def validate_prepared_artifacts(
    source_path: str | Path,
    manifest_path: str | Path,
) -> dict[str, Any]:
    source = Path(source_path)
    manifest_file = Path(manifest_path)
    try:
        manifest = json.loads(manifest_file.read_text(encoding="utf-8"))
    except (OSError, json.JSONDecodeError) as exc:
        raise SatMutPreparationError(f"could not read {manifest_file}: {exc}") from exc
    if (
        manifest.get("schema_version") != "1.0"
        or manifest.get("kind") != "vepbench_satmut_mpra_prepared_source"
        or manifest.get("task_family") != TASK_FAMILY
    ):
        raise SatMutPreparationError("satMutMPRA manifest identity does not match")
    output = manifest.get("output")
    if not isinstance(output, dict):
        raise SatMutPreparationError("satMutMPRA manifest output must be an object")
    if source.stat().st_size != output.get("bytes") or sha256_file(source) != output.get("sha256"):
        raise SatMutPreparationError("satMutMPRA source digest or size mismatch")
    records = read_jsonl(source)
    if len(records) != output.get("records") or len(records) != len(ELEMENT_SPECS):
        raise SatMutPreparationError("satMutMPRA source must contain exactly 16 records")
    configuration = manifest.get("configuration")
    if (
        not isinstance(configuration, dict)
        or configuration.get("sampling_seed") != SAMPLING_SEED
        or configuration.get("model_visible_sequence") != MODEL_VISIBLE_SEQUENCE_POLICY
    ):
        raise SatMutPreparationError("satMutMPRA sampling configuration does not match")
    population = manifest.get("population")
    if (
        not isinstance(population, dict)
        or population.get("filter_counts") != EXPECTED_FILTER_COUNTS
        or population.get("records") != sum(EXPECTED_FILTER_COUNTS.values())
        or not isinstance(population.get("elements"), dict)
    ):
        raise SatMutPreparationError("satMutMPRA population counts do not match")
    population_elements = population["elements"]
    crosswalk = manifest.get("crosswalk")
    expected_crosswalk = {
        (spec.cadd_filename, spec.mavedb_urn, spec.source_study_label, spec.model_name)
        for spec in ELEMENT_SPECS
    }
    if (
        not isinstance(crosswalk, list)
        or {
            (
                row.get("cadd_filename"),
                row.get("mavedb_score_set_urn"),
                row.get("source_study_element_label"),
                row.get("model_visible_name"),
            )
            for row in crosswalk
            if isinstance(row, dict)
        }
        != expected_crosswalk
    ):
        raise SatMutPreparationError("satMutMPRA crosswalk does not match the canonical set")

    labels: list[str] = []
    for record in records:
        label = record.get("source_record_id")
        if (
            not isinstance(label, str)
            or label not in SPEC_BY_LABEL
            or record.get("task_family") != TASK_FAMILY
        ):
            raise SatMutPreparationError(f"invalid satMutMPRA source identity {label!r}")
        labels.append(label)
        candidates = record.get("candidates")
        metadata = record.get("source_metadata")
        if not isinstance(candidates, list) or len(candidates) != PANEL_SIZE:
            raise SatMutPreparationError(f"{label}: expected exactly 50 candidates")
        if not isinstance(metadata, dict):
            raise SatMutPreparationError(f"{label}: missing private source metadata")
        private = metadata.get("selected_candidates")
        target = metadata.get("target")
        if not isinstance(private, list) or len(private) != PANEL_SIZE:
            raise SatMutPreparationError(f"{label}: private candidate provenance mismatch")
        if (
            not isinstance(target, dict)
            or not isinstance(target.get("chrom"), str)
            or isinstance(target.get("start"), bool)
            or not isinstance(target.get("start"), int)
            or target["start"] < 1
            or target.get("sequence_basis") != "reporter_construct"
            or target.get("genomic_mapping_orientation") != "forward"
        ):
            raise SatMutPreparationError(f"{label}: target coordinate provenance is invalid")
        element_summary = population_elements.get(label)
        if (
            not isinstance(element_summary, dict)
            or hashlib.sha256(record["reference_sequence"].encode()).hexdigest()
            != element_summary.get("target_sequence_sha256")
            or element_summary.get("target_sequence_sha256")
            != element_summary.get("mavedb_target_sequence_sha256")
            or not isinstance(element_summary.get("grch38_sequence_sha256"), str)
        ):
            raise SatMutPreparationError(
                f"{label}: reporter-construct sequence provenance mismatch"
            )
        expected_ids = [f"V{index:02d}" for index in range(1, PANEL_SIZE + 1)]
        if [candidate.get("candidate_id") for candidate in candidates] != expected_ids:
            raise SatMutPreparationError(f"{label}: candidate IDs or display order mismatch")
        if [candidate.get("candidate_id") for candidate in private] != expected_ids:
            raise SatMutPreparationError(f"{label}: private candidate IDs mismatch")
        keys = [
            (
                candidate.get("chrom"),
                candidate.get("pos"),
                candidate.get("ref"),
                candidate.get("alt"),
            )
            for candidate in candidates
        ]
        if len(set(keys)) != PANEL_SIZE:
            raise SatMutPreparationError(f"{label}: candidate VCF keys are not unique")
        bins = Counter(item.get("quantile_bin") for item in private)
        if bins != Counter(dict.fromkeys(range(1, QUANTILE_BINS + 1), SAMPLES_PER_BIN)):
            raise SatMutPreparationError(f"{label}: quantile representation is invalid")
        if any(item.get("source_filter") != "SIGN" for item in private):
            raise SatMutPreparationError(f"{label}: non-SIGN candidate was selected")
        for candidate, private_candidate in zip(candidates, private, strict=True):
            if candidate.get("reference_score") != private_candidate.get("effect"):
                raise SatMutPreparationError(f"{label}: candidate effect provenance mismatch")
            local_pos = candidate.get("pos")
            ref = candidate.get("ref")
            alt = candidate.get("alt")
            if (
                candidate.get("chrom") != REFERENCE_CONTIG
                or isinstance(local_pos, bool)
                or not isinstance(local_pos, int)
                or local_pos < 1
                or not isinstance(ref, str)
                or not isinstance(alt, str)
                or record["reference_sequence"][local_pos - 1 : local_pos - 1 + len(ref)] != ref
            ):
                raise SatMutPreparationError(f"{label}: candidate local VCF mapping is invalid")
            genomic_key = f"{target['chrom']}:{target['start'] + local_pos - 1}:{ref}:{alt}"
            if private_candidate.get("vcf_key") != genomic_key:
                raise SatMutPreparationError(f"{label}: candidate genomic provenance mismatch")
    if set(labels) != set(SPEC_BY_LABEL) or labels != sorted(labels):
        raise SatMutPreparationError("satMutMPRA source records must be canonical and sorted")
    return manifest


def eligible_cache_rows(elements: Iterable[PreparedElement]) -> list[dict[str, Any]]:
    """Return the complete processed SIGN population for reusable bucket caching."""

    rows = []
    for element in elements:
        for variant in element.variants:
            if variant.source_filter != "SIGN":
                continue
            rows.append(
                {
                    "element": element.spec.cadd_label,
                    "chrom": variant.chrom,
                    "pos": variant.pos,
                    "ref": variant.ref,
                    "alt": variant.alt,
                    "effect": variant.effect,
                    "p_value": variant.p_value,
                    "barcode_count": variant.barcode_count,
                    "source_filter": variant.source_filter,
                }
            )
    return sorted(rows, key=lambda row: (row["element"], row["pos"], row["ref"], row["alt"]))


def _sample_digest(seed: str, *parts: str) -> bytes:
    payload = "\0".join((SAMPLING_ALGORITHM, seed, *parts)).encode()
    return hashlib.sha256(payload).digest()
