import runpy
from pathlib import Path
from types import SimpleNamespace

import pytest
from pyhgvs.utils import make_transcript

AUDIT = runpy.run_path(
    str(Path(__file__).resolve().parents[1] / "scripts/analyze_task_sampling.py")
)
map_allele = AUDIT["map_sge_allele"]
SEQUENCE = "ACGT" * 100


def _inputs(strand):
    transcript = make_transcript(
        {
            "id": "NM_000001.1",
            "chrom": "NC_000001.11",
            "start": 20,
            "end": 80,
            "strand": strand,
            "cds_start": 20,
            "cds_end": 80,
            "gene_name": "TEST",
            "exons": [[20, 80]],
        }
    )
    spec = SimpleNamespace(
        coordinate_mode="hgvs_transcript", transcript="NM_000001.1", expected_chrom="1"
    )
    return spec, transcript, {"NC_000001.11": SEQUENCE}


@pytest.mark.parametrize(
    "strand,hgvs,start,end,alt",
    [
        ("+", "c.5_7del", 24, 27, ""),
        ("+", "c.5_7delinsAGC", 24, 27, "AGC"),
        ("+", "c.10_11insAC", 30, 30, "AC"),
        ("+", "c.5_7dup", 27, 27, SEQUENCE[24:27]),
        ("-", "c.5_7del", 73, 76, ""),
        ("-", "c.5_7delinsAGC", 73, 76, "GCT"),
        ("-", "c.10_11insAC", 70, 70, "GT"),
        ("-", "c.5_7dup", 73, 73, SEQUENCE[73:76]),
    ],
)
def test_hgvs_allele_reconstructs_complete_edit_on_both_strands(strand, hgvs, start, end, alt):
    spec, transcript, genome = _inputs(strand)
    pos, ref, observed_alt = map_allele(f"NM_000001.1:{hgvs}", spec, transcript, genome)
    assert ref and observed_alt
    assert SEQUENCE[pos - 1 : pos - 1 + len(ref)] == ref
    reconstructed = SEQUENCE[: pos - 1] + observed_alt + SEQUENCE[pos - 1 + len(ref) :]
    assert reconstructed == SEQUENCE[:start] + alt + SEQUENCE[end:]


def test_mapper_rejects_source_reference_repair():
    spec, transcript, genome = _inputs("+")
    with pytest.raises(ValueError, match="source REF mismatch"):
        map_allele("NM_000001.1:c.5G>T", spec, transcript, genome)


def test_insertion_endpoints_must_be_adjacent():
    spec, transcript, genome = _inputs("+")
    with pytest.raises(ValueError, match="not adjacent"):
        map_allele("NM_000001.1:c.5_7insAC", spec, transcript, genome)
