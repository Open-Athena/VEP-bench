# Ensembl VEP most-severe consequence

This task asks a model to predict the most severe consequence assigned by
Ensembl VEP for a human GRCh38 single-nucleotide variant, using only the local
reference sequence and variant alleles.

- **Task family:** `vep_most_severe_consequence`
- **Prompt version:** 1.1
- **Development set:** 190 public chromosome 17 SNVs
- **Scoring:** multiple choice, deterministic exact match
- **Explorer:** [Consequence classification](https://openathena.ai/VEPBench/tasks/consequence-classification.html)

## Model-visible input

Each prompt contains a 1,001-base forward-strand reference window and a local
VCF record with the SNV at position 501 on a contig named `window`. It also
states:

```text
Reference genome: human GRCh38
VEP version: release 109.1
VEP flags: --most_severe --distance 1000
```

The sequence is uppercase A/C/G/T. The original chromosome and position remain
in provenance and the question ID for auditing, but are omitted from the
model-visible prompt. Transcript annotations are also omitted.

The versioned prompt is defined in
[`templates/vep_most_severe_consequence.json`](../../templates/vep_most_severe_consequence.json).
Every response must end with a valid `FINAL: <choice-id>` line.

## Labels and sampling

The task has 19 answer choices and exactly 10 questions per final class.
Intronic, intergenic, upstream-gene, and downstream-gene source consequences
are collapsed into one answer choice because their distinctions are not
reliably recoverable from the model-visible input. Its ten questions contain:

- three `intergenic_variant` examples;
- three `intron_variant` examples;
- two `upstream_gene_variant` examples;
- two `downstream_gene_variant` examples.

Every other consequence observed on chromosome 17 remains a separate class.
All questions use the same lexicographically ordered choice vocabulary and
stable choice IDs.

## Sources and provenance

Labels come from
[`songlab/hg38-variant-consequences`](https://huggingface.co/datasets/songlab/hg38-variant-consequences)
at revision `eb3022cc6797b9369cca16af72ff3c4197df343a`. The source labels were
generated with VEP release 109.1 and `--most_severe --distance 1000`;
`consequence_cre` is not used.

Reference sequence comes from
[`marin-dna/human-genome`](https://huggingface.co/datasets/marin-dna/human-genome)
at revision `11b9433582981bb929af333bc6422f10a8fd71b4`.

The committed compact
[`source JSONL`](../../data/sources/chr17-vep-consequences.jsonl) and
[`source manifest`](../../data/sources/chr17-vep-consequences.manifest.json)
record the selected variants, source versions, raw counts, collapse mapping,
class composition, seed, and output digest. The task-level completion ceiling
is 262,144 tokens (2^18) and is versioned in
[`configs/tasks/vep-most-severe-consequence.yaml`](../../configs/tasks/vep-most-severe-consequence.yaml).

## Interpretation and limitation

The prompt does not contain all information used by VEP. In particular,
transcript annotations are absent, so even after collapsing the most obvious
coordinate-dependent classes, other transcript-dependent terms may remain
underdetermined.

Scores should therefore be interpreted as sequence-context inference and model
prior performance, not exact reconstruction of VEP from sufficient annotation
data. The questions and reference answers are a public development set, not a
contamination-resistant held-out benchmark.

## Published baseline

The official prompt 1.1 Luna runs contain all 190 responses without API errors:

| Reasoning effort | Correct | Exact match |
| --- | ---: | ---: |
| Low | 18 / 190 | 9.5% |
| Medium | 28 / 190 | 14.7% |
| High | 44 / 190 | 23.2% |

Inspect the individual questions, responses, available provider-exposed
reasoning, and scores in the [public explorer](https://openathena.ai/VEPBench/).

## Rebuilding the source

Preparation is an explicit networked operation and is not run in CI. It needs
an authenticated Hugging Face token in `HF_TOKEN` or the standard local token
cache, AWS credentials available to SkyPilot, and substantially more memory
than a normal development machine.

```bash
bash scripts/run_prepare_vep_consequence_sky.sh
```

The launcher creates an on-demand EC2 instance in `us-east-1`, passes `HF_TOKEN`
as a SkyPilot secret, downloads the pinned 383 MB chromosome 17 Parquet file,
and loads only the five required columns. The decoded file contains 248,760,612
rows, so the task requests 256 GiB RAM. It copies back only the compact source
JSONL and manifest, validates them locally, rebuilds the questions, and
terminates the instance.

Sampling uses seed `2026082800` and a versioned integer rank over
`(chrom, pos, ref, alt)`. Preparation retains a bounded candidate pool per
source consequence instead of sorting the chromosome. Invalid reference
windows are skipped with deterministic backfill, and any underfilled class is a
hard error.

Validate the committed artifacts and reproduce the official fingerprint with:

```bash
uv run --locked python scripts/validate_vep_consequence_artifacts.py
uv run --locked vepbench build --output /tmp/questions.jsonl
cmp benchmark/expected-manifest.json /tmp/questions.manifest.json
```
