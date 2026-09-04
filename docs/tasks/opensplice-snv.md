# Splicing (OpenSplice)

This task asks a model to predict the signed change in exon inclusion for 50
single-nucleotide variants in one minigene cassette, then evaluates the ranking
and relative numerical spacing of those predictions within the exon.

- **Task family:** `opensplice_snv`
- **Size:** 20 questions from 20 distinct genes, with 50 SNVs per question
- **Primary metric:** mean within-exon Spearman rank correlation
- **Secondary metrics:** mean within-exon Pearson correlation and valid-output rate

## Model-visible input and output

Every prompt contains the exact assay and reporter context, the complete
source-derived wild-type three-exon splicing cassette in construct orientation,
and a compact synthetic-contig VCF. A segment table marks the fixed FAS exons
and introns, proximal native introns, and tested alternative exon with 1-based
inclusive coordinates. The displayed 410--504 nt cassette is the complete
biologically relevant substrate recoverable from the versioned sources, not the
entire circular plasmid.

Candidate positions refer to the displayed cassette. Opaque IDs `V01` through
`V50` replace source variant identifiers. The prompt omits gene and exon names,
native genomic coordinates, replicate and absolute PSI values, measured
`delta_psi`, significance and region annotations, selection bins, clinical
annotations, and all deposited predictor outputs. AlphaGenome's 16,384-base
interface padding and native genome context are also not rendered.

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
and the pinned OpenSplice library-design and AlphaGenome inference scripts to
recover reporter and predictor geometry. Exact input identities live in
[`source-pins.yaml`](../../tasks/opensplice-snv/config/source-pins.yaml).
For explorer provenance, the assay's first indexed-public date is the Figshare
article's initial publication date, 2026-05-24.

The primary master table has 590,104 measured rows. A row enters the 300,327-SNV
eligible population only when it is a measured single-base substitution with a
finite experimental `delta_psi` and three finite replicate PSI values, complete
identity and sequence fields, and an exact construct-oriented mutation mapping.
RNA `U` is normalized to DNA `T`. Preparation checks local position, reference
base, mutant sequence, exon length, unique construct keys and mutant sequences,
and one consistent gene assignment per exon. Malformed, non-finite,
sequence-inconsistent, duplicate, or conflicting data abort preparation.

AlphaGenome availability and values do not participate in eligibility,
selection, or reference scoring.

## Deterministic exon and panel selection

For each of the 585 exons with at least 50 eligible SNVs, preparation computes
`Q95(delta_psi) - Q05(delta_psi)` with an explicit Hyndman-Fan type 7
implementation. It keeps the widest-range exon per gene, then ranks the 457
per-gene winners by descending robust range, descending eligible count,
ascending gene, and ascending Ensembl exon ID. The first 20 become questions.
The source manifest records all 608 metadata exons, their statistics, winner
rank, selected rank, and exclusion reasons.

Within each selected exon, eligible variants are sorted by measured effect with
stable variant tie-breakers and divided into ten equal-population rank bins.
The first remainder bins receive one extra row. Five variants per bin are
chosen by seeded SHA-256 ordering. The resulting 50
variants are sorted by construct position, REF, and ALT before opaque IDs are
assigned. Exact parameters live in
[`preparation.yaml`](../../tasks/opensplice-snv/config/preparation.yaml), and
[`task.py`](../../tasks/opensplice-snv/src/vepbench_opensplice_snv/task.py)
implements deterministic selection independent of upstream row order.

## Reporter and private AlphaGenome provenance

The complete cassette is constructed exactly as:

```text
FAS exon 5 + FAS intron 5 + wt_seq + FAS intron 6 + FAS exon 7
```

The deposited `wt_seq` contains proximal native upstream intron, the tested
exon, and 25 nt of downstream intron. Preparation derives the actual upstream
length for every record: most have 70 nt, while four long-exon inserts have
35, 55, or 58 nt. No 70-nt assumption is used for displayed intervals.

Each selected candidate retains private AlphaGenome genome-mode and
minigene-mode input geometry, predictions, explicit nulls, and quality flags.
Minigene provenance records the exact variable sequence, 16,384-base input
length, reconstructable cassette and left/right padding sizes, model loading
identifier, ontology term, and intended splice sites. The upstream inference
script hard-codes the acceptor position appropriate to a 70-nt upstream flank;
short-flank records are flagged and excluded from baseline eligibility unless
independently verified. Genome-mode provenance records the deposited VCF,
native GRCh38 interval, exon midpoint, strand-aware sites, and model settings.
These fields remain private source metadata.

Three selected exons use historical labels in the deposited variant-metadata
table. Preparation therefore joins minigene inputs by Ensembl exon ID and exact
mutant sequence, then verifies the deposited AlphaGenome identifier; it does
not assume mutable gene or exon-label text is a stable cross-file key.

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
panel is quantile-balanced. The task therefore emphasizes discrimination across
effects rather than reproducing the natural distribution of exon architectures
or effect sizes. Spearman measures within-panel ordering, not calibration.

The reference values measure exon inclusion in a specific minigene reporter and
human embryonic-kidney 293T cellular context. They are not direct measurements
of native-tissue splicing and must not be interpreted as clinical
pathogenicity. Genome-mode AlphaGenome sees native sequence absent from the
assay construct and is a secondary, information-mismatched baseline. Questions
and answers are public development data and may be present in model training
corpora.
