"""Sequence-relative VCF normalization without splitting an assayed allele."""

from __future__ import annotations


def normalize_allele(sequence: str, pos: int, ref: str, alt: str) -> tuple[int, str, str]:
    """Validate, left-align and anchor a 1-based edit against its displayed sequence.

    Empty REF inserts before ``pos``; empty ALT deletes REF. Shared VCF padding
    outside the displayed sequence may be removed after upstream REF validation.
    At the first base, an indel uses the following reference base as its anchor.
    """
    if isinstance(pos, bool) or not isinstance(pos, int):
        raise ValueError("allele position must be an integer")
    while pos < 1 and ref and alt and ref[0] == alt[0]:
        pos, ref, alt = pos + 1, ref[1:], alt[1:]
    while pos + len(ref) - 1 > len(sequence) and ref and alt and ref[-1] == alt[-1]:
        ref, alt = ref[:-1], alt[:-1]
    if (
        not 1 <= pos <= len(sequence) + (not ref)
        or set(sequence + ref + alt) - set("ACGT")
        or ref == alt
        or sequence[pos - 1 : pos - 1 + len(ref)] != ref
    ):
        raise ValueError("invalid sequence-relative allele or REF mismatch")
    mutant = sequence[: pos - 1] + alt + sequence[pos - 1 + len(ref) :]
    # Trim from the right, extending left when one allele becomes empty. This
    # rotates repetitive indels to their leftmost equivalent representation.
    while True:
        if ref and alt and ref[-1] == alt[-1]:
            ref, alt = ref[:-1], alt[:-1]
        elif (not ref or not alt) and pos > 1:
            pos -= 1
            anchor = sequence[pos - 1]
            ref, alt = anchor + ref, anchor + alt
        else:
            break
    if not ref or not alt:
        following = pos - 1 + len(ref)
        if following >= len(sequence):
            raise ValueError("allele leaves no reference base for a VCF anchor")
        ref, alt = ref + sequence[following], alt + sequence[following]
    while len(ref) > 1 and len(alt) > 1 and ref[0] == alt[0]:
        pos, ref, alt = pos + 1, ref[1:], alt[1:]
    if (
        sequence[pos - 1 : pos - 1 + len(ref)] != ref
        or sequence[: pos - 1] + alt + sequence[pos - 1 + len(ref) :] != mutant
    ):
        raise ValueError("normalization changed the complete assayed allele")
    return pos, ref, alt
