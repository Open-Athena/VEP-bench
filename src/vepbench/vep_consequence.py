"""Deterministic Polars preparation for the VEP consequence task."""

import json
import textwrap
from collections import Counter
from collections.abc import Callable, Mapping
from dataclasses import dataclass
from pathlib import Path
from typing import Any

import polars as pl

from .builder import BuildError, canonical_json, read_jsonl, sha256_file

REQUIRED_COLUMNS = ("chrom", "pos", "ref", "alt", "consequence")
VARIANT_COLUMNS = ("chrom", "pos", "ref", "alt")
BASE_CODES = {"A": 1, "C": 2, "G": 3, "T": 4}
CHROM_CODES = {str(index): index for index in range(1, 23)} | {
    "X": 23,
    "Y": 24,
    "MT": 25,
}
RANK_MULTIPLIER = 1_000_003
RANK_MODULUS = 2_147_483_647
COLLAPSED_LABEL = (
    "intergenic_variant / intron_variant / upstream_gene_variant / "
    "downstream_gene_variant"
)
COLLAPSE_MAP = {
    "downstream_gene_variant": COLLAPSED_LABEL,
    "intergenic_variant": COLLAPSED_LABEL,
    "intron_variant": COLLAPSED_LABEL,
    "upstream_gene_variant": COLLAPSED_LABEL,
}
COLLAPSED_SOURCE_QUOTAS = {
    "downstream_gene_variant": 2,
    "intergenic_variant": 3,
    "intron_variant": 3,
    "upstream_gene_variant": 2,
}


class PreparationError(BuildError):
    """Raised when source data cannot produce the fixed VEP task."""


@dataclass(frozen=True)
class VariantCandidate:
    chrom: str
    pos: int
    ref: str
    alt: str
    consequence: str

    @property
    def key(self) -> tuple[str, int, str, str]:
        return (self.chrom, self.pos, self.ref, self.alt)

    @property
    def source_record_id(self) -> str:
        return f"{self.chrom}:{self.pos}:{self.ref}:{self.alt}"


@dataclass(frozen=True)
class RankedCandidate:
    rank: int
    candidate: VariantCandidate

    @property
    def sort_key(self) -> tuple[int, str, int, str, str]:
        return (self.rank, *self.candidate.key)


@dataclass(frozen=True)
class CandidateScan:
    candidates: Mapping[str, tuple[RankedCandidate, ...]]
    raw_counts: Mapping[str, int]


@dataclass(frozen=True)
class PreparationConfig:
    chromosome: str = "17"
    flank_size: int = 500
    per_class_quota: int = 10
    candidate_pool_size: int = 128
    seed: int = 2_026_082_800

    @property
    def window_size(self) -> int:
        return self.flank_size * 2 + 1


@dataclass(frozen=True)
class PreparedDataset:
    records: tuple[dict[str, Any], ...]
    manifest: dict[str, Any]


def stable_variant_rank(chrom: str, pos: int, ref: str, alt: str, seed: int) -> int:
    """Return the versioned, platform-independent sampling rank for an SNV."""

    try:
        chrom_code = CHROM_CODES[chrom.upper()]
        ref_code = BASE_CODES[ref.upper()]
        alt_code = BASE_CODES[alt.upper()]
    except KeyError as exc:
        raise PreparationError(
            f"unsupported chromosome or allele in {chrom}:{pos}:{ref}:{alt}"
        ) from exc
    if pos < 1:
        raise PreparationError(f"position must be positive in {chrom}:{pos}:{ref}:{alt}")
    if not 0 <= seed < RANK_MODULUS:
        raise PreparationError(f"seed must be between 0 and {RANK_MODULUS - 1}")
    variant_number = (((chrom_code * 1_000_000_000 + pos) * 5 + ref_code) * 5) + alt_code
    return (variant_number * RANK_MULTIPLIER + seed) % RANK_MODULUS


def scan_candidate_pools(
    parquet_source: str | Path,
    *,
    seed: int,
    pool_size: int,
    progress: Callable[[str], None] | None = None,
) -> CandidateScan:
    """Load the chr17 Parquet file and retain a bounded pool per label."""

    if pool_size < 1:
        raise PreparationError("pool_size must be positive")
    if not 0 <= seed < RANK_MODULUS:
        raise PreparationError(f"seed must be between 0 and {RANK_MODULUS - 1}")

    source = str(parquet_source)
    report = progress or (lambda _message: None)
    try:
        frame = pl.read_parquet(
            source,
            columns=list(REQUIRED_COLUMNS),
            low_memory=True,
        )
        report(
            f"loaded {frame.height:,} rows "
            f"({frame.estimated_size() / (1024**3):.1f} GiB estimated)"
        )
        frame = (
            frame.with_columns(
                pl.col("chrom").cast(pl.String),
                pl.col("pos").cast(pl.Int64),
                pl.col("ref").cast(pl.String).str.to_uppercase(),
                pl.col("alt").cast(pl.String).str.to_uppercase(),
                pl.col("consequence").cast(pl.String),
            )
            .with_columns(_stable_rank_expression(seed).alias("_sample_rank"))
        )
        _validate_source_frame(frame, source)
        frame = frame.with_columns(
            pl.col("chrom").cast(pl.Categorical),
            pl.col("consequence").cast(pl.Categorical),
        )
        report(
            "normalized and ranked variants "
            f"({frame.estimated_size() / (1024**3):.1f} GiB estimated)"
        )
        report("computing bounded candidate pools per consequence")
        grouped = frame.group_by("consequence").agg(
            pl.len().alias("_raw_count"),
            pl.struct("chrom", "pos", "ref", "alt", "_sample_rank")
            .top_k_by(
                ["_sample_rank", "chrom", "pos", "ref", "alt"],
                k=pool_size,
                reverse=True,
            )
            .alias("_candidate"),
        )
        report("candidate-pool aggregation complete")
    except Exception as exc:
        raise PreparationError(f"could not load and aggregate {source}: {exc}") from exc

    raw_counts = {
        str(consequence): int(count)
        for consequence, count in grouped.select(
            "consequence", "_raw_count"
        ).iter_rows()
    }
    pools: dict[str, list[RankedCandidate]] = {}
    try:
        reduced = grouped.explode("_candidate", empty_as_null=True).unnest(
            "_candidate"
        )
        for row in reduced.iter_rows(named=True):
            consequence = str(row["consequence"])
            candidate = VariantCandidate(
                chrom=str(row["chrom"]),
                pos=int(row["pos"]),
                ref=str(row["ref"]),
                alt=str(row["alt"]),
                consequence=consequence,
            )
            _validate_candidate(candidate)
            pools.setdefault(consequence, []).append(
                RankedCandidate(int(row["_sample_rank"]), candidate)
            )
        for consequence in pools:
            pools[consequence] = sorted(
                pools[consequence], key=lambda item: item.sort_key
            )
    except PreparationError:
        raise
    except Exception as exc:
        raise PreparationError(f"failed to convert candidates from {source}: {exc}") from exc

    if not raw_counts:
        raise PreparationError(f"{source}: no variants found")

    return CandidateScan(
        candidates={key: tuple(value) for key, value in sorted(pools.items())},
        raw_counts=dict(sorted(raw_counts.items())),
    )


def prepare_dataset(
    scan: CandidateScan,
    genome: Callable[[str, int, int], str],
    *,
    config: PreparationConfig,
    source: Mapping[str, str],
    reference: Mapping[str, str],
) -> PreparedDataset:
    """Fetch valid windows and turn candidate pools into builder source records."""

    observed = set(scan.raw_counts)
    missing_collapsed = set(COLLAPSED_SOURCE_QUOTAS) - observed
    if missing_collapsed:
        raise PreparationError(
            f"missing collapsed source consequences: {sorted(missing_collapsed)}"
        )

    final_vocabulary = sorted(COLLAPSE_MAP.get(label, label) for label in observed)
    final_vocabulary = sorted(set(final_vocabulary))
    choices = [
        {"choice_id": f"C{index:02d}", "text": label}
        for index, label in enumerate(final_vocabulary, start=1)
    ]
    choice_by_label = {choice["text"]: choice["choice_id"] for choice in choices}
    source_quotas = {
        label: COLLAPSED_SOURCE_QUOTAS.get(label, config.per_class_quota)
        for label in observed
    }

    records: list[dict[str, Any]] = []
    skipped: Counter[str] = Counter()
    selected_source_counts: Counter[str] = Counter()
    record_source_consequences: dict[str, str] = {}
    for source_consequence in sorted(source_quotas):
        quota = source_quotas[source_consequence]
        for ranked in scan.candidates.get(source_consequence, ()):
            if selected_source_counts[source_consequence] >= quota:
                break
            candidate = ranked.candidate
            sequence = _validated_window(candidate, genome, config)
            if sequence is None:
                skipped[source_consequence] += 1
                continue
            final_label = COLLAPSE_MAP.get(source_consequence, source_consequence)
            records.append(
                {
                    "answer_choice_id": choice_by_label[final_label],
                    "choices": choices,
                    "question": (
                        "What is the Ensembl VEP most severe consequence for this SNV?"
                    ),
                    "source_dataset": source["dataset_revision"],
                    "source_record_id": candidate.source_record_id,
                    "tags": ["grch38", "sequence_context", "snv", "vep_109.1"],
                    "task_family": "vep_most_severe_consequence",
                    "variant": render_local_variant(sequence, candidate.ref, candidate.alt),
                }
            )
            record_source_consequences[candidate.source_record_id] = source_consequence
            selected_source_counts[source_consequence] += 1
        if selected_source_counts[source_consequence] != quota:
            raise PreparationError(
                f"{source_consequence}: needed {quota} valid variants, found "
                f"{selected_source_counts[source_consequence]} in candidate pool of "
                f"{len(scan.candidates.get(source_consequence, ()))}"
            )

    records.sort(key=lambda record: record["source_record_id"])
    final_counts = Counter(
        next(
            choice["text"]
            for choice in record["choices"]
            if choice["choice_id"] == record["answer_choice_id"]
        )
        for record in records
    )
    expected_final_counts = dict.fromkeys(final_vocabulary, config.per_class_quota)
    if dict(sorted(final_counts.items())) != expected_final_counts:
        raise PreparationError(
            f"final class counts do not match quotas: {dict(sorted(final_counts.items()))}"
        )

    manifest = {
        "schema_version": "1.0",
        "source": dict(source),
        "reference": dict(reference),
        "vep": {
            "version": "release 109.1",
            "flags": ["--most_severe", "--distance", "1000"],
        },
        "configuration": {
            "chromosome": config.chromosome,
            "window_size": config.window_size,
            "flank_size": config.flank_size,
            "uppercase_sequence": True,
            "allowed_bases": "ACGT",
            "local_contig": "window",
            "local_variant_position": config.flank_size + 1,
        },
        "sampling": {
            "seed": config.seed,
            "algorithm": "vepbench_variant_rank_v1",
            "rank_multiplier": RANK_MULTIPLIER,
            "rank_modulus": RANK_MODULUS,
            "candidate_pool_size": config.candidate_pool_size,
            "input_mode": "local_eager_polars",
            "raw_counts": dict(sorted(scan.raw_counts.items())),
            "source_quotas": dict(sorted(source_quotas.items())),
            "selected_source_counts": dict(sorted(selected_source_counts.items())),
            "invalid_candidates_skipped": dict(sorted(skipped.items())),
        },
        "collapse_mapping": dict(sorted(COLLAPSE_MAP.items())),
        "final_vocabulary": final_vocabulary,
        "choices": choices,
        "final_class_counts": dict(sorted(final_counts.items())),
        "record_source_consequences": dict(sorted(record_source_consequences.items())),
        "preparation_software": {"polars": pl.__version__},
    }
    return PreparedDataset(records=tuple(records), manifest=manifest)


def write_prepared_dataset(
    prepared: PreparedDataset,
    *,
    output: str | Path,
    manifest_output: str | Path,
    output_relpath: str,
) -> tuple[int, str]:
    """Write deterministic source JSONL and its digest-bearing manifest."""

    output_path = Path(output)
    output_path.parent.mkdir(parents=True, exist_ok=True)
    payload = "".join(f"{canonical_json(record)}\n" for record in prepared.records)
    output_path.write_text(payload, encoding="utf-8", newline="\n")
    digest = sha256_file(output_path)

    manifest = dict(prepared.manifest)
    manifest["output"] = {
        "path": output_relpath,
        "records": len(prepared.records),
        "sha256": digest,
    }
    manifest_path = Path(manifest_output)
    manifest_path.parent.mkdir(parents=True, exist_ok=True)
    manifest_path.write_text(
        f"{canonical_json(manifest)}\n", encoding="utf-8", newline="\n"
    )
    return len(prepared.records), digest


def validate_prepared_artifacts(
    source_path: str | Path, manifest_path: str | Path
) -> dict[str, Any]:
    """Validate the compact committed artifacts without accessing their sources."""

    records = read_jsonl(source_path)
    try:
        manifest = json.loads(Path(manifest_path).read_text(encoding="utf-8"))
    except json.JSONDecodeError as exc:
        raise PreparationError(f"{manifest_path}: invalid JSON: {exc.msg}") from exc
    output = manifest.get("output", {})
    if output.get("records") != len(records):
        raise PreparationError("manifest record count does not match source JSONL")
    if output.get("sha256") != sha256_file(source_path):
        raise PreparationError("manifest digest does not match source JSONL")

    vocabulary = manifest.get("final_vocabulary")
    choices = manifest.get("choices")
    if not isinstance(vocabulary, list) or vocabulary != sorted(vocabulary):
        raise PreparationError("manifest vocabulary must be a sorted list")
    expected_choices = [
        {"choice_id": f"C{index:02d}", "text": label}
        for index, label in enumerate(vocabulary, start=1)
    ]
    if choices != expected_choices:
        raise PreparationError("manifest choices do not match the stable vocabulary")

    answer_text_by_id = {choice["choice_id"]: choice["text"] for choice in choices}
    final_counts: Counter[str] = Counter()
    source_counts: Counter[str] = Counter()
    for record in records:
        if record["choices"] != choices:
            raise PreparationError(
                f"{record['source_record_id']}: choices do not match manifest"
            )
        try:
            final_counts[answer_text_by_id[record["answer_choice_id"]]] += 1
        except KeyError as exc:
            raise PreparationError(
                f"{record['source_record_id']}: unknown answer choice"
            ) from exc
        source_consequence = _source_consequence_for_record(record, manifest)
        source_counts[source_consequence] += 1
        _validate_rendered_variant(record["variant"], manifest)

    if dict(sorted(final_counts.items())) != manifest.get("final_class_counts"):
        raise PreparationError("source final class counts do not match manifest")
    if dict(sorted(source_counts.items())) != manifest.get("sampling", {}).get(
        "selected_source_counts"
    ):
        raise PreparationError("source consequence counts do not match manifest")
    return manifest


def render_local_variant(sequence: str, ref: str, alt: str) -> str:
    wrapped = "\n".join(textwrap.wrap(sequence, width=80))
    return (
        "```fasta\n"
        ">window\n"
        f"{wrapped}\n"
        "```\n\n"
        "```vcf\n"
        "##fileformat=VCFv4.3\n"
        f"##contig=<ID=window,length={len(sequence)}>\n"
        "#CHROM\tPOS\tID\tREF\tALT\tQUAL\tFILTER\tINFO\n"
        f"window\t{len(sequence) // 2 + 1}\t.\t{ref}\t{alt}\t.\tPASS\t.\n"
        "```"
    )


def _stable_rank_expression(seed: int) -> pl.Expr:
    chrom = pl.col("chrom").replace_strict(CHROM_CODES, return_dtype=pl.Int64)
    ref = pl.col("ref").replace_strict(BASE_CODES, return_dtype=pl.Int64)
    alt = pl.col("alt").replace_strict(BASE_CODES, return_dtype=pl.Int64)
    variant_number = (((chrom * 1_000_000_000 + pl.col("pos")) * 5 + ref) * 5) + alt
    return (variant_number * RANK_MULTIPLIER + seed) % RANK_MODULUS


def _validate_source_frame(frame: pl.DataFrame, source: str) -> None:
    invalid = pl.any_horizontal(
        *[pl.col(column).is_null() for column in REQUIRED_COLUMNS],
        pl.col("pos") < 1,
        pl.col("ref") == pl.col("alt"),
        ~pl.col("chrom").is_in(list(CHROM_CODES)),
        ~pl.col("ref").is_in(list(BASE_CODES)),
        ~pl.col("alt").is_in(list(BASE_CODES)),
        pl.col("consequence").str.len_chars() == 0,
    ).any()
    if frame.select(invalid).item():
        raise PreparationError(f"{source}: required columns contain invalid values")


def _validate_candidate(candidate: VariantCandidate) -> None:
    stable_variant_rank(*candidate.key, seed=0)
    if candidate.ref == candidate.alt:
        raise PreparationError(
            f"{candidate.source_record_id}: REF and ALT must be different SNV alleles"
        )
    if not candidate.consequence:
        raise PreparationError(f"{candidate.source_record_id}: consequence is empty")


def _validated_window(
    candidate: VariantCandidate,
    genome: Callable[[str, int, int], str],
    config: PreparationConfig,
) -> str | None:
    zero_based_position = candidate.pos - 1
    start = zero_based_position - config.flank_size
    end = zero_based_position + config.flank_size + 1
    try:
        sequence = str(genome(candidate.chrom, start, end)).upper()
    except (KeyError, ValueError):
        return None
    if len(sequence) != config.window_size or set(sequence) - set(BASE_CODES):
        return None
    if sequence[config.flank_size] != candidate.ref:
        return None
    return sequence


def _source_consequence_for_record(
    record: Mapping[str, Any], manifest: Mapping[str, Any]
) -> str:
    source_id = record["source_record_id"]
    selected_counts = manifest.get("sampling", {}).get("selected_source_counts", {})
    final_by_choice = {
        choice["choice_id"]: choice["text"] for choice in manifest.get("choices", [])
    }
    final_label = final_by_choice.get(record["answer_choice_id"])
    if final_label != COLLAPSED_LABEL:
        return str(final_label)

    # Collapsed records cannot be reverse-mapped from their public builder fields.
    # The source composition is instead committed explicitly as an audit mapping.
    audit_mapping = manifest.get("record_source_consequences", {})
    source_consequence = audit_mapping.get(source_id)
    if source_consequence not in selected_counts:
        raise PreparationError(f"{source_id}: missing collapsed source audit mapping")
    return str(source_consequence)


def _validate_rendered_variant(variant: str, manifest: Mapping[str, Any]) -> None:
    try:
        fasta_block = variant.split("```fasta\n", 1)[1].split("\n```", 1)[0]
        vcf_block = variant.split("```vcf\n", 1)[1].split("\n```", 1)[0]
    except IndexError as exc:
        raise PreparationError("variant is missing inline FASTA or VCF") from exc
    fasta_lines = fasta_block.splitlines()
    if not fasta_lines or fasta_lines[0] != ">window":
        raise PreparationError("FASTA header must be >window")
    sequence = "".join(fasta_lines[1:])
    window_size = manifest["configuration"]["window_size"]
    if len(sequence) != window_size or set(sequence) - set(BASE_CODES):
        raise PreparationError("FASTA sequence does not match the configured window")
    fields = vcf_block.splitlines()[-1].split("\t")
    if len(fields) != 8 or fields[0] != "window":
        raise PreparationError("VCF data line is malformed")
    position = int(fields[1])
    ref, alt = fields[3], fields[4]
    if position != manifest["configuration"]["local_variant_position"]:
        raise PreparationError("VCF position does not match the configured center")
    invalid_alleles = ref == alt or ref not in BASE_CODES or alt not in BASE_CODES
    if sequence[position - 1] != ref or invalid_alleles:
        raise PreparationError("VCF alleles disagree with the FASTA center")
