---
title: AlphaGenome and AVI comparison methods
---

# AlphaGenome and AVI comparison methods

The [introduction post](../introducing-vep-bench.html) compares specialist scores
with saved LLM answers on identical eligible variants within original panels.

## Publication snapshot

All performance analyses in the post use the public LLM publication frozen on
September 17, 2026 in `specialist-2026-09-17.manifest.json`. The name reflects its
initial use for this comparison; it is also the manifest for the stratum,
SGE cutoff, and external-benchmark comparisons.

Six maximum-effort runs—GPT-5.6 Luna, Terra, and Sol on OpenSplice and satMutMPRA—
use the recovered publication. Their 108 answers comprise **85 recovered
responses and 23 newly generated replacements for missing artifacts**, already
present in that public release. They use the same questions and model settings.
No new LLM requests were made for this analysis. Recovery metadata distinguishes
original responses, retries, and replacements; unrecoverable attempts are not
invented. SGE runs were unaffected. The complete question-set digest and source
alleles are unchanged.

The September 17 update adds complete Muse Spark 1.3 maximum-effort and Kimi K3
low-effort evaluations. It also corrects three provider connection errors in
the older Muse medium-effort runs: CTCF and DDX3X in SGE and HBB in satMutMPRA.
Those requests were retried with unchanged inference settings; their failures
remain in the recovered answers' provenance and receive no score or benchmark
cost. Completed invalid answers and truncations still count.

Specialist predictions reuse the original saved AlphaGenome and AVI inference.
The question sets, alleles, eligibility policy, and biological requests are
unchanged. The saved prediction artifact retains its original inference session
and links the old plan and predictions by digest; only the compared LLM
publication changes. `specialist-plan-2026-09-15.json.gz` retains that original
plan. This refresh makes no new specialist calls.

The blog rescores existing answers on each comparison's eligible variants. It
does not modify the official leaderboard. The previous snapshot remains in Git
history; external benchmark measurements retain their September 11 retrievals.

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
maps to the table's GP1BB and MYCrs6983267 to MYC. The remaining two elements
use the explicitly chosen proxies below. Missing current tracks stop inference
instead of silently selecting alternatives. No LASSO fitting or selection using
observed benchmark performance is performed. CAGI5 MPRA was already evaluated
in the original AlphaGenome paper.

### Expression cell-type proxies

TCF7L2 and ZRSh13 have no mapping in Supplementary Table 10. We extend the
published mapping using assay context, with the ontology sets frozen before
scoring either element. Both are human DNA elements tested in mouse cell lines;
the following human output tracks are approximations of those assay contexts.
They use the same native-context DNase scorer and equal track weighting as the
other 14 elements.

**TCF7L2:** the [original assay metadata](https://kircherlab.bihealth.org/satMutMPRA/)
places both TCF7L2 and ZFAND3 in MIN6 with no added treatment. We therefore reuse
AlphaGenome's published ZFAND3 mapping:

| Ontology ID | Track biosample |
| --- | --- |
| `CL:0002351` | Progenitor cell of endocrine pancreas |
| `UBERON:0001150` | Body of pancreas |
| `UBERON:0001264` | Pancreas |

This is an extrapolation of the published ZFAND3 mapping, not a published
TCF7L2 mapping or an exact MIN6 match.

**ZRSh13:** the assay used NIH3T3 with Hoxd13 co-transfection. NIH3T3 is a
[mouse embryonic fibroblast line](https://www.atcc.org/products/crl-1658).
We select the nine available human DNase tracks whose metadata labels their
life stage as embryonic and their biosample name contains “fibroblast” or is
“IMR-90”; [IMR-90 is a fetal lung fibroblast line](https://www.atcc.org/products/ccl-186).
This lineage/stage rule avoids choosing a particular anatomical source by its
benchmark performance. The exact ontology list is fixed, rather than expanded
automatically when API metadata changes:

| Ontology ID | Track biosample |
| --- | --- |
| `CL:0011021` | Fibroblast of upper back skin |
| `CL:0011022` | Fibroblast of skin of back |
| `CL:2000013` | Fibroblast of skin of abdomen |
| `EFO:0001196` | IMR-90 |
| `NTR:0000521` | Fibroblast of skin of left biceps |
| `NTR:0000522` | Fibroblast of skin of left quadriceps |
| `NTR:0000523` | Fibroblast of skin of right quadriceps |
| `NTR:0000524` | Fibroblast of skin of scalp |
| `NTR:0000525` | Fibroblast of skin of right biceps |

These tracks approximate the cell lineage and developmental stage, but do not
reproduce HOXD13 overexpression. This is our proxy choice, not an AlphaGenome
paper mapping. It is a greater context mismatch than the TCF7L2 mapping.

For comparison, [CADD v1.7](https://academic.oup.com/nar/article/52/D1/D1143/7511313)
uses an average of Enformer DNase tracks for its main MPRA comparison, and
also tests matching cell lines where available (Supplementary Notes S1/S3,
Figures S6/S7). TCF7L2 and ZRSh13 enter its cell-type-agnostic comparison; it
does not supply a specific matched cell type for either. We retain the
AlphaGenome cell-matching approach across all 16 elements with the two
documented extensions above.

### Fitness and model version

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

Error bars use the same [panel-level interval method](./strata-methods.html#confidence-intervals)
as the allele-type plots: the panel mean plus or minus the 97.5th percentile of
Student's t with `n - 1` degrees of freedom times the sample standard error.
They are pointwise 95% intervals across matched panels, conditional on the
saved answers, with invalid-answer zero penalties retained. Bounds are not
clipped; constant panel scores or fewer than two panels have no estimated
interval. Shared assay effects, repeated-run variability and multiple
comparisons are not accounted for. Individual model intervals are not paired
tests of differences between models.

The figure uses separate automatic task scales and orders models by the frozen
overall matched score, weighting each of the three tasks equally. The fitness
comparison uses the same SNVs for every model; it does not assess indel
performance or make AVI's general-impact target assay-specific.

## Reproduction

The implementation is the optional `projects/blog-analysis` workspace package;
the website reads compact exports and never calls a model. Its
`specialist-policy.json` pins the scientific settings and source evidence.
Install with `uv sync --locked --all-packages --extra alphagenome --group test`.

Use a local publication mirror containing the exact artifacts in
`specialist-2026-09-17.manifest.json`. The existing `fetch-strata-inputs.py`
downloader verifies public files against that manifest; if a mutable `main`
artifact has changed, restore its frozen copy rather than weakening the hash
check.

To reproduce the published comparison without inference, use the committed
`specialist-plan.json.gz` and `specialist-predictions.json.gz` as the `compare`
inputs below. To refresh only the compared LLM runs, make a new plan and use
`reuse --original-plan ORIGINAL --plan NEW --predictions SAVED --output REUSED`.
Use the original inference predictions from Git history for `SAVED`; reuse
exports cannot be chained into another reuse operation.
Reuse verifies identical biological requests and policy, checks saved evidence,
and preserves the original inference session. It refuses changed alleles or
specialist inference settings.

The following workflow starts a new specialist inference session:

```bash
uv run --no-sync vepbench-blog-specialists plan \
  --publication /tmp/specialist-publication --plan /tmp/specialist-plan.json

# Source a private file exporting ALPHAGENOME_API_KEY into this process.
uv run --no-sync vepbench-blog-specialists predict \
  --plan /tmp/specialist-plan.json --cache /tmp/specialist-cache --workers 1

uv run --no-sync vepbench-blog-specialists collect \
  --plan /tmp/specialist-plan.json --cache /tmp/specialist-cache \
  --output /tmp/specialist-predictions.json.gz

uv run --no-sync vepbench-blog-specialists compare \
  --publication /tmp/specialist-publication --plan /tmp/specialist-plan.json \
  --predictions /tmp/specialist-predictions.json.gz \
  --output /tmp/specialist-comparison.json.gz

uv run --no-sync python -m vepbench_blog_analysis.specialist_intervals \
  --input /tmp/specialist-comparison.json.gz \
  --output /tmp/specialist-intervals.json
```

`predict` resumes verified cached requests; `--limit` bounds new calls during
validation. Up to four requests run concurrently, and each cached response
records its concurrency limit. Partial runs cannot be collected or compared.
A failed batch stops the run after preserving successful in-flight responses;
failures never silently shrink the matched set. Output paths are exclusive to
preserve frozen artifacts. A new
implementation or policy requires a new plan and cache. Measured request time
distinguishes live inference from Atlas lookup; summed request durations are
not elapsed wall time when requests overlap. Free access does not imply
zero underlying compute cost; that cost remains unknown.

OpenSplice author-provided predictions may also be used in a future collection
when their complete allele identities, context, score definition and version
can be verified. This implementation uses live AlphaGenome for splicing and
MPRA, and Atlas lookup for AVI.
