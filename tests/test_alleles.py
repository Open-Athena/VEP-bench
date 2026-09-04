import pytest

from vepbench.alleles import normalize_allele


@pytest.mark.parametrize(
    "sequence,pos,ref,alt,expected",
    [
        ("CACACG", 4, "AC", "", (1, "CAC", "C")),
        ("CAAAAG", 5, "A", "", (1, "CA", "C")),
        ("CAAAAG", 5, "", "A", (1, "C", "CA")),
        ("ACGT", 1, "A", "", (1, "AC", "C")),
        ("ACGT", 1, "", "T", (1, "A", "TA")),
        ("ACGT", 3, "GT", "CA", (3, "GT", "CA")),
    ],
)
def test_normalization_preserves_complete_mutant(sequence, pos, ref, alt, expected):
    assert normalize_allele(sequence, pos, ref, alt) == expected
    p, r, a = expected
    assert sequence[: p - 1] + a + sequence[p - 1 + len(r) :] == (
        sequence[: pos - 1] + alt + sequence[pos - 1 + len(ref) :]
    )
    assert normalize_allele(sequence, *expected) == expected


@pytest.mark.parametrize(
    "pos,ref,alt", [(0, "A", "C"), (1, "T", "C"), (1, "A", "A"), (1, "A", "N")]
)
def test_invalid_or_unresolved_alleles_fail(pos, ref, alt):
    with pytest.raises(ValueError):
        normalize_allele("ACGT", pos, ref, alt)


def test_external_padding_can_be_reanchored_inside_the_display():
    # Genomic TG>T deletes the first displayed G; its T anchor is outside.
    assert normalize_allele("GAC", 0, "TG", "T") == (1, "GA", "A")
    with pytest.raises(ValueError):
        normalize_allele("GAC", 0, "TG", "C")
