# Blog analysis

This optional workspace package generates saved statistical results for the
blog. It requires Node.js for the explorer's run selection and date grouping;
SciPy performs the statistical test and estimates confidence intervals. Website
builds consume the exported JSON without installing this package.

For SGE, select the highest available effort per model before examining scores.
The one-sided test targets a before-cutoff performance advantage. Gene and assay
composition can confound that advantage, so it is an exploratory per-model test,
not proof of training-data exposure. Its p-values are unadjusted.

The plotted 95% confidence intervals use SciPy's Student's t interval for the
mean, with n − 1 degrees of freedom and the sample standard deviation divided
by √n as the standard error. The independent unit is the gene panel within each
date group. Coverage is exact at finite sample sizes under independent,
identically distributed normal scores; coverage for other distributions is
approximate. These intervals do not measure variation across model runs.
Intervals are not clipped to correlation bounds. Fewer than two genes or
constant scores leave the interval unestimated.

Collect public results and export the post's snapshot:

```bash
uv sync --locked --package vepbench-blog-analysis
uv run --no-sync vepbench-blog-sge-cutoff \
  --save-input /tmp/sge-cutoff-input.json \
  --output projects/explorer/web/blog/introducing-vep-bench/cutoff-analysis.json
```

Use `--input /tmp/sge-cutoff-input.json` to replay the saved collection offline.
The export records library versions and data provenance. Run the package's
offline tests with `uv run --locked --all-packages --group test pytest
projects/blog-analysis/tests`.

For the variant-stratum figures, generate pointwise intervals directly from the
frozen per-panel scores with `vepbench-blog-strata-intervals`. The sampling unit
is an eligible original gene or regulatory-element panel, keeping invalid
answers' zero penalties. The [stratum methodology](../explorer/web/blog/introducing-vep-bench/strata-methods.md#confidence-intervals)
explains the independence assumptions and gives the offline replay command.
