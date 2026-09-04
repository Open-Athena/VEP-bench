# Splicing (OpenSplice)

This task asks a model to predict the signed change in exon inclusion for 50
complete assayed variants in one minigene cassette, then evaluates the ranking
and relative numerical spacing of those predictions within the exon.

- **Task family:** `opensplice_snv`
- **Size:** 20 questions from 20 distinct genes, with 50 alleles per question
- **Primary metric:** mean within-exon Spearman rank correlation
- **Secondary metrics:** mean within-exon Pearson correlation and valid-output rate

## Model-visible input and output

Every prompt contains the exact assay and reporter context, the complete
source-derived wild-type three-exon splicing cassette in construct orientation,
and a compact synthetic-contig VCF. A segment table marks the fixed FAS exons
and introns, proximal native introns, and tested alternative exon with 1-based
inclusive coordinates. The displayed cassette is the complete
biologically relevant substrate recoverable from the versioned sources, not the
entire circular plasmid.

Candidate positions refer to the displayed cassette. Opaque IDs `V01` through
`V50` replace source variant identifiers. The prompt omits gene and exon names,
native genomic coordinates, replicate and absolute PSI values, measured
`delta_psi`, significance and region annotations, selection bins, clinical
annotations, and deposited predictor outputs.

The model predicts one finite signed change in percentage spliced in (PSI), in
percentage points, per candidate. Positive values mean increased exon inclusion
and negative values mean decreased inclusion. Its final line must be a strict
JSON object:

```text
FINAL: {"V01": -12.4, "V02": 3.1, ...}
```

All 50 IDs must appear exactly once with no additional keys. The versioned
[`prompt.yaml`](../../configs/tasks/opensplice-snv/prompt.yaml) and
[`task.yaml`](../../configs/tasks/opensplice-snv/task.yaml) define the complete
model-visible contract.

## Canonical source and eligibility

The task uses the CC BY 4.0
[OpenSplice Figshare v5 dataset](https://doi.org/10.6084/m9.figshare.32337414.v5)
for assay measurements and exon sequence geometry. Exact input identities live in
[`source-pins.yaml`](../../tasks/opensplice-snv/config/source-pins.yaml).
For explorer provenance, the assay's first indexed-public date is the Figshare
article's initial publication date, 2026-05-24.

Rows must be measured, have finite experimental `delta_psi` and three finite
replicate PSI values, and have complete identity and sequence fields. Missing
measurements or exon metadata are counted as exclusions. RNA `U` is normalized
to DNA `T`. Substitutions and deposited `∆Nnt` deletions must reproduce the entire
mutant insert from the source REF interval, and the deletion marker must agree
with its length. Malformed notation, sequence inconsistencies, duplicate mutant
constructs, and conflicting gene assignments abort preparation. Exon geometry
comes from the wild-type exon metadata; the master table's per-variant
`exon_length` annotation is not used because it disagrees with the reconstructed
sequence for some deletions. Predictor data are neither downloaded separately
nor tracked.

## Deterministic exon and panel selection

All source exons are reconsidered using the expanded eligible population.
Preparation excludes windows below 50 alleles or with collapsed score anchors,
then chooses the exon with the largest P95 minus P05 per gene, breaking ties by
eligible count and exon ID. The 20 gene winners with the largest robust ranges
become questions; final ties use eligible count, gene, and exon ID. The previous
20 windows are not a constraint. Every metadata exon's statistics, selection
rank, and exclusion reasons are retained in the manifest.

The [shared score-space sampler](../task-construction.md) selects 50 complete
alleles per exon. Whole-cassette reconstruction checks every displayed VCF edit
before local coordinate sorting and opaque ID assignment. Exact parameters live
in [preparation.yaml](../../tasks/opensplice-snv/config/preparation.yaml).
Private source edits retain their original insert and cassette coordinates,
which can differ from the normalized VCF position after indel anchoring.

## Reporter geometry

The complete cassette is constructed exactly as:

```text
FAS exon 5 + FAS intron 5 + wt_seq + FAS intron 6 + FAS exon 7
```

The deposited `wt_seq` contains proximal native upstream intron, the tested
exon, and 25 nt of downstream intron. Preparation derives the actual upstream
length for every record: most have 70 nt, while four long-exon inserts have
35, 55, or 58 nt. No 70-nt assumption is used for displayed intervals.

## Parsing and scoring

The task uses the [shared ranking rules](../evaluation.md#completion-and-failure-semantics).
Each selected exon contributes equally to the mean Spearman and Pearson scores;
valid-output rate is reported alongside them.

## Artifacts, cache, and regeneration

The compact
[`source JSONL`](../../data/sources/opensplice-snv-figshare-v5.jsonl) contains
20 selected panels and their private audit data. Its
[`manifest`](../../data/sources/opensplice-snv-figshare-v5.manifest.json)
records upstream pins, full-population checks, selection statistics and
configuration, and the exact output digest.

Full-data preparation runs on a SkyPilot worker. Its processed cache stores
validated eligible rows and exon metadata. Cache configuration lives in
`preparation.yaml` and its implementation in
[`prepare.py`](../../tasks/opensplice-snv/src/vepbench_opensplice_snv/prepare.py).

Rebuild and copy back the compact artifacts with:

```bash
bash scripts/run_prepare_opensplice_snv_sky.sh
```

Then validate the committed source and question fingerprint offline:

```bash
uv sync --locked --package vepbench-task-opensplice-snv --group test
uv run --no-sync vepbench-opensplice-snv validate
uv run --no-sync vepbench questions build \
  --task configs/tasks/opensplice-snv/task.yaml \
  --output /tmp/opensplice-snv-questions.jsonl
cmp benchmark/opensplice-snv-expected-manifest.json \
  /tmp/opensplice-snv-questions.manifest.json
```

## Interpretation and limitations

Exons were deliberately selected for large measured dynamic range, and every
panel is sampled across score-space bins. The task therefore emphasizes
discrimination across effects rather than reproducing the natural distribution of exon architectures
or effect sizes. Spearman measures within-panel ordering, not calibration.

The reference values measure exon inclusion in a specific minigene reporter and
human embryonic-kidney 293T cellular context. They are not direct measurements
of native-tissue splicing and must not be interpreted as clinical
pathogenicity. Questions and answers are public development data and may be
present in model training corpora.
