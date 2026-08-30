# Plan: chr17 Ensembl VEP most-severe-consequence task

## Goal

Add a real multiple-choice benchmark task that asks a model to predict an
Ensembl VEP most-severe consequence from an SNV and its local DNA sequence.
The first version will use chromosome 17 only and will replace the public
synthetic benchmark question set.

The task remains a public development set with deterministic exact-match
scoring. Dataset preparation is an explicit, networked one-time action; normal
benchmark builds, tests, CI, and evaluations remain offline except for the
existing explicit OpenRouter evaluation command.

## Fixed task design

- Consequence labels come from
  `songlab/hg38-variant-consequences`, pinned to revision
  `eb3022cc6797b9369cca16af72ff3c4197df343a`.
- Read only `chrom`, `pos`, `ref`, `alt`, and `consequence` from
  `17.parquet`. Ignore `consequence_cre`.
- Sample 10 variants per final output class.
- Collapse these four source labels into one model-visible choice:
  `intergenic_variant / intron_variant / upstream_gene_variant / downstream_gene_variant`.
- Give that collapsed class 10 questions composed of:
  - 3 `intergenic_variant`
  - 3 `intron_variant`
  - 2 `upstream_gene_variant`
  - 2 `downstream_gene_variant`
- Retain every other consequence observed on chr17 as its own class.
- Show the complete post-collapse chr17 vocabulary as the choices in every
  question. Sort choice text lexicographically and assign stable IDs `C01`,
  `C02`, and so on.
- The final question count is 10 times the number of post-collapse classes.

## Model-visible input

Fetch reference sequence from `marin-dna/human-genome`, pinned to revision
`11b9433582981bb929af333bc6422f10a8fd71b4`. Use a 1,001-base forward-strand
window with 500 bases on either side of the SNV. Uppercase the soft-masked
sequence and require all bases to be A/C/G/T.

The prompt must not expose chromosome 17 or the original genomic position.
Instead, represent the window as an inline FASTA contig and the SNV as an
inline VCF record at the center of that contig:

````text
**Reference genome:** human GRCh38
**VEP version:** release 109.1
**VEP flags:** `--most_severe --distance 1000`

```fasta
>window
...1,001 bases, wrapped as FASTA...
```

```vcf
##fileformat=VCFv4.3
##contig=<ID=window,length=1001>
#CHROM POS ID REF ALT QUAL FILTER INFO
window 501 . A G . PASS .
```
````

The original `17:position:ref:alt` remains in the committed source record ID
for auditing, but only `question["prompt"]` is sent to the model. Keep the
existing response requirement: the last well-formed answer line must be
`FINAL: <choice-id>`.

## Dataset preparation

Do not add another public `vepbench` CLI subcommand. Add an ordinary Python
script run as:

```bash
uv run python scripts/prepare_vep_consequence.py
```

Put reusable preparation logic in an importable package module so it can be
tested without invoking the script. Keep the production configuration pinned
in the script rather than exposing a large argument surface.

The preparation flow should:

1. Download the single pinned 383 MB chr17 Parquet file to temporary storage
   with the authenticated Hugging Face Hub/Xet client, then eagerly load only
   the five required columns with Polars. The decoded table has 248,760,612
   rows, so this step runs on high-memory EC2 rather than the shared node.
2. Use a fixed seed and a versioned integer rank over `(chrom, pos, ref, alt)`
   to retain a bounded set of candidates for each source consequence. Do not
   keep the Parquet file as a persistent artifact or perform a chromosome-wide
   sort.
3. Apply the per-class quotas above and fetch the corresponding reference
   windows through indexed HTTP range requests. A small adapted copy of the
   Marin `Genome` helper may be vendored with attribution.
4. Verify that every window is 1,001 bases, contains only A/C/G/T after
   uppercasing, and has the expected REF base at zero-based index 500. Skip an
   invalid candidate and deterministically use the next candidate for that
   class.
5. Fail rather than silently underfill any class.
6. Write deterministic UTF-8/LF source JSONL sorted by source record ID, plus a
   JSON manifest containing source URLs and revisions, configuration, raw
   counts, the collapse mapping, final vocabulary, quotas, seed, and output
   digest.

Add Polars, `pyfaidx`, `fsspec[http]`, and the authenticated Hugging Face
Hub/Xet download client in a dedicated locked preparation dependency group.
Do not add the Hugging Face `datasets` package or pandas.

## SkyPilot execution

Add a small SkyPilot task that runs the preparation script on an on-demand EC2
instance:

- AWS `us-east-1`
- at least 8 CPUs and 256 GiB RAM (the grouped top-k over 248,760,612 decoded
  rows has a much larger working set than the 383 MB compressed Parquet file)
- 20 GiB disk
- no GPU and no spot instance
- automatic teardown after 10 idle minutes

SkyPilot should sync the repository, install the locked preparation
dependencies, and write the compact source JSONL and manifest in the remote
worktree. Run the task synchronously, copy those two artifacts back immediately,
validate them locally, and explicitly terminate the cluster. SkyPilot must not
run in CI.

## Repository integration

- Add a dedicated prompt template and make the default `vepbench build` use the
  new chr17 source and template.
- Commit the compact prepared source, its manifest, and the generated
  `benchmark/questions.jsonl`; never commit the remote Parquet or full genome.
- Replace the committed synthetic benchmark and remove the public synthetic
  result. Retain small synthetic/local fixtures only for unit tests.
- Retire the `build-demo-result` command if it no longer has a meaningful
  public artifact to build.
- Update the README and site notice with the source versions, flags, local
  FASTA/VCF representation, collapse rule, sampling balance, and the absence of
  committed evaluation runs.

No database, backend, new scoring method, live VEP call, or model API call is
part of dataset preparation.

## Tests and acceptance criteria

Use tiny local Parquet and indexed FASTA fixtures; tests and CI must not access
the network or launch EC2.

Test that:

- repeated preparation is byte-identical for the same inputs and seed;
- every retained class has exactly 10 questions;
- the collapsed class has the exact 3/3/2/2 source composition;
- every question has the same complete post-collapse vocabulary and stable
  choice IDs;
- inline FASTA and VCF blocks are syntactically correct, the sequence is 1,001
  uppercase bases, VCF position 501 matches REF, and ALT is a different SNV;
- no model-visible prompt contains `chr17` or the original genomic position;
- invalid sequence windows are backfilled deterministically and insufficient
  classes fail clearly;
- the committed questions rebuild byte-for-byte from the committed source;
- the question and result schemas, exact-match scorer, and static site still
  pass with no committed result files.

Run the required offline checks:

```bash
uv run --locked pytest
uv run --locked ruff check .
uv run --locked vepbench build --output /tmp/questions.jsonl
cmp benchmark/questions.jsonl /tmp/questions.jsonl
uv run --locked vepbench site --output /tmp/vepbench-site
```

## Known limitation

The prompt intentionally omits genomic coordinates and transcript annotation,
so it does not contain all information used by VEP. Collapsing intronic,
intergenic, upstream-gene, and downstream-gene labels removes the most obvious
unresolvable distinction, including the part affected by `--distance 1000`,
but other transcript-dependent terms may still be underdetermined from a local
sequence alone. Document the task as measuring sequence-context inference and
model priors, not exact reconstruction of VEP from sufficient annotation data.
