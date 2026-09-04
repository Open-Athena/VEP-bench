# Evaluation

Evaluation is an explicit local operation through OpenRouter. It is never run
by tests or CI, and the credential is read only from `OPENROUTER_API_KEY`.

## Install and fetch questions

The default locked sync installs only the evaluator runtime:

```bash
uv sync --locked
```

`evaluate` fetches and verifies the published `main` question set when
`--questions` is omitted. To populate the local cache without a model call:

```bash
uv run --no-sync vepbench questions fetch --version main
```

Use `--question-version <version>` on `evaluate` for a named publication, or
`--questions path/to/questions.jsonl` for an explicit local file. Maintainers
can generate questions from a task descriptor:

```bash
uv run --no-sync vepbench questions build \
  --task configs/tasks/satmut-mpra/task.yaml \
  --output .vepbench/questions/satmut-mpra.jsonl
```

See the [task pages](tasks/README.md) for source preparation and fingerprint
validation. Download limits and verification rules live in the
[question fetcher](../src/vepbench/questions/fetch.py).

## Profiles

A [task profile](../configs/tasks/) owns settings shared by every model on that
task, such as the completion-token ceiling. A [model profile](../configs/models/)
owns model- or provider-specific settings. Change task-owned settings in the
versioned task descriptor; the evaluator rejects overlapping task and model
settings. Paths, run IDs, and secrets remain run-specific, while resolved
non-secret request parameters are retained in results.

A task profile requires a question file containing only that task family.
The examples below use the satMutMPRA file generated above.

Use the profiles for current parameter values and
`uv run --no-sync vepbench evaluate --help` for CLI options and defaults.

## Run a versioned configuration

```bash
export OPENROUTER_API_KEY=...
uv run --locked vepbench evaluate \
  --task configs/tasks/satmut-mpra/task.yaml \
  --questions .vepbench/questions/satmut-mpra.jsonl \
  --model-profile configs/models/openai-gpt-5.6-luna-medium.yaml
```

By default, `evaluate` submits to OpenRouter's asynchronous Batch API and records
state under `.vepbench/batches/`. Refresh and collect a submitted batch with:

```bash
uv run --locked vepbench batch status --state <state.json>
uv run --locked vepbench batch collect --state <state.json>
```

Collection writes scored results in deterministic question order. When the
provider reports only an aggregate batch cost, per-result costs are allocations,
not separately measured charges. The original receipt and allocation provenance
remain in results; merge and publication validate that the total reconciles.
See the [batch evaluator](../src/vepbench/evaluation/batch.py) for the allocation
algorithm.

Some providers reserve the theoretical maximum completion cost on submission.
If that exceeds the available balance, submit bounded chunks sequentially:

```bash
uv run --locked vepbench evaluate \
  --task configs/tasks/satmut-mpra/task.yaml \
  --questions .vepbench/questions/satmut-mpra.jsonl \
  --model-profile configs/models/openai-gpt-5.6-sol-medium.yaml \
  --run-id <shared-run-id> --batch-offset 0 --batch-size 7 \
  --batch-state <chunk-01-state.json> --output <chunk-01-results.jsonl>
```

Refresh and collect each chunk before submitting the next, advancing the offset
by the chunk size. Keep the same full question file, profiles, and run ID for
every chunk. After collecting all questions, merge the chunk files:

```bash
uv run --locked vepbench batch merge \
  --output .vepbench/results/<shared-run-id>.jsonl \
  <chunk-01-results.jsonl> <chunk-02-results.jsonl> ...
```

Use direct evaluation when a model has no live batch endpoint:

```bash
uv run --locked vepbench evaluate --direct \
  --task configs/tasks/satmut-mpra/task.yaml \
  --questions .vepbench/questions/satmut-mpra.jsonl \
  --model-profile configs/models/openai-gpt-5.6-luna-high.yaml
```

Use `--concurrency` to bound direct requests and `--resume` to validate and
continue an interrupted result file. For an ad hoc model, use
`--model provider/model-id` in place of `--model-profile`. Local results are
written under `.vepbench/results/` unless an output path is provided.

## Completion and failure semantics

Ranking tasks use the last well-formed `FINAL: {JSON object}` line, requiring
every candidate ID exactly once with finite numeric predictions. Spearman
measures ordering (with average ranks for ties); Pearson compares the raw
predicted and reference values. Each correlation is computed within a question
and then arithmetically averaged, giving every panel equal weight. The all-task
leaderboard averages task scores for configurations that completed every task.

Constant vectors receive correlation zero. Invalid completed output also
contributes zero to both correlations and lowers valid-output rate. This
represents no usable ranking signal while distinguishing malformed output from
a valid, perfectly reversed ranking.

The shared evaluator also supports exact-match multiple-choice scoring.
[The scorer and classifier](../src/vepbench/evaluation/core.py) and
[their offline tests](../tests/test_evaluator.py) define parsing edge cases and
refusal, token-limit, and format-error classification.

For either question type, API failures have null scores and make the run
incomplete; they cannot appear in the official leaderboard. Retained reasoning
is only what the provider exposes, not a claim of access to private reasoning.
