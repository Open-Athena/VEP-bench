# Expression (satMutMPRA)

This task asks a model to predict the signed reporter-activity effect of 50
variants from one saturation-mutagenesis MPRA, then evaluates the ranking and
relative numerical spacing of those predictions within the regulatory element.

- **Task family:** `satmut_mpra`
- **Size:** 16 questions, one per regulatory element and 50 candidates per question
- **Primary metric:** mean within-element Spearman rank correlation
- **Secondary metrics:** mean within-element Pearson correlation and valid-output rate

## Model-visible input and output

Every prompt contains the assay cell line, measurement time and perturbation,
physical reporter configuration, the complete uppercase mutagenized insert in
reporter-construct orientation, any fixed downstream sequence needed to define
the insert-to-reporter interface, and a compact VCF table with `CHROM`, `POS`,
opaque candidate ID, `REF`, and `ALT`. Element and gene names, source-study
labels, accessions, genomic coordinates, and vector names are retained as
provenance or site display metadata but omitted from the model prompt.

For promoter constructs, the mutagenized insert directly drives luciferase and
the prompt states that no separate minimal promoter is present. For standard
enhancer constructs, the prompt includes the complete fixed 54 bp minimal
promoter sequence between the insert and luciferase. For ZRS, it includes the
complete fixed 143 bp sequence between the mutagenized insert and luciferase.
The whole plasmid backbone is excluded because it is shared assay machinery,
not local regulatory context needed to interpret the tested sequence.

Every VCF record uses the synthetic contig `element`, with 1-based
positions relative to the first base of the displayed sequence. Genomic
chromosomes and positions are not model-visible. The prompt does not expose the
measured effect, p-value, barcode count, source `FILTER`, score bin, MaveDB
accession, or selection metadata.

The model predicts one finite signed log2-scale activity effect per candidate.
Positive values mean increased reporter activity and negative values mean
decreased activity. Its final line must be a strict JSON object:

```text
FINAL: {"V01": -0.42, "V02": 0.08, ...}
```

All 50 IDs must appear exactly once with no additional keys. The versioned
prompt is
[`configs/tasks/satmut-mpra/prompt.yaml`](../../configs/tasks/satmut-mpra/prompt.yaml),
and the prepared source, prompt, question type, and task completion ceiling are
selected by
[`configs/tasks/satmut-mpra/task.yaml`](../../configs/tasks/satmut-mpra/task.yaml).

## Canonical sources and crosswalk

The source is the canonical 16-file RegSeq validation collection distributed
with [CADD v1.7](https://kircherlab.bihealth.org/download/CADD-development/v1.7/validation/regseq/).
`FILTER=SIGN` and `FILTER=MIN` rows are eligible for sampling. `QUAL` rows are
excluded but retained for provenance and full-source crosswalk validation.
The VCF parser preserves complete REF/ALT sequences without imposing an allele
length or variant-type restriction; downstream reference and crosswalk checks
still apply.

Every CADD row is independently cross-checked against a pinned
[MaveDB](https://www.mavedb.org/) score set using source-study coordinates and
alleles, effect after CADD's decimal rounding, p-value, and barcode count. The
element-to-score-set crosswalk and reviewed assay conditions live in
[`preparation.yaml`](../../tasks/satmut-mpra/config/preparation.yaml).

`GP1BA` is the historical CADD filename; the matched MaveDB target and site
display metadata correctly call the assayed sequence the GP1BB promoter. The
verified input metadata and validation counts are retained in the
[`source manifest`](../../data/sources/satmut-mpra-cadd-v1.7.manifest.json).

## Reference validation and one excluded discrepancy

The model-visible sequence is the pinned MaveDB target sequence, representing
the physical reporter-construct orientation. The Methods and Supplementary
Table 18 of the
[source study](https://doi.org/10.1038/s41467-019-11526-w) document the
restriction-site overhangs used for directional cloning. The study treats the
reverse-orientation SORT1 library as a separate experiment, which this task
excludes.
These sequences and eligible alleles are validated against the GRCh38 primary
assembly from
[`marin-dna/human-genome`](https://huggingface.co/datasets/marin-dna/human-genome).
GRCh38 is used only for validation and genomic provenance, not as the
display-orientation policy.
[`tasks/satmut-mpra/config/source-pins.yaml`](../../tasks/satmut-mpra/config/source-pins.yaml)
records the exact input identities; preparation rejects unpinned changes.
The earliest indexed-public date verified from the pinned provenance is the
source study's 2019-08-08 online publication; this shared date is attached to
all 16 explorer rows.

The ZRS reporter-construct sequence has `A` at its terminal position
7:156791604, whereas the pinned GRCh38 primary assembly has `T`. The prompt
retains the construct `A`. The CADD file consequently contains
one deletion, `7:156791603:CA:C`, whose REF also disagrees with GRCh38 (`CT`).
That row has `FILTER=QUAL`, is ineligible by construction, and is never sampled.
Preparation allows only this exact, source-pinned exception and records both
the sequence and allele discrepancy in the manifest. Any eligible REF mismatch,
or any other mismatch, aborts preparation.

## Deterministic panel construction

The [shared score-space protocol](../task-construction.md) samples the signed
activity coefficients of the SIGN+MIN population. Complete alleles are normalized
against the reporter sequence; all rows sharing a normalized allele identity
are excluded rather than choosing among repeated measurements. There is no SNV
preference. A deletion's genomic VCF anchor may precede the displayed insert:
shared padding outside the insert is removed and the edit is re-anchored inside
the display, preserving the assayed mutant sequence.

The 50 selected variants are sorted by displayed position, REF, and ALT before
opaque IDs are assigned. Score-bin boundaries, capacities, allocations, source
FILTER values, duplicate exclusions, and original genomic allele keys remain
private provenance. Exact parameters live in `preparation.yaml` and selection
is implemented in [task.py](../../tasks/satmut-mpra/src/vepbench_satmut_mpra/task.py).

## Parsing and scoring

The task uses the [shared ranking rules](../evaluation.md#completion-and-failure-semantics).
Each element contributes equally to the mean Spearman and Pearson scores;
valid-output rate is reported alongside them.

## Artifacts, cache, and regeneration

The committed
[`source JSONL`](../../data/sources/satmut-mpra-cadd-v1.7.jsonl) contains the 16
selected panels plus private audit fields. The manifest records population
checks, provenance, and output digests. A processed cache retains the full
eligible pool so later sampling changes can reuse validated inputs. Its
configuration lives in `preparation.yaml` and its implementation in
[`prepare.py`](../../tasks/satmut-mpra/src/vepbench_satmut_mpra/prepare.py).

To prepare on a SkyPilot worker, upload the cache, copy back the compact
artifacts, validate them, and build questions:

```bash
bash scripts/run_prepare_satmut_mpra_sky.sh
```

Preparation uploads its cache by default. Use `--skip-cache-upload` when
rebuilding without publication. Full reference validation runs on cloud compute;
the compact source and question fingerprints can be checked offline:

```bash
uv sync --locked --package vepbench-task-satmut-mpra
uv run --no-sync vepbench-satmut-mpra validate
uv run --no-sync vepbench questions build \
  --task configs/tasks/satmut-mpra/task.yaml \
  --output /tmp/satmut-mpra-questions.jsonl
cmp benchmark/satmut-mpra-expected-manifest.json \
  /tmp/satmut-mpra-questions.manifest.json
```

## Interpretation and limitations

Spearman measures within-panel ordering, not absolute calibration, and Pearson
is sensitive to numerical scale and outliers. Sampling across score-space bins
provides broad effect coverage but do not reproduce the natural frequency of
effects in the complete assay. A 50-candidate panel is a sampled view of each
element, not an exhaustive score of all variants.

Reporter assays measure activity in specific constructs, cell lines, and
experimental conditions. Their effects need not transfer directly to native
chromatin, another tissue, or clinical phenotype. The questions and answers
are public development data and may be present in model training corpora.
