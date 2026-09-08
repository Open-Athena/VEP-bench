"""Validated human-maintained configuration for OpenSplice preparation."""

import hashlib
from dataclasses import dataclass
from pathlib import Path
from typing import Any

from vepbench.artifacts import canonical_json
from vepbench.config.loader import load_yaml_mapping
from vepbench.errors import BuildError

PREPARATION_CONFIG_PATH = Path(__file__).resolve().parents[2] / "config" / "preparation.yaml"


@dataclass(frozen=True)
class PreparationConfig:
    """Resolved OpenSplice preparation settings and source pins."""

    path: Path
    values: dict[str, Any]
    pins: dict[str, Any]

    def resolve_path(self, key: str) -> Path:
        value = self.values[key]
        if not isinstance(value, str) or not value:
            raise BuildError(f"{self.path}: {key} must be a non-empty path")
        return (self.path.parent / value).resolve()


def _mapping(value: Any, path: Path, label: str) -> dict[str, Any]:
    if not isinstance(value, dict):
        raise BuildError(f"{path}: {label} must be an object")
    return value


def _exact_fields(value: dict[str, Any], expected: set[str], path: Path, label: str) -> None:
    missing = expected - value.keys()
    unknown = value.keys() - expected
    if missing or unknown:
        raise BuildError(
            f"{path}: invalid {label} fields; missing={sorted(missing)}, unknown={sorted(unknown)}"
        )


def _string(value: Any, path: Path, label: str) -> str:
    if not isinstance(value, str) or not value:
        raise BuildError(f"{path}: {label} must be a non-empty string")
    return value


def _positive_int(value: Any, path: Path, label: str) -> int:
    if isinstance(value, bool) or not isinstance(value, int) or value < 1:
        raise BuildError(f"{path}: {label} must be a positive integer")
    return value


def load_preparation_config(path: str | Path = PREPARATION_CONFIG_PATH) -> PreparationConfig:
    """Load and strictly validate the OpenSplice preparation configuration."""

    config_path, values = load_yaml_mapping(path, label="OpenSplice preparation config")
    _exact_fields(
        values,
        {
            "schema_version",
            "task_family",
            "source_dataset",
            "reference_contig",
            "source_pins",
            "output",
            "manifest_output",
            "sampling",
            "population",
            "reporter",
            "assay",
            "cache",
        },
        config_path,
        "preparation config",
    )
    if values["schema_version"] != "1.0" or values["task_family"] != "opensplice_snv":
        raise BuildError(f"{config_path}: unsupported OpenSplice preparation config")
    for field in (
        "source_dataset",
        "reference_contig",
        "source_pins",
        "output",
        "manifest_output",
    ):
        _string(values[field], config_path, field)

    from vepbench.sampling import validate_sampling_config

    sampling = _mapping(values["sampling"], config_path, "sampling")
    validate_sampling_config({k: v for k, v in sampling.items() if k != "exon_count"})
    if sampling.get("exon_count") != 20:
        raise BuildError("OpenSplice requires 20 distinct genes")

    population = _mapping(values["population"], config_path, "population")
    _exact_fields(
        population,
        {
            "master_rows",
            "exon_metadata_records",
            "eligible_rows",
            "eligible_exons",
            "eligible_genes",
        },
        config_path,
        "population",
    )
    for field, value in population.items():
        _positive_int(value, config_path, f"population.{field}")

    reporter = _mapping(values["reporter"], config_path, "reporter")
    _exact_fields(
        reporter,
        {"fas_e5", "fas_i5", "fas_i6", "fas_e7", "downstream_native_flank"},
        config_path,
        "reporter",
    )
    for field in ("fas_e5", "fas_i5", "fas_i6", "fas_e7"):
        sequence = _string(reporter[field], config_path, f"reporter.{field}")
        if sequence != sequence.upper() or set(sequence) - set("ACGT"):
            raise BuildError(f"{config_path}: reporter.{field} must be uppercase DNA")
    _positive_int(reporter["downstream_native_flank"], config_path, "downstream flank")

    assay = _mapping(values["assay"], config_path, "assay")
    _exact_fields(assay, {"cell_line", "cellular_context", "measurement"}, config_path, "assay")
    for field in assay:
        _string(assay[field], config_path, f"assay.{field}")

    cache = _mapping(values["cache"], config_path, "cache")
    _exact_fields(
        cache,
        {"bucket", "root", "implementation_sha256", "data_files"},
        config_path,
        "cache",
    )
    _string(cache["bucket"], config_path, "cache.bucket")
    _string(cache["root"], config_path, "cache.root")
    implementation_sha256 = _string(
        cache["implementation_sha256"], config_path, "cache.implementation_sha256"
    )
    if len(implementation_sha256) != 64 or any(
        character not in "0123456789abcdef" for character in implementation_sha256
    ):
        raise BuildError(f"{config_path}: cache.implementation_sha256 is invalid")
    if not isinstance(cache["data_files"], list) or not all(
        isinstance(item, str) and item for item in cache["data_files"]
    ):
        raise BuildError(f"{config_path}: cache.data_files must contain paths")

    pins_path = (config_path.parent / values["source_pins"]).resolve()
    _, pins = load_yaml_mapping(pins_path, label="OpenSplice source pins")
    _exact_fields(pins, {"schema_version", "retrieval_date", "dataset", "files"}, pins_path, "pins")
    if pins["schema_version"] != "1.0":
        raise BuildError(f"{pins_path}: unsupported source-pin schema")
    _string(pins["retrieval_date"], pins_path, "retrieval_date")
    dataset = _mapping(pins["dataset"], pins_path, "dataset")
    _exact_fields(dataset, {"doi", "article_id", "version", "license"}, pins_path, "dataset")
    _string(dataset["doi"], pins_path, "dataset.doi")
    _string(dataset["license"], pins_path, "dataset.license")
    _positive_int(dataset["article_id"], pins_path, "dataset.article_id")
    _positive_int(dataset["version"], pins_path, "dataset.version")
    files = _mapping(pins["files"], pins_path, "files")
    expected_files = {"master", "exon_metadata"}
    _exact_fields(files, expected_files, pins_path, "files")
    for label, raw_pin in files.items():
        pin = _mapping(raw_pin, pins_path, f"files.{label}")
        _exact_fields(pin, {"file_id", "filename", "bytes", "md5", "sha256"}, pins_path, label)
        _positive_int(pin["file_id"], pins_path, f"files.{label}.file_id")
        _positive_int(pin["bytes"], pins_path, f"files.{label}.bytes")
        _string(pin["filename"], pins_path, f"files.{label}.filename")
        if not isinstance(pin["md5"], str) or len(pin["md5"]) != 32:
            raise BuildError(f"{pins_path}: files.{label}.md5 is invalid")
        if not isinstance(pin["sha256"], str) or len(pin["sha256"]) != 64:
            raise BuildError(f"{pins_path}: files.{label}.sha256 is invalid")
    return PreparationConfig(config_path, values, pins)


CONFIG = load_preparation_config()


def cache_configuration() -> dict[str, Any]:
    """Return the complete content-addressed processed-cache identity."""

    return {
        "schema_version": "1.0",
        "implementation_sha256": CONFIG.values["cache"]["implementation_sha256"],
        "dataset": CONFIG.pins["dataset"],
        "files": {
            label: {
                **pin,
                "url": f"https://ndownloader.figshare.com/files/{pin['file_id']}",
            }
            for label, pin in CONFIG.pins["files"].items()
        },
        "construct_geometry": {
            "downstream_native_flank": CONFIG.values["reporter"]["downstream_native_flank"],
        },
        "eligibility": {
            "variant": "complete sequence-validated substitutions and deletions",
            "measurement": "measured and finite delta_psi, psi_r1, psi_r2, psi_r3",
            "mapping": ("construct-oriented REF and ALT reproduce nt_seq against exon wt_seq"),
            "uniqueness": ("unique (ensembl_exon_id, start, wt, mut) and mutant sequence"),
        },
    }


def cache_key(configuration: dict[str, Any] | None = None) -> str:
    """Hash the canonical processed-cache identity."""

    identity = configuration if configuration is not None else cache_configuration()
    return hashlib.sha256(canonical_json(identity).encode()).hexdigest()
