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

## Benchmark task

| Task | Model input | Target | Questions |
| --- | --- | --- | ---: |
| [satMutMPRA](docs/tasks/satmut-mpra.md) | Physical reporter context, the complete mutagenized insert, and a 50-variant panel | Signed reporter-activity effects | 16 |

The task has versioned sources, a prompt, methodology, limitations, and results.

## Results

The [explorer](https://openathena.ai/VEP-bench/) provides the satMutMPRA
leaderboard and lets readers inspect the exact prompt given to a model, its
complete response, available reasoning, and deterministic score. Interpret the
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
