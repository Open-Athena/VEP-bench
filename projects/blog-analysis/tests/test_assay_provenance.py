"""Offline checks for source identity, score revisions and public-release dates."""

import hashlib
import json
from pathlib import Path

import pytest
from vepbench_blog_analysis.assay_provenance import (
    allele_key,
    cached_file,
    compare_scores,
    public_date,
)
from vepbench_explorer.site import load_assay_publications


def test_padding_and_unanchored_deletions_refer_to_the_same_allele() -> None:
    assert allele_key("chr3", "100", "ATC", "A") == allele_key("3", "101", "TC", "-")
    assert allele_key("3", "100", "ACGT", "ATGT") == allele_key("3", "101", "C", "T")
    assert allele_key("chr3", "100", "A", "AT") == "3:101::T"


def test_partial_coverage_is_distinct_from_revised_and_identical_scores() -> None:
    selected = [
        {"candidate_id": f"V{i}", "genomic_key": f"3:{i}:A:C", "source_score": 0.5}
        for i in (1, 2, 3)
    ]
    rows = [
        {"chrom": "3", "pos": str(i), "ref": "A", "alt": "C", "score": score}
        for i, score in ((1, "0.5"), (2, "0.4"), (4, "0.1"))
    ]
    result = compare_scores(selected, iter(rows))
    assert result["selected_count"] == 3
    assert result["matched_count"] == 2
    assert result["exact_score_count"] == 1
    assert result["missing_candidate_ids"] == ["V3"]
    assert result["matches"][1]["public_score"] == 0.4
    assert result["matches"][1]["pinned_score"] == 0.5
    with pytest.raises(ValueError, match="duplicate"):
        compare_scores(selected, rows + rows[:1])
    with pytest.raises(ValueError, match="Non-finite"):
        compare_scores(selected, [{**rows[0], "score": "NaN"}])


def test_hgvs_matching_preserves_transcript_version_and_resolves_repeat_alignment() -> None:
    selected = [
        {
            "candidate_id": "V1",
            "genomic_key": "3:100:AG:A",
            "source_hgvs": "ENST1.2:c.49del",
            "source_score": -0.2,
        }
    ]
    row = {"source_hgvs": "ENST1.2:c.49del", "score": "-0.2"}
    assert compare_scores(selected, [row], "source_hgvs")["exact_score_count"] == 1
    assert (
        compare_scores(selected, [{**row, "source_hgvs": "ENST1.3:c.49del"}], "source_hgvs")[
            "matched_count"
        ]
        == 0
    )


def test_public_date_uses_file_release_instead_of_private_creation() -> None:
    record = {
        "creation_timestamp": "2026-03-30T00:00:00Z",
        "release_timestamp": "2026-08-26T20:15:11Z",
    }
    assert public_date(record, "IGVF") == "2026-08-26"
    with pytest.raises(KeyError):
        public_date({"creation_timestamp": "2026-03-30T00:00:00Z"}, "IGVF")


def test_cached_releases_are_digest_checked_without_network(tmp_path: Path) -> None:
    path = tmp_path / "scores.csv"
    path.write_bytes(b"score\n0.5\n")
    spec = {"filename": path.name, "sha256": hashlib.sha256(path.read_bytes()).hexdigest()}
    assert cached_file(spec, tmp_path, False) == path
    path.write_bytes(b"score\n0.6\n")
    with pytest.raises(ValueError, match="Digest mismatch"):
        cached_file(spec, tmp_path, False)


def test_saved_audit_matches_its_pinned_sources_and_selected_panel() -> None:
    root = Path(__file__).resolve().parents[3]
    manifest = root / "projects/blog-analysis/config/assay-provenance-sources.json"
    audit = json.loads(
        (
            root / ("projects/explorer/web/blog/introducing-vep-bench/assay-provenance-audit.json")
        ).read_text()
    )
    assert audit["manifest_sha256"] == hashlib.sha256(manifest.read_bytes()).hexdigest()
    panels = root / "data/sources/sge-mavedb-2026-09-03.jsonl"
    assert audit["source_panels_sha256"] == hashlib.sha256(panels.read_bytes()).hexdigest()
    expected = {row["url"]: row for row in json.loads(manifest.read_text())["sources"]}
    assert len(audit["sources"]) == len(expected)
    for row in audit["sources"]:
        assert row["scores_sha256"] == expected[row["url"]]["scores"]["sha256"]
        assert row["metadata_sha256"] == expected[row["url"]]["metadata"]["sha256"]
        assert len(row["matches"]) + len(row["missing_candidate_ids"]) == row["selected_count"]


def test_reviewed_full_and_partial_dates_agree_with_the_variant_audit() -> None:
    root = Path(__file__).resolve().parents[3]
    audit = json.loads(
        (
            root / ("projects/explorer/web/blog/introducing-vep-bench/assay-provenance-audit.json")
        ).read_text()
    )
    releases = {row["url"]: row for row in audit["sources"]}
    metadata = load_assay_publications(root / "projects/explorer/config/assay-publications.yaml")
    for gene, evidence in metadata["by_task_family"]["sge"]["records"].items():
        if evidence["url"] in releases:
            row = releases[evidence["url"]]
            assert row["gene"] == gene
            assert row["public_date"] == evidence["date"]
            assert row["matched_count"] == row["selected_count"]
        if "earlier_evidence" in evidence:
            earlier = evidence["earlier_evidence"]
            row = releases[earlier["url"]]
            assert row["gene"] == gene
            assert row["public_date"] == earlier["date"]
            assert 0 < row["matched_count"] < row["selected_count"]
