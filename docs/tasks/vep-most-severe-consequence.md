# Ensembl VEP most-severe consequence

This task asks a model to predict the most severe consequence assigned by
Ensembl VEP for a human GRCh38 single-nucleotide variant, using only the local
reference sequence and variant alleles.

- **Task family:** `vep_most_severe_consequence`
- **Prompt version:** 1.2
- **Development set:** 51 public chromosome 17 SNVs
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

The prompt states that FASTA sequence lines contain 80 bases except the final
line. This makes the deterministic serialization explicit without giving the
line-relative location of the variant or restating standard VCF semantics.

The versioned prompt is defined in
[`templates/vep_most_severe_consequence.json`](../../templates/vep_most_severe_consequence.json).
Every response must end with a valid `FINAL: <choice-id>` line.

<details>
<summary>Complete prompt example</summary>

````text
Predict the Ensembl VEP most severe consequence for the SNV using only the local sequence context below.

**Reference genome:** human GRCh38
**VEP version:** release 109.1
**VEP flags:** `--most_severe --distance 1000`

**FASTA formatting:** Sequence lines contain 80 bases except the final line.

```fasta
>window
TTTCTTCCCATGGTTCTTCCTATAACTGGTCCTTCCCTGTCTTCTAAATCTCAGACCACATCACTATGGAAGGAAGAGGC
CTTCCCTGAGCAGCTCATTCCTCCAGTGATGACTTTCCATTGGATCACCCTATTTGTTTCCTGCAGAGCATGTTCCACTA
CCCATAGTGATGCATGTATTGTGTATTGCCTGTTTCCTCCAACTCCTTGTGAGCTGCACAATGGCAGGGACCTGCTCTTT
CTTGTTCATCTCTGTGCACCCCATTTCTAGAACAGTGCCCAGTACATAGGACAGGCTCAATAGAGTTGAAGAACACATGA
ATGGATCTCACTATTAGCTCTTCAAAAACAAAAGAGGCTCTCTTGAAGGTAATAAGTTCCCCATCAGTGGTAGGATGAAC
ATGTGGTAGGCATATTGCAGAGGGGTCTGTGGCTCGATGGGATAGGAAGGAGGGATGGGCCAGATGACTCCGGGTCCCTG
CCAGTGTTGGGGGCCACTTGTGTGTGGATGACTGCACACTCATGTGGCCCCCAGCACTGGCAGGGACCTGACCACAGTCA
GAAGGCTTCATCCCAGACAGCCTTCTCTCGGGCACCAAAGATCAACATCCACAACCTCAGGCTGCACCATGGCCAGTCCC
TGCTCCTGAAATGCAAGGGATGTTTTCTTGGTGCTGGTGAAATCTTGGGGATACAATTGAAGTTTTTTTAAAGCAAAGAC
TATAGATTCCAGGTCATACAGTTCAGACACAGGTTCAAACAAACATTTCATACTAATTCCTGGGTATTGGCATCAGAGAA
ACCCTAAGGGTTGAAATCCTGGCATGTTGACAACACATTCCGTCCTCTGTACCTCCCTTGTACCACCAGGGTCTCAGCAT
CTCATAAGAGCCTGTAAAATCCAGAGCCATTCCTTGGGTAGAGATTATGGGTTGGCTGAGGGAGCCACGAAGATTTTAAA
ATGTAAAACATGAACCCATAAATGCAGAAGGTCTCCCGTGT
```

```vcf
##fileformat=VCFv4.3
##contig=<ID=window,length=1001>
#CHROM	POS	ID	REF	ALT	QUAL	FILTER	INFO
window	501	.	T	C	.	PASS	.
```

What is the Ensembl VEP most severe consequence for this SNV?

Choices:
C01. 3_prime_UTR_variant
C02. 5_prime_UTR_variant
C03. intergenic_variant / intron_variant / upstream_gene_variant / downstream_gene_variant
C04. mature_miRNA_variant
C05. missense_variant
C06. non_coding_transcript_exon_variant
C07. splice_acceptor_variant
C08. splice_donor_5th_base_variant
C09. splice_donor_region_variant
C10. splice_donor_variant
C11. splice_polypyrimidine_tract_variant
C12. splice_region_variant
C13. start_lost
C14. stop_gained
C15. stop_lost
C16. stop_retained_variant
C17. synonymous_variant

You may explain your reasoning.

Your final line must contain only the word `FINAL`, a colon, a space, and the choice ID.
Example: `FINAL: C07`
Do not include the consequence name, a period, or any other text on that line.
````

</details>

## Labels and sampling

The task has 17 answer choices and exactly three questions per final class.
Intronic, intergenic, upstream-gene, and downstream-gene source consequences
are collapsed into one answer choice because their distinctions are not
reliably recoverable from the model-visible input. Its three questions contain
one intergenic example, one intronic example, and one gene-proximal example
selected deterministically from the combined upstream/downstream candidate
pool. The remaining selected source consequences are separate classes. All
questions use the same lexicographically ordered choice vocabulary and stable
choice IDs.

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

## Historical published baseline

The prompt 1.1 Luna runs remain available as historical artifacts and contain
all 190 responses without API errors. They use the earlier question set and
should not be compared directly with future prompt 1.2 runs.

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
source consequence instead of sorting the chromosome. Each final class has a
quota of three. The collapsed class uses separate intergenic and intronic
groups plus a combined upstream/downstream group. Invalid reference windows are
skipped with deterministic backfill, and any underfilled class is a hard error.

Validate the committed artifacts and reproduce the official fingerprint with:

```bash
uv run --locked python scripts/validate_vep_consequence_artifacts.py
uv run --locked vepbench build --output /tmp/questions.jsonl
cmp benchmark/expected-manifest.json /tmp/questions.manifest.json
```
