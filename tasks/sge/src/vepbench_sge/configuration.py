"""Strict human-maintained configuration for SGE preparation."""

from __future__ import annotations

from dataclasses import dataclass
from pathlib import Path
from typing import Any

from vepbench.config.loader import load_yaml_mapping
from vepbench.errors import BuildError

PREPARATION_CONFIG_PATH = Path(__file__).resolve().parents[2] / "config" / "preparation.yaml"
REVIEWED_GENES = frozenset(
    {
        "BAP1",
        "BARD1",
        "BRCA1",
        "BRCA2",
        "CTCF",
        "CARD11",
        "DDX3X",
        "PALB2",
        "RAD51C",
        "RAD51D",
        "SBDS",
        "SFPQ",
        "TINF2",
        "TP53",
        "VHL",
        "XRCC2",
    }
)


@dataclass(frozen=True)
class PreparationConfig:
    """Resolved SGE preparation configuration and immutable source pins."""

    path: Path
    values: dict[str, Any]
    pins: dict[str, Any]

    def resolve_path(self, key: str) -> Path:
        value = self.values[key]
        if not isinstance(value, str) or not value:
            raise BuildError(f"{self.path}: {key} must be a non-empty path")
        return (self.path.parent / value).resolve()


def load_preparation_config(path: str | Path = PREPARATION_CONFIG_PATH) -> PreparationConfig:
    """Load and validate every scientific choice needed to build SGE."""

    source_path, values = load_yaml_mapping(path, label="SGE preparation config")
    _require_exact_fields(
        values,
        {
            "schema_version",
            "task_family",
            "reference_contig",
            "source_dataset",
            "source_pins",
            "output",
            "manifest_output",
            "sampling",
            "sequence",
            "upstream",
            "cache",
            "genes",
        },
        source_path,
        "preparation config",
    )
    if values["schema_version"] != "1.0" or values["task_family"] != "sge":
        raise BuildError(f"{source_path}: unsupported SGE preparation config")
    for field in (
        "reference_contig",
        "source_dataset",
        "source_pins",
        "output",
        "manifest_output",
    ):
        _require_string(values[field], source_path, field)
    if values["reference_contig"] != "element":
        raise BuildError(f"{source_path}: reference_contig must be 'element'")

    from vepbench.sampling import validate_sampling_config

    validate_sampling_config(values["sampling"])

    sequence = _require_mapping(values["sequence"], source_path, "sequence")
    _require_exact_fields(
        sequence,
        {"flank_bases", "display_orientation", "line_width"},
        source_path,
        "sequence",
    )
    _require_positive_int(sequence["flank_bases"], source_path, "sequence.flank_bases")
    _require_positive_int(sequence["line_width"], source_path, "sequence.line_width")
    if sequence["flank_bases"] != 100 or sequence["display_orientation"] != "transcript_5_to_3":
        raise BuildError(f"{source_path}: SGE display policy must use exact 100 bp flanks")

    upstream = _require_mapping(values["upstream"], source_path, "upstream")
    _require_exact_fields(
        upstream,
        {"mavedb", "cdot", "reference"},
        source_path,
        "upstream",
    )
    mavedb = _require_mapping(upstream["mavedb"], source_path, "upstream.mavedb")
    _require_exact_fields(mavedb, {"api_base", "catalog_query"}, source_path, "mavedb")
    _require_string(mavedb["api_base"], source_path, "upstream.mavedb.api_base")
    query = _require_mapping(mavedb["catalog_query"], source_path, "catalog_query")
    _require_exact_fields(query, {"published", "text", "offset", "limit"}, source_path, "query")
    if query["published"] is not True or query["offset"] != 0:
        raise BuildError(f"{source_path}: catalog query must cover public records from offset zero")
    _require_string(query["text"], source_path, "catalog_query.text")
    _require_positive_int(query["limit"], source_path, "catalog_query.limit")
    for field in ("cdot", "reference"):
        record = _require_mapping(upstream[field], source_path, f"upstream.{field}")
        required = {
            "cdot": {"api_base", "data_version"},
            "reference": {"dataset", "revision", "filename", "assembly"},
        }[field]
        _require_exact_fields(record, required, source_path, f"upstream.{field}")
        for name in required:
            _require_string(record[name], source_path, f"upstream.{field}.{name}")

    cache = _require_mapping(values["cache"], source_path, "cache")
    _require_exact_fields(
        cache,
        {"bucket", "root", "data_files", "implementation_sha256"},
        source_path,
        "cache",
    )
    for field in ("bucket", "root", "implementation_sha256"):
        _require_string(cache[field], source_path, f"cache.{field}")
    if (
        len(cache["implementation_sha256"]) != 64
        or any(character not in "0123456789abcdef" for character in cache["implementation_sha256"])
        or set(cache["implementation_sha256"]) == {"0"}
    ):
        raise BuildError(f"{source_path}: cache.implementation_sha256 must be a nonzero digest")
    _require_unique_strings(cache["data_files"], source_path, "cache.data_files")

    genes = values["genes"]
    if not isinstance(genes, list) or len(genes) != len(REVIEWED_GENES):
        raise BuildError(f"{source_path}: genes must contain one entry per reviewed gene")
    required_gene = {
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
        "assay_context",
    }
    seen_genes: set[str] = set()
    seen_urns: set[str] = set()
    for index, raw in enumerate(genes, start=1):
        gene = _require_mapping(raw, source_path, f"genes[{index}]")
        _require_exact_fields(gene, required_gene, source_path, f"genes[{index}]")
        for field in required_gene - {"score_direction", "qc"}:
            _require_string(gene[field], source_path, f"genes[{index}].{field}")
        if gene["gene"] not in REVIEWED_GENES or gene["gene"] in seen_genes:
            raise BuildError(f"{source_path}: duplicate or unexpected reviewed gene")
        if gene["mavedb_urn"] in seen_urns:
            raise BuildError(f"{source_path}: canonical score-set URNs must be unique")
        if gene["transcript_policy"] not in {"declared", "mane_select_fallback"}:
            raise BuildError(f"{source_path}: invalid transcript policy for {gene['gene']}")
        if gene["coordinate_mode"] not in {
            "hgvs_genomic",
            "hgvs_transcript",
            "target_coding_hgvs",
        }:
            raise BuildError(f"{source_path}: invalid coordinate mode for {gene['gene']}")
        if gene["score_direction"] not in {-1, 1}:
            raise BuildError(f"{source_path}: score direction must be -1 or 1")
        qc = _require_mapping(gene["qc"], source_path, f"genes[{index}].qc")
        _require_exact_fields(qc, {"field", "pass_values", "fail_values"}, source_path, "qc")
        if qc["field"] is not None:
            _require_string(qc["field"], source_path, f"genes[{index}].qc.field")
        for field in ("pass_values", "fail_values"):
            _require_unique_strings(qc[field], source_path, f"genes[{index}].qc.{field}")
        if qc["field"] is None and (qc["pass_values"] or qc["fail_values"]):
            raise BuildError(f"{source_path}: QC values require a QC field")
        seen_genes.add(gene["gene"])
        seen_urns.add(gene["mavedb_urn"])
    if seen_genes != REVIEWED_GENES:
        raise BuildError(f"{source_path}: reviewed gene set is incomplete")

    pins_path = (source_path.resolve().parent / values["source_pins"]).resolve()
    _, pins = load_yaml_mapping(pins_path, label="SGE source pins")
    _require_exact_fields(
        pins,
        {
            "schema_version",
            "retrieval_date",
            "catalog_audit",
            "mavedb",
            "cdot",
            "reference",
        },
        pins_path,
        "source pins",
    )
    if pins["schema_version"] != "1.0":
        raise BuildError(f"{pins_path}: unsupported source-pin schema")
    _require_string(pins["retrieval_date"], pins_path, "retrieval_date")
    expected_urns = {gene["mavedb_urn"] for gene in genes}
    expected_transcripts = {gene["transcript"] for gene in genes}
    if set(_require_mapping(pins["mavedb"], pins_path, "mavedb pins")) != expected_urns:
        raise BuildError(f"{pins_path}: MaveDB pins do not cover canonical score sets")
    if set(_require_mapping(pins["cdot"], pins_path, "cdot pins")) != expected_transcripts:
        raise BuildError(f"{pins_path}: cdot pins do not cover display transcripts")
    _validate_pin(pins["catalog_audit"], pins_path, "catalog_audit", records=True)
    for urn, pin in pins["mavedb"].items():
        record = _require_mapping(pin, pins_path, urn)
        _require_exact_fields(record, {"metadata", "scores"}, pins_path, urn)
        _validate_pin(record["metadata"], pins_path, f"{urn}.metadata")
        _validate_pin(record["scores"], pins_path, f"{urn}.scores")
    for transcript, pin in pins["cdot"].items():
        _validate_pin(pin, pins_path, transcript)
    _validate_pin(pins["reference"], pins_path, "reference")
    return PreparationConfig(source_path.resolve(), values, pins)


def _validate_pin(value: Any, path: Path, label: str, *, records: bool = False) -> None:
    pin = _require_mapping(value, path, label)
    fields = {"bytes", "sha256"} | ({"records"} if records else set())
    _require_exact_fields(pin, fields, path, label)
    _require_positive_int(pin["bytes"], path, f"{label}.bytes")
    if not isinstance(pin["sha256"], str) or len(pin["sha256"]) != 64:
        raise BuildError(f"{path}: {label}.sha256 must be a SHA-256 digest")
    if records:
        _require_positive_int(pin["records"], path, f"{label}.records")


def _require_mapping(value: Any, path: Path, label: str) -> dict[str, Any]:
    if not isinstance(value, dict):
        raise BuildError(f"{path}: {label} must be a mapping")
    return value


def _require_string(value: Any, path: Path, label: str) -> None:
    if not isinstance(value, str) or not value:
        raise BuildError(f"{path}: {label} must be a non-empty string")


def _require_positive_int(value: Any, path: Path, label: str) -> None:
    if isinstance(value, bool) or not isinstance(value, int) or value < 1:
        raise BuildError(f"{path}: {label} must be a positive integer")


def _require_unique_strings(value: Any, path: Path, label: str) -> None:
    if (
        not isinstance(value, list)
        or any(not isinstance(item, str) or not item for item in value)
        or len(value) != len(set(value))
    ):
        raise BuildError(f"{path}: {label} must be a list of unique non-empty strings")


def _require_exact_fields(
    value: dict[str, Any], required: set[str], path: Path, label: str
) -> None:
    missing = required - value.keys()
    unknown = value.keys() - required
    if missing or unknown:
        raise BuildError(
            f"{path}: invalid {label} fields; missing={sorted(missing)}, unknown={sorted(unknown)}"
        )


CONFIG = load_preparation_config()
