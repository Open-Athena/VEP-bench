---
title: Tasks
---

# Tasks

Browse benchmark tasks and inspect their published questions, reference answers, model responses, and deterministic scores.

<div class="card">

## Consequence classification

Predict the most severe consequence assigned by Ensembl VEP from a centered human GRCh38 sequence window and the variant alleles.

- 51 balanced chromosome 17 SNVs
- 17 multiple-choice consequence classes
- Public development questions with bucket-published model responses

[Open task →](./tasks/consequence-classification.html)

</div>

<div class="card">

## ClinVar

Classify a GRCh38 SNV as Benign or Pathogenic from local sequence, using a July 2026 first-public ClinVar cohort matched within exact VEP consequences.

- 42 consequence-matched SNVs (21 balanced pairs)
- Fixed Benign and Pathogenic choices
- Temporal public development questions with hidden clinical provenance

[Open task →](./tasks/clinvar.html)

</div>

<div class="card">

## satMutMPRA regulatory-effect ranking

Predict signed reporter-activity effects for panels sampled across the measured effect distribution of 16 regulatory elements.

- 16 element-level questions with 50 variants each
- Full assayed element sequence, assay context, and compact VCF input
- Mean within-element Spearman correlation, with Pearson and output-validity diagnostics

[Open task →](./tasks/satmut-mpra.html)

</div>
