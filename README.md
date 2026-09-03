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
| [OpenSplice SNV](docs/tasks/opensplice-snv.md) | Complete three-exon minigene cassette, assay context, and a 50-SNV panel | Signed change in exon inclusion | 20 |
| [satMutMPRA](docs/tasks/satmut-mpra.md) | Physical reporter context, the complete mutagenized insert, and a 50-variant panel | Signed reporter-activity effects | 16 |
| [Saturation genome editing](docs/tasks/sge.md) | Gene and endogenous assay context, one exon with 100 bp flanks, and a 50-SNV panel | Continuous functional damage | 15 |

Each task has versioned sources, a prompt, methodology, limitations, and results.

## Evaluate a model

The default environment contains only the evaluator. It fetches and verifies
published questions on demand; website, publication, and task-preparation
dependencies are separate private workspace projects.

```bash
uv sync --locked
export OPENROUTER_API_KEY=...
uv run --no-sync vepbench evaluate --model provider/model-id
```

Evaluation is an explicit, potentially paid local action. See the
[evaluation guide](docs/evaluation.md) for versioned profiles, direct runs,
batch collection, and local question files.

## Results

The [explorer](https://openathena.ai/VEP-bench/) provides task-specific
leaderboards and lets readers inspect the exact prompt given to a model, its
complete response, available reasoning, and deterministic score. Interpret each
score alongside the task's methodology and limitations.

## Documentation

- [Benchmark task catalog](docs/tasks/README.md)
- [Contributor documentation](docs/README.md)
- [Architecture and data contracts](docs/architecture.md)
- [Development setup](docs/development.md)
- [Evaluation](docs/evaluation.md)
- [Publishing](docs/publishing.md)

## License

VEP-bench is available under the [Apache License 2.0](LICENSE).
