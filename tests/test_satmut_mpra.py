import gzip
import json
from collections import Counter
from pathlib import Path

import pytest
import yaml
from jsonschema import Draft202012Validator
from vepbench_satmut_mpra.task import (
    ELEMENT_SPECS,
    PGL4_23_MINIMAL_PROMOTER_SEQUENCE,
    PGL4Z_FIXED_DOWNSTREAM_SEQUENCE,
    ElementMetadata,
    PreparedElement,
    SatMutPreparationError,
    Variant,
    build_source_record,
    parse_cadd_vcf,
    select_panel,
    validate_mavedb_crosswalk,
    validate_prepared_artifacts,
    validate_reference,
)

from vepbench.artifacts import read_jsonl
from vepbench.questions.builder import build_questions, load_template

ROOT = Path(__file__).resolve().parents[1]
SOURCE = ROOT / "tests/fixtures/synthetic-ranking-source.jsonl"
TEMPLATE = ROOT / "configs/tasks/satmut-mpra/prompt.yaml"
SCHEMA = ROOT / "src/vepbench/schemas/question.schema.json"
PRODUCTION_SOURCE = ROOT / "data/sources/satmut-mpra-cadd-v1.7.jsonl"
PRODUCTION_MANIFEST = ROOT / "data/sources/satmut-mpra-cadd-v1.7.manifest.json"
INPUT_PINS = ROOT / "tasks/satmut-mpra/config/source-pins.yaml"


def test_ranking_builder_emits_structured_candidates_and_exact_prompt_rows() -> None:
    schema = json.loads(SCHEMA.read_text(encoding="utf-8"))
    question = build_questions(read_jsonl(SOURCE), load_template(TEMPLATE), schema)[0]

    Draft202012Validator(schema).validate(question)
    assert question["schema_version"] == "2.0"
    assert question["task_type"] == "ranking"
    assert question["question_id"] == "satmut-mpra-ranking-v2:synthetic-ranking-001"
    assert len(question["candidates"]) == 5
    assert "element\t1\tV01\tA\tC" in question["prompt"]
    assert "Sequence lines contain 80 bases except the final line." in question["prompt"]
    assert (
        "**Complete mutagenized insert (reporter-construct orientation):**\n\n"
        "Sequence lines contain 80 bases except the final line.\n\n```fasta" in question["prompt"]
    )
    assert "**Reference genome:**" not in question["prompt"]
    assert "synthetic contig" not in question["prompt"]
    assert "VCF deletions use" not in question["prompt"]
    assert "reference_score" not in question["prompt"]
    assert 'FINAL: {"V01": -0.42, "V02": 0.08}' in question["prompt"]


def test_ranking_builder_sorts_candidates_by_full_vcf_key() -> None:
    schema = json.loads(SCHEMA.read_text(encoding="utf-8"))
    source = read_jsonl(SOURCE)[0]
    source["candidates"].reverse()

    question = build_questions([source], load_template(TEMPLATE), schema)[0]

    keys = [
        (candidate["chrom"], candidate["pos"], candidate["ref"], candidate["alt"])
        for candidate in question["candidates"]
    ]
    assert keys == sorted(keys)
    rendered_rows = [
        f"{candidate['chrom']}\t{candidate['pos']}\t{candidate['candidate_id']}\t"
        f"{candidate['ref']}\t{candidate['alt']}"
        for candidate in question["candidates"]
    ]
    assert [question["prompt"].index(row) for row in rendered_rows] == sorted(
        question["prompt"].index(row) for row in rendered_rows
    )


def test_score_space_panel_is_deterministic_and_balanced() -> None:
    variants = [
        Variant("1", index + 1, "A", "C", float(index), 1e-8, 20, "SIGN") for index in range(103)
    ]

    first = select_panel(variants, element_label="synthetic")
    second = select_panel(list(reversed(variants)), element_label="synthetic")

    assert first == second
    assert len(first) == 50
    assert len({variant.key for variant, _ in first}) == 50
    assert Counter(bin_index for _, bin_index in first) == Counter(dict.fromkeys(range(5), 10))
    assert [variant.key for variant, _ in first] == sorted(variant.key for variant, _ in first)


def test_panel_requires_fifty_eligible_records() -> None:
    variants = [
        Variant("1", index + 1, "A", "C", float(index), 1e-8, 20, "SIGN") for index in range(49)
    ]
    with pytest.raises(ValueError, match="insufficient bin capacity"):
        select_panel(variants, element_label="small")


def test_vcf_reference_and_mavedb_crosswalk_include_anchored_deletion() -> None:
    vcf = gzip.compress(
        b"##fileformat=VCFv4.2\n"
        b"#CHROM\tPOS\tID\tREF\tALT\tQUAL\tFILTER\tINFO\n"
        b"1\t9\t.\tTA\tT\t.\tSIGN\tEF=-1.234;PV=1e-08;BC=12\n"
        b"1\t11\t.\tC\tG\t.\tMIN\tEF=0.126;PV=0.25;BC=20\n"
        b"1\t12\t.\tG\tA\t.\tQUAL\tEF=0.0;PV=0.9;BC=2\n"
    )
    variants, filters = parse_cadd_vcf(vcf, label="synthetic.vcf.gz")
    metadata = ElementMetadata("ACGT", "ACGT", "1", 10, 13, "2026-01-01")
    genome_sequence = "TTTTTTTTTACGTAAAA"

    def genome(chrom: str, start: int, end: int) -> str:
        assert chrom == "1"
        return genome_sequence[start:end]

    scores = (
        b"accession,hgvs_nt,score,p-value,unique_tags\n"
        b"urn:test#1,n.1=,-1.23,0.0,12.0\n"
        b"urn:test#2,n.2C>G,0.13,0.25,20.0\n"
        b"urn:test#3,n.3G>A,0.0,0.9,2.0\n"
    )
    spec = ELEMENT_SPECS[0]

    assert filters == {"SIGN": 1, "MIN": 1, "QUAL": 1}
    metadata, validated = validate_reference(variants, metadata, genome)
    assert validated == 3
    assert validate_mavedb_crosswalk(variants, metadata, scores, spec=spec, genome=genome) == 3


@pytest.mark.parametrize(
    "pos,ref,alt",
    [(2, "CG", "TA"), (2, "C", "CAT"), (2, "CGTA", "C"), (2, "CGT", "GA"), (1, "AC", "C")],
)
def test_vcf_parser_preserves_complete_alleles_without_length_restrictions(pos, ref, alt):
    payload = gzip.compress(
        (
            "##fileformat=VCFv4.2\n"
            "#CHROM\tPOS\tID\tREF\tALT\tQUAL\tFILTER\tINFO\n"
            f"1\t{pos}\t.\t{ref}\t{alt}\t.\tSIGN\tEF=-1.2;PV=1e-8;BC=12\n"
            "1\t1\t.\tA\tG\t.\tMIN\tEF=0.2;PV=0.25;BC=20\n"
            "1\t8\t.\tT\tA\t.\tQUAL\tEF=0;PV=0.9;BC=2\n"
        ).encode()
    )
    variants, _filters = parse_cadd_vcf(payload, label="complete-alleles.vcf.gz")
    assert variants[0].key == ("1", pos, ref, alt)
    sequence = "ACGTACGT"
    metadata = ElementMetadata(sequence, sequence, "1", 1, len(sequence), "2026-01-01")
    _metadata, checked = validate_reference(
        variants, metadata, lambda chrom, start, end: sequence[start:end]
    )
    assert checked == 3


def test_source_record_keeps_qc_private_and_assigns_opaque_ids() -> None:
    spec = ELEMENT_SPECS[0]
    variants = tuple(
        Variant("X", index + 100, "A", "C", index / 10, 1e-8, 20, "SIGN") for index in range(50)
    )
    element = PreparedElement(
        spec,
        ElementMetadata("C" * 60, "A" * 60, "X", 100, 159, "2026-01-01"),
        variants,
        {"SIGN": 50, "MIN": 1, "QUAL": 1},
        52,
        52,
    )

    record = build_source_record(element)

    assert [candidate["candidate_id"] for candidate in record["candidates"]] == [
        f"V{index:02d}" for index in range(1, 51)
    ]
    assert all("p_value" not in candidate for candidate in record["candidates"])
    assert all(
        candidate["source_filter"] == "SIGN"
        for candidate in record["source_metadata"]["selected_candidates"]
    )
    assert all(candidate["chrom"] == "element" for candidate in record["candidates"])
    assert record["reference_sequence"] == "A" * 60
    assert record["source_metadata"]["display_name"] == "F9 promoter"
    assert "F9" not in record["assay_context"]
    assert "F9" not in record["reporter_context"]
    assert "directly drives" in record["assay_context"]
    assert "no separate minimal promoter" in record["reporter_context"]
    assert record["source_metadata"]["target"]["sequence_basis"] == "reporter_construct"
    assert record["source_metadata"]["target"]["genomic_mapping_orientation"] == "forward"
    assert [candidate["pos"] for candidate in record["candidates"]] == [
        int(item["vcf_key"].split(":")[1]) - 99
        for item in record["source_metadata"]["selected_candidates"]
    ]


def test_panel_includes_min_and_excludes_qual_and_equivalent_alleles() -> None:
    variants = (
        *(
            Variant("X", 100 + i, "A", "C", i / 10, 0.2, 20, "MIN" if i % 2 else "SIGN")
            for i in range(50)
        ),
        Variant("X", 150, "AA", "A", -1.0, 0.2, 20, "MIN"),
        Variant("X", 151, "AA", "A", -2.0, 1e-8, 20, "SIGN"),
        Variant("X", 159, "A", "G", 100.0, 0.9, 2, "QUAL"),
    )
    record = build_source_record(
        PreparedElement(
            ELEMENT_SPECS[0],
            ElementMetadata("A" * 60, "A" * 60, "X", 100, 159, "2026-01-01"),
            variants,
            {"MIN": 26, "SIGN": 26, "QUAL": 1},
            53,
            53,
        )
    )
    metadata = record["source_metadata"]
    assert metadata["duplicate_normalized_allele_rows_excluded"] == 2
    assert Counter(c["source_filter"] for c in metadata["selected_candidates"]) == {
        "MIN": 25,
        "SIGN": 25,
    }
    assert len(record["candidates"]) == 50
    assert all(c["ref"] == "A" and c["alt"] == "C" for c in record["candidates"])


def test_known_zrs_reference_discrepancy_is_allowed_only_for_excluded_row() -> None:
    metadata = ElementMetadata("CA", "CA", "7", 156_791_603, 156_791_604, "2026-01-01")

    def genome(chrom: str, start: int, end: int) -> str:
        assert chrom == "7"
        reference = "CT"
        return reference[start - (156_791_603 - 1) : end - (156_791_603 - 1)]

    excluded = Variant("7", 156_791_603, "CA", "C", 0.0, 1.0, 1, "QUAL")
    validated, records = validate_reference([excluded], metadata, genome)
    assert records == 1
    assert validated.sequence == "CT"
    assert validated.mavedb_sequence == "CA"
    assert len(validated.reference_discrepancies) == 2

    eligible = Variant("7", 156_791_603, "CA", "C", 0.0, 1e-8, 20, "SIGN")
    with pytest.raises(SatMutPreparationError, match="eligible or barcode-qualified"):
        validate_reference([eligible], metadata, genome)


def test_unexpected_target_sequence_discrepancy_is_rejected() -> None:
    metadata = ElementMetadata("A", "A", "1", 10, 10, "2026-01-01")
    variant = Variant("1", 10, "C", "G", 0.0, 1e-8, 20, "SIGN")

    def genome(chrom: str, start: int, end: int) -> str:
        assert chrom == "1"
        return "C"

    with pytest.raises(SatMutPreparationError, match="MaveDB target base"):
        validate_reference([variant], metadata, genome)


def test_committed_satmut_artifacts_and_question_set_are_complete() -> None:
    manifest = validate_prepared_artifacts(PRODUCTION_SOURCE, PRODUCTION_MANIFEST)
    pins = yaml.safe_load(INPUT_PINS.read_text(encoding="utf-8"))
    records = read_jsonl(PRODUCTION_SOURCE)
    schema = json.loads(SCHEMA.read_text(encoding="utf-8"))
    questions = build_questions(records, load_template(TEMPLATE), schema)

    assert manifest["output"]["records"] == 16
    assert manifest["population"]["records"] == 23_516
    assert manifest["population"]["filter_counts"] == {
        "MIN": 17_685,
        "QUAL": 1_499,
        "SIGN": 4_332,
    }
    assert len(records) == len(questions) == 16
    for filename, expected in pins["cadd"].items():
        observed = manifest["sources"]["cadd"]["files"][filename]
        assert {"bytes": observed["bytes"], "sha256": observed["sha256"]} == expected
    observed_md5_manifest = manifest["sources"]["cadd"]["md5_manifest"]
    assert {
        "bytes": observed_md5_manifest["bytes"],
        "sha256": observed_md5_manifest["sha256"],
    } == pins["cadd_md5_manifest"]
    for urn, expected in pins["mavedb"].items():
        observed = manifest["sources"]["mavedb"]["records"][urn]
        for payload_name in ("metadata", "scores"):
            assert {
                "bytes": observed[payload_name]["bytes"],
                "sha256": observed[payload_name]["sha256"],
            } == expected[payload_name]
    reference = manifest["sources"]["reference"]
    assert {"bytes": reference["bytes"], "sha256": reference["sha256"]} == pins["reference"]
    assert reference["retrieval_date"] == pins["retrieval_date"]
    for record, question in zip(records, questions, strict=True):
        assert len(record["candidates"]) == 50
        assert Counter(
            candidate["score_bin"] for candidate in record["source_metadata"]["selected_candidates"]
        ) == Counter(dict(enumerate(record["source_metadata"]["sampling"]["allocations"])))
        assert len(question["candidates"]) == 50
        assert question["prompt"].count("#CHROM\tPOS\tID\tREF\tALT") == 1
        assert all(candidate["chrom"] == "element" for candidate in question["candidates"])
        assert [
            (candidate["pos"], candidate["ref"], candidate["alt"])
            for candidate in question["candidates"]
        ] == sorted(
            (candidate["pos"], candidate["ref"], candidate["alt"])
            for candidate in question["candidates"]
        )
        assert all(
            record["reference_sequence"][
                candidate["pos"] - 1 : candidate["pos"] - 1 + len(candidate["ref"])
            ]
            == candidate["ref"]
            for candidate in question["candidates"]
        )
        assert all(marker not in question["prompt"] for marker in ("EF=", "PV=", "BC="))
        assert record["source_metadata"]["display_name"] not in question["prompt"]
        assert "rs6983267" not in question["prompt"]
        assert "\nX\t" not in question["prompt"]

    question_by_id = {question["question_id"]: question for question in questions}
    f9_prompt = question_by_id["satmut-mpra-ranking-v2:F9"]["prompt"]
    assert "F9" not in f9_prompt
    assert "directly drives a firefly luciferase reporter" in f9_prompt
    assert PGL4_23_MINIMAL_PROMOTER_SEQUENCE not in f9_prompt

    irf4_prompt = question_by_id["satmut-mpra-ranking-v2:IRF4"]["prompt"]
    assert "IRF4" not in irf4_prompt
    assert PGL4_23_MINIMAL_PROMOTER_SEQUENCE in irf4_prompt
    assert "24 hours after transfection" in irf4_prompt

    myc_prompt = question_by_id["satmut-mpra-ranking-v2:MYCrs6983267"]["prompt"]
    assert "MYC" not in myc_prompt
    assert "rs6983267" not in myc_prompt
    assert "32 hours after transfection" in myc_prompt
    assert "20 nM LiCl was added 24 hours after transfection" in myc_prompt

    zrs_prompt = question_by_id["satmut-mpra-ranking-v2:ZRSh13"]["prompt"]
    assert "ZRS" not in zrs_prompt
    assert "Hoxd13" in zrs_prompt
    assert PGL4Z_FIXED_DOWNSTREAM_SEQUENCE[:80] in zrs_prompt
    assert PGL4Z_FIXED_DOWNSTREAM_SEQUENCE[80:] in zrs_prompt

    zrs = manifest["population"]["elements"]["ZRSh13"]
    zrs_record = next(record for record in records if record["source_record_id"] == "ZRSh13")
    assert zrs_record["reference_sequence"].endswith("A")
    assert zrs["target_sequence_sha256"] == zrs["mavedb_target_sequence_sha256"]
    assert zrs["target_sequence_sha256"] != zrs["grch38_sequence_sha256"]
    assert zrs["reference_discrepancies"] == [
        {
            "chrom": "7",
            "grch38_base": "T",
            "mavedb_base": "A",
            "pos": 156_791_604,
            "treatment": "retain_reporter_construct_base_in_model_visible_reference",
        },
        {
            "cadd_ref": "CA",
            "grch38_ref": "CT",
            "source_filter": "QUAL",
            "treatment": "exclude_via_source_filter_and_retain_in_provenance",
            "vcf_key": "7:156791603:CA:C",
        },
    ]
