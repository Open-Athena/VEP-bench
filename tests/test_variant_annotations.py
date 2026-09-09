import copy
import json
from pathlib import Path

import pytest
from vepbench_explorer.annotations import (
    EnsemblCache,
    generate_annotations,
    load_annotations,
    opensplice_alleles,
    variants_for_record,
    write_annotation_json,
)

from vepbench.errors import BuildError


def source(count: int = 2) -> dict:
    return {
        "task_family": "sge",
        "source_record_id": "test",
        "candidates": [{"candidate_id": f"V{i}"} for i in range(count)],
        "source_metadata": {
            "selected_candidates": [
                {"candidate_id": f"V{i}", "genomic_key": f"1:{100 + i}:A:C"} for i in range(count)
            ]
        },
    }


def fake_response(request: dict) -> dict | list:
    if request["path"] == "info/software":
        return {"release": 115}
    assert request["path"] == "vep/homo_sapiens/region"
    assert request["params"]["distance"] == 1000
    # Deliberately return a different ordering to exercise the identity join.
    return [
        {
            "id": line.split()[2],
            "input": line,
            "assembly_name": "GRCh38",
            "most_severe_consequence": "missense_variant",
            "transcript_consequences": [{"consequence_terms": ["synonymous_variant"]}],
        }
        for line in reversed(request["body"]["variants"])
    ]


def test_annotation_batches_deduplicate_and_replay_offline(tmp_path: Path) -> None:
    record = source(201)
    duplicate = {**record, "source_record_id": "duplicate"}
    path = tmp_path / "source.jsonl"
    path.write_text(json.dumps(record) + "\n" + json.dumps(duplicate) + "\n")
    requests = []

    def transport(request: dict) -> dict | list:
        requests.append(request)
        return fake_response(request)

    cache = tmp_path / "cache"
    document = generate_annotations([path], EnsemblCache(cache, transport))
    assert [len(r["body"]["variants"]) for r in requests if r["body"]] == [200, 1]
    variants = variants_for_record(record, document)
    assert variants["V0"]["most_severe_consequence"] == "missense_variant"
    assert variants["V200"]["genomic"]["pos"] == 300
    saved = tmp_path / "annotations.json"
    write_annotation_json(saved, document)
    assert load_annotations(saved) == document

    def offline(request: dict) -> None:
        pytest.fail(f"unexpected network request: {request}")

    assert generate_annotations([path], EnsemblCache(cache, offline)) == document
    changed = copy.deepcopy(record)
    changed["source_record_id"] = "reselected"
    with pytest.raises(BuildError, match="missing or stale"):
        variants_for_record(changed, document)
    incomplete = copy.deepcopy(document)
    del incomplete["by_task_family"]["sge"]["test"]["variants"]["V0"]
    with pytest.raises(BuildError, match="missing or stale"):
        variants_for_record(record, incomplete)


@pytest.mark.parametrize("failure", ["missing", "duplicate", "wrong_input", "wrong_assembly"])
def test_annotation_rejects_incomplete_or_mismatched_vep_results(
    tmp_path: Path, failure: str
) -> None:
    path = tmp_path / "source.jsonl"
    path.write_text(json.dumps(source()) + "\n")

    def transport(request: dict) -> dict | list:
        response = fake_response(request)
        if isinstance(response, list):
            if failure == "missing":
                response.pop()
            elif failure == "duplicate":
                response[1] = response[0]
            elif failure == "wrong_input":
                response[0]["input"] = "a different allele"
            else:
                response[0]["assembly_name"] = "GRCh37"
        return response

    with pytest.raises(BuildError, match="VEP"):
        generate_annotations([path], EnsemblCache(tmp_path / "cache", transport))


@pytest.mark.parametrize(
    "strand,sequence,expected",
    [
        (1, "GAACCGTTAC", {"pos": 11, "ref": "ACC", "alt": "A"}),
        (-1, "GTAACGGTTC", {"pos": 13, "ref": "CGG", "alt": "C"}),
    ],
)
def test_opensplice_deletion_projection_checks_whole_mutant(
    tmp_path: Path, strand: int, sequence: str, expected: dict
) -> None:
    record = {
        "source_metadata": {
            "gene": "TEST",
            "native_exon": {"start_exon": 12, "end_exon": 15, "strand": strand},
            "construct": {
                "native_upstream_length": 2,
                "native_downstream_length": 2,
                "components": {"wt_seq": "AACCGTTA"},
            },
            "selected_candidates": [
                {
                    "candidate_id": "V01",
                    "source": {
                        "construct_variant": {"insert_position": 3, "ref": "CC", "alt": ""},
                        "mutant_insert_sequence": "AAGTTA",
                    },
                }
            ],
        },
    }

    def transport(request: dict) -> dict:
        if request["path"].startswith("lookup/"):
            return {
                "assembly_name": "GRCh38",
                "strand": strand,
                "start": 1,
                "end": 100,
                "seq_region_name": "1",
            }
        assert request["path"] == "sequence/region/homo_sapiens/1:9..18:1"
        return {"seq": sequence}

    cache = EnsemblCache(tmp_path / "cache", transport)
    assert opensplice_alleles(record, cache) == {
        "V01": {"assembly": "GRCh38", "chrom": "1", **expected}
    }
    record["source_metadata"]["construct"]["components"]["wt_seq"] = "TACCGTTA"
    with pytest.raises(BuildError, match="does not match GRCh38"):
        opensplice_alleles(record, cache)


def test_cache_detects_changed_raw_response(tmp_path: Path) -> None:
    client = EnsemblCache(tmp_path, fake_response)
    client.get("info/software")
    path = next(tmp_path.glob("*.json"))
    cached = json.loads(path.read_text())
    cached["response"]["release"] += 1
    path.write_text(json.dumps(cached))
    with pytest.raises(BuildError, match="invalid Ensembl cache"):
        client.get("info/software")


def test_resuming_annotation_refuses_to_mix_ensembl_releases(tmp_path: Path) -> None:
    EnsemblCache(tmp_path, fake_response).get("info/software")

    def newer_release(request: dict) -> dict:
        assert request["path"] == "info/software"
        return {"release": 116}

    resumed = EnsemblCache(tmp_path, newer_release)
    resumed.get("info/software")
    with pytest.raises(BuildError, match="release changed"):
        resumed.get("lookup/symbol/homo_sapiens/TEST")


@pytest.mark.parametrize("failure", ["release_changed", "unavailable"])
@pytest.mark.parametrize("previous_snapshot", [False, True])
def test_failed_final_release_check_cannot_be_bypassed_by_cache_replay(
    tmp_path: Path, failure: str, previous_snapshot: bool
) -> None:
    path = tmp_path / "source.jsonl"
    cache = tmp_path / "cache"
    if previous_snapshot:
        path.write_text(json.dumps(source(1)) + "\n")
        generate_annotations([path], EnsemblCache(cache, fake_response))
    path.write_text(json.dumps(source(2)) + "\n")
    release_checks = 0

    def failed_verification(request: dict) -> dict | list:
        nonlocal release_checks
        if request["path"] == "info/software":
            release_checks += 1
            if release_checks == 2:
                if failure == "release_changed":
                    return {"release": 116}
                raise BuildError("Ensembl unavailable")
        return fake_response(request)

    with pytest.raises(BuildError, match=r"release changed|unavailable"):
        generate_annotations([path], EnsemblCache(cache, failed_verification))

    def offline(request: dict) -> None:
        assert request["path"] == "info/software"
        raise BuildError("offline: final release verification is still required")

    with pytest.raises(BuildError, match="verification is still required"):
        generate_annotations([path], EnsemblCache(cache, offline))


def test_archive_identity_is_preserved_and_caches_are_separate(tmp_path: Path) -> None:
    path = tmp_path / "source.jsonl"
    path.write_text(json.dumps(source()) + "\n")
    cache = tmp_path / "cache"
    current = generate_annotations([path], EnsemblCache(cache, fake_response))
    archive_url = "https://may2025.rest.ensembl.org"
    requests = []

    def archived(request: dict) -> dict | list:
        requests.append(request)
        assert request["base_url"] == archive_url
        if request["path"] == "info/software":
            return {"release": 114}
        return fake_response(request)

    document = generate_annotations([path], EnsemblCache(cache, archived, base_url=archive_url))
    assert document["annotation"]["base_url"] == archive_url
    assert document["annotation"]["software"] == {"release": 114}
    assert document["by_task_family"] == current["by_task_family"]
    assert any(request["body"] for request in requests)
    saved = tmp_path / "annotations.json"
    write_annotation_json(saved, document)
    assert load_annotations(saved) == document


def test_grch37_server_is_not_accepted_for_grch38_sources(tmp_path: Path) -> None:
    with pytest.raises(BuildError, match="GRCh38"):
        EnsemblCache(tmp_path, base_url="https://grch37.rest.ensembl.org")
