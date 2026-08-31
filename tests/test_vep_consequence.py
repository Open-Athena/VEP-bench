import json
import re
import textwrap
from collections import Counter
from dataclasses import dataclass
from pathlib import Path

import polars as pl
import pytest

from vepbench.builder import build_questions, load_template, read_jsonl
from vepbench.genome import Genome
from vepbench.vep_consequence import (
    COLLAPSED_LABEL,
    COLLAPSED_SOURCE_QUOTAS,
    CandidateScan,
    PreparationConfig,
    PreparationError,
    PreparedDataset,
    prepare_dataset,
    scan_candidate_pools,
    stable_variant_rank,
    validate_prepared_artifacts,
    write_prepared_dataset,
)

ROOT = Path(__file__).resolve().parents[1]
TEMPLATE = ROOT / "templates/vep_most_severe_consequence.json"
SCHEMA = ROOT / "schemas/question.schema.json"
PRODUCTION_SOURCE = ROOT / "data/sources/chr17-vep-consequences.jsonl"
PRODUCTION_MANIFEST = ROOT / "data/sources/chr17-vep-consequences.manifest.json"


@dataclass(frozen=True)
class LocalFixture:
    parquet: Path
    fasta: Path
    config: PreparationConfig
    scan: CandidateScan
    prepared: PreparedDataset
    invalid_source_record_id: str


@pytest.fixture
def local_fixture(tmp_path: Path) -> LocalFixture:
    source_consequences = [
        *COLLAPSED_SOURCE_QUOTAS,
        "missense_variant",
        "synonymous_variant",
    ]
    config = PreparationConfig(
        per_class_quota=10,
        candidate_pool_size=16,
        seed=271_828,
    )
    rows: list[dict[str, object]] = []
    for label_index, consequence in enumerate(source_consequences):
        for row_index in range(16):
            rows.append(
                {
                    "chrom": "17",
                    "pos": 10_000 + label_index * 1_000 + row_index * 17,
                    "ref": "A",
                    "alt": ("C", "G", "T")[row_index % 3],
                    "consequence": consequence,
                    "consequence_cre": "ignored",
                }
            )

    missense_rows = [row for row in rows if row["consequence"] == "missense_variant"]
    invalid_row = min(
        missense_rows,
        key=lambda row: stable_variant_rank(
            str(row["chrom"]),
            int(row["pos"]),
            str(row["ref"]),
            str(row["alt"]),
            config.seed,
        ),
    )
    sequence = ["A"] * 30_000
    sequence[int(invalid_row["pos"]) - 1] = "C"
    fasta = tmp_path / "tiny.fa"
    fasta.write_text(
        ">17\n" + "\n".join(textwrap.wrap("".join(sequence), 80)) + "\n",
        encoding="utf-8",
        newline="\n",
    )
    parquet = tmp_path / "tiny.parquet"
    pl.DataFrame(rows).write_parquet(parquet)

    scan = scan_candidate_pools(
        parquet,
        seed=config.seed,
        pool_size=config.candidate_pool_size,
    )
    with Genome(fasta, subset_chroms={"17"}) as genome:
        prepared = prepare_dataset(
            scan,
            genome,
            config=config,
            source={
                "dataset": "local/test",
                "dataset_revision": "local/test@test-revision",
                "parquet_url": str(parquet),
                "revision": "test-revision",
            },
            reference={
                "dataset": "local/genome",
                "fasta_url": str(fasta),
                "revision": "test-revision",
            },
        )
    return LocalFixture(
        parquet=parquet,
        fasta=fasta,
        config=config,
        scan=scan,
        prepared=prepared,
        invalid_source_record_id=(
            f"17:{invalid_row['pos']}:{invalid_row['ref']}:{invalid_row['alt']}"
        ),
    )


def test_eager_scan_uses_stable_bounded_candidate_pools(
    local_fixture: LocalFixture,
) -> None:
    assert local_fixture.scan.raw_counts == {
        consequence: 16
        for consequence in sorted(
            [*COLLAPSED_SOURCE_QUOTAS, "missense_variant", "synonymous_variant"]
        )
    }
    for candidates in local_fixture.scan.candidates.values():
        assert len(candidates) == local_fixture.config.candidate_pool_size
        assert list(candidates) == sorted(candidates, key=lambda item: item.sort_key)
        assert all(
            ranked.rank
            == stable_variant_rank(
                *ranked.candidate.key,
                seed=local_fixture.config.seed,
            )
            for ranked in candidates
        )

    smaller = scan_candidate_pools(
        local_fixture.parquet,
        seed=local_fixture.config.seed,
        pool_size=5,
    )
    for consequence, candidates in smaller.candidates.items():
        expected = sorted(
            local_fixture.scan.candidates[consequence],
            key=lambda item: item.sort_key,
        )[:5]
        assert list(candidates) == expected


def test_committed_production_artifacts_are_balanced_and_pinned() -> None:
    manifest = validate_prepared_artifacts(PRODUCTION_SOURCE, PRODUCTION_MANIFEST)

    assert manifest["output"]["records"] == 190
    assert len(manifest["final_vocabulary"]) == 19
    assert set(manifest["final_class_counts"].values()) == {10}
    assert manifest["sampling"]["seed"] == 2_026_082_800
    assert sum(manifest["sampling"]["raw_counts"].values()) == 248_760_612
    assert {
        label: manifest["sampling"]["selected_source_counts"][label]
        for label in COLLAPSED_SOURCE_QUOTAS
    } == COLLAPSED_SOURCE_QUOTAS


def test_generated_production_prompts_hide_absolute_coordinates() -> None:
    schema = json.loads(SCHEMA.read_text(encoding="utf-8"))
    questions = build_questions(
        read_jsonl(PRODUCTION_SOURCE), load_template(TEMPLATE), schema
    )

    assert len(questions) == 190
    for question in questions:
        prompt = question["prompt"]
        original_position = question["provenance"]["source_record_id"].split(":")[1]
        assert "**Reference genome:** human GRCh38" in prompt
        assert "chr17" not in prompt.lower()
        assert original_position not in prompt


def test_preparation_is_byte_identical_and_balanced(
    local_fixture: LocalFixture, tmp_path: Path
) -> None:
    first_source = tmp_path / "first/source.jsonl"
    first_manifest = tmp_path / "first/manifest.json"
    second_source = tmp_path / "second/source.jsonl"
    second_manifest = tmp_path / "second/manifest.json"
    first = write_prepared_dataset(
        local_fixture.prepared,
        output=first_source,
        manifest_output=first_manifest,
        output_relpath="data/sources/example.jsonl",
    )
    second = write_prepared_dataset(
        local_fixture.prepared,
        output=second_source,
        manifest_output=second_manifest,
        output_relpath="data/sources/example.jsonl",
    )

    assert first == second
    assert first_source.read_bytes() == second_source.read_bytes()
    assert first_manifest.read_bytes() == second_manifest.read_bytes()
    assert validate_prepared_artifacts(first_source, first_manifest)["output"][
        "records"
    ] == 30

    manifest = json.loads(first_manifest.read_text(encoding="utf-8"))
    assert set(manifest["final_class_counts"].values()) == {10}
    assert {
        label: manifest["sampling"]["selected_source_counts"][label]
        for label in COLLAPSED_SOURCE_QUOTAS
    } == COLLAPSED_SOURCE_QUOTAS
    assert manifest["sampling"]["invalid_candidates_skipped"] == {
        "missense_variant": 1
    }
    assert (
        local_fixture.invalid_source_record_id
        not in manifest["record_source_consequences"]
    )


def test_generated_prompts_have_complete_local_fasta_and_vcf(
    local_fixture: LocalFixture,
) -> None:
    schema = json.loads(SCHEMA.read_text(encoding="utf-8"))
    questions = build_questions(
        local_fixture.prepared.records,
        load_template(TEMPLATE),
        schema,
    )
    expected_choices = local_fixture.prepared.manifest["choices"]
    assert len(questions) == 30

    final_counts: Counter[str] = Counter()
    for question in questions:
        assert question["choices"] == expected_choices
        assert "**Reference genome:** human GRCh38" in question["prompt"]
        assert "**VEP version:** release 109.1" in question["prompt"]
        assert "**VEP flags:** `--most_severe --distance 1000`" in question["prompt"]
        assert "chr17" not in question["prompt"].lower()
        original_position = question["provenance"]["source_record_id"].split(":")[1]
        assert original_position not in question["prompt"]

        fasta_match = re.search(
            r"```fasta\n>window\n(?P<sequence>[ACGT\n]+)\n```",
            question["prompt"],
        )
        assert fasta_match is not None
        sequence = fasta_match.group("sequence").replace("\n", "")
        assert len(sequence) == 1_001
        assert sequence == sequence.upper()

        vcf_match = re.search(
            r"window\t501\t\.\t(?P<ref>[ACGT])\t(?P<alt>[ACGT])\t\.\tPASS\t\.",
            question["prompt"],
        )
        assert vcf_match is not None
        assert sequence[500] == vcf_match.group("ref")
        assert vcf_match.group("ref") != vcf_match.group("alt")
        answer = next(
            choice["text"]
            for choice in question["choices"]
            if choice["choice_id"] == question["answer_choice_id"]
        )
        final_counts[answer] += 1

    assert final_counts == {
        COLLAPSED_LABEL: 10,
        "missense_variant": 10,
        "synonymous_variant": 10,
    }


def test_invalid_windows_are_backfilled_deterministically(
    local_fixture: LocalFixture,
) -> None:
    selected_ids = {
        record["source_record_id"] for record in local_fixture.prepared.records
    }
    assert local_fixture.invalid_source_record_id not in selected_ids
    missense_ids = {
        source_id
        for source_id, source_consequence in local_fixture.prepared.manifest[
            "record_source_consequences"
        ].items()
        if source_consequence == "missense_variant"
    }
    assert len(missense_ids) == 10


def test_insufficient_candidate_pool_fails_clearly(
    local_fixture: LocalFixture,
) -> None:
    candidates = dict(local_fixture.scan.candidates)
    candidates["synonymous_variant"] = candidates["synonymous_variant"][:9]
    insufficient = CandidateScan(
        candidates=candidates,
        raw_counts=local_fixture.scan.raw_counts,
    )

    with Genome(local_fixture.fasta, subset_chroms={"17"}) as genome:
        with pytest.raises(
            PreparationError,
            match="synonymous_variant: needed 10 valid variants, found 9",
        ):
            prepare_dataset(
                insufficient,
                genome,
                config=local_fixture.config,
                source={"dataset_revision": "local/test@test-revision"},
                reference={},
            )
