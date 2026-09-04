# Fitness (SGE)

The SGE task asks a model to rank 50 assayed variants by
continuous functional damage within one gene and one exon window. It preserves
endogenous-locus measurements from MaveDB rather than reusing a derived
benchmark artifact.

- **Task family:** `sge`
- **Question unit:** one deterministically selected exon window per included gene
- **Size:** 16 questions, one per reviewed gene
- **Candidates:** 50 complete alleles per question
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
genome editing” score sets and records inclusion or exclusion reasons. It
selects at most one canonical score set per gene from the reviewed
[`preparation.yaml`](../../tasks/sge/config/preparation.yaml).
The reviewed selection prefers current aggregates, direct endogenous
loss-of-function assays, combined measurements, baseline conditions, and
primary continuous scores. CARD11 uses the baseline TMD8 growth assay; its
multibase codon alleles compete alongside other validated alleles.

For every selected score set, the primary MaveDB `score` is used directly.
Preparation records source metadata and fingerprints. A reviewed direction of
`1` or `-1` is then applied so larger values always mean more damage; there is
no other normalization, standardization, calibration, or rescaling.

Exact source payload pins are in
[`source-pins.yaml`](../../tasks/sge/config/source-pins.yaml).
For explorer provenance, each question uses the earlier of its linked
PubMed-indexed online publication and its MaveDB publication date. When MaveDB
does not link a paper, its own published date is the verified indexed record;
the explorer does not guess an earlier date.

## Coordinate and reference validation

Sequence extraction and REF validation use the GRCh38 primary assembly from
`marin-dna/human-genome`. Declared target transcripts define exon geometry and
display orientation. DDX3X and TINF2 use pinned MANE Select
fallbacks because their deposits do not declare a usable transcript accession.
Exact reference and cdot versions are recorded in the preparation configuration.

Transcript `c.` HGVS is projected to GRCh38 with PyHGVS using the pinned cdot
transcript JSON adapter, including intronic coordinates. Genomic `g.` HGVS is
parsed directly. TINF2 target `n.` coordinates are interpreted as MANE CDS
coordinates only after its pinned target sequence is verified byte-for-byte
against the reconstructed spliced CDS. Unmapped or ambiguous records are
rejected. Every REF must match GRCh38; REF and ALT are never swapped.

All finite, source-QC-passing alleles with unambiguous mapping and matching
complete REF sequence are eligible. Consequence annotations, precomputed
predictor scores, and nearest-exon classifications are not used. Mapping handles
substitutions, deletions, insertions, replacements, and duplications; normalized
allele identities are deduplicated by excluding all rows for an ambiguous
identity rather than choosing one measurement.

## Exon and panel selection

Every transcript exon defines a genomic window extended by exactly 100 bp on
both sides. The complete normalized REF span must fit inside that window.
Windows with fewer than 50 eligible alleles or collapsed score anchors are
excluded. The chosen window maximizes damage-score P95 minus P05, then eligible
count, with the ascending genomic exon key resolving ties. Variant consequences
and variant types have no preference or quota.

Within that window, the [shared score-space protocol](../task-construction.md)
selects 50 alleles. The complete eligible population, all exon comparisons,
score-bin boundaries and allocations, and selected source rows remain private
provenance. Reverse-strand alleles are projected using the entire REF span,
reverse-complemented, and normalized against the displayed sequence.

## Parsing and scoring

SGE uses the [shared ranking rules](../evaluation.md#completion-and-failure-semantics).
Every included gene has equal weight. This measures within-panel ordering and
numerical agreement, not a common cross-assay scale.

## Artifacts, cache, and regeneration

The compact [source JSONL](../../data/sources/sge-mavedb-2026-09-03.jsonl)
contains only the selected panels plus private audit fields. Its
[manifest](../../data/sources/sge-mavedb-2026-09-03.manifest.json) records the
catalog audit, source pins and metadata, mapping and eligibility counts,
excluded genes, exon comparisons, cache identity, and output digest. The
[expected question manifest](../../benchmark/sge-expected-manifest.json)
fingerprints deterministic prompt generation.

Reference indexing and full-source preparation run on a SkyPilot worker:

```bash
bash scripts/run_prepare_sge_sky.sh
```

The command uploads a reusable cache of the complete post-eligibility
population and provenance. Cache configuration lives in `preparation.yaml`
and its implementation in
[`prepare.py`](../../tasks/sge/src/vepbench_sge/prepare.py).
Offline validation and question regeneration use:

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
protein-domain context. Score-space sampling improves effect-range coverage but
does not reproduce the natural variant distribution.
