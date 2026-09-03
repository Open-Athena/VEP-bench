---
title: Tasks
---

# Tasks

VEP-bench publishes transparent development tasks with exact model prompts, complete responses, and deterministic scores.

<div class="card">

## satMutMPRA

Predict signed reporter-activity effects for panels sampled across the measured effect distribution of 16 regulatory elements.

- 16 element-level questions with 50 variants each
- Full assayed element sequence, assay context, and compact VCF input
- Mean within-element Spearman correlation, with Pearson and output-validity diagnostics

[Open task →](./tasks/satmut-mpra.html)

</div>

<div class="card">

## Saturation genome editing

Predict continuous functional damage for panels sampled from endogenous-locus saturation genome editing assays.

- One eligible exon-level question per gene with 50 assayed SNVs
- Gene, assay mechanism, and exon sequence with exact 100 bp flanks
- Mean within-gene Spearman correlation, with Pearson and output-validity diagnostics

[Open task →](./tasks/sge.html)

</div>
