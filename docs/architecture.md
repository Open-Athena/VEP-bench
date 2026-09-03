# Architecture

VEP-bench separates scientific benchmark tasks from a small shared execution and
publication layer. A task defines what a model sees and what counts as the
correct answer; the shared layer generates questions, calls one provider,
scores deterministic outputs, and produces a static explorer.

## Artifact flow

```text
task source + prompt template
            |
            v
 deterministic question JSONL
            |
            v
 task profile + model profile
            |
            v
 local resumable result JSONL
            |
            v
 validated publication version <--- model catalog
            |
            v
 Hugging Face Storage Bucket + static explorer
```

Only the model-visible prompt is sent to OpenRouter. Answer keys stay local and
are applied after a response is returned. Tests, CI, and static-site builds do
not make model calls.

## Shared and task-specific concerns

The shared layer owns:

- deterministic question generation and validation;
- OpenRouter batch and bounded-parallel direct evaluation;
- strict final-answer parsing, exact-match or rank-correlation scoring;
- resumable local results and immutable question fingerprints;
- publication formats, validation, and the static results explorer.

Each task owns:

- its scientific question and intended interpretation;
- source data, provenance, sampling, and preparation code;
- its prompt template, answer vocabulary, and task-level model settings;
- task-specific validity checks and tests;
- a methodology page under [`docs/tasks/`](tasks/README.md).

Expensive task preparation may keep immutable, content-addressed processed
intermediates under the public bucket's separate `data_prep/` namespace. Such a
cache is task-owned, is not an official benchmark version, and must not contain
raw upstream data that can be fetched from its authoritative archive. Cache
completion manifests are installed last so readers never mistake a partial
upload for a reusable result.

Task-specific assumptions should not be added to shared evaluator,
publication, or explorer code. Published questions identify their task through
`metadata.task_family`, and task-profile evaluation checks that a question set
contains exactly the expected family.

## Adding a task

Use a stable slug consistently and add, as applicable:

1. A deterministic source artifact under `data/sources/`, with a provenance
   manifest when the source is generated or downloaded.
2. A versioned prompt template under `templates/`.
3. A task profile under `configs/tasks/` for generation settings shared by all
   model runs on that task.
4. Preparation or validation code under `src/vepbench/` and `scripts/` when the
   source cannot be maintained directly.
5. Offline fixtures and tests for generation, invariants, and schema
   compatibility.
6. A page at `docs/tasks/<task-slug>.md` following the conventions in the
   [task catalog](tasks/README.md).
7. A task page and catalog entry in the static explorer when the task is ready
   to publish.

Question IDs must be globally unique across tasks. New task families should use
explicit source, template, and profile paths rather than relying on the current
single-task CLI defaults. The evaluator intentionally accepts one task family
at a time. Publication combines those independently pinned task runs without
rewriting their question-set identities.

## Data contracts

The public on-disk contracts are:

- [Generated questions](../schemas/question.schema.json)
- [Local resumable results](../schemas/result.schema.json)
- [Published runs](../schemas/run.schema.json)
- [Normalized browser answers](../schemas/answer.schema.json)
- [Raw response envelopes](../schemas/raw-response.schema.json)
- [Published version manifests](../schemas/manifest.schema.json)

The schema `$id` values retain their original `VEPBench` URLs as stable public
identifiers; the product rename does not change existing contract identities.

Generated questions are sorted by `question_id` and written as UTF-8 JSONL with
LF line endings. The complete file has a lowercase SHA-256 fingerprint; each
question is also fingerprinted from canonical compact JSON.

Task source records may contain a `source_metadata` object for audit fields
that must remain out of model-visible prompts. The builder includes that object
in the source-record fingerprint but does not copy it into generated questions;
task-specific compact-source validators own its structure.

Question schema 1.0 describes multiple-choice tasks and schema 2.0 describes
quantitative ranking tasks. JSON Schema cannot express every question
invariant. The builder additionally checks unique choice or candidate IDs,
valid answer references, finite reference scores, and exact agreement between
the structured choices or candidate rows and the rendered prompt. Ranking
candidates and their rendered VCF rows are sorted by `CHROM`, `POS`, `REF`, and
`ALT`.

For multiple choice, the scorer reads only the last well-formed
`FINAL: <choice-id>` line. A complete response is assigned one flat result type:
`correct`, `incorrect`, `refusal`, `token_limit`, or `format_error`. Structured
provider refusal evidence has precedence; otherwise a valid parsed answer
determines correctness, an unparseable response finished for `length` is a
token limit, and any remaining unparseable completion is a format error. For
ranking, the scorer reads the last well-formed
`FINAL: {<candidate-id>: <number>, ...}` object and requires every candidate
exactly once with finite numeric values. Invalid completed ranking output gets
the documented floor correlations rather than a null API score. An API failure
is not a result type: it receives null scoring and makes the run incomplete.

Result snapshots retain the complete question, provider response, final
content, nullable provider-exposed reasoning, usage, finish reason, non-secret
request parameters, question-set digest and size, and individual question
digest. Historical results therefore remain inspectable without reconstructing
the original run. When a provider exposes cost only for a whole batch, the
normalized usage records retain the aggregate receipt and identify the
deterministic allocation used to make per-result costs sum to that receipt.
The retained batch membership lets merge and publication validation reject
missing members, inconsistent receipts, or totals that do not reconcile.

## Publication and explorer

Local result JSONL is a resumable staging format. Publication validates and
deduplicates it into run metadata, compact per-run outcome indexes, browser
answers, and complete raw response archives. It also aggregates
the five result counts, provider-reported token usage, and USD cost into each
run and joins versioned model-family and release-date metadata from
`configs/models/catalog.json`. Model catalog fields are display metadata
and do not affect configuration identity. Question and raw-run archives are
deterministic zstd JSONL; browser answers are deterministic gzip JSON objects.
A manifest records compressed and decompressed sizes and digests.

A model configuration key includes gateway, model ID, model revision, all
generation parameters, and the prompt and task identity. The observed upstream
provider is response metadata rather than configuration identity because
OpenRouter may route one unpinned run across providers. A run with more than one
observed provider is labeled `OpenRouter auto-routing`; every per-response
provider remains preserved in local results and raw archives. The official
version accepts only complete runs without API errors and at most one run for
each configuration key. Every published task family must have at least one
complete run, but those runs may belong to different model configurations.
Publication processes result and raw-response data as streams so memory use
does not grow with the total amount of model reasoning.
Legacy result records without `result_type` remain publishable because the
publisher derives the same classification from their score, finish reason, and
retained structured provider response.
For compatibility, `metrics.format_failures` continues to count every completed
response with a parse error. The narrower five-way taxonomy is reported in
`metrics.result_counts`, where only `format_error` excludes refusals and token
limits.

An official multi-task version publishes the sorted union of its task question
sets as the browsable question artifact. Each run still records the digest,
size, and evaluation profile of the single task question set that was actually
evaluated. The publication's `runs.json` maps those profiles to task families
and records the complete list required by the leaderboard.

The provisional `classification_task_macro_average_v0` overall score groups
runs with identical gateway, model, model revision, and fully resolved
generation parameters. A configuration is eligible only when it has
one complete run for every published classification profile. Its overall score
is the arithmetic mean of classification-task accuracies, so every included
task has equal weight regardless of question count. Quantitative ranking tasks
remain visible as task-specific Spearman leaderboards and are not mixed with
accuracy. Displayed overall token usage and cost sum only the included
classification runs. A future aggregation change must use a new method
identifier rather than silently changing this rule.

The leaderboard task selector controls both its table and line chart. `All
classification tasks` uses the macro-average score and summed cost and token
usage described above; a specific task uses that task run's exact-match score
or mean within-element Spearman correlation, plus its cost and token usage. The
score column retains the generic `Score` label because the selector
provides its scope. The line chart connects configurations within each
published model family and can compare the selected score against cost or total
tokens. The page uses Observable's native inputs, table, and plot so both views
react to the same selected row set. The native table's score formatter adds an
absolute zero-to-one inline bar behind the percentage without changing the
numeric value used for sorting.

The question explorer selects complete model configurations, ranked by overall
score, and resolves the task-specific run only after a question is selected.
Displayed `Qnnn` labels are global ordinals in the explorer's canonical task
order, not per-task row numbers; task pages label their filtered questions from
that same combined ordering.
Its compact `question-metadata.json` asset is deterministically derived from
committed task sources and provenance manifests. Classification tasks normally
supply `source_metadata.vep_consequence`; other tasks may supply an appropriate
display label such as `source_metadata.model_visible_name`. This display-only
metadata is never added to model-visible prompts and does not change question
or historical result fingerprints.

Only `versions/main/` in the public bucket is official. Named lowercase-slug
versions are reviewable release candidates or disposable experiments. The
Observable Framework explorer is static and reads published artifacts directly
from the bucket; it has no database, authentication, or backend service.
