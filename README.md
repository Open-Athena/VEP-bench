# VEP-bench

VEP-bench is a public benchmark for measuring the native genetic variant effect
prediction capabilities of language models. Models answer without internet
access or tools, and every response can be inspected in the public results
explorer.

[Explore the leaderboard and responses](https://openathena.ai/VEP-bench/)

## What VEP-bench measures

VEP-bench uses deterministic, versioned question sets built from biological
reference data. Models receive only the model-visible prompt; answer keys stay
local and scoring is deterministic.

The benchmark is intentionally transparent:

- questions and reference answers are public development data;
- prompts use strict, machine-readable final-answer formats;
- complete responses and provider-exposed reasoning are preserved when
  available;
- evaluations use one OpenRouter integration; and
- results are published to a static explorer with no database or backend.

VEP-bench measures current model capability. Temporal task cohorts can reduce
direct source leakage but do not guarantee absence from training data.

## Benchmark tasks

| Task | Model input | Target | Questions |
| --- | --- | --- | ---: |
| [Ensembl VEP most-severe consequence](docs/tasks/vep-most-severe-consequence.md) | A centered GRCh38 sequence window and SNV alleles | Ensembl VEP consequence class | 51 |
| [ClinVar](docs/tasks/clinvar.md) | A centered GRCh38 sequence window and SNV alleles | ClinVar Benign or Pathogenic | 42 |
| [satMutMPRA regulatory-effect ranking](docs/tasks/satmut-mpra.md) | Assay context, a full assayed regulatory-element sequence, and a 50-variant panel | Signed reporter-activity effects | 16 |

Each task has its own versioned sources, prompt, methodology, limitations, and
results. Task details live under [`docs/tasks/`](docs/tasks/README.md) so new
tasks can be added without making this README task-specific.

## Results

The [explorer](https://openathena.ai/VEP-bench/) provides a provisional overall
score that weights each classification task equally, plus task-level results
and question-level prompts, reference answers, model responses, available
reasoning, and deterministic scores. Quantitative ranking tasks have separate
leaderboards because correlation is not commensurate with exact match. Each
score should be interpreted alongside the corresponding task's methodology and
limitations.

## Documentation

- [Benchmark task catalog](docs/tasks/README.md)
- [Contributor documentation](docs/README.md)
- [Architecture and data contracts](docs/architecture.md)
- [Development setup](docs/development.md)
- [Evaluation](docs/evaluation.md)
- [Publishing](docs/publishing.md)

## License

VEP-bench is available under the [Apache License 2.0](LICENSE).
