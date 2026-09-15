---
title: AlphaGenome and AVI comparison methods
---

# AlphaGenome and AVI comparison methods

The [introduction post](../introducing-vep-bench.html) compares specialist scores
with saved LLM answers on identical eligible variants within original panels.
The comparison uses the post's frozen September 12 publication manifest. It
does not modify the benchmark questions, responses, or official leaderboard.

## Scoring choices

Splicing uses the [OpenSplice native-context approach](https://github.com/lehner-lab/OpenSplice/blob/3e4ad8c037c216b952f1a8945f8f498669bff589/benchmarking_predictors/scripts/inference/alphagenome_genome_mode_snvs_inference.py):
a 16,384-bp window centered on the tested exon, with the mean signed ALT-minus-REF
probability change at its canonical acceptor and donor. Strand determines both
site roles and prediction tracks. Complete alleles are passed to AlphaGenome.
Unaligned alternate tracks are mapped back to the reference sites, preserving
VCF padding; a deleted site contributes zero alternate probability. This is a
proxy for signed exon inclusion, not calibrated delta PSI. The 16-kb choice was
selected by OpenSplice using its dataset, so this setting is not held out.
The [deletion processing script](https://github.com/lehner-lab/OpenSplice/blob/3e4ad8c037c216b952f1a8945f8f498669bff589/benchmarking_predictors/scripts/processing/process_alphagenome_genome_deletions_siteonly_minimal.py)
also uses zero alternate probability for deleted canonical sites.

Expression follows the zero-shot native-context MPRA evaluation in the
[AlphaGenome paper](https://storage.googleapis.com/deepmind-media/papers/alphagenome.pdf),
Methods pp. 55–56. It uses the published recommended DNase scorer:
`log2((sum(ALT) + 1) / (sum(REF) + 1))` over 501 bp centered on the variant,
with 1-Mb input context. Scores are averaged equally across all matching tracks,
including replicate tracks. API `score_variant` supplies reference-coordinate
indel alignment. We use raw signed scores, not quantiles or absolute effects.

Track ontology IDs are the **Enformer-setting** DNase matches in
[Supplementary Table 10](https://media.springernature.com/original/springer-static/esm/art%3A10.1038%2Fs41586-025-10014-0/MediaObjects/41586_2025_10014_MOESM3_ESM.xlsx).
The API does not expose the historical 512-bp `DIFF_SUM` mask. Combining the
paper's recommended 501-bp scorer with this published track list is an explicit
departure from that historical configuration. GRCh38 replaces the paper's hg19
inputs because our complete source alleles were validated on GRCh38. GP1BA
maps to the table's GP1BB and MYCrs6983267 to MYC. TCF7L2 and ZRSh13 have no
published match in this table and remain excluded; missing current tracks stop
inference instead of silently selecting alternatives. No LASSO fitting or
selection using observed benchmark performance is performed. CAGI5 MPRA was
already evaluated in the original AlphaGenome paper.

Fitness uses raw `AVI_SCORE` from Atlas, with exact assembly, chromosome,
position, REF and ALT matching. The [Atlas paper](https://storage.googleapis.com/deepmind-media/DeepMind.com/Blog/alphagenome-atlas-a-predictive-map-of-every-possible-dna-letter-change-in-the-human-genome/alphagenome-atlas.pdf)
reports computing observed indels, but its public release statement limits
datasets to SNVs. This policy queries the 487 selected SGE SNVs and records
actual missing lookups. Molecular inference cannot replace missing AVI scores.
AVI's direction is greater functional impact, matching the task's damage-score
direction, but it is not conditioned on each SGE assay. BRCA1, RAD51C and DDX3X
source studies were used for AVI model selection; both the broader comparison
and a comparison excluding these panels are reported. Unknown overlap remains
unknown.

The current API exposes `ALL_FOLDS`, not an immutable historical weights
revision. Client version, selected model, metadata, source hashes, timestamps,
complete requests and reduced prediction evidence are cached. This enables
offline reproduction of the reported comparison, but does not promise that a
future server will reproduce identical raw predictions.

## Matching and aggregation

Eligibility is frozen before scoring. The plan binds each candidate to the full
question digest, source record digest, and source-validated genomic allele.
Variant types are not decomposed into independent substitutions. Annotation
consequences do not determine scoring eligibility. At least two covered
variants are needed to compute a panel correlation; counts remain visible,
including small AVI panels. Constant vectors receive zero correlation, as in
the original benchmark.

The analysis selects the highest available reasoning effort per model and task
from the frozen publication, with latest completion time and run ID breaking
ties. It validates the complete original LLM answer before restricting to
matched IDs. An invalid completed answer retains zero correlation; an API
failure is incomplete and cannot enter the comparison. Each panel has equal
weight within its task. The exported overall comparison gives each of the
three tasks equal weight and requires the same model, generation parameters,
and retry policy across all tasks. No variants are pooled across panels.

## Reproduction

The implementation is the optional `projects/blog-analysis` workspace package;
the website reads compact exports and never calls a model. Its
`specialist-policy.json` pins the scientific settings and source evidence.
Install with `uv sync --locked --all-packages --extra alphagenome --group test`.

Use a local publication mirror containing the exact artifacts in
`strata-2026-09-12.manifest.json`. The existing `fetch-strata-inputs.py` downloader
verifies public files against that manifest; if a mutable `main` artifact has
changed, restore its frozen copy rather than weakening the hash check. The
frozen runs index can be reproduced byte-for-byte from the `leaderboard` field
of `strata-2026-09-12.json.gz` using canonical JSON plus a trailing newline.

```bash
uv run --no-sync vepbench-blog-specialists plan \
  --publication /tmp/specialist-publication --plan /tmp/specialist-plan.json

# Source a private file exporting ALPHAGENOME_API_KEY into this process.
uv run --no-sync vepbench-blog-specialists predict \
  --plan /tmp/specialist-plan.json --cache /tmp/specialist-cache

uv run --no-sync vepbench-blog-specialists collect \
  --plan /tmp/specialist-plan.json --cache /tmp/specialist-cache \
  --output /tmp/specialist-predictions.json.gz

uv run --no-sync vepbench-blog-specialists compare \
  --publication /tmp/specialist-publication --plan /tmp/specialist-plan.json \
  --predictions /tmp/specialist-predictions.json.gz \
  --output /tmp/specialist-comparison.json
```

`predict` resumes verified cached requests; `--limit` bounds new calls during
validation. Partial runs cannot be collected or compared. A failed call stops
the run and preserves completed work; failures never silently shrink the
matched set. Output paths are exclusive to preserve frozen artifacts. A new
implementation or policy requires a new plan and cache. Measured request time
distinguishes live inference from Atlas lookup. Free access does not imply
zero underlying compute cost; that cost remains unknown.

OpenSplice author-provided predictions may also be used in a future collection
when their complete allele identities, context, score definition and version
can be verified. This implementation uses live AlphaGenome for splicing and
MPRA, and Atlas lookup for AVI.
