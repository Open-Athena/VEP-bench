# Evaluation

Evaluation is an explicit local operation through OpenRouter. It is never run
by tests or CI, and the credential is read only from `OPENROUTER_API_KEY`.

## Install and fetch questions

The default locked sync installs only the evaluator runtime. It does not install
the explorer, publishing, Hugging Face, or task-preparation dependencies:

```bash
uv sync --locked
```

`evaluate` fetches and verifies the published `main` question set when
`--questions` is omitted. To populate or inspect the content-addressed cache
without evaluating a model, run:

```bash
uv run --no-sync vepbench questions fetch --version main
```

The command downloads the version manifest and zstd archive, verifies the
compressed and decompressed sizes and SHA-256 digests, validates the record
count, and caches the JSONL under `.vepbench/questions/` with its content digest.
Use a named version for an immutable historical release, or pass
`--questions path/to/questions.jsonl` to `evaluate` for an explicit local file.

Maintainers can generate questions from any strict YAML task descriptor:

```bash
uv run --no-sync vepbench questions build \
  --task configs/tasks/satmut-mpra/task.yaml \
  --output .vepbench/questions/satmut-mpra.jsonl
```

The descriptor selects the question type, prepared source, prompt, and
task-owned evaluation settings. Its relative paths are resolved from the
descriptor's directory.

## Profiles

A task profile contains settings that must be shared by every model evaluated
on that task, such as the completion-token ceiling. A model profile contains
only model- or provider-specific settings. Question paths, result paths, run
IDs, and secrets remain run-specific.

The satMutMPRA task profile uses a 128,000-token completion ceiling. This fits
the supported output limit of every benchmarked model while leaving substantial
headroom for reasoning and the required final answer.

The evaluator rejects overlapping task and model settings. Fully resolved
non-secret request parameters are copied into every result record for
reproducibility.

## Run a versioned configuration

```bash
export OPENROUTER_API_KEY=...
uv run --locked vepbench evaluate \
  --task configs/tasks/satmut-mpra/task.yaml \
  --model-profile configs/models/openai-gpt-5.6-luna-medium.yaml
```

By default, `evaluate` submits the complete question set to OpenRouter's
asynchronous Batch API and records state under `.vepbench/batches/`. Refresh and
collect a submitted batch with:

```bash
uv run --locked vepbench batch status --state <state.json>
uv run --locked vepbench batch collect --state <state.json>
```

Collection writes canonical scored JSONL in deterministic question order.
Submission maps benchmark question IDs to stable provider-safe batch custom IDs
and preserves that mapping in the resumable state file.
OpenRouter reports cost for a completed batch as one aggregate rather than on
each response. Collection allocates that exact total deterministically across
all submitted results in proportion to their provider-reported token totals
when every result reports them, or equally otherwise. This includes failed or
malformed responses, so their billed cost is not dropped. The allocations sum
to the batch total, allowing publication to retain exact run cost. Each result
marks the cost as an allocated batch total and retains the batch ID, allocation
method, submitted question IDs, and complete provider usage receipt under
`usage.vepbench`. Merge and publication validation require every recorded batch
member to be present with identical provenance and require the allocated costs
to reconcile to the receipt within one floating-point ULP. Publication carries
that provenance into both normalized answers and raw-response envelopes.

Some providers reserve the theoretical maximum completion cost when a batch is
submitted. If that reservation exceeds the available balance, submit bounded
chunks sequentially while retaining the identity of the complete question set:

```bash
uv run --locked vepbench evaluate \
  --task configs/tasks/satmut-mpra/task.yaml \
  --model-profile configs/models/openai-gpt-5.6-sol-medium.yaml \
  --run-id <shared-run-id> --batch-offset 0 --batch-size 7 \
  --batch-state <chunk-01-state.json> --output <chunk-01-results.jsonl>
```

Refresh and collect each chunk before submitting the next one, advancing
`--batch-offset` by the chunk size. After every question is collected, merge
the chunk result files into one complete, deterministically ordered run:

```bash
uv run --locked vepbench batch merge \
  --output .vepbench/results/<shared-run-id>.jsonl \
  <chunk-01-results.jsonl> <chunk-02-results.jsonl> ...
```

Chunk state records both the submitted question IDs and the full question-set
digest and size. The merge rejects missing, duplicate, mismatched, or
mixed-configuration records.

Use direct evaluation when a model has no live batch endpoint:

```bash
uv run --locked vepbench evaluate --direct \
  --task configs/tasks/satmut-mpra/task.yaml \
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

Only prompts cross the provider boundary; answer keys never do. The shared
evaluator still supports a complete multiple-choice response with one flat, deterministic
`scoring.result_type`:

- `correct` or `incorrect` when the last well-formed `FINAL: <choice-id>` line
  identifies a known choice;
- `refusal` when the provider supplies a structured refusal or a
  `content_filter` finish reason;
- `token_limit` when no valid final answer is present and the finish reason is
  `length`; or
- `format_error` for every other completed response without a valid final
  answer.

A valid final answer remains `correct` or `incorrect` when the finish reason is
`length`. Refusal evidence takes precedence over answer parsing.

A complete ranking response must end with a strict JSON mapping containing
every expected candidate ID exactly once and finite numeric predictions.
Invalid completed ranking output contributes zero to both Spearman and Pearson
and is counted as a format failure. Zero represents no usable ranking signal;
it avoids treating malformed output as equivalent to a valid, perfectly
reversed ranking. A constant valid vector also receives correlation zero but
remains distinguishable through its valid-output status.

That taxonomy does not include provider or transport failures. For either task
type, an API failure has null scoring, makes the run incomplete,
and causes direct evaluation to exit nonzero. Such a run cannot appear in the
official leaderboard.

Local results are written under `.vepbench/results/` unless an output path is
provided. They preserve provider-exposed reasoning when present, but do not
claim access to a model's private chain of thought.

## Fable profile

The Claude Fable 5.1 medium profile requests medium adaptive reasoning and
omits unsupported temperature and seed parameters. Use its canonical base
model ID with the default OpenRouter Batch API path; OpenRouter selects the
discounted batch route during submission.

The DeepSeek V4 Flash 0731 low profile requests low provider-exposed reasoning
and a deterministic seed. Its direct and batch prices should be checked before
a full run because the cheapest live route can change independently of the
versioned profile.

## Existing Luna profiles

The low, medium, and high profiles request their corresponding reasoning
efforts. The medium Flex profile requests OpenAI's Flex service tier through
OpenRouter and should be used with `--direct` when the advertised batch route
is unavailable.

On 2026-08-29, OpenRouter's live Batch API rejected both the documented Luna
base model ID and its `:batch` slug as lacking a batch endpoint. The committed
Luna baselines therefore used direct evaluation with concurrency 16. Native
batch submission remains the default for models with a working batch endpoint.
