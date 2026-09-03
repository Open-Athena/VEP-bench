import json
from pathlib import Path

import pytest
from vepbench_opensplice_snv import prepare as preparation
from vepbench_opensplice_snv.task import OpenSplicePreparationError

from vepbench.artifacts import canonical_json, sha256_file

_cache_configuration = preparation._cache_configuration
_cache_key = preparation._cache_key
_validate_cache = preparation._validate_cache


def _write_synthetic_cache(destination: Path) -> tuple[str, dict]:
    destination.mkdir()
    configuration = _cache_configuration()
    key = _cache_key(configuration)
    for name, payload in {
        "eligible-variants.jsonl.gz": b"eligible\n",
        "exons.json": b'{"exons":[]}\n',
        "population.json": b'{"master_rows":0}\n',
    }.items():
        (destination / name).write_bytes(payload)
    manifest = {
        "schema_version": "1.0",
        "kind": "vepbench_opensplice_snv_processed_cache",
        "cache_key": key,
        "configuration": configuration,
        "files": {
            name: {
                "bytes": (destination / name).stat().st_size,
                "sha256": sha256_file(destination / name),
                "records": 0,
            }
            for name in preparation.CACHE_DATA_FILES
        },
    }
    (destination / "manifest.json").write_text(
        f"{canonical_json(manifest)}\n", encoding="utf-8", newline="\n"
    )
    return key, manifest


def test_cache_identity_is_content_addressed_and_round_trips(tmp_path: Path) -> None:
    configuration = _cache_configuration()
    assert "retrieval_date" not in canonical_json(configuration)
    key, written = _write_synthetic_cache(tmp_path / "cache")

    loaded = _validate_cache(
        tmp_path / "cache",
        expected_key=key,
        expected_configuration=configuration,
    )

    assert key == _cache_key(configuration) == written["cache_key"]
    assert loaded == written


def test_cache_validation_rejects_tampered_data(tmp_path: Path) -> None:
    destination = tmp_path / "cache"
    key, manifest = _write_synthetic_cache(destination)
    (destination / "population.json").write_text(
        f"{json.dumps({'master_rows': 1})}\n", encoding="utf-8"
    )

    with pytest.raises(OpenSplicePreparationError, match="digest mismatch"):
        _validate_cache(
            destination,
            expected_key=key,
            expected_configuration=manifest["configuration"],
        )
