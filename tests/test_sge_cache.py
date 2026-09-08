from pathlib import Path

import pytest
from vepbench_sge.prepare import (
    _cache_configuration,
    _cache_key,
    _load_cache,
    _verify_payload,
    _write_cache,
)
from vepbench_sge.task import GENE_SPECS, Exon, SGEPreparationError, Transcript, Variant


def _cache_inputs() -> tuple[dict, dict, dict, dict, dict]:
    variants = {}
    transcripts = {}
    source_provenance = {"mavedb": {}}
    population = {}
    for index, spec in enumerate(GENE_SPECS, start=1):
        variants[spec.gene] = (
            Variant(
                spec.gene,
                f"{spec.mavedb_urn}#1",
                "n.1A>C",
                spec.expected_chrom,
                index,
                "A",
                "C",
                -1.0,
                1.0,
                {"score": "-1.0"},
            ),
        )
        transcripts[spec.gene] = Transcript(
            spec.transcript,
            spec.gene,
            spec.expected_chrom,
            "+",
            (Exon(1, 100),),
            0,
            100,
        )
        source_provenance["mavedb"][spec.mavedb_urn] = {"score_set_urn": spec.mavedb_urn}
        population[spec.gene] = {"eligible_records": 1}
    catalog = {"selected_score_sets": sorted(spec.mavedb_urn for spec in GENE_SPECS)}
    return variants, transcripts, source_provenance, population, catalog


def test_processed_cache_is_deterministic_and_round_trips(tmp_path: Path) -> None:
    variants, transcripts, provenance, population, catalog = _cache_inputs()
    configuration = _cache_configuration()
    destination = tmp_path / "cache"

    manifest = _write_cache(
        destination,
        variants_by_gene=variants,
        transcripts=transcripts,
        source_provenance=provenance,
        population=population,
        catalog_audit=catalog,
        configuration=configuration,
    )
    loaded = _load_cache(
        destination,
        expected_key=_cache_key(configuration),
        expected_configuration=configuration,
    )

    assert manifest["cache_key"] == _cache_key(configuration)
    assert loaded[0] == variants
    assert loaded[1] == transcripts
    assert loaded[2] == provenance
    assert loaded[3] == population
    assert loaded[4] == catalog


def test_processed_cache_rejects_tampering(tmp_path: Path) -> None:
    variants, transcripts, provenance, population, catalog = _cache_inputs()
    configuration = _cache_configuration()
    destination = tmp_path / "cache"
    _write_cache(
        destination,
        variants_by_gene=variants,
        transcripts=transcripts,
        source_provenance=provenance,
        population=population,
        catalog_audit=catalog,
        configuration=configuration,
    )
    with (destination / "eligible-variants.jsonl.gz").open("ab") as output:
        output.write(b"tampered")

    with pytest.raises(SGEPreparationError, match="cache file is corrupt"):
        _load_cache(
            destination,
            expected_key=_cache_key(configuration),
            expected_configuration=configuration,
        )


def test_payload_identity_allows_separate_catalog_record_count() -> None:
    _verify_payload(
        "catalog",
        b"pinned",
        {
            "bytes": 6,
            "sha256": "3fab5c181bd28a09b64397df76ae2bfaf1eac182979b5fdb7a342858004f36af",
            "records": 98,
        },
    )
