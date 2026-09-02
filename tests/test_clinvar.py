import gzip
import json
import textwrap
from collections import Counter
from datetime import date
from pathlib import Path
from types import SimpleNamespace

import polars as pl
import pytest

from vepbench.builder import build_file, build_questions, load_template
from vepbench.clinvar import (
    CHOICES,
    LABELS,
    ClinVarPreparationError,
    PreparationConfig,
    VepCandidate,
    VepJoinResult,
    allocate_pairs,
    parse_clinvar_vcv,
    prepare_dataset,
    sparse_join_vep,
    validate_prepared_artifacts,
    validate_reference_windows,
    write_prepared_dataset,
)
from vepbench.clinvar_cache import (
    download_processed_cache,
    implementation_digest,
    load_processed_cache,
    processed_cache_key,
    processed_cache_prefix,
    publish_processed_cache,
    remote_cache_state,
    write_processed_cache,
)
from vepbench.genome import Genome

ROOT = Path(__file__).resolve().parents[1]
XML_FIXTURE = ROOT / "tests/fixtures/clinvar-vcv-synthetic.xml"
TEMPLATE = ROOT / "templates/clinvar.json"
SCHEMA = ROOT / "schemas/question.schema.json"
PRODUCTION_SOURCE = ROOT / "data/sources/clinvar-july-2026.jsonl"
PRODUCTION_MANIFEST = ROOT / "data/sources/clinvar-july-2026.manifest.json"
EXPECTED_MANIFEST = ROOT / "benchmark/clinvar-expected-manifest.json"


def _parsed():
    return parse_clinvar_vcv(
        XML_FIXTURE,
        start_date=date(2026, 7, 1),
        end_date=date(2026, 7, 31),
    )


def _write_fasta(path: Path, *, invalid_positions: set[int] | None = None) -> None:
    sequence = ["A"] * 20_000
    for position in invalid_positions or set():
        sequence[position - 1] = "C"
    path.write_text(
        ">17\n" + "\n".join(textwrap.wrap("".join(sequence), 80)) + "\n",
        encoding="utf-8",
        newline="\n",
    )


def _join_all(parsed) -> VepJoinResult:
    consequence_by_position = {
        1001: "missense_variant",
        2001: "missense_variant",
        3001: "synonymous_variant",
        4001: "synonymous_variant",
    }
    return VepJoinResult(
        matched=tuple(
            VepCandidate(candidate, consequence_by_position[candidate.pos])
            for candidate in parsed.candidates
        ),
        missing=(),
    )


def _prepare(tmp_path: Path, *, invalid_positions: set[int] | None = None):
    parsed = _parsed()
    fasta = tmp_path / "reference.fa"
    _write_fasta(fasta, invalid_positions=invalid_positions)
    with Genome(fasta, subset_chroms={"17"}) as genome:
        prepared = prepare_dataset(
            parsed,
            _join_all(parsed),
            genome,
            config=PreparationConfig(seed=314_159),
            clinvar_source={
                "dataset": "NCBI ClinVar VCV",
                "dataset_revision": "ClinVarVCVRelease_2026-08",
                "release_url": "https://example.test/ClinVarVCVRelease_2026-08.xml.gz",
                "release_date": "2026-08-06",
                "schema_version": "2.6",
                "bytes": XML_FIXTURE.stat().st_size,
                "sha256": "0" * 64,
            },
            vep_source={
                "dataset": "songlab/hg38-variant-consequences",
                "revision": "test-revision",
                "vep_release": "109.1",
                "flags": ["--most_severe", "--distance", "1000"],
                "selected_column": "consequence",
                "join_keys": ["chrom", "pos", "ref", "alt"],
                "query": "position filter before exact streaming join",
            },
            reference={
                "dataset": "local/reference",
                "revision": "test-revision",
                "assembly": "GRCh38",
            },
        )
    return prepared


def test_streaming_parser_applies_date_label_review_location_and_duplicate_filters(
    tmp_path: Path,
) -> None:
    parsed = _parsed()

    assert [candidate.source_record_id for candidate in parsed.candidates] == [
        "VCV000000001.1",
        "VCV000000002.2",
        "VCV000000003.1",
        "VCV000000004.1",
    ]
    assert {candidate.date_created for candidate in parsed.candidates} >= {
        "2026-07-01",
        "2026-07-31",
    }
    assert {candidate.review_status for candidate in parsed.candidates} == {
        "criteria provided, single submitter",
        "criteria provided, multiple submitters, no conflicts",
        "reviewed by expert panel",
        "practice guideline",
    }
    assert {candidate.review_stars for candidate in parsed.candidates} == {1, 2, 3, 4}
    assert Counter(candidate.label for candidate in parsed.candidates) == {
        "Benign": 2,
        "Pathogenic": 2,
    }
    assert parsed.duplicate_allele_keys == 1
    assert parsed.duplicate_records_rejected == 2
    stages = {stage["stage"]: stage for stage in parsed.filter_stages}
    for rejected_label in (
        "Likely benign",
        "Likely pathogenic",
        "Uncertain significance",
        "Conflicting classifications of pathogenicity",
    ):
        assert stages["date_created"]["by_label"][rejected_label] == 1
    assert set(stages["exact_classification"]["by_label"]) == set(LABELS)
    assert parsed.filter_stages[-1] == {
        "stage": "unique_allele_key",
        "records": 4,
        "by_label": {"Benign": 2, "Pathogenic": 2},
    }
    first = parsed.candidates[0]
    assert first.genes == ("GENE1",)
    assert first.transcripts == ("NM_000001.2",)
    assert first.conditions == (("Condition one", "MONDO", "0000001"),)
    assert all(candidate.chrom == "17" for candidate in parsed.candidates)
    assert all(len(candidate.ref) == len(candidate.alt) == 1 for candidate in parsed.candidates)

    compressed = tmp_path / "fixture.xml.gz"
    compressed.write_bytes(gzip.compress(XML_FIXTURE.read_bytes(), mtime=0))
    assert (
        parse_clinvar_vcv(
            compressed,
            start_date=date(2026, 7, 1),
            end_date=date(2026, 7, 31),
        )
        == parsed
    )


def test_sparse_vep_join_is_exact_and_uses_original_consequence(
    tmp_path: Path,
) -> None:
    parsed = _parsed()
    parquet = tmp_path / "17.parquet"
    pl.DataFrame(
        [
            {
                "chrom": "17",
                "pos": 1001,
                "ref": "A",
                "alt": "C",
                "consequence": "missense_variant",
                "consequence_cre": "ignored_cre_value",
            },
            {
                "chrom": "17",
                "pos": 2001,
                "ref": "A",
                "alt": "G",
                "consequence": "missense_variant",
                "consequence_cre": "ignored_cre_value",
            },
            {
                "chrom": "17",
                "pos": 3001,
                "ref": "A",
                "alt": "T",
                "consequence": "synonymous_variant",
                "consequence_cre": "ignored_cre_value",
            },
            {
                "chrom": "17",
                "pos": 4001,
                "ref": "A",
                "alt": "G",
                "consequence": "wrong_allele",
                "consequence_cre": "ignored_cre_value",
            },
            {
                "chrom": "17",
                "pos": 9001,
                "ref": "A",
                "alt": "C",
                "consequence": "position_not_requested",
                "consequence_cre": "ignored_cre_value",
            },
        ]
    ).write_parquet(parquet, row_group_size=2)

    result = sparse_join_vep(parsed.candidates, {"17": parquet})

    assert [candidate.consequence for candidate in result.matched] == [
        "missense_variant",
        "missense_variant",
        "synonymous_variant",
    ]
    assert [candidate.pos for candidate in result.missing] == [4001]
    assert all(candidate.consequence != "ignored_cre_value" for candidate in result.matched)

    with parquet.open("rb") as stream:
        assert sparse_join_vep(parsed.candidates, {"17": stream}) == result


def test_sparse_vep_join_rejects_duplicate_exact_allele_rows(tmp_path: Path) -> None:
    parsed = _parsed()
    candidate = parsed.candidates[0]
    parquet = tmp_path / "17.parquet"
    pl.DataFrame(
        [
            {
                "chrom": candidate.chrom,
                "pos": candidate.pos,
                "ref": candidate.ref,
                "alt": candidate.alt,
                "consequence": consequence,
            }
            for consequence in ("missense_variant", "synonymous_variant")
        ]
    ).write_parquet(parquet)

    with pytest.raises(ClinVarPreparationError, match="more than one row"):
        sparse_join_vep((candidate,), {"17": parquet})


def test_pair_allocation_is_deterministic_diverse_balanced_and_capacity_bounded() -> None:
    capacities = {"a": 1, "b": 2, "c": 4, "d": 4, "unshared": 0}

    allocation = allocate_pairs(capacities, target_pairs=7, seed=271_828)

    assert allocation == allocate_pairs(capacities, target_pairs=7, seed=271_828)
    assert set(allocation) == {"a", "b", "c", "d"}
    assert sum(allocation.values()) == 7
    assert all(0 < count <= capacities[consequence] for consequence, count in allocation.items())
    assert allocate_pairs({"one": 1, "two": 1}, target_pairs=25, seed=1) == {
        "one": 1,
        "two": 1,
    }


def test_preparation_excludes_invalid_reference_and_accepts_cohort_below_50(
    tmp_path: Path,
) -> None:
    prepared = _prepare(tmp_path, invalid_positions={4001})

    assert len(prepared.records) == 2
    assert Counter(record["source_metadata"]["classification"] for record in prepared.records) == {
        "Benign": 1,
        "Pathogenic": 1,
    }
    assert {record["source_metadata"]["vep_consequence"] for record in prepared.records} == {
        "missense_variant"
    }
    reference_counts = prepared.manifest["counts"]["reference_validation"]
    assert reference_counts["invalid"]["records"] == 1
    assert prepared.manifest["sampling"]["pair_budget"] == 1
    assert prepared.manifest["sampling"]["reason_below_target"] is not None


def test_source_and_question_regeneration_are_byte_identical_and_prompts_are_redacted(
    tmp_path: Path,
) -> None:
    prepared = _prepare(tmp_path)
    source_one = tmp_path / "one/source.jsonl"
    manifest_one = tmp_path / "one/source.manifest.json"
    source_two = tmp_path / "two/source.jsonl"
    manifest_two = tmp_path / "two/source.manifest.json"

    first = write_prepared_dataset(
        prepared,
        output=source_one,
        manifest_output=manifest_one,
        output_relpath="data/sources/clinvar-july-2026.jsonl",
    )
    second = write_prepared_dataset(
        prepared,
        output=source_two,
        manifest_output=manifest_two,
        output_relpath="data/sources/clinvar-july-2026.jsonl",
    )

    assert first == second
    assert source_one.read_bytes() == source_two.read_bytes()
    assert manifest_one.read_bytes() == manifest_two.read_bytes()
    assert validate_prepared_artifacts(source_one, manifest_one)["output"]["records"] == 4

    question_one = tmp_path / "one/questions.jsonl"
    question_two = tmp_path / "two/questions.jsonl"
    assert build_file(source_one, TEMPLATE, SCHEMA, question_one) == build_file(
        source_two, TEMPLATE, SCHEMA, question_two
    )
    assert question_one.read_bytes() == question_two.read_bytes()

    questions = build_questions(
        prepared.records,
        load_template(TEMPLATE),
        json.loads(SCHEMA.read_text(encoding="utf-8")),
    )
    assert len(questions) == 4
    for question in questions:
        prompt = question["prompt"]
        source_id = question["provenance"]["source_record_id"]
        record = next(
            record for record in prepared.records if record["source_record_id"] == source_id
        )
        metadata = record["source_metadata"]
        assert "VCV" not in prompt
        assert metadata["review_status"] not in prompt
        assert metadata["vep_consequence"] not in prompt
        assert all(gene not in prompt for gene in metadata["genes"])
        assert all(transcript not in prompt for transcript in metadata["transcripts"])
        assert all(condition["name"] not in prompt for condition in metadata["conditions"])
        if metadata["pos"] != 1001:
            assert str(metadata["pos"]) not in prompt
        assert "window\t501\t." in prompt
        assert "##contig=<ID=window,length=1001>" in prompt
        assert list(question["choices"]) == list(CHOICES)
        assert question["metadata"]["task_family"] == "clinvar"
        assert metadata["classification"] in LABELS


def test_processed_cache_round_trips_complete_presampling_state(tmp_path: Path) -> None:
    parsed = _parsed()
    joined = _join_all(parsed)
    fasta = tmp_path / "reference.fa"
    _write_fasta(fasta, invalid_positions={4001})
    with Genome(fasta, subset_chroms={"17"}) as genome:
        validation = validate_reference_windows(
            joined,
            genome,
            config=PreparationConfig(seed=999),
        )
    configuration = {
        "release": "ClinVarVCVRelease_2026-08",
        "vep_revision": "test-vep",
        "reference_revision": "test-reference",
        "window_size": 1001,
        "implementation_sha256": "0" * 64,
    }
    cache_dir = tmp_path / "cache"

    manifest = write_processed_cache(
        cache_dir,
        configuration=configuration,
        parsed=parsed,
        joined=joined,
        reference_validation=validation,
    )
    loaded_parsed, loaded_joined, loaded_validation, loaded_manifest = load_processed_cache(
        cache_dir, expected_key=processed_cache_key(configuration)
    )

    assert manifest == loaded_manifest
    assert loaded_parsed == parsed
    assert loaded_joined == joined
    assert loaded_validation == validation
    assert loaded_manifest["files"]["clinvar-eligible.parquet"]["records"] == 4
    prefix = processed_cache_prefix("2026-08", manifest["cache_key"])
    assert prefix.startswith("data_prep/clinvar/v1/2026-08/")

    prepared = prepare_dataset(
        loaded_parsed,
        loaded_joined,
        None,
        config=PreparationConfig(seed=314_159),
        clinvar_source={
            "dataset": "NCBI ClinVar VCV",
            "dataset_revision": "ClinVarVCVRelease_2026-08",
        },
        vep_source={"dataset": "synthetic VEP"},
        reference={"dataset": "synthetic reference"},
        reference_validation=loaded_validation,
        processed_cache={"bucket": "open-athena/vepbench", "prefix": prefix},
    )
    assert len(prepared.records) == 2
    assert prepared.manifest["processed_cache"]["prefix"] == prefix


def test_processed_cache_is_immutable_and_publishes_manifest_last(tmp_path: Path) -> None:
    parsed = _parsed()
    joined = _join_all(parsed)
    fasta = tmp_path / "reference.fa"
    _write_fasta(fasta)
    with Genome(fasta, subset_chroms={"17"}) as genome:
        validation = validate_reference_windows(joined, genome, config=PreparationConfig())
    configuration = {"release": "synthetic", "implementation_sha256": "1" * 64}
    key = processed_cache_key(configuration)
    prefix = processed_cache_prefix("2026-08", key)
    cache_dir = tmp_path / "cache"
    write_processed_cache(
        cache_dir,
        configuration=configuration,
        parsed=parsed,
        joined=joined,
        reference_validation=validation,
    )

    class FakeApi:
        def __init__(self) -> None:
            self.files: dict[str, bytes] = {}
            self.events: list[str] = []

        def list_bucket_tree(self, _bucket, prefix, recursive, token):
            return [
                SimpleNamespace(type="file", path=path, size=len(payload))
                for path, payload in self.files.items()
                if path == prefix or path.startswith(f"{prefix}/")
            ]

        def batch_bucket_files(self, _bucket, add, token):
            for local, remote in add:
                self.files[remote] = Path(local).read_bytes()
                self.events.append(remote)

        def download_bucket_files(self, _bucket, files, raise_on_missing_files, token):
            for remote, local in files:
                path = Path(local)
                path.parent.mkdir(parents=True, exist_ok=True)
                path.write_bytes(self.files[remote])

    api = FakeApi()
    assert remote_cache_state(api, bucket_id="open-athena/vepbench", prefix=prefix, token="x") == (
        "absent"
    )
    publish_processed_cache(
        api,
        bucket_id="open-athena/vepbench",
        prefix=prefix,
        cache_dir=cache_dir,
        token="x",
    )
    assert api.events[-1] == f"{prefix}/manifest.json"
    assert remote_cache_state(api, bucket_id="open-athena/vepbench", prefix=prefix, token="x") == (
        "complete"
    )
    with pytest.raises(ClinVarPreparationError, match="refusing to overwrite"):
        publish_processed_cache(
            api,
            bucket_id="open-athena/vepbench",
            prefix=prefix,
            cache_dir=cache_dir,
            token="x",
        )

    downloaded = download_processed_cache(
        api,
        bucket_id="open-athena/vepbench",
        prefix=prefix,
        destination=tmp_path / "downloaded",
        token="x",
        expected_key=key,
    )
    assert downloaded[:3] == (parsed, joined, validation)


def test_cache_implementation_digest_is_workspace_independent(tmp_path: Path) -> None:
    roots = [tmp_path / "first", tmp_path / "second"]
    for root in roots:
        source = root / "src/prepare.py"
        source.parent.mkdir(parents=True)
        source.write_text("PINNED = True\n", encoding="utf-8")

    first = implementation_digest([roots[0] / "src/prepare.py"], root=roots[0])
    second = implementation_digest([roots[1] / "src/prepare.py"], root=roots[1])

    assert first == second
    (roots[1] / "src/prepare.py").write_text("PINNED = False\n", encoding="utf-8")
    assert implementation_digest([roots[1] / "src/prepare.py"], root=roots[1]) != first
    with pytest.raises(ClinVarPreparationError, match="outside the digest root"):
        implementation_digest([roots[0] / "src/prepare.py"], root=roots[1])


def test_production_artifacts_are_pinned_balanced_and_deterministic(tmp_path: Path) -> None:
    manifest = validate_prepared_artifacts(PRODUCTION_SOURCE, PRODUCTION_MANIFEST)

    assert manifest["clinvar"]["bytes"] == 5_851_397_371
    assert manifest["clinvar"]["sha256"] == (
        "95b07120541dae708ecca2bf749c12000bcd7bfa468fbe613c581c76b436dd0f"
    )
    assert manifest["counts"]["vep_join"]["input"]["records"] == 490
    assert manifest["counts"]["vep_join"]["matched"]["records"] == 490
    assert manifest["counts"]["vep_join"]["missing"]["records"] == 0
    assert manifest["counts"]["reference_validation"]["invalid"]["records"] == 0
    assert manifest["sampling"]["target_pairs"] == 25
    assert manifest["sampling"]["pair_budget"] == 21
    assert manifest["final_class_counts"] == {"Benign": 21, "Pathogenic": 21}
    assert manifest["processed_cache"]["prefix"].startswith("data_prep/clinvar/v1/2026-08/")

    output = tmp_path / "clinvar-questions.jsonl"
    build_file(PRODUCTION_SOURCE, TEMPLATE, SCHEMA, output)
    assert json.loads((tmp_path / "clinvar-questions.manifest.json").read_text()) == json.loads(
        EXPECTED_MANIFEST.read_text()
    )
