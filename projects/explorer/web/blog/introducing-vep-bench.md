---
title: "Introducing VEP-bench: predicting variant effects with language models"
theme: [air, near-midnight, alt]
---

# Introducing VEP-bench: predicting variant effects with language models

VEP-bench v0.1

**Draft — dataset composition and model performance.**

VEP-bench asks language models to predict the effects of genetic variants from
DNA sequence and experimental context, without tools or internet access. Its
three tasks measure different outcomes: functional damage in
[saturation genome editing (SGE)](../tasks/sge.html), reporter activity in
[satMutMPRA](../tasks/satmut-mpra.html), and exon inclusion in
[OpenSplice](../tasks/opensplice-snv.html).

Before comparing model performance, we can ask what kinds of variants each
task contains.

```js
import {variantComposition, compositionCsv} from "./introducing-vep-bench/analysis.js";
import {distributionFigure} from "./introducing-vep-bench/plots.js";

const metadata = await FileAttachment("../data/question-metadata.json").json();
const composition = variantComposition(metadata);
const complete = composition.tasks.every((task) => task.available);
const percent = (value) => `${(value * 100).toFixed(1)}%`;
const integer = (value) => value.toLocaleString("en-US");
function share(family, dimension, category) {
  const row = composition.rows.find((row) => row.task_family === family
    && row.dimension === dimension && row.category === category);
  return row ? `${percent(row.proportion)} (${integer(row.count)}/${integer(row.total)})` : "unavailable";
}
```

## What is being counted?

These plots describe the **selected benchmark panels** in the source snapshot
bundled with this site. Each panel contains 50 complete assayed alleles. We count
each allele once per panel, regardless of how many models evaluated it. An
allele appearing in two panels contributes twice; the denominator is panel
appearances, not unique genomic sites.

```js
display(Inputs.table(composition.tasks.map((task) => ({
  Task: task.label,
  Panels: task.panels,
  "Selected variants": task.available ? task.total : null
})), {select: false, rows: 3}));
if (!complete) {
  display(html`<div class="warning" label="Incomplete snapshot">Variant metadata is missing for one or more tasks. The comparisons below are unavailable.</div>`);
}
```

Panel sampling spreads selections across the experimental score range. It
does not impose variant-type or consequence quotas, and these distributions
should not be read as frequencies in human populations or in the full source
assays. See the [shared sampling protocol](https://github.com/Open-Athena/VEP-bench/blob/main/docs/task-construction.md).

## SNVs, indels, and multibase substitutions

We classify the complete REF-to-ALT edit after trimming shared flanking bases.
An **SNV** substitutes one base; an **indel** changes sequence length, including
unequal-length replacements; a **multibase substitution** replaces multiple
bases without changing length. Each variant belongs to one category.

```js
if (complete) display(resize((width) => distributionFigure(composition, "type", width)));
```

Each task panel has its own automatically scaled percentage axis; compare the
percentage labels across tasks. Hover over a bar for its count and denominator;
zero labels indicate absent categories. On narrow screens, scroll horizontally
to compare all three tasks.

```js
if (complete) display(html`<p>
  Expression is predominantly SNVs: ${share("satmut_mpra", "type", "SNV")}.
  Splicing has a majority of indels: ${share("opensplice_snv", "type", "Indel")}.
  Fitness includes SNVs, indels, and a substantial multibase-substitution component:
  ${share("sge", "type", "Multibase substitution")}.
  The OpenSplice task therefore covers more than the SNVs suggested by its historical
  <code>opensplice_snv</code> identifier.
</p>`);
```

## Genomic consequences

For each complete allele, we use the saved Ensembl VEP
`most_severe_consequence`: one highest-priority category per allele across the
returned transcript and regulatory-feature annotations. Transcript consequences
can differ between transcripts. The retained category is not an experimental
measurement or a clinical interpretation.
See Ensembl's [consequence definitions and severity ordering](https://www.ensembl.org/info/genome/variation/prediction/predicted_data.html).

```js
if (metadata.variant_annotation) display(html`<p class="muted">
  Annotation snapshot: ${metadata.variant_annotation.provider}, release ${metadata.variant_annotation.software.release};
  ${metadata.variant_annotation.assembly}; ${metadata.variant_annotation.transcripts};
  upstream/downstream distance ${integer(metadata.variant_annotation.parameters.distance)} bp.
</p>`);
if (complete) display(resize((width) => distributionFigure(composition, "consequence", width)));
```

Consequence rows are alphabetical and aligned across the three tasks. All
observed categories are retained, including rare consequences.

```js
if (complete && metadata.variant_annotation) display(html`<p>
  Missense variants are the largest category in fitness (${share("sge", "consequence", "missense_variant")}).
  Expression spans several genomic contexts, including intronic
  (${share("satmut_mpra", "consequence", "intron_variant")}) and upstream-gene
  (${share("satmut_mpra", "consequence", "upstream_gene_variant")}) annotations.
  Splicing includes splice-region, splice-site, coding, and intronic consequences;
  in-frame deletions account for ${share("opensplice_snv", "consequence", "inframe_deletion")}.
</p>`);
```

The assay determines the prediction target. A variant labeled “missense” in its
genomic context can still be evaluated for its effect on exon inclusion in a
splicing reporter. Likewise, an intronic genomic annotation does not establish
what a sequence will do in an expression reporter. These differences in
composition provide context for model comparisons across tasks; they do not by
themselves explain performance differences. The consequence annotations shown
here are not supplied to the models.

### Why are there so few intergenic variants in expression?

Our satMutMPRA subset contains nine promoter panels and seven enhancer panels,
selected from the [source study's targeted regulatory-element assays](https://doi.org/10.1038/s41467-019-11526-w).
Promoter sequences can receive upstream, UTR, or overlapping-transcript labels.
Enhancers can also lie within genes: in this snapshot, every selected variant
in the IRF4, TCF7L2, ZFAND3, and ZRS enhancer panels is labeled intronic. The MYC
enhancer panel is labeled noncoding-transcript exon, and the SORT1 enhancer panel
is labeled 3′ UTR. These names describe the assayed elements; VEP considers all
annotated transcripts, including transcripts of other genes.

```js
if (complete && metadata.variant_annotation) display(html`<p>
  The expression task's intergenic category contains ${share("satmut_mpra", "consequence", "intergenic_variant")},
  all from the IRF6 enhancer panel. Its remaining 46 selected alleles receive the
  regulatory-region label. The intergenic bar therefore counts only alleles whose
  retained VEP label is <code>intergenic_variant</code>; it does not count every
  allele outside annotated transcripts. Upstream and regulatory-feature annotations
  can take precedence over that label.
</p>`);
```

### What does “regulatory region” mean here?

`regulatory_region_variant` denotes overlap with a regulatory-feature interval
in the saved Ensembl annotation. Ensembl identifies features such as promoters,
enhancers, and CTCF-binding sites using genomic annotations and epigenomic
evidence, including chromatin accessibility and ChIP-seq measurements of histone
marks or protein binding. See [Ensembl Regulation](https://regulation.ensembl.org/)
for the annotation methods. The 1,000 bp setting reported above controls
upstream/downstream gene annotations; it does not define regulatory-region
boundaries.

This label does not establish that the variant changes regulatory activity or
that the feature is active in the MPRA's cell line. The regulatory-region bar
is not a count of all regulatory-feature overlaps: a variant that overlaps both
a regulatory feature and a transcript can receive a higher-priority transcript
consequence in this single-label view. “Regulatory” and “intronic,” for example,
can describe different aspects of the same allele.

## SGE performance before and after the knowledge cutoff

SGE's gene panels have different recorded public dates, so we can split them
relative to each model's reported knowledge cutoff. The expression and splicing
tasks each use a single assay date in this snapshot; they do not offer the same
within-task split.

Each point below is the **mean within-gene Spearman correlation**, with equal
weight per gene panel. Blue circles show panels before the cutoff; orange
diamonds show panels after it. Counts are gene panels, not individual variants.
Horizontal bars show **95% confidence intervals** for each group mean.
For each model we select its **highest available reasoning effort** among
complete published SGE runs, breaking ties by the most recent run. Selection
does not depend on performance. Models without a known cutoff are excluded.

```js
import {cutoffCsv} from "./introducing-vep-bench/cutoff.js";
import {cutoffFigure} from "./introducing-vep-bench/plots.js";

const cutoffAnalysis = await FileAttachment("./introducing-vep-bench/cutoff-analysis.json").json();
const correlation = (value) => value == null ? "—" : value.toFixed(3);
const pValue = (value) => value == null ? "—" : value < 0.001 ? "<0.001" : value.toFixed(3);
```

```js
if (cutoffAnalysis.summaries.length) {
  display(resize((width) => cutoffFigure(cutoffAnalysis.summaries, width)));
} else {
  display(html`<p>No complete SGE results with known model cutoffs are available.</p>`);
}
```

All four models in this snapshot have the same **9 before / 7 after** split.
Their cutoffs (February 16, March, and April 30, 2026) fall in the same gap
between recorded assay dates: PALB2 on January 19 and the next four panels on
June 8. No panel falls between those dates, so each cutoff selects the same
genes; the groups were not balanced to achieve these counts.

The bars are symmetric **95% Student's t confidence intervals**:
mean ± t(0.975, n − 1) × s / √n, where s is the sample standard deviation of
the gene-level scores in that date group. We compute them with
[SciPy's t distribution](https://docs.scipy.org/doc/scipy/reference/generated/scipy.stats.t.html).
The interval has exact 95% coverage at finite sample sizes when the gene scores
are independent and identically normally distributed; it does not require a
large-sample approximation under those assumptions. Normality is an approximation
for these bounded correlation scores, so coverage here is approximate, especially
with only 9 and 7 panels. The intervals estimate uncertainty in the mean across
genes, rather than variability across repeated model runs. We do not clip their
bounds to the correlation range. A group with fewer than two genes or no
variation has no estimated interval. Overlap
between the two intervals is not the significance test; the p-value below
tests the before-cutoff advantage directly.

The assay date is the earliest verified public date among indexed records linked
from the benchmark's pinned provenance (PubMed or MaveDB). It is a proxy for
public availability, not proof of when the model saw the data. For a cutoff
specified only to the month, panels dated in that month are excluded because
their ordering is unknown. An exact cutoff date includes that day in the
“before” group. Missing or unmatched assay dates are also excluded.

A gap is **descriptive, not a causal estimate of training-data exposure**:
the before and after groups contain different genes and experiments, with
potentially different difficulty. Group membership can also change across
models with different cutoffs. Completed format failures retain their published
zero scores; negative correlations are retained. An empty group has no mean.

### Does this model have a before-cutoff advantage?

The hypothesis is per model: **does this model perform better on genes whose
assays were available before its knowledge cutoff?** We use SciPy's
[independent-sample permutation test](https://docs.scipy.org/doc/scipy/reference/generated/scipy.stats.permutation_test.html)
with the statistic **mean Spearman before minus mean Spearman after**, a
one-sided alternative (before greater than after), and exact enumeration.
The gene panel is the independent unit. SciPy reassigns whole genes between the
groups while keeping their sizes fixed; it counts allocations with a difference
at least as large as observed. With 9 and 7 genes, all 11,440 allocations are
included. This does not require normally distributed scores.

The test assumes independent genes whose scores are exchangeable between date
groups under the null (the same score distribution). These genes and assays
were not randomly assigned to publication dates, so the test describes an
exploratory association. It cannot separate date from gene or assay differences.
The reported p-values are **unadjusted and interpreted separately for each
model**, with a per-model threshold of 0.05. A detected before-cutoff advantage
would be consistent with source exposure, but would not prove overfitting.
With only 16 panels, a nonsignificant result cannot rule out overfitting or
establish equivalence. Groups with fewer than two genes are not tested.

```js
if (cutoffAnalysis.summaries.length) display(Inputs.table(cutoffAnalysis.summaries, {
  columns: ["model", "knowledge_cutoff", "before_n", "before_mean", "after_n", "after_mean", "difference", "p_value", "excluded_n"],
  header: {model: "Model", knowledge_cutoff: "Cutoff", before_n: "Before n", before_mean: "Before ρ",
    after_n: "After n", after_mean: "After ρ", difference: "Before − after", p_value: "One-sided p", excluded_n: "Excluded panels"},
  format: {before_mean: correlation, after_mean: correlation, difference: correlation, p_value: pValue,
    knowledge_cutoff: (value, i) => html`<a href=${cutoffAnalysis.summaries[i].knowledge_cutoff_url}>${value}</a>`},
  select: false,
  rows: cutoffAnalysis.summaries.length
}));
if (cutoffAnalysis.summaries.length) display(html`<p>
  <a download="vepbench-sge-cutoff-summary.csv" href=${`data:text/csv;charset=utf-8,${encodeURIComponent(cutoffCsv(cutoffAnalysis.summaries))}`}>Download cutoff summary (CSV)</a>
  · <a download="vepbench-sge-cutoff-panels.csv" href=${`data:text/csv;charset=utf-8,${encodeURIComponent(cutoffCsv(cutoffAnalysis.scores))}`}>Download gene scores and date provenance (CSV)</a>
</p>`);
```

<details>
<summary>Inspect the gene panels in each group</summary>

```js
const cutoffModel = view(Inputs.select(cutoffAnalysis.summaries, {label: "Model", format: (row) => row.model}));
```

```js
const cutoffPanels = cutoffAnalysis.scores.filter((row) => row.run_id === cutoffModel?.run_id);
display(Inputs.table(cutoffPanels, {
  columns: ["gene", "assay_date", "relation", "spearman_rho", "valid"],
  header: {gene: "Gene", assay_date: "Recorded public date", relation: "Group", spearman_rho: "Spearman ρ", valid: "Valid output"},
  format: {spearman_rho: correlation,
    assay_date: (value, i) => value ? html`<a href=${cutoffPanels[i].assay_url}>${value}</a>` : "Unknown"},
  select: false,
  rows: 16
}));
```

</details>

## Counts and provenance

```js
const csv = compositionCsv(composition.rows);
if (complete) display(html`<p><a download="vepbench-variant-composition.csv"
  href=${`data:text/csv;charset=utf-8,${encodeURIComponent(csv)}`}>Download all counts and proportions (CSV)</a>
  · <a href=${await FileAttachment("../data/question-metadata.json").url()} download="question-metadata.json">Download source-linked annotation metadata (JSON)</a></p>`);
```

The metadata records each panel's source-record digest, complete genomic
alleles, and VEP annotation provenance. The composition figures are recomputed
from that bundled snapshot when the page loads. The cutoff analysis is a saved
snapshot generated with SciPy from the published run and outcome indexes. Its
CSV downloads retain run IDs, question-set digests, cutoffs, and assay-date
sources for the displayed comparison.

```js
display(html`<p class="muted">Cutoff analysis collected ${cutoffAnalysis.retrieved_at}.
  SciPy ${cutoffAnalysis.statistics.software.scipy}; NumPy ${cutoffAnalysis.statistics.software.numpy}.
  <a href=${await FileAttachment("./introducing-vep-bench/cutoff-analysis.json").url()} download="sge-cutoff-analysis.json">Download the analysis snapshot (JSON)</a>.
</p>`);
```

<details>
<summary>View the exact counts</summary>

```js
if (complete) display(Inputs.table(composition.rows, {
  columns: ["task", "dimension", "category", "count", "total", "proportion"],
  header: {task: "Task", dimension: "Distribution", category: "Category", count: "Count", total: "Task total", proportion: "Percent"},
  format: {proportion: percent},
  select: false,
  rows: 12
}));
```

</details>

- [Explore the leaderboard](../index.html)
- [Task methodology](../tasks.html)
- [Code on GitHub](https://github.com/Open-Athena/VEP-bench)
