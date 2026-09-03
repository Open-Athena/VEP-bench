import json
from collections import Counter
from dataclasses import replace
from io import StringIO
from pathlib import Path

import pytest
from jsonschema import Draft202012Validator
from vepbench_opensplice_snv.configuration import CONFIG
from vepbench_opensplice_snv.prepare import _validate_variant_metadata
from vepbench_opensplice_snv.task import (
    FAS_E5,
    FAS_E7,
    FAS_I5,
    FAS_I6,
    REQUIRED_MASTER_COLUMNS,
    ExonMetadata,
    OpenSplicePreparationError,
    Variant,
    build_source_record,
    cassette_segments,
    complete_cassette,
    eligible_variant_from_row,
    parse_exon_metadata,
    select_panel,
    summarize_and_select_exons,
    type7_quantile,
    validate_prepared_artifacts,
    validate_required_columns,
    validate_unique_variants,
)

from vepbench.artifacts import canonical_json, read_jsonl, sha256_file
from vepbench.questions.builder import build_questions, load_template

ROOT = Path(__file__).resolve().parents[1]
SOURCE = ROOT / "data/sources/opensplice-snv-figshare-v5.jsonl"
MANIFEST = ROOT / "data/sources/opensplice-snv-figshare-v5.manifest.json"
TEMPLATE = ROOT / "configs/tasks/opensplice-snv/prompt.yaml"
SCHEMA = ROOT / "src/vepbench/schemas/question.schema.json"


def _write_tampered_artifacts(
    tmp_path: Path,
    records: list[dict],
    manifest: dict,
) -> tuple[Path, Path]:
    source = tmp_path / "source.jsonl"
    source.write_text(
        "".join(f"{canonical_json(record)}\n" for record in records),
        encoding="utf-8",
        newline="\n",
    )
    manifest["output"]["bytes"] = source.stat().st_size
    manifest["output"]["sha256"] = sha256_file(source)
    manifest_path = tmp_path / "manifest.json"
    manifest_path.write_text(
        f"{canonical_json(manifest)}\n",
        encoding="utf-8",
        newline="\n",
    )
    return source, manifest_path


def _prediction(mode: str, identifier: str) -> dict:
    if mode == "genome":
        return {
            "variant_id": identifier,
            "acceptor_ref_at_canonical": 0.8,
            "acceptor_alt_at_canonical": 0.7,
            "donor_ref_at_canonical": 0.6,
            "donor_alt_at_canonical": 0.5,
            "delta_acceptor": -0.1,
            "delta_donor": -0.1,
            "mean_delta_splice": -0.1,
        }
    return {
        "identifier": identifier,
        "acceptor_wt": 0.8,
        "donor_wt": 0.6,
        "acceptor_mut": 0.7,
        "donor_mut": 0.5,
        "delta_acceptor": -0.1,
        "delta_donor": -0.1,
        "delta_mean": -0.1,
    }


def _variant(exon_id: str, gene: str, index: int, effect: float) -> Variant:
    start = index // 3 + 1
    mut = "CGT"[index % 3]
    wt_seq = "A" * 80
    variant_id = f"{exon_id}_A{start}{mut}"
    return Variant(
        chrom="chr1",
        pos=1_000 + start,
        genomic_ref="A",
        genomic_alt=mut,
        gene=gene,
        exon_id=f"{gene}_e1",
        ensembl_exon_id=exon_id,
        variant_id=variant_id,
        nt_seq=wt_seq[: start - 1] + mut + wt_seq[start:],
        start=start,
        wt="A",
        mut=mut,
        region="Exon",
        exon_length=30,
        psi_r1=50.0,
        psi_r2=51.0,
        psi_r3=49.0,
        wt_psi=50.0,
        psi=50.0 + effect,
        delta_psi=effect,
        se_wt_psi=0.1,
        se_psi=0.2,
        se=0.3,
        se_wt=0.4,
        se_d=0.5,
        significant="yes",
        alphagenome_genome=_prediction("genome", f"{exon_id}_A{start}{mut}"),
        alphagenome_minigene=_prediction("minigene", variant_id),
    )


def _master_row(exon: ExonMetadata) -> dict[str, str]:
    row = dict.fromkeys(REQUIRED_MASTER_COLUMNS, "")
    row.update(
        {
            "CHROM": "chr1",
            "POS": "100",
            "REF": "A",
            "ALT": "G",
            "gene": "TEST",
            "exon_id": "TEST_e1",
            "ensembl_exon_id": exon.ensembl_exon_id,
            "variant_id": "TEST_e1_U2G",
            "nt_seq": exon.wt_seq[:1] + "G" + exon.wt_seq[2:],
            "start": "2.0",
            "end": "2",
            "length": "1.0",
            "wt": "U",
            "mut": "G",
            "mut_type": "sub",
            "region": "Intron up",
            "exon_length": str(exon.exon_length),
            "psi_r1": "10.0",
            "psi_r2": "11.0",
            "psi_r3": "12.0",
            "wt_psi": "20.0",
            "se_wt_psi": "0.2",
            "psi": "15.0",
            "delta_psi": "-5.0",
            "se_psi": "0.3",
            "se": "0.4",
            "se_wt": "0.5",
            "se_d": "0.6",
            "significant": "yes",
            "measured": "True",
        }
    )
    for field in row:
        if field.startswith("alphagenome_genome__"):
            row[field] = "0.25"
        elif field.startswith("alphagenome_minigene__"):
            row[field] = "0.5"
    row["alphagenome_genome__variant_id"] = "ENSE_TEST_A2G"
    row["alphagenome_minigene__Identifier"] = "TEST_e1_U2G"
    return row


def test_required_columns_and_source_pins_are_strict() -> None:
    with pytest.raises(OpenSplicePreparationError, match="missing required columns"):
        validate_required_columns(["gene"], REQUIRED_MASTER_COLUMNS, label="master")
    assert set(CONFIG.pins["files"]) == {
        "master",
        "exon_metadata",
        "variant_metadata",
        "alphagenome_genome_inputs",
    }
    assert CONFIG.pins["files"]["master"]["sha256"] == (
        "1ec5aa239793bf6e84d5d771990d04486f03017b8d8cfb67d323076575d05bf4"
    )


def test_exon_and_variant_parsing_normalizes_rna_on_negative_strand() -> None:
    wt_seq = "AU" + "ACGT" + "A" * 25
    source = StringIO(
        "ensembl_exon_id\tstrand\tstart_exon\tend_exon\tup_5k\twt_seq\tdown_5k\texon_length\n"
        f"ENSE_TEST\t-1\t100\t103\tAAAA\t{wt_seq}\tTTTT\t4\n"
    )
    exon = parse_exon_metadata(source, label="exons")["ENSE_TEST"]
    variant, reasons = eligible_variant_from_row(
        _master_row(exon), {exon.ensembl_exon_id: exon}, label="master:2"
    )

    assert reasons == ()
    assert variant is not None
    assert variant.wt == "T"
    assert variant.mut == "G"
    assert variant.nt_seq[1] == "G"
    assert exon.strand == -1


def test_eligibility_excludes_missing_replicates_and_fails_closed() -> None:
    exon = ExonMetadata("ENSE_TEST", 1, 100, 103, "AT" + "ACGT" + "A" * 25, 4, 2, 25)
    missing = _master_row(exon)
    missing["psi_r2"] = "NA"
    variant, reasons = eligible_variant_from_row(
        missing, {exon.ensembl_exon_id: exon}, label="master:2"
    )
    assert variant is None
    assert reasons == ("nonfinite_psi_r2",)

    malformed = _master_row(exon)
    malformed["measured"] = "maybe"
    with pytest.raises(OpenSplicePreparationError, match="malformed boolean"):
        eligible_variant_from_row(malformed, {exon.ensembl_exon_id: exon}, label="master:2")

    nonfinite = _master_row(exon)
    nonfinite["delta_psi"] = "NaN"
    with pytest.raises(OpenSplicePreparationError, match="non-finite number"):
        eligible_variant_from_row(nonfinite, {exon.ensembl_exon_id: exon}, label="master:2")

    disagreement = _master_row(exon)
    disagreement["nt_seq"] = exon.wt_seq
    with pytest.raises(OpenSplicePreparationError, match="expected substitution"):
        eligible_variant_from_row(disagreement, {exon.ensembl_exon_id: exon}, label="master:2")


def test_duplicate_construct_keys_and_sequences_fail_closed() -> None:
    variant = _variant("ENSE_TEST", "TEST", 1, 1.0)
    with pytest.raises(OpenSplicePreparationError, match="duplicate construct variant key"):
        validate_unique_variants([variant, variant], exon_id="ENSE_TEST")


def test_minigene_metadata_join_uses_exact_sequence_and_deposited_identifier(
    tmp_path: Path,
) -> None:
    exon = ExonMetadata("ENSE_TEST", 1, 100, 129, "A" * 80, 30, 25, 25)
    variant = _variant("ENSE_TEST", "RENAMED", 1, 1.0)
    deposited_id = "LEGACY_e1_A1C"
    variant = replace(
        variant,
        alphagenome_minigene={
            **variant.alphagenome_minigene,
            "identifier": deposited_id,
        },
    )
    metadata = tmp_path / "variant-metadata.tsv"
    metadata.write_text(
        "ensembl_exon_id\tvariant_id\tnt_seq\texon_length\tstart\tlength\n"
        f"ENSE_TEST\tLEGACY_e1_wt\t{exon.wt_seq}\t30\t0\t0\n"
        f"ENSE_TEST\t{deposited_id}\t{variant.nt_seq}\t\t{variant.start}\t1\n",
        encoding="utf-8",
        newline="\n",
    )

    _validate_variant_metadata(metadata, {"ENSE_TEST": [variant]}, {"ENSE_TEST": exon})


def test_type7_quantile_and_exon_selection_are_order_invariant() -> None:
    assert type7_quantile([0.0, 10.0, 20.0, 30.0], 0.05) == pytest.approx(1.5)
    assert type7_quantile([0.0, 10.0, 20.0, 30.0], 0.95) == pytest.approx(28.5)

    variants_by_exon = {
        f"ENSE{index:03d}": [
            _variant(f"ENSE{index:03d}", f"GENE{index:03d}", item, item * index)
            for item in range(60)
        ]
        for index in range(1, 22)
    }
    variants_by_exon["ENSE_ALT"] = [
        _variant("ENSE_ALT", "GENE021", item, item * 0.1) for item in range(60)
    ]
    first_summaries, first = summarize_and_select_exons(variants_by_exon)
    reversed_input = {
        exon_id: list(reversed(variants))
        for exon_id, variants in reversed(list(variants_by_exon.items()))
    }
    second_summaries, second = summarize_and_select_exons(reversed_input)

    assert first_summaries == second_summaries
    assert first == second
    assert len(first) == 20
    assert len({summary.gene for summary in first}) == 20
    assert first[0].gene == "GENE021"
    assert "ENSE_ALT" not in {summary.ensembl_exon_id for summary in first}


def test_rank_quantile_panel_is_balanced_and_order_invariant() -> None:
    variants = [_variant("ENSE_TEST", "TEST", index, float(index)) for index in range(83)]
    first = select_panel(variants, exon_id="ENSE_TEST")
    second = select_panel(list(reversed(variants)), exon_id="ENSE_TEST")

    assert first == second
    assert len(first) == 50
    assert Counter(bin_index for _, bin_index, _ in first) == Counter(
        dict.fromkeys(range(1, 11), 5)
    )
    assert [item[0].construct_key for item in first] == sorted(
        item[0].construct_key for item in first
    )


def test_complete_cassette_and_segments_use_actual_upstream_flank() -> None:
    exon = ExonMetadata("ENSE_TEST", 1, 100, 129, "A" * 55 + "C" * 30 + "G" * 25, 30, 55, 25)
    cassette = complete_cassette(exon)
    segments = cassette_segments(exon)

    assert cassette == FAS_E5 + FAS_I5 + exon.wt_seq + FAS_I6 + FAS_E7
    assert len(cassette) == len(exon.wt_seq) + 300
    assert segments[2] == {
        "segment": "native upstream intron",
        "start": 147,
        "end": 201,
        "length": 55,
    }
    assert segments[3]["start"] == 202
    assert [segment["start"] for segment in segments[1:]] == [
        segment["end"] + 1 for segment in segments[:-1]
    ]


def test_source_record_keeps_predictions_private_and_renders_full_cassette() -> None:
    exon = ExonMetadata("ENSE_TEST", -1, 100, 129, "A" * 80, 30, 25, 25)
    variants = [_variant("ENSE_TEST", "SECRETGENE", index, float(index)) for index in range(60)]
    variants[0] = replace(
        variants[0],
        alphagenome_minigene={
            **variants[0].alphagenome_minigene,
            "delta_mean": None,
        },
    )
    _summaries, selected = summarize_and_select_exons(
        {
            **{
                f"ENSE{index:03d}": [
                    _variant(f"ENSE{index:03d}", f"GENE{index:03d}", item, item * index)
                    for item in range(60)
                ]
                for index in range(1, 20)
            },
            "ENSE_TEST": variants,
        }
    )
    summary = next(item for item in selected if item.ensembl_exon_id == "ENSE_TEST")
    record = build_source_record(
        exon,
        summary,
        variants,
        source_record_id="E01",
        genome_vcf_path="inputs/ENSE_TEST.vcf",
    )
    schema = json.loads(SCHEMA.read_text(encoding="utf-8"))
    question = build_questions([record], load_template(TEMPLATE), schema)[0]

    Draft202012Validator(schema).validate(question)
    assert record["reference_sequence"] == complete_cassette(exon)
    assert len(record["candidates"]) == 50
    assert "SECRETGENE" not in question["prompt"]
    assert "ENSE_TEST" not in question["prompt"]
    assert "AlphaGenome" not in question["prompt"]
    assert "left_n_padding" not in question["prompt"]
    assert "NNNNNNNN" not in question["prompt"]
    assert "three replicate measurements" not in question["prompt"]
    assert "Every candidate is a single-nucleotide substitution" not in question["prompt"]
    assert "tested alternative exon" in question["prompt"]
    assert record["source_metadata"]["selected_candidates"][0]["alphagenome"]
    assert any(
        not candidate["alphagenome"]["minigene"]["quality"]["available"]
        for candidate in record["source_metadata"]["selected_candidates"]
    )


def test_committed_opensplice_artifacts_are_complete_and_reproducible() -> None:
    manifest = validate_prepared_artifacts(SOURCE, MANIFEST)
    records = read_jsonl(SOURCE)
    schema = json.loads(SCHEMA.read_text(encoding="utf-8"))
    questions = build_questions(records, load_template(TEMPLATE), schema)

    assert manifest["output"]["records"] == len(records) == len(questions) == 20
    assert manifest["output"]["candidates"] == 1_000
    assert len({record["source_metadata"]["gene"] for record in records}) == 20
    for record, question in zip(records, questions, strict=True):
        assert len(record["candidates"]) == len(question["candidates"]) == 50
        assert all(
            record["reference_sequence"][candidate["pos"] - 1] == candidate["ref"]
            for candidate in record["candidates"]
        )
        assert all(
            marker not in question["prompt"]
            for marker in (
                record["source_metadata"]["gene"],
                record["source_metadata"]["ensembl_exon_id"],
                "AlphaGenome",
                "delta_psi",
                "quantile_bin",
            )
        )


def test_artifact_validation_recomputes_sampling_digest(tmp_path: Path) -> None:
    records = read_jsonl(SOURCE)
    manifest = json.loads(MANIFEST.read_text(encoding="utf-8"))
    fabricated = "0" * 64
    records[0]["source_metadata"]["selected_candidates"][0]["selection"]["sampling_digest"] = (
        fabricated
    )
    manifest["selected_panels"][0]["members"][0]["sampling_digest"] = fabricated
    source, manifest_path = _write_tampered_artifacts(tmp_path, records, manifest)

    with pytest.raises(OpenSplicePreparationError, match="private candidate provenance"):
        validate_prepared_artifacts(source, manifest_path)


def test_artifact_validation_recomputes_alphagenome_quality(tmp_path: Path) -> None:
    records = read_jsonl(SOURCE)
    manifest = json.loads(MANIFEST.read_text(encoding="utf-8"))
    quality = records[0]["source_metadata"]["selected_candidates"][0]["alphagenome"]["minigene"][
        "quality"
    ]
    quality["baseline_eligible"] = not quality["baseline_eligible"]
    source, manifest_path = _write_tampered_artifacts(tmp_path, records, manifest)

    with pytest.raises(OpenSplicePreparationError, match="minigene input reconstruction"):
        validate_prepared_artifacts(source, manifest_path)


def test_artifact_validation_recomputes_alphagenome_genome_input(tmp_path: Path) -> None:
    records = read_jsonl(SOURCE)
    manifest = json.loads(MANIFEST.read_text(encoding="utf-8"))
    genome_input = records[0]["source_metadata"]["selected_candidates"][0]["alphagenome"]["genome"][
        "input"
    ]
    genome_input["canonical_sites"][0]["site_pos1"] += 1
    source, manifest_path = _write_tampered_artifacts(tmp_path, records, manifest)

    with pytest.raises(OpenSplicePreparationError, match="genome input reconstruction"):
        validate_prepared_artifacts(source, manifest_path)


def test_artifact_validation_recomputes_processed_cache_key(tmp_path: Path) -> None:
    records = read_jsonl(SOURCE)
    manifest = json.loads(MANIFEST.read_text(encoding="utf-8"))
    fabricated = "0" * 64
    manifest["sources"]["processed_cache"]["cache_key"] = fabricated
    manifest["sources"]["processed_cache"]["prefix"] = f"data_prep/opensplice-snv/v1/{fabricated}"
    source, manifest_path = _write_tampered_artifacts(tmp_path, records, manifest)

    with pytest.raises(OpenSplicePreparationError, match="processed-cache provenance"):
        validate_prepared_artifacts(source, manifest_path)
