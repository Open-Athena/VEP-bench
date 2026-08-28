# VEPBench

VEPBench is an early-stage, public benchmark for measuring the native genetic
variant effect prediction capabilities of language models. Models receive no
internet access or tools during evaluation.

The first version is deliberately small and measures current capabilities
rather than aiming to be a contamination-resistant benchmark:

- questions are generated deterministically from structured variant records and
  versioned prompt templates;
- all questions use multiple-choice answers and exact-match scoring;
- evaluations run locally through OpenRouter;
- public questions, answer keys, responses, scores, and provider-exposed
  reasoning are stored in this repository; and
- a static GitHub Pages site makes results inspectable without a backend.

Questions normally identify a variant or allele change without supplying
external annotations. Results may therefore reflect a mixture of variant
knowledge, nomenclature interpretation, memorization, and biological inference.

## Workflow

```text
structured variant records + versioned templates
                       |
                       v
                 questions.jsonl
                       |
                       v
              local OpenRouter run
                       |
                       v
              results/<run-id>.jsonl
                       |
                       v
          static GitHub Pages explorer
```

Only model-visible prompts cross the API boundary. Answer keys remain local and
are applied by the deterministic scorer after each response returns. Paid model
calls run only from an explicitly invoked local command, never from tests or CI.

The static explorer shows model summaries, per-family accuracy,
individual prompts and responses, available provider-exposed reasoning, and a
question-by-model comparison matrix. It will not require a database or backend.

## Data contracts

- [Generated question schema](schemas/question.schema.json)
- [Evaluation result schema](schemas/result.schema.json)

Generated questions are sorted by `question_id` and written as UTF-8 JSONL with
LF line endings. A question-set fingerprint is the lowercase SHA-256 digest of
the complete file. A per-question fingerprint is computed from compact JSON
with recursively sorted keys and no trailing newline.

Every multiple-choice prompt asks the model to end with:

```text
FINAL: <choice-id>
```

The scorer uses the last well-formed `FINAL:` line and compares its choice ID
exactly. A missing or unknown choice ID scores zero as a parse error. API errors
have a null score and make the run incomplete instead of counting as scientific
errors.

Each result snapshots the exact prompt, choices, and expected answer alongside
the raw provider response. Provider-exposed reasoning is preserved when
available and is otherwise null; it is not presented as guaranteed access to a
model's private chain of thought.

JSON Schema cannot enforce that `answer_choice_id` occurs exactly once in
`choices`. The builder and tests must enforce this cross-field invariant, unique
choice IDs, and agreement between the structured choices and rendered prompt.

## Development

Python environments and dependencies are managed with
[`uv`](https://docs.astral.sh/uv/). Install the locked development environment,
build the synthetic benchmark fixture, and run the offline tests with:

```bash
uv sync --locked --group dev
uv run --locked vepbench build
uv run --locked vepbench build-demo-result --output /tmp/synthetic-demo.jsonl
uv run --locked pytest
uv run --locked ruff check .
uv run --locked vepbench site --output /tmp/vepbench-site
```

The committed `benchmark/questions.jsonl` is generated; do not edit it directly.
The committed `results/synthetic-demo.jsonl` is also generated and is clearly
labelled as a mock OpenRouter response for exercising the explorer. Neither is
real benchmark evidence.

To run a real evaluation, export an OpenRouter key locally and name the exact
OpenRouter model ID:

```bash
export OPENROUTER_API_KEY=...
uv run --locked vepbench evaluate --model provider/model-id
```

Evaluation is sequential and non-streaming. The command refuses to overwrite an
existing run, appends and flushes one result at a time, and exits non-zero when
an API error leaves the run scientifically incomplete. It never sends answer
keys to the provider. No test or GitHub Actions workflow reads the API key or
makes model calls.

GitHub Actions validates the generated fixtures, tests every committed result,
and assembles the Pages artifact from `web/`, `benchmark/`, and `results/`.
Implementation work is tracked in GitHub issues rather than as a roadmap here.

VEPBench is a public development set for now. If it becomes a formal benchmark,
a separate unpublished held-out set can be introduced without changing this
public workflow. A future ranking task can add a versioned question schema and a
deterministic Spearman-correlation scorer while keeping the same runner and
result-file layout.
