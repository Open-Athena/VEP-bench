import importlib.util
import json
from pathlib import Path
from types import SimpleNamespace

import pytest

from vepbench.builder import canonical_json
from vepbench.satmut_mpra import (
    ELEMENT_SPECS,
    ElementMetadata,
    PreparedElement,
    SatMutPreparationError,
    Variant,
)

ROOT = Path(__file__).resolve().parents[1]
PRODUCTION_MANIFEST = ROOT / "data/sources/satmut-mpra-cadd-v1.7.manifest.json"
SPEC = importlib.util.spec_from_file_location(
    "prepare_satmut_mpra", ROOT / "scripts/prepare_satmut_mpra.py"
)
assert SPEC is not None and SPEC.loader is not None
preparation = importlib.util.module_from_spec(SPEC)
SPEC.loader.exec_module(preparation)
LEGACY_PREPARATION_IMPLEMENTATION_SHA256 = preparation.LEGACY_PREPARATION_IMPLEMENTATION_SHA256
_cache_configuration = preparation._cache_configuration
_cache_key = preparation._cache_key
_load_cache = preparation._load_cache
_normalize_cached_reference_discrepancies = preparation._normalize_cached_reference_discrepancies
_verify_reference_metadata = preparation._verify_reference_metadata
_write_cache = preparation._write_cache


def _synthetic_elements() -> list[PreparedElement]:
    manifest = json.loads(PRODUCTION_MANIFEST.read_text(encoding="utf-8"))
    elements = []
    next_position = 1
    for spec in ELEMENT_SPECS:
        counts = manifest["population"]["elements"][spec.cadd_label]["filter_counts"]
        variants = tuple(
            Variant("1", next_position + index, "A", "C", float(index), 1e-8, 10, "SIGN")
            for index in range(counts["SIGN"])
        )
        next_position += len(variants)
        total = sum(counts.values())
        elements.append(
            PreparedElement(
                spec,
                ElementMetadata("A", "A", "1", 1, 1, "2026-09-02"),
                variants,
                counts,
                total,
                total,
            )
        )
    return elements


def test_cache_configuration_is_date_stable_and_cache_round_trips(tmp_path: Path) -> None:
    configuration = _cache_configuration()
    assert "retrieval_date" not in canonical_json(configuration)
    expected_key = _cache_key(configuration)
    legacy_key = _cache_key(
        _cache_configuration(implementation_sha256=LEGACY_PREPARATION_IMPLEMENTATION_SHA256)
    )
    destination = tmp_path / "cache"

    written_key, manifest = _write_cache(
        destination,
        elements=_synthetic_elements(),
        configuration=configuration,
    )
    loaded = _load_cache(destination, expected_key=expected_key)

    assert written_key == expected_key == manifest["cache_key"]
    assert expected_key != legacy_key
    assert len(loaded) == len(ELEMENT_SPECS)
    assert sum(len(element.variants) for element in loaded) == 4_332


def test_cache_load_rejects_tampered_data(tmp_path: Path) -> None:
    configuration = _cache_configuration()
    destination = tmp_path / "cache"
    key, _ = _write_cache(
        destination,
        elements=_synthetic_elements(),
        configuration=configuration,
    )
    with (destination / "eligible-variants.jsonl.gz").open("ab") as cache_file:
        cache_file.write(b"tampered")

    with pytest.raises(SatMutPreparationError, match="digest or size mismatch"):
        _load_cache(destination, expected_key=key)


def test_cached_zrs_discrepancy_is_normalized_to_current_display_policy() -> None:
    discrepancies = _normalize_cached_reference_discrepancies(
        [
            {
                "chrom": "7",
                "pos": 156_791_604,
                "mavedb_base": "A",
                "grch38_base": "T",
                "treatment": "use_grch38_in_model_visible_reference",
            }
        ]
    )

    assert discrepancies[0]["treatment"] == (
        "retain_reporter_construct_base_in_model_visible_reference"
    )


def test_reference_metadata_is_verified_without_downloading_fasta(monkeypatch) -> None:
    expected = {"bytes": 12, "sha256": "a" * 64}
    monkeypatch.setattr(
        preparation,
        "get_hf_file_metadata",
        lambda *args, **kwargs: SimpleNamespace(
            commit_hash=preparation.REFERENCE_REVISION,
            etag="a" * 64,
            size=12,
        ),
    )

    _verify_reference_metadata(None, expected)

    expected["bytes"] = 13
    with pytest.raises(RuntimeError, match="GRCh38 remote metadata"):
        _verify_reference_metadata(None, expected)
