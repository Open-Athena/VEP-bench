# Saturation genome editing

The SGE task asks a model to rank 50 assayed single-nucleotide variants by
continuous functional damage within one gene and one exon window. It preserves
endogenous-locus measurements from MaveDB rather than reusing a derived
benchmark artifact.

- **Task family:** `sge`
- **Question schema:** 2.0 (the shared `ranking` question)
- **Prompt version:** 1.0
- **Question unit:** one deterministically selected exon window per included gene
- **Size:** 15 questions, one for every provisional gene
- **Candidates:** 50 SNVs per question
- **Primary metric:** arithmetic mean of within-gene Spearman correlations
- **Secondary metrics:** mean within-gene Pearson correlation and valid-output rate

Scores are never pooled across genes. Each gene contributes one equally
weighted correlation because experimental scales differ between assays.

## Model-visible input and output

Each prompt contains the gene symbol, a manually reviewed description of the
editing design, cellular system, selection or measurement, timing or contrast,
and the causal interpretation of loss of function. It also contains one
uppercase DNA sequence with the selected exon and exactly 100 unmarked genomic
bases on either side, displayed in transcript 5-prime to 3-prime orientation.
A compact VCF uses synthetic contig `element`, opaque IDs `V01` through `V50`,
and 1-based positions and alleles relative to that displayed sequence.

The prompt omits genomic coordinates, transcript and source identifiers, exon
numbers and boundaries, consequences, amino-acid annotations, experimental
scores, QC and uncertainty fields, author classes, selection bins, publication
metadata, and disease labels. For negative-strand transcripts the entire window
and both alleles are reverse-complemented before local positions are computed.

The model predicts one finite number for every candidate, with larger values
meaning greater functional damage. Absolute calibration between genes is not
required. The final line is a strict JSON object:

```text
FINAL: {"V01": -0.42, "V02": 0.08, ...}
```

The versioned prompt and task descriptor are
[`configs/tasks/sge/prompt.yaml`](../../configs/tasks/sge/prompt.yaml) and
[`configs/tasks/sge/task.yaml`](../../configs/tasks/sge/task.yaml).

## Catalog and source selection

Preparation reruns the pinned public MaveDB search for published “saturation
genome editing” score sets and records all 98 results from the 2026-09-03
snapshot with an inclusion or exclusion reason. It selects at most one
canonical score set for each provisional gene: BAP1, BARD1, BRCA1, BRCA2,
CTCF, DDX3X, PALB2, RAD51C, RAD51D, SBDS, SFPQ, TINF2, TP53, VHL, and XRCC2.
The reviewed selection prefers current aggregates, direct endogenous
loss-of-function assays, combined measurements, baseline conditions, and
primary continuous scores. BRCA2 is subject to the same eligibility checks as
every other gene. CARD11 is excluded because its library is predominantly
multi-base codon substitutions and does not provide the intended SNV and
splice-region panel.

For every selected score set, the primary MaveDB `score` is used directly.
Preparation validates and records the score-set and experiment URNs, title,
dates, license, target, controlled vocabulary, publications, column inventory,
payload URL, byte size, and SHA-256. A reviewed direction of `1` or `-1` is
then applied so larger values always mean more damage; there is no other
normalization, standardization, calibration, or rescaling.

The strict reviewed gene configuration is
[`preparation.yaml`](../../tasks/sge/config/preparation.yaml), and exact source
payload pins are in
[`source-pins.yaml`](../../tasks/sge/config/source-pins.yaml).

## Coordinate and consequence policy

Sequence extraction and REF validation use the GRCh38 primary assembly from
`marin-dna/human-genome` at revision
`11b9433582981bb929af333bc6422f10a8fd71b4`. Declared target transcripts define
exon geometry and display orientation. DDX3X and TINF2 use pinned MANE Select
fallbacks because their deposits do not declare a usable transcript accession.
Their exact cdot records use data version 0.2.34.

Transcript `c.` HGVS is projected to GRCh38 with PyHGVS using the pinned cdot
transcript JSON adapter, including intronic coordinates. Genomic `g.` HGVS is
parsed directly. TINF2 target `n.` coordinates are interpreted as MANE CDS
coordinates only after its pinned target sequence is verified byte-for-byte
against the reconstructed spliced CDS. Unmapped or ambiguous records are
rejected. Every REF must match GRCh38; REF and ALT are never swapped.

Global most-severe consequences come from
[`songlab/hg38-variant-consequences`](https://huggingface.co/datasets/songlab/hg38-variant-consequences)
at revision `eb3022cc6797b9369cca16af72ff3c4197df343a`, produced with Ensembl
VEP 109.1 and `--most_severe --distance 1000`. The annotation may refer to a
different transcript than the display transcript by design. Exon distance is
computed from Ensembl release 107.

The pinned upstream policy is applied in this order:

1. Reject `transcript_ablation`, canonical splice acceptor or donor, stop-gain,
   frameshift, stop-loss, start-loss, transcript amplification, feature
   elongation, and feature truncation consequences.
2. Reclassify an `intron_variant` at most 30 bp from the nearest annotated exon
   as `exon_proximal`.
3. Group donor-fifth-base, splice-region, donor-region, polypyrimidine-tract,
   and exon-proximal records privately as `splicing`.
4. Retain only `missense_variant` and `splicing` SNVs with finite primary scores
   that pass source QC and map uniquely.

## Exon and panel selection

Every transcript exon defines a genomic window extended by exactly 100 bp on
both sides. Windows with fewer than 50 eligible variants are discarded. The
selector first prefers windows that can supply 25 missense and 25 splicing
variants. If none can, it maximizes the achievable smaller class and fills the
remaining positions from the other class. Remaining ties use the largest
damage-score `P95 - P05` spread with R type-7 percentiles, followed by the
stable transcript and genomic exon key.

Within each class allocation, variants are divided into five equal-population
score-rank bins. SHA-256 ordering with seed `2026090300` selects evenly from
each bin. Selected variants are sorted by displayed local position, REF, and
ALT before opaque IDs are assigned. The complete eligible population, class
counts, every exon-window feature, allocations, bins, and selected source rows
remain private provenance.

## Parsing and scoring

SGE uses the shared ranking parser and scorer without task-specific heuristics.
Only the last well-formed `FINAL: {JSON object}` line is parsed; all expected
IDs must occur exactly once with finite numeric values and no extra keys.
Invalid completed output receives zero for Spearman and Pearson and lowers
valid-output rate. Constant vectors have correlation zero. Provider or API
failure remains null and makes the run incomplete.

Spearman and Pearson are computed within each question before arithmetic
macro-averaging, so every included gene has equal weight. This measures
within-panel ordering and numerical agreement, not a common cross-assay scale.

## Artifacts, cache, and regeneration

The compact [source JSONL](../../data/sources/sge-mavedb-2026-09-03.jsonl)
contains only the selected panels plus private audit fields. Its
[manifest](../../data/sources/sge-mavedb-2026-09-03.manifest.json) records the
catalog audit, source pins and metadata, mapping and eligibility counts,
excluded genes, exon comparisons, cache identity, and output digest. The
[expected question manifest](../../benchmark/sge-expected-manifest.json)
fingerprints deterministic prompt generation.

All 15 provisional genes pass the complete source, coordinate, consequence,
QC, and exon-capacity checks. The reusable cache contains 55,924 eligible SNVs.

The full preparation exceeds the shared development VM limits because the
pinned per-chromosome consequence objects total several gigabytes. Run it on a
SkyPilot worker:

```bash
bash scripts/run_prepare_sge_sky.sh
```

The command uploads a reusable content-addressed cache under:

```text
hf://buckets/open-athena/VEP-bench/data_prep/sge/v1/<cache-key>/
```

The cache contains the complete post-eligibility population and provenance,
not redistributable upstream payloads. Data objects are uploaded first and the
digest-bearing completion manifest last; existing prefixes are never
overwritten. Subsequent preparations verify and reuse a complete matching
cache. Offline validation and question regeneration use:

```bash
uv sync --locked --package vepbench-task-sge
uv run --no-sync vepbench-sge validate
uv run --no-sync vepbench questions build \
  --task configs/tasks/sge/task.yaml \
  --output /tmp/sge-questions.jsonl
cmp benchmark/sge-expected-manifest.json \
  /tmp/sge-questions.manifest.json
```

## Interpretation and limitations

An SGE score is specific to its endogenous locus, cell system, engineered
background, selection, timing, and treatment. It is experimental function, not
clinical pathogenicity or universal organismal fitness. One exon plus local
flanks omits the rest of the gene, distant splice and regulatory context, and
protein-domain context. Global VEP consequences may come from another
transcript. Class balancing and quantile sampling improve evaluation coverage
but do not reproduce the natural variant or effect distribution. Questions and
answers are a public development set and may occur in model training data.
