"""Validated human-maintained configuration for satMutMPRA preparation."""

from dataclasses import dataclass
from pathlib import Path
from typing import Any

from vepbench.config.loader import load_yaml_mapping
from vepbench.errors import BuildError

PREPARATION_CONFIG_PATH = Path(__file__).resolve().parents[2] / "config" / "preparation.yaml"


@dataclass(frozen=True)
class PreparationConfig:
    """Resolved task preparation settings and upstream pins."""

    path: Path
    values: dict[str, Any]
    pins: dict[str, Any]

    def resolve_path(self, key: str) -> Path:
        value = self.values[key]
        if not isinstance(value, str) or not value:
            raise BuildError(f"{self.path}: {key} must be a non-empty path")
        return (self.path.parent / value).resolve()


def load_preparation_config(path: str | Path = PREPARATION_CONFIG_PATH) -> PreparationConfig:
    """Load and validate the complete satMutMPRA preparation configuration."""

    source_path, values = load_yaml_mapping(path, label="satMutMPRA preparation config")
    required = {
        "schema_version",
        "task_family",
        "reference_contig",
        "source_dataset",
        "source_pins",
        "output",
        "manifest_output",
        "element_defaults",
        "sampling",
        "sequence_policy",
        "validation",
        "upstream",
        "cache",
        "elements",
    }
    _require_exact_fields(values, required, source_path, "preparation config")
    if values["schema_version"] != "1.0" or values["task_family"] != "satmut_mpra":
        raise BuildError(f"{source_path}: unsupported satMutMPRA preparation config")
    for field in ("reference_contig", "source_dataset", "source_pins", "output", "manifest_output"):
        _require_string(values[field], source_path, field)

    element_defaults = _require_mapping(values["element_defaults"], source_path, "element_defaults")
    _require_exact_fields(
        element_defaults,
        {"transfection_hours", "reporter_vector", "reporter_vector_accession"},
        source_path,
        "element_defaults",
    )
    if (
        isinstance(element_defaults["transfection_hours"], bool)
        or not isinstance(element_defaults["transfection_hours"], int)
        or element_defaults["transfection_hours"] < 1
    ):
        raise BuildError(f"{source_path}: element_defaults.transfection_hours must be positive")
    for field in ("reporter_vector", "reporter_vector_accession"):
        _require_string(element_defaults[field], source_path, f"element_defaults.{field}")

    sampling = _require_mapping(values["sampling"], source_path, "sampling")
    _require_exact_fields(
        sampling,
        {"panel_size", "quantile_bins", "samples_per_bin", "seed", "algorithm"},
        source_path,
        "sampling",
    )
    for field in ("panel_size", "quantile_bins", "samples_per_bin"):
        value = sampling[field]
        if isinstance(value, bool) or not isinstance(value, int) or value < 1:
            raise BuildError(f"{source_path}: sampling.{field} must be a positive integer")
    if sampling["panel_size"] != sampling["quantile_bins"] * sampling["samples_per_bin"]:
        raise BuildError(
            f"{source_path}: sampling panel size must equal bins times samples per bin"
        )
    _require_string(sampling["seed"], source_path, "sampling.seed")
    _require_string(sampling["algorithm"], source_path, "sampling.algorithm")

    sequence_policy = _require_mapping(values["sequence_policy"], source_path, "sequence_policy")
    _require_exact_fields(
        sequence_policy,
        {"model_visible", "target_mismatch_treatment", "reporter_contexts"},
        source_path,
        "sequence_policy",
    )
    _require_string(sequence_policy["model_visible"], source_path, "sequence_policy.model_visible")
    _require_string(
        sequence_policy["target_mismatch_treatment"],
        source_path,
        "sequence_policy.target_mismatch_treatment",
    )
    contexts = _require_mapping(
        sequence_policy["reporter_contexts"], source_path, "sequence_policy.reporter_contexts"
    )
    if not contexts or any(
        not isinstance(name, str)
        or not isinstance(sequence, str)
        or not sequence
        or set(sequence) - set("ACGT")
        for name, sequence in contexts.items()
    ):
        raise BuildError(f"{source_path}: reporter contexts must be named uppercase DNA sequences")

    validation = _require_mapping(values["validation"], source_path, "validation")
    _require_exact_fields(
        validation,
        {
            "expected_filter_counts",
            "known_reference_mismatches",
            "known_target_sequence_mismatches",
        },
        source_path,
        "validation",
    )
    counts = _require_mapping(
        validation["expected_filter_counts"], source_path, "validation.expected_filter_counts"
    )
    if set(counts) != {"SIGN", "MIN", "QUAL"} or any(
        isinstance(count, bool) or not isinstance(count, int) or count < 0
        for count in counts.values()
    ):
        raise BuildError(f"{source_path}: invalid expected filter counts")
    for field in ("known_reference_mismatches", "known_target_sequence_mismatches"):
        rows = values["validation"][field]
        if not isinstance(rows, list) or any(
            not isinstance(row, list)
            or len(row) != 4
            or not isinstance(row[0], str)
            or isinstance(row[1], bool)
            or not isinstance(row[1], int)
            or not isinstance(row[2], str)
            or not isinstance(row[3], str)
            for row in rows
        ):
            raise BuildError(f"{source_path}: validation.{field} must contain variant keys")

    upstream = _require_mapping(values["upstream"], source_path, "upstream")
    _require_exact_fields(
        upstream,
        {
            "cadd_release",
            "cadd_validation_set",
            "cadd_base_url",
            "mavedb_api_label",
            "mavedb_api_base",
            "reference",
            "expected_cadd_md5",
        },
        source_path,
        "upstream",
    )
    for field in (
        "cadd_release",
        "cadd_validation_set",
        "cadd_base_url",
        "mavedb_api_label",
        "mavedb_api_base",
    ):
        _require_string(upstream[field], source_path, f"upstream.{field}")
    reference = _require_mapping(upstream["reference"], source_path, "upstream.reference")
    _require_exact_fields(
        reference, {"dataset", "revision", "filename", "assembly"}, source_path, "reference"
    )
    for field in ("dataset", "revision", "filename", "assembly"):
        _require_string(reference[field], source_path, f"upstream.reference.{field}")
    md5s = _require_mapping(upstream["expected_cadd_md5"], source_path, "expected_cadd_md5")
    if len(md5s) != 16 or any(
        not isinstance(name, str) or not isinstance(digest, str) or len(digest) != 32
        for name, digest in md5s.items()
    ):
        raise BuildError(f"{source_path}: expected_cadd_md5 must contain 16 MD5 digests")

    cache = _require_mapping(values["cache"], source_path, "cache")
    _require_exact_fields(
        cache,
        {
            "bucket",
            "root",
            "data_files",
            "legacy_implementation_sha256",
            "implementation_sha256",
        },
        source_path,
        "cache",
    )
    for field in ("bucket", "root", "legacy_implementation_sha256", "implementation_sha256"):
        _require_string(cache[field], source_path, f"cache.{field}")
    if not isinstance(cache["data_files"], list) or not all(
        isinstance(item, str) and item for item in cache["data_files"]
    ):
        raise BuildError(f"{source_path}: cache.data_files must be a list of paths")

    elements = values["elements"]
    if not isinstance(elements, list) or len(elements) != 16:
        raise BuildError(f"{source_path}: elements must contain exactly 16 records")
    required_element = {
        "cadd_label",
        "mavedb_urn",
        "model_name",
        "source_study_label",
        "element_class",
        "cell_line",
        "experimental_context",
    }
    optional_element = {
        "mavedb_target_name",
        "transfection_hours",
        "reporter_vector",
        "reporter_vector_accession",
        "reporter_context_label",
        "reporter_context_sequence",
    }
    labels: set[str] = set()
    urns: set[str] = set()
    for index, element_value in enumerate(elements, start=1):
        element = _require_mapping(element_value, source_path, f"elements[{index}]")
        missing = required_element - element.keys()
        unknown = element.keys() - required_element - optional_element
        if missing or unknown:
            raise BuildError(
                f"{source_path}: invalid elements[{index}] fields; "
                f"missing={sorted(missing)}, unknown={sorted(unknown)}"
            )
        for field in required_element:
            _require_string(element[field], source_path, f"elements[{index}].{field}")
        for field in (
            "mavedb_target_name",
            "reporter_vector",
            "reporter_vector_accession",
            "reporter_context_label",
        ):
            if field in element:
                _require_string(element[field], source_path, f"elements[{index}].{field}")
        if "transfection_hours" in element and (
            isinstance(element["transfection_hours"], bool)
            or not isinstance(element["transfection_hours"], int)
            or element["transfection_hours"] < 1
        ):
            raise BuildError(
                f"{source_path}: elements[{index}].transfection_hours must be positive"
            )
        context_key = element.get("reporter_context_sequence")
        if context_key is not None and context_key not in contexts:
            raise BuildError(f"{source_path}: elements[{index}] references an unknown context")
        if element["cadd_label"] in labels or element["mavedb_urn"] in urns:
            raise BuildError(f"{source_path}: duplicate element identity")
        labels.add(element["cadd_label"])
        urns.add(element["mavedb_urn"])

    pins_path = (source_path.resolve().parent / values["source_pins"]).resolve()
    _, pins = load_yaml_mapping(pins_path, label="satMutMPRA source pins")
    if pins.get("schema_version") != "1.0" or set(pins) != {
        "schema_version",
        "retrieval_date",
        "cadd_md5_manifest",
        "cadd",
        "mavedb",
        "reference",
    }:
        raise BuildError(f"{pins_path}: invalid satMutMPRA source pins")
    return PreparationConfig(source_path.resolve(), values, pins)


def _require_mapping(value: Any, path: Path, label: str) -> dict[str, Any]:
    if not isinstance(value, dict):
        raise BuildError(f"{path}: {label} must be a mapping")
    return value


def _require_string(value: Any, path: Path, label: str) -> None:
    if not isinstance(value, str) or not value:
        raise BuildError(f"{path}: {label} must be a non-empty string")


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
