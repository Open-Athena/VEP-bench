# Evaluation

Evaluation is an explicit local operation through OpenRouter. It is never run
by tests or CI, and the credential is read only from `OPENROUTER_API_KEY`.

## Build questions

The default build currently targets the only implemented task:

```bash
uv run --locked vepbench build
```

The command accepts explicit `--source`, `--template`, `--schema`, and
`--output` paths. Use explicit paths for additional task families rather than
depending on the default.

## Profiles

A task profile contains settings that must be shared by every model evaluated
on that task, such as the completion-token ceiling. A model profile contains
only model- or provider-specific settings. Question paths, result paths, run
IDs, and secrets remain run-specific.

The evaluator rejects overlapping task and model settings. Fully resolved
non-secret request parameters are copied into every result record for
reproducibility.

## Run a versioned configuration

```bash
export OPENROUTER_API_KEY=...
uv run --locked vepbench evaluate \
  --task-profile configs/tasks/vep-most-severe-consequence.yaml \
  --model-profile configs/models/openai-gpt-5.6-luna-medium.yaml
```

By default, `evaluate` submits the complete question set to OpenRouter's
asynchronous Batch API and records state under `.vepbench/batches/`. Refresh and
collect a submitted batch with:

```bash
uv run --locked vepbench batch-status --state <state.json>
uv run --locked vepbench batch-collect --state <state.json>
```

Collection writes canonical scored JSONL in deterministic question order.
OpenRouter reports cost for a completed batch as one aggregate rather than on
each response. Collection allocates that exact total deterministically across
successful results in proportion to their provider-reported token totals (or
equally when token totals are unavailable). The allocations sum to the batch
total, allowing publication to retain exact run cost.

Some providers reserve the theoretical maximum completion cost when a batch is
submitted. If that reservation exceeds the available balance, submit bounded
chunks sequentially while retaining the identity of the complete question set:

```bash
uv run --locked vepbench evaluate \
  --task-profile configs/tasks/vep-most-severe-consequence.yaml \
  --model-profile configs/models/openai-gpt-5.6-sol-medium.yaml \
  --run-id <shared-run-id> --batch-offset 0 --batch-size 7 \
  --batch-state <chunk-01-state.json> --output <chunk-01-results.jsonl>
```

Refresh and collect each chunk before submitting the next one, advancing
`--batch-offset` by the chunk size. After every question is collected, merge
the chunk result files into one complete, deterministically ordered run:

```bash
uv run --locked vepbench batch-merge \
  --output .vepbench/results/<shared-run-id>.jsonl \
  <chunk-01-results.jsonl> <chunk-02-results.jsonl> ...
```

Chunk state records both the submitted question IDs and the full question-set
digest and size. The merge rejects missing, duplicate, mismatched, or
mixed-configuration records.

Use direct evaluation when a model has no live batch endpoint:

```bash
uv run --locked vepbench evaluate --direct \
  --task-profile configs/tasks/vep-most-severe-consequence.yaml \
  --model-profile configs/models/openai-gpt-5.6-luna-high.yaml
```

Direct evaluation is non-streaming and bounded-parallel, with eight concurrent
requests by default. Use `--concurrency 1` for a sequential diagnostic and
`--resume` to validate and continue an interrupted ordered result file. The
command refuses to overwrite an existing run and flushes one result at a time.

For an ad hoc model, `--model provider/model-id` is available with defaults of
`temperature: 0.0` and `max_tokens: 4096`. Temperature and reasoning arguments
may override model settings. A completion ceiling owned by a versioned task
profile can only be changed in that profile.

## Completion and failure semantics

Only prompts cross the provider boundary; answer keys never do. A complete
response with an invalid or missing final answer is a scientific error and
scores zero. An API failure has a null score, makes the run incomplete, and
causes direct evaluation to exit nonzero.

Local results are written under `.vepbench/results/` unless an output path is
provided. They preserve provider-exposed reasoning when present, but do not
claim access to a model's private chain of thought.

## Existing Luna profiles

The low, medium, and high profiles request their corresponding reasoning
efforts. The medium Flex profile requests OpenAI's Flex service tier through
OpenRouter and should be used with `--direct` when the advertised batch route
is unavailable.

On 2026-08-29, OpenRouter's live Batch API rejected both the documented Luna
base model ID and its `:batch` slug as lacking a batch endpoint. The committed
Luna baselines therefore used direct evaluation with concurrency 16. Native
batch submission remains the default for models with a working batch endpoint.
