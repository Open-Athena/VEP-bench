# ClinVar

This task asks a model to classify one human GRCh38 single-nucleotide variant
as exactly `Benign` or `Pathogenic` using only a centered local reference
sequence window and the variant alleles.

- **Task family:** `clinvar`
- **Prompt version:** 1.0
- **Temporal cohort:** VCV records first public from 2026-07-01 through
  2026-07-31, inclusive
- **Size:** 42 public development questions (21 matched pairs; target 25 pairs)
- **Scoring:** multiple choice, deterministic exact match

## Model-visible input

Every prompt contains a 1,001-base uppercase A/C/G/T GRCh38 window, a local
VCFv4.3 SNV at position 501 on a contig named `window`, and the two choices in
this fixed order:

```text
C01. Benign
C02. Pathogenic
```

Chromosome, genomic position, gene, transcript, phenotype, condition, ClinVar
accession, review status, review stars, first-public date, and VEP consequence
are provenance only. None appears in the prompt. The versioned prompt is
[`templates/clinvar.json`](../../templates/clinvar.json), and the task-level
completion ceiling is defined by
[`configs/tasks/clinvar.yaml`](../../configs/tasks/clinvar.yaml).

## Frozen ClinVar cohort

Labels and review status come from NCBI's
[`ClinVarVCVRelease_2026-08.xml.gz`](https://ftp.ncbi.nlm.nih.gov/pub/clinvar/xml/ClinVarVCVRelease_2026-08.xml.gz),
published on 2026-08-06 with VCV schema 2.6. The pinned compressed object is
5,851,397,371 bytes with SHA-256
`95b07120541dae708ecca2bf749c12000bcd7bfa468fbe613c581c76b436dd0f`.
Preparation streams
`VariationArchive` elements and interprets the archive-level `DateCreated` as
the date the aggregate VCV record first became public.

A record is eligible only when:

- `DateCreated` is in July 2026;
- the aggregate germline description is exactly `Benign` or exactly
  `Pathogenic`;
- aggregate review status is one of the four one-star-or-higher statuses;
- the classified record is a direct simple allele rather than a haplotype or
  genotype;
- exactly one usable GRCh38 allele key resolves to a primary chromosome
  `1`-`22`, `X`, or `Y`; and
- REF and ALT are distinct one-base A/C/G/T alleles.

After those filters, every VCV sharing an allele key with another eligible VCV
is excluded. This prevents a genomic allele from appearing more than once or
carrying ambiguous labels. The source manifest records total and label-split
counts after every filter, plus duplicate-key attrition. The production release
yields 490 eligible unique SNVs: 271 benign and 219 pathogenic.

## Exact consequence matching

Eligible alleles are joined to
[`songlab/hg38-variant-consequences`](https://huggingface.co/datasets/songlab/hg38-variant-consequences)
at revision `eb3022cc6797b9369cca16af72ff3c4197df343a`. For each chromosome,
preparation collects the small set of candidate positions, lazily filters the
pinned Parquet by `pos`, reads only `pos`, `ref`, `alt`, and `consequence`,
materializes `chrom` from the chromosome shard, and then performs a streaming
exact join on all four allele-key columns. Parquet row-group `pos` statistics
identify the only row groups that
can contain a candidate, and an 8 MiB sparse mmap cache coalesces and
deduplicates their remote byte ranges. This preserves predicate pushdown without
multiplying a sparse query into thousands of resolver requests or downloading
every shard in full.

The original `consequence` column produced with VEP release 109.1 and
`--most_severe --distance 1000` defines matching strata. It is not collapsed,
and `consequence_cre` is not read. A missing exact match is counted and
excluded; multiple VEP rows for one candidate allele are a source-integrity
error. All 490 production candidates have exactly one pinned VEP match.

## Reference validation and deterministic sampling

Reference windows come from
[`marin-dna/human-genome`](https://huggingface.co/datasets/marin-dna/human-genome)
at revision `11b9433582981bb929af333bc6422f10a8fd71b4`. Preparation excludes and
counts candidates whose complete 1,001-base window cannot be built or whose
center base disagrees with REF. All 490 production candidates pass this check.

For each exact consequence `c`, capacity is the smaller of its valid benign
and pathogenic counts. The pair budget is the smaller of 25 and total matched
capacity. Sampling first represents as many shared consequences as possible,
then assigns remaining pairs to the least-represented non-full stratum. Ties
and within-stratum allele order use SHA-256 with seed `2026090100` and algorithm
version `clinvar_consequence_matched_sha256_v1`.

The result is exactly balanced overall and within every retained consequence.
The production cohort contains 21 pairs: 17 missense pairs and one pair each
for `3_prime_UTR_variant`, `splice_donor_region_variant`,
`splice_polypyrimidine_tract_variant`, and `stop_gained`. Because total shared
capacity is below the 25-pair target, the deterministic smaller cohort is
retained and the manifest records the reason. No unmatched consequence is used
as padding.

## Artifacts and regeneration

The committed compact
[`source JSONL`](../../data/sources/clinvar-july-2026.jsonl) retains hidden
selection metadata alongside each builder record. The
[`source manifest`](../../data/sources/clinvar-july-2026.manifest.json) records
the ClinVar release size and SHA-256, all pinned source revisions, filter and
join attrition, reference validation, consequence capacities, pair allocation,
per-record provenance, and the source artifact digest.

The pinned ClinVar release is 5.85 GB compressed, so preparation follows the
repository's explicit remote-worker pattern rather than running on a shared
development VM:

```bash
bash scripts/run_prepare_clinvar_sky.sh
```

The command requires `HF_TOKEN` and cloud credentials available to SkyPilot. On
a cache miss it downloads and streams the VCV XML on the worker, performs
block-cached sparse queries against pinned Parquet shards, and validates every
reference window. It then uploads
the processed pre-sampling state to the existing `open-athena/vepbench` Hugging
Face Storage Bucket under:

```text
data_prep/clinvar/v1/2026-08/<preparation-configuration-sha256>/
```

This namespace is separate from published `versions/`. It contains only three
processed Parquet tables—eligible ClinVar records, exact VEP matches, and
reference-valid windows—plus a digest-bearing completion manifest. The raw
ClinVar XML, VEP shards, and reference FASTA are never copied into the bucket.
The completion manifest is uploaded last; existing complete or incomplete
prefixes are never overwritten.

The cache key pins upstream release identities, preprocessing rules, window
configuration, and an explicit pre-sampling implementation revision. Maintainers
must replace that revision whenever parsing, filtering, joining, or reference
validation behavior changes. Sampling target, seed, and sampling implementation
are deliberately excluded, so the full reference-valid pool can be reused to
expand beyond 50 variants or compare deterministic sampling policies. A cache
hit downloads and verifies every processed file before reconstructing the exact
pre-sampling state.

Finally the command copies back only the compact benchmark artifacts, validates
them offline, and builds the task's questions. Tests use synthetic XML,
Parquet, and reference data and never access ClinVar, Hugging Face, or a model
provider.

Validate and rebuild locally after the compact artifacts exist:

```bash
uv run --locked python scripts/validate_clinvar_artifacts.py
uv run --locked vepbench build \
  --source data/sources/clinvar-july-2026.jsonl \
  --template templates/clinvar.json \
  --output /tmp/clinvar-questions.jsonl
```

The task-level completion ceiling is 128,000 tokens and is versioned in
[`configs/tasks/clinvar.yaml`](../../configs/tasks/clinvar.yaml).

## Interpretation and limitations

Membership means only that the aggregate VCV record first became public in
ClinVar during July 2026. A variant, its evidence, or its classification may
have appeared elsewhere earlier, and a submitter's last-evaluated date may
predate its first appearance in ClinVar. The cohort therefore reduces direct
ClinVar temporal leakage; it does not prove absence from model training data.

Pathogenicity is not determined by local sequence alone. The prompt omits
phenotype, inheritance, allele frequency, segregation, functional evidence,
gene, and transcript context. Scores measure sequence-context inference and
learned priors under a consequence-matched label distribution. They are not
clinical variant interpretations and must not be presented as such.
