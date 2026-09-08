import hashlib
import json
import re
from collections import Counter
from dataclasses import replace
from pathlib import Path

import pytest
from jsonschema import Draft202012Validator
from vepbench_sge.configuration import CONFIG, REVIEWED_GENES
from vepbench_sge.prepare import _pyhgvs_mapper
from vepbench_sge.task import (
    GENE_SPECS,
    PANEL_SIZE,
    Exon,
    SGEPreparationError,
    Transcript,
    Variant,
    build_catalog_audit,
    build_source_record,
    choose_panel,
    parse_score_csv,
    reverse_complement,
    transcript_coding_sequence,
    validate_prepared_artifacts,
    validate_reference_variants,
)

from vepbench.artifacts import read_jsonl
from vepbench.questions.builder import build_questions, load_template

ROOT = Path(__file__).resolve().parents[1]
TEMPLATE = ROOT / "configs/tasks/sge/prompt.yaml"
SCHEMA = ROOT / "src/vepbench/schemas/question.schema.json"
PRODUCTION_SOURCE = ROOT / "data/sources/sge-mavedb-2026-09-03.jsonl"
PRODUCTION_MANIFEST = ROOT / "data/sources/sge-mavedb-2026-09-03.manifest.json"
EXPECTED_QUESTION_MANIFEST = ROOT / "benchmark/sge-expected-manifest.json"
IMPLEMENTATION_PATHS = (
    "tasks/sge/src/vepbench_sge/task.py",
    "tasks/sge/src/vepbench_sge/prepare.py",
    "tasks/sge/src/vepbench_sge/configuration.py",
    "src/vepbench/sampling.py",
    "src/vepbench/alleles.py",
)


def _variant(
    gene: str,
    pos: int,
    score: float,
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
    )


def _mapper_for(chrom: str):
    def mapper(value: str) -> tuple[str, int, str, str]:
        return chrom, 107, "A", "C"

    return mapper


def test_configuration_covers_exact_reviewed_gene_and_source_sets() -> None:
    assert {spec.gene for spec in GENE_SPECS} == REVIEWED_GENES
    assert len({spec.mavedb_urn for spec in GENE_SPECS}) == 16
    assert set(CONFIG.pins["mavedb"]) == {spec.mavedb_urn for spec in GENE_SPECS}
    assert set(CONFIG.pins["cdot"]) == {spec.transcript for spec in GENE_SPECS}
    assert CONFIG.values["sampling"]["panel_size"] == 50
    assert CONFIG.values["sequence"]["flank_bases"] == 100
    assert all(
        re.search(r"\bexons?\s+\d", spec.assay_context, flags=re.IGNORECASE) is None
        for spec in GENE_SPECS
    )


def test_cache_implementation_digest_matches_source_bytes() -> None:
    sha256sum_lines = "".join(
        f"{hashlib.sha256((ROOT / path).read_bytes()).hexdigest()}  {path}\n"
        for path in IMPLEMENTATION_PATHS
    )
    observed = hashlib.sha256(sha256sum_lines.encode()).hexdigest()
    assert CONFIG.values["cache"]["implementation_sha256"] == observed


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

    assert len(audit["selected_score_sets"]) == 16
    card11 = next(record for record in audit["records"] if record["urn"].endswith("card11-test"))
    assert card11["decision"] == "excluded"
    assert "noncanonical duplicate" in card11["reason"]


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
        assert report["mapped_unique_allele_records"] == 1


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


def test_reference_validation_checks_every_base_without_consequence_annotations():
    variant = _variant("TEST", 10, 1.0, ref="ACG", alt="A")
    assert validate_reference_variants([variant], genome=lambda c, s, e: "ACG") == (variant,)
    with pytest.raises(SGEPreparationError, match="REF mismatch"):
        validate_reference_variants([variant], genome=lambda c, s, e: "ACT")


def test_panel_selection_is_deterministic_and_score_space_covered() -> None:
    transcript = Transcript("NM_TEST.1", "TEST", "1", "+", (Exon(101, 200),))
    variants = tuple(
        [_variant("TEST", index + 1, float(index)) for index in range(30)]
        + [_variant("TEST", index + 51, float(index + 30)) for index in range(30)]
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
    assert Counter(candidate.score_bin for candidate in first.candidates) == Counter(
        dict.fromkeys(range(5), 10)
    )
    assert summaries[0]["exclusion_reason"] is None


def test_panel_selection_handles_reverse_orientation() -> None:
    transcript = Transcript("NM_TEST.1", "TEST", "1", "-", (Exon(101, 200),))
    variants = tuple(
        [_variant("TEST", index + 1, float(index)) for index in range(10)]
        + [_variant("TEST", index + 21, float(index + 10)) for index in range(50)]
    )
    panel, _ = choose_panel(
        variants,
        transcript,
        genome=lambda chrom, start, end: "A" * (end - start),
    )

    assert panel is not None
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
    transcript = Transcript(spec.transcript, spec.gene, spec.expected_chrom, "+", (Exon(101, 200),))
    variants = tuple(
        [
            _variant(
                spec.gene,
                index + 1,
                float(index),
                chrom=spec.expected_chrom,
            )
            for index in range(25)
        ]
        + [
            _variant(
                spec.gene,
                index + 51,
                float(index + 25),
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
    assert question["question_id"] == f"sge-ranking-v2:{spec.gene}"
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


def test_committed_sge_artifacts_and_question_set_are_complete():
    manifest = validate_prepared_artifacts(PRODUCTION_SOURCE, PRODUCTION_MANIFEST)
    records = read_jsonl(PRODUCTION_SOURCE)
    assert len(records) == 16
    assert {r["source_record_id"] for r in records} == REVIEWED_GENES
    assert len(manifest["catalog_audit"]["selected_score_sets"]) == 16
    assert all(len(r["candidates"]) == 50 for r in records)
    assert any(len(c["ref"]) != 1 or len(c["alt"]) != 1 for r in records for c in r["candidates"])
    assert "consequences" not in manifest["sources"]
    assert "annotation" not in manifest["sources"]
    schema = json.loads(SCHEMA.read_text())
    questions = build_questions(records, load_template(TEMPLATE), schema)
    assert all(q["metadata"]["task_family"] == "sge" for q in questions)


@pytest.mark.parametrize(
    "strand,hgvs,start,end,alt",
    [
        ("+", "c.5_7del", 24, 27, ""),
        ("+", "c.5_7delinsAGC", 24, 27, "AGC"),
        ("+", "c.10_11insAC", 30, 30, "AC"),
        ("+", "c.5_7dup", 27, 27, "ACG"),
        ("-", "c.5_7del", 73, 76, ""),
        ("-", "c.5_7delinsAGC", 73, 76, "GCT"),
        ("-", "c.10_11insAC", 70, 70, "GT"),
        ("-", "c.5_7dup", 73, 73, "CGT"),
    ],
)
def test_hgvs_complete_allele_and_reverse_display_reconstruct_same_edit(
    strand, hgvs, start, end, alt
):
    sequence = "ACGT" * 100
    record = {
        "id": "NM_000001.1",
        "gene_name": "TEST",
        "genome_builds": {
            "GRCh38": {
                "contig": "NC_000001.11",
                "strand": strand,
                "exons": [[20, 80]],
                "cds_start": 20,
                "cds_end": 80,
            }
        },
    }
    mapper = _pyhgvs_mapper({"1": sequence}, {record["id"]: record})
    mapped = mapper(f"NM_000001.1:{hgvs}")
    assert mapped is not None
    _, pos, ref, observed_alt = mapped
    mutant = sequence[: pos - 1] + observed_alt + sequence[pos - 1 + len(ref) :]
    assert mutant == sequence[:start] + alt + sequence[end:]
    from vepbench.alleles import normalize_allele

    visible_pos = len(sequence) - (pos + len(ref) - 1) + 1
    rc = reverse_complement(sequence)
    p, r, a = normalize_allele(
        rc, visible_pos, reverse_complement(ref), reverse_complement(observed_alt)
    )
    assert rc[: p - 1] + a + rc[p - 1 + len(r) :] == reverse_complement(mutant)


def test_window_selection_uses_expanded_range_without_snv_preference():
    sequence = "ACGT" * 400
    transcript = Transcript("NM_TEST.1", "TEST", "1", "+", (Exon(201, 300), Exon(1001, 1100)))
    variants = []
    for offset in range(50):
        pos = 201 + offset
        ref = sequence[pos - 1]
        variants.append(
            _variant("TEST", pos, offset / 100, ref=ref, alt="T" if ref != "T" else "A")
        )
        pos = 1001 + offset
        ref = sequence[pos - 1 : pos + 1]
        variants.append(
            _variant("TEST", pos, float(offset), ref=ref, alt="TT" if ref != "TT" else "AA")
        )
    # Its start fits, but its complete REF span extends beyond the window.
    variants.append(_variant("TEST", 1195, 1000.0, ref=sequence[1194:1214], alt="A"))
    panel, summaries = choose_panel(variants, transcript, genome=lambda c, s, e: sequence[s:e])
    assert panel is not None and panel.exon == Exon(1001, 1100)
    assert summaries[1]["eligible_records"] == 50
    assert all(len(c.variant.ref) == 2 for c in panel.candidates)
