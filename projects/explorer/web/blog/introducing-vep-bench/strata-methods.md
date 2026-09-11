# Reproducing the 11 September 2026 variant-stratum snapshot

The analysis uses the original question panels and saved OpenRouter completions.
It does not make model calls. The 10-variant and 5-panel cutoffs were selected
after reviewing annotation coverage and before inspecting stratum correlations.
They limit very small comparisons, without implying a significance threshold.
Keep them fixed when adding a specialist comparison; do not lower them to retain
a preferred result.

The source-linked annotation is Ensembl VEP release 114, GRCh38, retrieved from
the May 2025 REST archive. The retained label is `most_severe_consequence` across
all Ensembl transcripts and returned regulatory features. There is no canonical
transcript filter. This single-label rule deliberately loses transcript-specific
differences; a reporter assay's measured effect can differ from its genomic
consequence. Missing or stale source matches become unknown, and competing
unresolved labels become ambiguous. The annotation snapshot records the exact
request and response hashes.

Only the highest available named reasoning effort for each model and task is
included, using the latest complete run to break ties. Unrecognized or absent
effort labels are rejected instead of guessing how an implicit default compares
with a named effort. This snapshot selects six models: Gemini and the three GPT
models at high, Muse at medium, and GLM at low. Lower-effort runs remain in the
publication and are listed as excluded from this analysis. Selection never uses
performance, cost, or response length.

Each selected configuration is compared with the other selected configurations on the
same variants within each eligible panel of a task/stratum. If a required answer
is missing, changed, or an API failure, the analysis aborts instead of changing
the denominator. Completed invalid full-panel answers still score zero. The
existing scorer supplies average-rank Spearman, Pearson, and its zero rule for
constant vectors; panel correlations receive equal weight. No task aggregate is
defined here because eligible panels and strata differ across tasks.

## Replay

Use the repository's `uv` environment. From the repository root:

```bash
post=projects/explorer/web/blog/introducing-vep-bench
mkdir -p .vepbench/strata-replay
uv run --locked python "$post/fetch-strata-inputs.py" \
  --manifest "$post/strata-2026-09-11.manifest.json" \
  --output .vepbench/strata-replay
uv run --locked python "$post/strata.py" coverage \
  --publication .vepbench/strata-replay \
  --manifest "$post/strata-2026-09-11.manifest.json" \
  --annotations projects/explorer/data/variant-annotations.json \
  --plan .vepbench/strata-replay-plan.json
uv run --locked python "$post/fetch-strata-inputs.py" \
  --manifest "$post/strata-2026-09-11.manifest.json" \
  --output .vepbench/strata-replay --answers
uv run --locked python "$post/strata.py" score \
  --publication .vepbench/strata-replay \
  --manifest "$post/strata-2026-09-11.manifest.json" \
  --annotations projects/explorer/data/variant-annotations.json \
  --plan .vepbench/strata-replay-plan.json \
  --output .vepbench/strata-replayed.json.gz
```

The fetch step verifies bytes against the committed manifest, including content
hashes during analysis. If `versions/main` changes, it refuses mismatching
artifacts; use the retained local inputs or a matching immutable release via
`--base-url`. Retain `.vepbench/strata-inputs` until the immutable dataset release
in issue #79 is available. The browser reads the committed compressed snapshot
and makes no live publication requests for these figures. The snapshot retains
the analysis/scorer hashes and color mapping; a regression check detects changes
to its shared plotting source. Later rendering changes need an explicit dated
correction or a separate snapshot, as required by issue #79.

Rendering revision, 11 September 2026: the draft now groups all three tasks into
two figures, one for allele type and one for consequence, with independently
scaled signed Spearman axes for each task and aligned category rows. Model order
uses the main leaderboard's task macro-average Spearman ranking for the selected
configurations, computed by the same leaderboard helper from the saved publication.
The regenerated snapshot retains that publication's verified run summaries and
pins the revised plot source; scores, cutoffs, model selection, and panel
memberships are unchanged.

## Confidence intervals

Uncertainty revision, 11 September 2026: both figures now show pointwise 95%
Student's t intervals for the unweighted mean of the eligible panel Spearman
scores. The interval is mean ± t(0.975, n − 1) × s / √n, with sample standard
deviation s. [SciPy's t distribution](https://docs.scipy.org/doc/scipy/reference/generated/scipy.stats.t.html)
provides the critical value. Invalid full-panel answers retain their zero
scores, and all models retain the same panels within a stratum. Scores and
coverage cutoffs are unchanged. Independent task axes include the full interval
bounds, without clipping to the correlation range. Constant scores or fewer
than two panels leave the interval unestimated rather than implying certainty.

The sampling unit is an original gene panel for SGE and OpenSplice and an
original regulatory-element panel for satMutMPRA. SGE uses one panel per gene;
OpenSplice selects one exon from each of 20 distinct genes; satMutMPRA uses one
panel per regulatory element. Distinct units do not guarantee independence:
panels can share assay sources and experimental conditions. Exact t coverage
requires independent, identically distributed normal panel scores. Normality
is an approximation for these bounded correlations, especially at the minimum
of five panels. Treat the intervals as exploratory uncertainty across the
selected panels, conditional on the saved answers. They do not estimate shared
assay effects, repeated-run variability, or uncertainty from model selection.
They are neither simultaneous intervals nor tests of model or class differences;
interval overlap should not be interpreted as a significance test.

The optional analysis package generates a separate interval artifact from the
frozen per-panel scores, recording the exact compressed input hash, analysis
source hash, and library versions. Website builds read this artifact without
installing SciPy. Reproduce it offline after the scoring step:

```bash
uv run --locked --all-packages vepbench-blog-strata-intervals \
  --input projects/explorer/web/blog/introducing-vep-bench/strata-2026-09-11.json.gz \
  --output .vepbench/strata-intervals-replayed.json
```

## Specialist inputs

Pass the same `--specialist FILE.json` to both coverage and score steps, using
new plan/output paths. The file contains `provenance` and `panels`. Provenance
must supply `name`, `version`, `source_sha256`, `score_definition`, `direction`,
`input_context`, `known_overlap`, and `variant_matching_rule`. Each panel key is
an exact question ID with its `question_sha256` and a `predictions` mapping from
candidate ID to a finite signed score. Omit unsupported candidates and panels;
zero is a supported prediction, not a missing-value marker.

The specialist preparation must verify assembly, position, REF, ALT, score
direction, and assay/input context before assigning question/candidate IDs.
This analysis validates those pinned identities; it does not infer a genomic
match from an ID or flip the specialist score direction. Support is intersected
with each stratum before either cutoff, and that identical subset is used for
the specialist and every LLM. The analysis reports unsupported candidate IDs,
lost panels, and groups that no longer qualify. Run separate comparisons for
specialists with different support. No specialist score file is yet available
for this snapshot; acquiring it is tracked in issue #78.
