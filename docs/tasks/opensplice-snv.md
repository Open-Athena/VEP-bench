# OpenSplice SNV

This task asks a model to predict the signed change in exon inclusion for 50
single-nucleotide variants in one minigene cassette, then evaluates the ranking
and relative numerical spacing of those predictions within the exon.

- **Task family:** `opensplice_snv`
- **Question schema:** 2.0
- **Prompt version:** 1.0
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
and pins every consumed file by article ID, file ID, byte size, upstream MD5,
and locally computed SHA-256. It also pins OpenSplice repository commit
`3e4ad8c037c216b952f1a8945f8f498669bff589`, whose library-design and
AlphaGenome inference scripts recover the reporter and predictor geometry.
Exact pins live in
[`source-pins.yaml`](../../tasks/opensplice-snv/config/source-pins.yaml).

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
chosen by ascending SHA-256 using algorithm `sha256_rank_quantile_v1`, seed
`2026090300`, exon ID, bin number, and the stable variant key. The resulting 50
variants are sorted by construct position, REF, and ALT before opaque IDs are
assigned. The source manifest records each selected candidate's stable key,
bin, and digest, and offline validation recomputes the digest rather than
trusting those recorded values. This makes output byte-identical regardless of
upstream row order.

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

The shared ranking evaluator reads only the last well-formed `FINAL: {JSON
object}` line. It requires every expected ID exactly once with a finite number.
For valid output, Spearman uses average ranks for ties and Pearson uses raw
predicted and measured signed effects. Constant vectors receive correlation
zero. Invalid completed output receives zero for both correlations and lowers
valid-output rate; API failures remain null and make a run incomplete. The
primary task score is the arithmetic mean of the 20 within-exon Spearman values.

## Artifacts, cache, and regeneration

The compact
[`source JSONL`](../../data/sources/opensplice-snv-figshare-v5.jsonl) contains
20 selected panels and their private audit data. Its
[`manifest`](../../data/sources/opensplice-snv-figshare-v5.manifest.json)
records upstream pins, full-population checks, selection statistics and
configuration, and the exact output digest.

Full-data preparation uses bounded-memory streaming on a SkyPilot worker. The
content-addressed processed cache stores only validated eligible rows and exon
metadata under:

```text
hf://buckets/open-athena/VEP-bench/data_prep/opensplice-snv/v1/<cache-key>/
```

Data objects are uploaded before the digest-bearing `manifest.json` completion
marker. Existing complete or incomplete prefixes are never overwritten. The
cache is separate from official `versions/` publication artifacts.

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
