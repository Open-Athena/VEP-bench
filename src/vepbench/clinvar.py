"""Deterministic preparation for the temporal ClinVar classification task."""

from __future__ import annotations

import gzip
import hashlib
import json
from collections import Counter, defaultdict
from collections.abc import Callable, Iterable, Mapping
from dataclasses import dataclass
from datetime import date
from pathlib import Path
from typing import Any
from xml.etree import ElementTree

import polars as pl

from .builder import BuildError, canonical_json, read_jsonl, sha256_file
from .vep_consequence import BASE_CODES, FASTA_LINE_WIDTH, render_local_variant

LABELS = ("Benign", "Pathogenic")
CHOICES = (
    {"choice_id": "C01", "text": "Benign"},
    {"choice_id": "C02", "text": "Pathogenic"},
)
CHOICE_BY_LABEL = {choice["text"]: choice["choice_id"] for choice in CHOICES}
PRIMARY_CHROMS = tuple([str(index) for index in range(1, 23)] + ["X", "Y"])
PRIMARY_ACCESSIONS = {
    "1": "NC_000001.11",
    "2": "NC_000002.12",
    "3": "NC_000003.12",
    "4": "NC_000004.12",
    "5": "NC_000005.10",
    "6": "NC_000006.12",
    "7": "NC_000007.14",
    "8": "NC_000008.11",
    "9": "NC_000009.12",
    "10": "NC_000010.11",
    "11": "NC_000011.10",
    "12": "NC_000012.12",
    "13": "NC_000013.11",
    "14": "NC_000014.9",
    "15": "NC_000015.10",
    "16": "NC_000016.10",
    "17": "NC_000017.11",
    "18": "NC_000018.10",
    "19": "NC_000019.10",
    "20": "NC_000020.11",
    "21": "NC_000021.9",
    "22": "NC_000022.11",
    "X": "NC_000023.11",
    "Y": "NC_000024.10",
}
REVIEW_STARS = {
    "criteria provided, single submitter": 1,
    "criteria provided, multiple submitters, no conflicts": 2,
    "reviewed by expert panel": 3,
    "practice guideline": 4,
}
VARIANT_COLUMNS = ("chrom", "pos", "ref", "alt")
VEP_COLUMNS = (*VARIANT_COLUMNS, "consequence")
SAMPLING_ALGORITHM = "clinvar_consequence_matched_sha256_v1"
DEFAULT_SEED = 2_026_090_100


class ClinVarPreparationError(BuildError):
    """Raised when a ClinVar source cannot produce the fixed task."""


@dataclass(frozen=True)
class ClinVarCandidate:
    accession: str
    version: int
    variation_id: int
    date_created: str
    label: str
    review_status: str
    review_stars: int
    chrom: str
    pos: int
    ref: str
    alt: str
    genes: tuple[str, ...]
    transcripts: tuple[str, ...]
    conditions: tuple[tuple[str, str | None, str | None], ...]

    @property
    def key(self) -> tuple[str, int, str, str]:
        return (self.chrom, self.pos, self.ref, self.alt)

    @property
    def source_record_id(self) -> str:
        return f"{self.accession}.{self.version}"


@dataclass(frozen=True)
class ParsedCohort:
    candidates: tuple[ClinVarCandidate, ...]
    filter_stages: tuple[dict[str, Any], ...]
    duplicate_allele_keys: int
    duplicate_records_rejected: int


@dataclass(frozen=True)
class VepCandidate:
    clinvar: ClinVarCandidate
    consequence: str

    @property
    def key(self) -> tuple[str, int, str, str]:
        return self.clinvar.key


@dataclass(frozen=True)
class VepJoinResult:
    matched: tuple[VepCandidate, ...]
    missing: tuple[ClinVarCandidate, ...]


@dataclass(frozen=True)
class WindowCandidate:
    joined: VepCandidate
    sequence: str


@dataclass(frozen=True)
class ReferenceValidation:
    valid: tuple[WindowCandidate, ...]
    invalid: tuple[VepCandidate, ...]


@dataclass(frozen=True)
class PreparationConfig:
    start_date: date = date(2026, 7, 1)
    end_date: date = date(2026, 7, 31)
    target_pairs: int = 25
    flank_size: int = 500
    seed: int = DEFAULT_SEED

    @property
    def window_size(self) -> int:
        return self.flank_size * 2 + 1


@dataclass(frozen=True)
class PreparedDataset:
    records: tuple[dict[str, Any], ...]
    manifest: dict[str, Any]


def parse_clinvar_vcv(
    source: str | Path,
    *,
    start_date: date,
    end_date: date,
) -> ParsedCohort:
    """Stream a ClinVar VCV XML release and return the eligible unique SNVs."""

    if start_date > end_date:
        raise ClinVarPreparationError("start_date must not be after end_date")
    source_path = Path(source)
    stages: dict[str, Counter[str]] = {
        name: Counter()
        for name in (
            "variation_archives",
            "date_created",
            "exact_classification",
            "accepted_review_status",
            "simple_allele",
            "grch38_location",
            "primary_chromosome",
            "snv_with_unique_location",
            "unique_allele_key",
        )
    }
    candidates: list[ClinVarCandidate] = []

    opener = gzip.open if source_path.suffix == ".gz" else Path.open
    try:
        with opener(source_path, "rb") as stream:
            context = ElementTree.iterparse(stream, events=("start", "end"))
            _, root = next(context)
            for event, archive in context:
                if event != "end" or _local_name(archive.tag) != "VariationArchive":
                    continue
                label, germline, classified = _classification_fields(archive)
                _count_stage(stages, "variation_archives", label)

                created = _parse_iso_date(archive.get("DateCreated"))
                if created is None or not start_date <= created <= end_date:
                    root.clear()
                    continue
                _count_stage(stages, "date_created", label)
                if label not in LABELS:
                    root.clear()
                    continue
                _count_stage(stages, "exact_classification", label)

                review_status = _child_text(germline, "ReviewStatus")
                if review_status not in REVIEW_STARS:
                    root.clear()
                    continue
                _count_stage(stages, "accepted_review_status", label)

                allele = _direct_child(classified, "SimpleAllele")
                if allele is None:
                    root.clear()
                    continue
                _count_stage(stages, "simple_allele", label)

                location = _direct_child(allele, "Location")
                grch38_locations = [
                    element
                    for element in _direct_children(location, "SequenceLocation")
                    if element.get("Assembly") == "GRCh38"
                ]
                if not grch38_locations:
                    root.clear()
                    continue
                _count_stage(stages, "grch38_location", label)

                primary_locations = [
                    element for element in grch38_locations if _is_primary_location(element)
                ]
                if not primary_locations:
                    root.clear()
                    continue
                _count_stage(stages, "primary_chromosome", label)

                keys = {
                    key
                    for element in primary_locations
                    if (key := _location_key(element)) is not None
                }
                if len(keys) != 1:
                    root.clear()
                    continue
                key = next(iter(keys))
                _count_stage(stages, "snv_with_unique_location", label)

                accession = archive.get("Accession")
                version = _positive_int(archive.get("Version"))
                variation_id = _positive_int(archive.get("VariationID"))
                if not accession or version is None or variation_id is None:
                    raise ClinVarPreparationError(
                        "VariationArchive is missing Accession, Version, or VariationID"
                    )
                genes = tuple(
                    sorted(
                        {
                            symbol
                            for gene_list in _direct_children(allele, "GeneList")
                            for gene in _direct_children(gene_list, "Gene")
                            if (symbol := gene.get("Symbol"))
                        }
                    )
                )
                transcripts = tuple(sorted(_transcript_accessions(allele)))
                conditions = tuple(sorted(_classified_conditions(classified)))
                candidates.append(
                    ClinVarCandidate(
                        accession=accession,
                        version=version,
                        variation_id=variation_id,
                        date_created=created.isoformat(),
                        label=label,
                        review_status=review_status,
                        review_stars=REVIEW_STARS[review_status],
                        chrom=key[0],
                        pos=key[1],
                        ref=key[2],
                        alt=key[3],
                        genes=genes,
                        transcripts=transcripts,
                        conditions=conditions,
                    )
                )
                root.clear()
    except (OSError, ElementTree.ParseError) as exc:
        raise ClinVarPreparationError(f"could not stream {source_path}: {exc}") from exc

    by_key: dict[tuple[str, int, str, str], list[ClinVarCandidate]] = defaultdict(list)
    for candidate in candidates:
        by_key[candidate.key].append(candidate)
    duplicate_keys = {key for key, records in by_key.items() if len(records) > 1}
    unique_candidates = sorted(
        (candidate for candidate in candidates if candidate.key not in duplicate_keys),
        key=lambda candidate: (candidate.key, candidate.source_record_id),
    )
    for candidate in unique_candidates:
        _count_stage(stages, "unique_allele_key", candidate.label)

    filter_stages = tuple(
        {
            "stage": stage,
            "records": sum(counts.values()),
            "by_label": dict(sorted(counts.items())),
        }
        for stage, counts in stages.items()
    )
    return ParsedCohort(
        candidates=tuple(unique_candidates),
        filter_stages=filter_stages,
        duplicate_allele_keys=len(duplicate_keys),
        duplicate_records_rejected=sum(len(by_key[key]) for key in duplicate_keys),
    )


def sparse_join_vep(
    candidates: Iterable[ClinVarCandidate],
    parquet_sources: Mapping[str, Any],
    *,
    storage_options: Mapping[str, Any] | None = None,
) -> VepJoinResult:
    """Filter each chromosome shard by position before the exact allele join."""

    candidate_list = sorted(candidates, key=lambda candidate: candidate.key)
    by_chrom: dict[str, list[ClinVarCandidate]] = defaultdict(list)
    for candidate in candidate_list:
        by_chrom[candidate.chrom].append(candidate)

    matched: list[VepCandidate] = []
    missing: list[ClinVarCandidate] = []
    for chrom in sorted(by_chrom, key=_chrom_sort_key):
        if chrom not in parquet_sources:
            raise ClinVarPreparationError(
                f"no VEP Parquet source configured for chromosome {chrom}"
            )
        chrom_candidates = by_chrom[chrom]
        positions = sorted({candidate.pos for candidate in chrom_candidates})
        source_value = parquet_sources[chrom]
        source = str(source_value) if isinstance(source_value, (str, Path)) else source_value
        source_display = str(source_value)
        try:
            scan = pl.scan_parquet(
                source,
                storage_options=(dict(storage_options or {}) if isinstance(source, str) else None),
            )
            filtered = (
                scan.filter(pl.col("pos").is_in(positions))
                .select(
                    pl.col("chrom").cast(pl.String),
                    pl.col("pos").cast(pl.Int64),
                    pl.col("ref").cast(pl.String).str.to_uppercase(),
                    pl.col("alt").cast(pl.String).str.to_uppercase(),
                    pl.col("consequence").cast(pl.String),
                )
                .collect(engine="streaming")
            )
            candidate_frame = pl.DataFrame(
                [
                    {
                        "_candidate_index": index,
                        "chrom": candidate.chrom,
                        "pos": candidate.pos,
                        "ref": candidate.ref,
                        "alt": candidate.alt,
                    }
                    for index, candidate in enumerate(chrom_candidates)
                ]
            )
            joined = (
                candidate_frame.lazy()
                .join(filtered.lazy(), on=list(VARIANT_COLUMNS), how="left")
                .collect(engine="streaming")
                .sort("_candidate_index")
            )
        except Exception as exc:
            raise ClinVarPreparationError(
                f"could not query and join VEP shard {source_display}: {exc}"
            ) from exc

        multiplicities = joined.group_by("_candidate_index").len().filter(pl.col("len") > 1)
        if multiplicities.height:
            duplicate_index = int(multiplicities.sort("_candidate_index").row(0)[0])
            duplicate = chrom_candidates[duplicate_index]
            raise ClinVarPreparationError(
                "VEP source contains more than one row for allele key "
                f"{duplicate.chrom}:{duplicate.pos}:{duplicate.ref}:{duplicate.alt}"
            )
        if joined.height != len(chrom_candidates):
            raise ClinVarPreparationError(
                f"VEP join for chromosome {chrom} changed candidate count"
            )

        for row in joined.iter_rows(named=True):
            candidate = chrom_candidates[int(row["_candidate_index"])]
            consequence = row["consequence"]
            if consequence is None or not str(consequence):
                missing.append(candidate)
            else:
                matched.append(VepCandidate(candidate, str(consequence)))

    return VepJoinResult(
        matched=tuple(sorted(matched, key=lambda item: item.key)),
        missing=tuple(sorted(missing, key=lambda item: item.key)),
    )


def stable_hash_rank(seed: int, *parts: object) -> int:
    """Return the sampling rank used for consequences and allele keys."""

    if seed < 0:
        raise ClinVarPreparationError("sampling seed must be non-negative")
    payload = "\0".join([SAMPLING_ALGORITHM, str(seed), *(str(part) for part in parts)])
    return int.from_bytes(hashlib.sha256(payload.encode()).digest(), "big")


def allocate_pairs(
    capacities: Mapping[str, int],
    *,
    target_pairs: int,
    seed: int,
) -> dict[str, int]:
    """Allocate pairs diversity-first, then as evenly as capacities permit."""

    if target_pairs < 1:
        raise ClinVarPreparationError("target_pairs must be positive")
    if any(capacity < 0 for capacity in capacities.values()):
        raise ClinVarPreparationError("consequence capacities must be non-negative")
    positive = [consequence for consequence, capacity in capacities.items() if capacity > 0]
    pair_budget = min(target_pairs, sum(capacities.values()))
    if pair_budget == 0:
        raise ClinVarPreparationError("no consequence-matched ClinVar pair survives")

    ranked = sorted(
        positive,
        key=lambda consequence: (
            stable_hash_rank(seed, "consequence", consequence),
            consequence,
        ),
    )
    represented = ranked[: min(pair_budget, len(ranked))]
    allocation = dict.fromkeys(represented, 1)
    remaining = pair_budget - len(represented)
    while remaining:
        eligible = [
            consequence
            for consequence in represented
            if allocation[consequence] < capacities[consequence]
        ]
        if not eligible:
            raise ClinVarPreparationError("pair allocation exhausted capacity unexpectedly")
        consequence = min(
            eligible,
            key=lambda item: (
                allocation[item],
                stable_hash_rank(seed, "consequence", item),
                item,
            ),
        )
        allocation[consequence] += 1
        remaining -= 1
    return dict(sorted(allocation.items()))


def prepare_dataset(
    parsed: ParsedCohort,
    joined: VepJoinResult,
    genome: Callable[[str, int, int], str] | None,
    *,
    config: PreparationConfig,
    clinvar_source: Mapping[str, Any],
    vep_source: Mapping[str, Any],
    reference: Mapping[str, Any],
    reference_validation: ReferenceValidation | None = None,
    processed_cache: Mapping[str, Any] | None = None,
) -> PreparedDataset:
    """Validate reference windows, sample matched strata, and build source records."""

    if reference_validation is None:
        if genome is None:
            raise ClinVarPreparationError(
                "genome is required when reference_validation is not supplied"
            )
        reference_validation = validate_reference_windows(joined, genome, config=config)
    valid = reference_validation.valid
    invalid = reference_validation.invalid

    strata: dict[tuple[str, str], list[WindowCandidate]] = defaultdict(list)
    for window_candidate in valid:
        strata[(window_candidate.joined.clinvar.label, window_candidate.joined.consequence)].append(
            window_candidate
        )
    consequences = sorted({consequence for _, consequence in strata})
    capacities = {
        consequence: min(
            len(strata.get(("Benign", consequence), ())),
            len(strata.get(("Pathogenic", consequence), ())),
        )
        for consequence in consequences
    }
    allocation = allocate_pairs(capacities, target_pairs=config.target_pairs, seed=config.seed)

    selected: list[WindowCandidate] = []
    for consequence, pairs in allocation.items():
        for label in LABELS:
            ranked = sorted(
                strata[(label, consequence)],
                key=lambda item: (
                    stable_hash_rank(
                        config.seed,
                        "variant",
                        label,
                        consequence,
                        *item.joined.key,
                    ),
                    item.joined.key,
                    item.joined.clinvar.source_record_id,
                ),
            )
            selected.extend(ranked[:pairs])

    records: list[dict[str, Any]] = []
    record_metadata: dict[str, dict[str, Any]] = {}
    for window_candidate in sorted(selected, key=lambda item: item.joined.clinvar.source_record_id):
        clinvar = window_candidate.joined.clinvar
        metadata = {
            "assembly": "GRCh38",
            "chrom": clinvar.chrom,
            "pos": clinvar.pos,
            "ref": clinvar.ref,
            "alt": clinvar.alt,
            "clinvar_accession": clinvar.accession,
            "clinvar_version": clinvar.version,
            "clinvar_variation_id": clinvar.variation_id,
            "first_public_date": clinvar.date_created,
            "classification": clinvar.label,
            "review_status": clinvar.review_status,
            "review_stars": clinvar.review_stars,
            "vep_consequence": window_candidate.joined.consequence,
            "genes": list(clinvar.genes),
            "transcripts": list(clinvar.transcripts),
            "conditions": [
                {"name": name, "database": database, "identifier": identifier}
                for name, database, identifier in clinvar.conditions
            ],
            "phenotypes": [name for name, _, _ in clinvar.conditions],
        }
        record_metadata[clinvar.source_record_id] = metadata
        records.append(
            {
                "answer_choice_id": CHOICE_BY_LABEL[clinvar.label],
                "choices": [dict(choice) for choice in CHOICES],
                "question": "Is this SNV classified by ClinVar as Benign or Pathogenic?",
                "source_dataset": str(clinvar_source["dataset_revision"]),
                "source_metadata": metadata,
                "source_record_id": clinvar.source_record_id,
                "tags": ["clinvar", "grch38", "sequence_context", "snv", "temporal_2026_07"],
                "task_family": "clinvar",
                "variant": render_local_variant(
                    window_candidate.sequence, clinvar.ref, clinvar.alt
                ),
            }
        )

    pair_budget = sum(allocation.values())
    capacity_detail = {
        consequence: {
            "Benign": len(strata.get(("Benign", consequence), ())),
            "Pathogenic": len(strata.get(("Pathogenic", consequence), ())),
            "capacity": capacities[consequence],
        }
        for consequence in consequences
    }
    manifest = {
        "schema_version": "1.0",
        "clinvar": dict(clinvar_source),
        "filters": {
            "date_created": {
                "field": "VariationArchive@DateCreated",
                "start_inclusive": config.start_date.isoformat(),
                "end_inclusive": config.end_date.isoformat(),
            },
            "classifications": list(LABELS),
            "review_statuses": dict(REVIEW_STARS),
            "assembly": "GRCh38",
            "primary_chromosomes": list(PRIMARY_CHROMS),
            "variant": "one-base A/C/G/T substitution with REF != ALT",
            "duplicate_allele_policy": "exclude every eligible VCV sharing an allele key",
        },
        "vep": dict(vep_source),
        "reference": dict(reference),
        "configuration": {
            "window_size": config.window_size,
            "flank_size": config.flank_size,
            "uppercase_sequence": True,
            "allowed_bases": "ACGT",
            "local_contig": "window",
            "local_variant_position": config.flank_size + 1,
            "fasta_line_width": FASTA_LINE_WIDTH,
        },
        "counts": {
            "clinvar_filter_stages": list(parsed.filter_stages),
            "rejected_duplicate_allele_keys": parsed.duplicate_allele_keys,
            "rejected_duplicate_records": parsed.duplicate_records_rejected,
            "vep_join": {
                "input": _clinvar_breakdown(parsed.candidates),
                "matched": _vep_breakdown(joined.matched),
                "missing": _clinvar_breakdown(joined.missing),
            },
            "reference_validation": {
                "valid": _window_breakdown(valid),
                "invalid": _vep_breakdown(invalid),
            },
        },
        "sampling": {
            "algorithm": SAMPLING_ALGORITHM,
            "seed": config.seed,
            "target_pairs": config.target_pairs,
            "pair_budget": pair_budget,
            "capacity_by_consequence": capacity_detail,
            "selected_pairs_by_consequence": allocation,
            "reason_below_target": (
                None
                if pair_budget == config.target_pairs
                else "matched reference-valid capacity is below the target pair count"
            ),
        },
        "choices": [dict(choice) for choice in CHOICES],
        "final_class_counts": dict(Counter(item.joined.clinvar.label for item in selected)),
        "final_consequence_counts": _nested_label_consequence_counts(
            item.joined for item in selected
        ),
        "record_metadata": dict(sorted(record_metadata.items())),
        "preparation_software": {"polars": pl.__version__},
    }
    if processed_cache is not None:
        manifest["processed_cache"] = dict(processed_cache)
    return PreparedDataset(records=tuple(records), manifest=manifest)


def validate_reference_windows(
    joined: VepJoinResult,
    genome: Callable[[str, int, int], str],
    *,
    config: PreparationConfig,
) -> ReferenceValidation:
    """Validate all joined candidates before deterministic sampling."""

    valid: list[WindowCandidate] = []
    invalid: list[VepCandidate] = []
    for joined_candidate in joined.matched:
        sequence = _validated_window(joined_candidate.clinvar, genome, config)
        if sequence is None:
            invalid.append(joined_candidate)
        else:
            valid.append(WindowCandidate(joined_candidate, sequence))
    return ReferenceValidation(
        valid=tuple(sorted(valid, key=lambda item: item.joined.key)),
        invalid=tuple(sorted(invalid, key=lambda item: item.key)),
    )


def write_prepared_dataset(
    prepared: PreparedDataset,
    *,
    output: str | Path,
    manifest_output: str | Path,
    output_relpath: str,
) -> tuple[int, str]:
    """Write canonical compact source JSONL and its digest-bearing manifest."""

    output_path = Path(output)
    output_path.parent.mkdir(parents=True, exist_ok=True)
    payload = "".join(f"{canonical_json(record)}\n" for record in prepared.records)
    output_path.write_text(payload, encoding="utf-8", newline="\n")
    digest = sha256_file(output_path)
    manifest = dict(prepared.manifest)
    manifest["output"] = {
        "path": output_relpath,
        "records": len(prepared.records),
        "bytes": output_path.stat().st_size,
        "sha256": digest,
    }
    manifest_path = Path(manifest_output)
    manifest_path.parent.mkdir(parents=True, exist_ok=True)
    manifest_path.write_text(f"{canonical_json(manifest)}\n", encoding="utf-8", newline="\n")
    return len(prepared.records), digest


def validate_prepared_artifacts(
    source_path: str | Path,
    manifest_path: str | Path,
) -> dict[str, Any]:
    """Validate committed ClinVar artifacts without accessing network sources."""

    records = read_jsonl(source_path)
    try:
        manifest = json.loads(Path(manifest_path).read_text(encoding="utf-8"))
    except json.JSONDecodeError as exc:
        raise ClinVarPreparationError(f"{manifest_path}: invalid JSON: {exc.msg}") from exc
    canonical_source = "".join(f"{canonical_json(record)}\n" for record in records).encode()
    if Path(source_path).read_bytes() != canonical_source:
        raise ClinVarPreparationError("source JSONL is not canonical UTF-8 with LF endings")
    output = manifest.get("output", {})
    if output.get("records") != len(records):
        raise ClinVarPreparationError("manifest record count does not match source JSONL")
    if output.get("bytes") != len(canonical_source):
        raise ClinVarPreparationError("manifest byte count does not match source JSONL")
    if output.get("sha256") != sha256_file(source_path):
        raise ClinVarPreparationError("manifest digest does not match source JSONL")
    if manifest.get("choices") != list(CHOICES):
        raise ClinVarPreparationError("manifest choices are not the stable ClinVar choices")

    expected_metadata = manifest.get("record_metadata")
    if not isinstance(expected_metadata, dict):
        raise ClinVarPreparationError("manifest record_metadata must be an object")
    labels: Counter[str] = Counter()
    consequence_labels: dict[str, Counter[str]] = defaultdict(Counter)
    seen_ids: set[str] = set()
    for record in records:
        source_id = record.get("source_record_id")
        if source_id in seen_ids:
            raise ClinVarPreparationError(f"duplicate source record ID {source_id!r}")
        seen_ids.add(str(source_id))
        if record.get("task_family") != "clinvar" or record.get("choices") != list(CHOICES):
            raise ClinVarPreparationError(f"{source_id}: invalid task family or choices")
        metadata = record.get("source_metadata")
        if metadata != expected_metadata.get(source_id):
            raise ClinVarPreparationError(f"{source_id}: metadata disagrees with manifest")
        if not isinstance(metadata, dict):
            raise ClinVarPreparationError(f"{source_id}: source_metadata must be an object")
        label = metadata.get("classification")
        consequence = metadata.get("vep_consequence")
        if label not in LABELS or not isinstance(consequence, str) or not consequence:
            raise ClinVarPreparationError(f"{source_id}: invalid label or VEP consequence")
        if record.get("answer_choice_id") != CHOICE_BY_LABEL[label]:
            raise ClinVarPreparationError(f"{source_id}: answer choice disagrees with label")
        labels[label] += 1
        consequence_labels[consequence][label] += 1
        _validate_rendered_variant(record.get("variant"), manifest)

    if set(expected_metadata) != seen_ids:
        raise ClinVarPreparationError("manifest record metadata IDs do not match source IDs")
    if labels["Benign"] != labels["Pathogenic"] or not labels["Benign"]:
        raise ClinVarPreparationError("final cohort is not non-empty and label-balanced")
    for consequence, counts in consequence_labels.items():
        if counts["Benign"] != counts["Pathogenic"]:
            raise ClinVarPreparationError(f"{consequence}: labels are not balanced")
    if dict(sorted(labels.items())) != manifest.get("final_class_counts"):
        raise ClinVarPreparationError("final label counts disagree with manifest")
    observed_consequences = {
        consequence: dict(sorted(counts.items()))
        for consequence, counts in sorted(consequence_labels.items())
    }
    if observed_consequences != manifest.get("final_consequence_counts"):
        raise ClinVarPreparationError("final consequence counts disagree with manifest")
    selected_pairs = manifest.get("sampling", {}).get("selected_pairs_by_consequence", {})
    observed_pairs = {
        consequence: counts["Benign"] for consequence, counts in consequence_labels.items()
    }
    if dict(sorted(observed_pairs.items())) != selected_pairs:
        raise ClinVarPreparationError("selected pair counts disagree with source records")
    return manifest


def _classification_fields(
    archive: ElementTree.Element,
) -> tuple[str, ElementTree.Element | None, ElementTree.Element | None]:
    classified = _direct_child(archive, "ClassifiedRecord")
    classifications = _direct_child(classified, "Classifications")
    germline = _direct_child(classifications, "GermlineClassification")
    label = _child_text(germline, "Description") or "<missing>"
    return label, germline, classified


def _is_primary_location(location: ElementTree.Element) -> bool:
    chrom = (location.get("Chr") or "").removeprefix("chr").upper()
    if chrom not in PRIMARY_ACCESSIONS:
        return False
    # Accession identifies the chromosome sequence. AssemblyAccessionVersion is
    # the assembly-level GCF accession and must not be used as the contig key.
    accession = location.get("Accession")
    return accession == PRIMARY_ACCESSIONS[chrom]


def _location_key(location: ElementTree.Element) -> tuple[str, int, str, str] | None:
    chrom = (location.get("Chr") or "").removeprefix("chr").upper()
    possible: set[tuple[str, int, str, str]] = set()
    for position_field, ref_field, alt_field in (
        ("positionVCF", "referenceAlleleVCF", "alternateAlleleVCF"),
        ("start", "referenceAllele", "alternateAllele"),
    ):
        position = _positive_int(location.get(position_field))
        ref = (location.get(ref_field) or "").upper()
        alt = (location.get(alt_field) or "").upper()
        if position is not None and ref and alt:
            possible.add((chrom, position, ref, alt))
    if len(possible) != 1:
        return None
    key = next(iter(possible))
    if key[0] not in PRIMARY_CHROMS:
        return None
    if key[2] not in BASE_CODES or key[3] not in BASE_CODES or key[2] == key[3]:
        return None
    start = _positive_int(location.get("start"))
    stop = _positive_int(location.get("stop"))
    if start is not None and stop is not None and start != stop:
        return None
    variant_length = _positive_int(location.get("variantLength"))
    if variant_length is not None and variant_length != 1:
        return None
    return key


def _transcript_accessions(allele: ElementTree.Element) -> set[str]:
    accessions: set[str] = set()
    hgvs_list = _direct_child(allele, "HGVSlist")
    for hgvs in _direct_children(hgvs_list, "HGVS"):
        expression = _direct_child(hgvs, "NucleotideExpression")
        if expression is None or expression.get("sequenceType") not in {"coding", "non-coding"}:
            continue
        accession = expression.get("sequenceAccessionVersion") or expression.get(
            "sequenceAccession"
        )
        if accession:
            accessions.add(accession)
    return accessions


def _classified_conditions(
    classified: ElementTree.Element | None,
) -> set[tuple[str, str | None, str | None]]:
    conditions: set[tuple[str, str | None, str | None]] = set()
    rcv_list = _direct_child(classified, "RCVList")
    for rcv in _direct_children(rcv_list, "RCVAccession"):
        condition_list = _direct_child(rcv, "ClassifiedConditionList")
        for condition in _direct_children(condition_list, "ClassifiedCondition"):
            name = (condition.text or "").strip()
            if name:
                conditions.add((name, condition.get("DB"), condition.get("ID")))
    return conditions


def _validated_window(
    candidate: ClinVarCandidate,
    genome: Callable[[str, int, int], str],
    config: PreparationConfig,
) -> str | None:
    start = candidate.pos - 1 - config.flank_size
    end = candidate.pos + config.flank_size
    try:
        sequence = str(genome(candidate.chrom, start, end)).upper()
    except KeyError, ValueError:
        return None
    if len(sequence) != config.window_size or set(sequence) - set(BASE_CODES):
        return None
    if sequence[config.flank_size] != candidate.ref:
        return None
    return sequence


def _validate_rendered_variant(variant: Any, manifest: Mapping[str, Any]) -> None:
    if not isinstance(variant, str) or variant.count("```fasta\n") != 1:
        raise ClinVarPreparationError("variant must contain one FASTA block")
    if variant.count("```vcf\n") != 1:
        raise ClinVarPreparationError("variant must contain one VCF block")
    fasta = variant.split("```fasta\n", 1)[1].split("\n```", 1)[0].splitlines()
    vcf = variant.split("```vcf\n", 1)[1].split("\n```", 1)[0].splitlines()
    if not fasta or fasta[0] != ">window" or not vcf:
        raise ClinVarPreparationError("variant has an invalid local FASTA or VCF")
    sequence_lines = fasta[1:]
    width = manifest["configuration"]["fasta_line_width"]
    if (
        not sequence_lines
        or any(len(line) != width for line in sequence_lines[:-1])
        or not 0 < len(sequence_lines[-1]) <= width
    ):
        raise ClinVarPreparationError("FASTA wrapping disagrees with the manifest")
    sequence = "".join(sequence_lines)
    if len(sequence) != manifest["configuration"]["window_size"]:
        raise ClinVarPreparationError("FASTA window length disagrees with the manifest")
    fields = vcf[-1].split("\t")
    if len(fields) != 8 or fields[0] != "window":
        raise ClinVarPreparationError("VCF data line is malformed")
    position = int(fields[1])
    ref, alt = fields[3], fields[4]
    if position != manifest["configuration"]["local_variant_position"]:
        raise ClinVarPreparationError("VCF position is not the configured center")
    if ref == alt or ref not in BASE_CODES or alt not in BASE_CODES:
        raise ClinVarPreparationError("VCF alleles are not a valid SNV")
    if sequence[position - 1] != ref or set(sequence) - set(BASE_CODES):
        raise ClinVarPreparationError("VCF REF disagrees with the FASTA center")


def _clinvar_breakdown(candidates: Iterable[ClinVarCandidate]) -> dict[str, Any]:
    candidate_list = list(candidates)
    return {
        "records": len(candidate_list),
        "by_label": dict(sorted(Counter(item.label for item in candidate_list).items())),
    }


def _vep_breakdown(candidates: Iterable[VepCandidate]) -> dict[str, Any]:
    candidate_list = list(candidates)
    return {
        "records": len(candidate_list),
        "by_label": dict(sorted(Counter(item.clinvar.label for item in candidate_list).items())),
        "by_consequence": dict(
            sorted(Counter(item.consequence for item in candidate_list).items())
        ),
        "by_label_and_consequence": _nested_label_consequence_counts(candidate_list),
    }


def _window_breakdown(candidates: Iterable[WindowCandidate]) -> dict[str, Any]:
    return _vep_breakdown(item.joined for item in candidates)


def _nested_label_consequence_counts(
    candidates: Iterable[VepCandidate],
) -> dict[str, dict[str, int]]:
    counts: dict[str, Counter[str]] = defaultdict(Counter)
    for candidate in candidates:
        counts[candidate.consequence][candidate.clinvar.label] += 1
    return {
        consequence: dict(sorted(label_counts.items()))
        for consequence, label_counts in sorted(counts.items())
    }


def _count_stage(stages: Mapping[str, Counter[str]], stage: str, label: str) -> None:
    stages[stage][label] += 1


def _chrom_sort_key(chrom: str) -> int:
    return PRIMARY_CHROMS.index(chrom)


def _positive_int(value: str | None) -> int | None:
    try:
        parsed = int(value) if value is not None else 0
    except ValueError:
        return None
    return parsed if parsed > 0 else None


def _parse_iso_date(value: str | None) -> date | None:
    try:
        return date.fromisoformat(value) if value is not None else None
    except ValueError:
        return None


def _child_text(parent: ElementTree.Element | None, name: str) -> str:
    child = _direct_child(parent, name)
    return (child.text or "").strip() if child is not None else ""


def _direct_child(
    parent: ElementTree.Element | None,
    name: str,
) -> ElementTree.Element | None:
    return next(iter(_direct_children(parent, name)), None)


def _direct_children(
    parent: ElementTree.Element | None,
    name: str,
) -> list[ElementTree.Element]:
    if parent is None:
        return []
    return [child for child in parent if _local_name(child.tag) == name]


def _local_name(tag: str) -> str:
    return tag.rsplit("}", 1)[-1]
