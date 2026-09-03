import json
import re
from collections import Counter
from dataclasses import replace
from pathlib import Path

import pytest
from jsonschema import Draft202012Validator
from vepbench_sge.configuration import CONFIG, PROVISIONAL_GENES
from vepbench_sge.prepare import _cache_configuration, _cache_key, _pyhgvs_mapper
from vepbench_sge.task import (
    EXON_PROXIMAL_DISTANCE,
    GENE_SPECS,
    PANEL_SIZE,
    Exon,
    ExonIndex,
    SGEPreparationError,
    Transcript,
    Variant,
    annotate_and_filter_variants,
    build_catalog_audit,
    build_source_record,
    choose_panel,
    parse_score_csv,
    reverse_complement,
    transcript_coding_sequence,
    validate_prepared_artifacts,
)

from vepbench.artifacts import read_jsonl
from vepbench.questions.builder import build_questions, load_template

ROOT = Path(__file__).resolve().parents[1]
TEMPLATE = ROOT / "configs/tasks/sge/prompt.yaml"
SCHEMA = ROOT / "src/vepbench/schemas/question.schema.json"
PRODUCTION_SOURCE = ROOT / "data/sources/sge-mavedb-2026-09-03.jsonl"
PRODUCTION_MANIFEST = ROOT / "data/sources/sge-mavedb-2026-09-03.manifest.json"
EXPECTED_QUESTION_MANIFEST = ROOT / "benchmark/sge-expected-manifest.json"


def _variant(
    gene: str,
    pos: int,
    score: float,
    group: str,
    *,
    chrom: str = "1",
    ref: str = "A",
    alt: str = "C",
) -> Variant:
    return Variant(
        gene=gene,
        source_accession=f"urn:test#{pos}",
        source_hgvs=f"NC_000001.11:g.{pos}{ref}>{alt}",
        chrom=chrom,
        pos=pos,
        ref=ref,
        alt=alt,
        source_score=-score,
        damage_score=score,
        source_fields={"score": str(-score)},
        consequence="missense_variant" if group == "missense_variant" else "splice_region_variant",
        consequence_final=(
            "missense_variant" if group == "missense_variant" else "splice_region_variant"
        ),
        consequence_group=group,
        nearest_exon_distance=0,
    )


def _mapper_for(chrom: str):
    def mapper(value: str) -> tuple[str, int, str, str]:
        return chrom, 107, "A", "C"

    return mapper


def test_configuration_covers_exact_provisional_gene_and_source_sets() -> None:
    assert {spec.gene for spec in GENE_SPECS} == PROVISIONAL_GENES
    assert len({spec.mavedb_urn for spec in GENE_SPECS}) == 15
    assert set(CONFIG.pins["mavedb"]) == {spec.mavedb_urn for spec in GENE_SPECS}
    assert set(CONFIG.pins["cdot"]) == {spec.transcript for spec in GENE_SPECS}
    assert CONFIG.values["sampling"]["panel_size"] == 50
    assert CONFIG.values["sequence"]["flank_bases"] == 100
    assert EXON_PROXIMAL_DISTANCE == 30
    assert all(
        re.search(r"\bexons?\s+\d", spec.assay_context, flags=re.IGNORECASE) is None
        for spec in GENE_SPECS
    )


def test_catalog_audit_records_selected_and_policy_excluded_score_sets() -> None:
    rows = [
        {
            "urn": spec.mavedb_urn,
            "title": spec.gene,
            "targetGenes": [{"mappedHgncName": spec.gene}],
            "experiment": {"urn": f"urn:mavedb:{index:08d}"},
            "publishedDate": "2026-01-01",
            "modificationDate": "2026-01-01",
        }
        for index, spec in enumerate(GENE_SPECS)
    ]
    rows.append(
        {
            "urn": "urn:mavedb:card11-test",
            "title": "CARD11 codon variants",
            "targetGenes": [{"mappedHgncName": "CARD11"}],
            "experiment": {"urn": "urn:mavedb:card11"},
        }
    )
    payload = json.dumps({"numScoreSets": len(rows), "scoreSets": rows}).encode()

    audit = build_catalog_audit(payload, expected_records=len(rows))

    assert len(audit["selected_score_sets"]) == 15
    card11 = next(record for record in audit["records"] if record["urn"].endswith("card11-test"))
    assert card11["decision"] == "excluded"
    assert "multi-base codon substitutions" in card11["reason"]


def test_score_parser_supports_genomic_transcript_and_target_coding_hgvs() -> None:
    base = GENE_SPECS[0]
    cases = (
        (replace(base, coordinate_mode="hgvs_genomic", expected_chrom="1"), "NC_000001.11:g.7A>C"),
        (replace(base, coordinate_mode="hgvs_transcript"), f"{base.transcript}:c.7A>C"),
        (replace(base, coordinate_mode="target_coding_hgvs"), "n.7A>C"),
    )
    for spec, hgvs in cases:
        payload = f"accession,hgvs_nt,score\n{spec.mavedb_urn}#1,{hgvs},-2.5\n".encode()
        variants, report = parse_score_csv(
            payload,
            spec,
            mapper=_mapper_for(spec.expected_chrom),
        )
        assert len(variants) == 1
        assert variants[0].damage_score == spec.score_direction * -2.5
        assert report["mapped_unique_snv_records"] == 1


def test_score_parser_applies_configured_qc_and_rejects_unknown_values() -> None:
    spec = next(spec for spec in GENE_SPECS if spec.gene == "SBDS")
    header = "accession,hgvs_nt,score,variant_qc_flag\n"
    rows = (
        f"{spec.mavedb_urn}#1,{spec.transcript}:c.1A>C,-1.0,PASS\n"
        f"{spec.mavedb_urn}#2,{spec.transcript}:c.2A>C,-2.0,FAIL\n"
    )
    variants, report = parse_score_csv(
        (header + rows).encode(), spec, mapper=lambda value: ("7", 100, "A", "C")
    )
    assert len(variants) == 1
    assert report["excluded"] == {"source_qc_failure": 1}

    invalid = f"{header}{spec.mavedb_urn}#3,{spec.transcript}:c.3A>C,-1.0,MAYBE\n"
    with pytest.raises(SGEPreparationError, match="unknown QC value"):
        parse_score_csv(invalid.encode(), spec, mapper=lambda value: ("7", 101, "A", "C"))


def test_consequence_policy_uses_inclusive_30_bp_boundary_after_high_impact_exclusion() -> None:
    variants = tuple(_variant("TEST", pos, float(pos), "splicing") for pos in (69, 70, 100))
    consequences = {
        variants[0].key: "intron_variant",
        variants[1].key: "intron_variant",
        variants[2].key: "splice_acceptor_variant",
    }
    exon_index = ExonIndex({"1": [Exon(100, 200)]})

    eligible, excluded = annotate_and_filter_variants(
        variants,
        consequences=consequences,
        exon_index=exon_index,
        genome=lambda chrom, start, end: "A",
    )

    assert [(variant.pos, variant.nearest_exon_distance) for variant in eligible] == [(70, 30)]
    assert eligible[0].consequence_final == "exon_proximal"
    assert excluded == {"excluded_high_impact_consequence": 1, "unretained_consequence": 1}


def test_panel_selection_is_deterministic_balanced_and_quantile_covered() -> None:
    transcript = Transcript("NM_TEST.1", "TEST", "1", "+", (Exon(101, 200),))
    variants = tuple(
        [_variant("TEST", index + 1, float(index), "missense_variant") for index in range(30)]
        + [_variant("TEST", index + 51, float(index + 30), "splicing") for index in range(30)]
    )
    def genome(chrom: str, start: int, end: int) -> str:
        return "A" * (end - start)

    first, summaries = choose_panel(variants, transcript, genome=genome, seed="test")
    second, _ = choose_panel(tuple(reversed(variants)), transcript, genome=genome, seed="test")

    assert first == second
    assert first is not None
    assert len(first.candidates) == PANEL_SIZE
    assert first.window_start == 1 and first.window_end == 300
    assert len(first.sequence) == 300
    assert (first.missense_allocation, first.splicing_allocation) == (25, 25)
    assert Counter(candidate.quantile_bin for candidate in first.candidates) == Counter(
        dict.fromkeys(range(1, 6), 10)
    )
    assert summaries[0]["selected"] is True


def test_panel_selection_relaxes_balance_and_handles_reverse_orientation() -> None:
    transcript = Transcript("NM_TEST.1", "TEST", "1", "-", (Exon(101, 200),))
    variants = tuple(
        [_variant("TEST", index + 1, float(index), "missense_variant") for index in range(10)]
        + [_variant("TEST", index + 21, float(index + 10), "splicing") for index in range(50)]
    )
    panel, _ = choose_panel(
        variants,
        transcript,
        genome=lambda chrom, start, end: "A" * (end - start),
    )

    assert panel is not None
    assert (panel.missense_allocation, panel.splicing_allocation) == (10, 40)
    assert panel.sequence == "T" * 300
    assert all(
        candidate.visible_ref == "T" and candidate.visible_alt == "G"
        for candidate in panel.candidates
    )
    assert [candidate.local_pos for candidate in panel.candidates] == sorted(
        candidate.local_pos for candidate in panel.candidates
    )


def test_source_record_builds_schema_valid_nonleaking_ranking_question() -> None:
    spec = GENE_SPECS[0]
    transcript = Transcript(
        spec.transcript, spec.gene, spec.expected_chrom, "+", (Exon(101, 200),)
    )
    variants = tuple(
        [
            _variant(
                spec.gene,
                index + 1,
                float(index),
                "missense_variant",
                chrom=spec.expected_chrom,
            )
            for index in range(25)
        ]
        + [
            _variant(
                spec.gene,
                index + 51,
                float(index + 25),
                "splicing",
                chrom=spec.expected_chrom,
            )
            for index in range(25)
        ]
    )
    panel, summaries = choose_panel(
        variants,
        transcript,
        genome=lambda chrom, start, end: "A" * (end - start),
    )
    assert panel is not None
    record = build_source_record(
        spec,
        transcript,
        panel,
        source_provenance={"score_set_urn": spec.mavedb_urn},
        population_summary={"status": "included"},
        exon_summaries=summaries,
    )
    schema = json.loads(SCHEMA.read_text(encoding="utf-8"))
    question = build_questions([record], load_template(TEMPLATE), schema)[0]

    Draft202012Validator(schema).validate(question)
    assert question["question_id"] == f"sge-ranking-v1:{spec.gene}"
    assert len(question["candidates"]) == 50
    assert f"**Gene:** {spec.gene}" in question["prompt"]
    assert spec.mavedb_urn not in question["prompt"]
    assert spec.transcript not in question["prompt"]
    assert "reference_score" not in question["prompt"]
    assert "Larger values must mean greater" in question["prompt"]


def test_transcript_coding_sequence_respects_splicing_and_strand() -> None:
    sequence = "ACGT" * 20

    def genome(chrom: str, start: int, end: int) -> str:
        return sequence[start:end]

    forward = Transcript("NM_TEST.1", "TEST", "1", "+", (Exon(1, 8), Exon(17, 24)), 2, 22)
    reverse = replace(forward, strand="-")
    observed = sequence[2:8] + sequence[16:22]

    assert transcript_coding_sequence(forward, genome) == observed
    assert transcript_coding_sequence(reverse, genome) == reverse_complement(observed)


def test_pyhgvs_mapper_rejects_source_reference_repair_and_handles_negative_strand() -> None:
    class Fasta(dict):
        pass

    plus = {
        "id": "NM_TEST.1",
        "gene_name": "TEST",
        "genome_builds": {
            "GRCh38": {
                "contig": "NC_000001.11",
                "strand": "+",
                "exons": [[40, 50]],
                "cds_start": 40,
                "cds_end": 50,
            }
        },
    }
    minus = {
        "id": "NM_REVERSE.1",
        "gene_name": "REVERSE",
        "genome_builds": {
            "GRCh38": {
                "contig": "NC_000001.11",
                "strand": "-",
                "exons": [[40, 50]],
                "cds_start": 40,
                "cds_end": 50,
            }
        },
    }
    mapper = _pyhgvs_mapper(
        Fasta({"1": "A" * 100}),
        {"NM_TEST.1": plus, "NM_REVERSE.1": minus},
    )

    assert mapper("NM_TEST.1:c.1A>C") == ("1", 41, "A", "C")
    assert mapper("NM_TEST.1:c.1G>C") is None
    assert mapper("NM_REVERSE.1:c.1T>G") == ("1", 50, "A", "C")


def test_committed_sge_artifacts_and_question_set_are_complete() -> None:
    manifest = validate_prepared_artifacts(PRODUCTION_SOURCE, PRODUCTION_MANIFEST)
    records = read_jsonl(PRODUCTION_SOURCE)
    schema = json.loads(SCHEMA.read_text(encoding="utf-8"))
    questions = build_questions(records, load_template(TEMPLATE), schema)
    expected_questions = json.loads(EXPECTED_QUESTION_MANIFEST.read_text(encoding="utf-8"))

    assert manifest["output"] == {
        "bytes": 503_439,
        "path": PRODUCTION_SOURCE.name,
        "records": 15,
        "sha256": "8f16fe63e5ee930e7878dbc31c9ca9907c0df6a9426a38862ae9b09c48be4e1b",
    }
    assert manifest["cache"]["manifest"]["files"]["eligible-variants.jsonl.gz"][
        "records"
    ] == 55_924
    assert manifest["cache"]["cache_key"] == _cache_key(_cache_configuration())
    assert manifest["cache"]["prefix"] == (
        "data_prep/sge/v1/f3f47401954f8c496db1518730189f1e634d812372538fec3cc40bb38d766ace"
    )
    assert len(manifest["catalog_audit"]["records"]) == 98
    assert len(manifest["catalog_audit"]["selected_score_sets"]) == 15
    card11_records = [
        record
        for record in manifest["catalog_audit"]["records"]
        if "CARD11" in record["genes"]
    ]
    assert len(card11_records) == 4
    assert all(
        record["decision"] == "excluded" and "multi-base codon substitutions" in record["reason"]
        for record in card11_records
    )
    assert all(summary["status"] == "included" for summary in manifest["population"].values())
    assert expected_questions == {
        "bytes": 136_048,
        "path": "sge-questions.jsonl",
        "records": 15,
        "schema_version": "1.0",
        "sha256": "70f96a265a08d11d931ec7fe30d8f9a34c37d0a593cd63124d55fb5fe60e6d00",
    }
    assert len(records) == len(questions) == 15

    spec_by_gene = {spec.gene: spec for spec in GENE_SPECS}
    for record, question in zip(records, questions, strict=True):
        spec = spec_by_gene[record["source_record_id"]]
        metadata = record["source_metadata"]
        exon = metadata["exon_selection"]["selected_exon"]
        selected = metadata["selected_candidates"]
        assert len(record["reference_sequence"]) == exon["end"] - exon["start"] + 201
        assert metadata["exon_selection"]["class_allocation"] == {
            "missense_variant": 25,
            "splicing": 25,
        }
        assert Counter(candidate["quantile_bin"] for candidate in selected) == Counter(
            dict.fromkeys(range(1, 6), 10)
        )
        assert all(
            candidate["damage_score"] == spec.score_direction * candidate["source_score"]
            for candidate in selected
        )
        window = metadata["exon_selection"]["selected_window"]
        for candidate, private in zip(record["candidates"], selected, strict=True):
            _, genomic_pos, genomic_ref, genomic_alt = private["genomic_key"].split(":")
            if metadata["transcript"]["strand"] == "+":
                assert candidate["pos"] == int(genomic_pos) - window["start"] + 1
                assert (candidate["ref"], candidate["alt"]) == (genomic_ref, genomic_alt)
            else:
                assert candidate["pos"] == window["end"] - int(genomic_pos) + 1
                assert (candidate["ref"], candidate["alt"]) == (
                    reverse_complement(genomic_ref),
                    reverse_complement(genomic_alt),
                )
        assert all(
            record["reference_sequence"][candidate["pos"] - 1] == candidate["ref"]
            for candidate in record["candidates"]
        )
        assert len(question["candidates"]) == PANEL_SIZE
        assert f"**Gene:** {spec.gene}" in question["prompt"]
        assert all(
            token not in question["prompt"]
            for token in (
                spec.mavedb_urn,
                spec.transcript,
                "missense_variant",
                "splice_region_variant",
                "reference_score",
                "damage_score",
                "quantile_bin",
            )
        )
        assert all(
            candidate["genomic_key"] not in question["prompt"] for candidate in selected
        )
