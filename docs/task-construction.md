# Variant eligibility and panel sampling

SGE, OpenSplice, and satMutMPRA use complete assayed alleles, including
substitutions, insertions, deletions, and multibase replacements when present
and sequence-valid. There is no SNV preference, variant-type quota, or arbitrary
allele-length limit. Source-specific measurement QC and reference checks still
apply. Each candidate describes one complete edit, never separately scored
pieces of a multibase allele.

## Score-space sampling

For each eligible assay/window, the shared
[sampler](../src/vepbench/sampling.py) places four equally spaced cutpoints between
the native score's P01 and P99, using type-7 interpolation. These define five
score-space bins. Scores below P01 and above P99 stay in the outer bins; the
original scores are never clipped or rescaled. A score equal to a cutpoint goes
into the bin on its right, so equal scores are never split across bins.

The target is 50 unique alleles, initially ten per bin. Sparse bins contribute
all available alleles; unused slots are redistributed evenly among bins with
capacity, with ascending bin index breaking remainder ties. For example,
capacities `[400, 80, 12, 6, 2]` yield allocations `[15, 15, 12, 6, 2]`.
A population below 50 or with collapsed P01/P99 anchors cannot produce a panel;
there is no fallback to rank-quantile sampling.

Within each bin, SHA-256 orders the compact JSON array
`[seed, question_key, zero_based_bin_index, allele_key]`, with the stable allele
key breaking hash ties. The seed and protocol are pinned in each task's
preparation configuration. Selected alleles are normalized against the displayed
sequence and sorted by local position, REF, and ALT before assigning `V01` to
`V50`. Reference validation and whole-mutant reconstruction check VCF projection,
including reverse strands and boundary anchors.

This favors coverage of effect magnitude without requiring every window to have
an artificial five-by-ten distribution. It does not estimate the natural
frequency of effects. Anchors, cutpoints, capacities, allocations, and selected
bin memberships are retained in provenance and omitted from prompts. All three
tasks retain their native signed measurement scales; SGE applies only its
reviewed damage-direction sign.

## Versioning and caches

The revised protocol uses version-2 question templates and new processed-cache
namespaces. Old consequence-filtered, SNV-only, or SIGN-only caches cannot supply
the expanded populations. Cache implementation fingerprints include shared
sampling and allele-normalization code. After changing preparation code, run
`uv run scripts/update_preparation_digests.py` before rebuilding sources;
`--check` verifies the fingerprints without updating them. Earlier result
snapshots remain self-contained; scores on different question-set fingerprints
are not directly comparable.

The explorer applies gene/element display metadata only when its source-record
digest matches the question. This prevents reused panel IDs after window
reselection from assigning new labels to older questions.

Exploratory analyses stay on retained branches and are permalinked from their
issues. They are not part of the production implementation.
