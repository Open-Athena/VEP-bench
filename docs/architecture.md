# Architecture

VEP-bench separates scientific benchmark tasks from a lightweight evaluator,
private maintainer projects, and the static explorer. A task defines what a
model sees and what counts as the correct answer; the evaluator fetches or
generates questions, calls one provider, and scores deterministic outputs.

## Artifact flow

```text
task source + YAML task descriptor and prompt
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

## Prompt minimality and causal context

Model-visible prompts should contain the least information needed to define the
prediction target and the experimental conditions that affect its
interpretation. Prefer molecular inputs and assay context that are causally
connected to the measured outcome. The benchmark should test reasoning about
those mechanisms rather than recognition, lookup, or memorization.

Names, genomic coordinates, disease or phenotype labels, accessions, and
derived annotations should remain in source provenance or display metadata
unless a task's methodology establishes that they are necessary inputs to the
prediction. Minimal does not mean context-free: include details that change the
physical reporter construct, cellular state, perturbation, or meaning of the
measurement. Each task methodology should explain why every model-visible
context field is needed.

## Shared and task-specific concerns

The root `vepbench` project owns:

- deterministic question generation and validation;
- OpenRouter batch and bounded-parallel direct evaluation;
- strict final-answer parsing, exact-match or rank-correlation scoring;
- resumable local results and immutable question fingerprints.

Publication and bucket operations live in the private
`projects/publishing/` workspace project. Static-site assembly and browser QA
live in `projects/explorer/`. This keeps the default evaluator independent of
Hugging Face, zstandard, task preparation, and Node tooling.

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
2. A versioned YAML prompt and strict task descriptor under
   `configs/tasks/<task-slug>/`.
3. Preparation configuration and code under `tasks/<task-slug>/` only when the
   source cannot be maintained directly. Tasks using the generic question
   formats can remain config-only.
5. Offline fixtures and tests for generation, invariants, and schema
   compatibility.
6. A page at `docs/tasks/<task-slug>.md` following the conventions in the
   [task catalog](tasks/README.md).
7. A task page and catalog entry in the static explorer when the task is ready
   to publish.

Question IDs must be globally unique across tasks. The shared CLI reads source,
prompt, question type, and task-level generation settings from the descriptor,
so adding a config-only task does not require a CLI or evaluator edit. Paths
are resolved relative to the YAML file that declares them. The evaluator
intentionally accepts one task family at a time. Publication combines those
independently pinned task runs without rewriting their question-set identities.

## Data contracts

The public on-disk contracts are:

- [Generated questions](../src/vepbench/schemas/question.schema.json)
- [Local resumable results](../src/vepbench/schemas/result.schema.json)
- [Published runs](../src/vepbench/schemas/run.schema.json)
- [Normalized browser answers](../src/vepbench/schemas/answer.schema.json)
- [Raw response envelopes](../src/vepbench/schemas/raw-response.schema.json)
- [Published version manifests](../src/vepbench/schemas/manifest.schema.json)

The schema `$id` values retain their original `VEPBench` URLs as stable public
identifiers; the product rename does not change existing contract identities.

Generated questions are sorted by `question_id` and written as UTF-8 JSONL with
LF line endings. The complete file has a lowercase SHA-256 fingerprint; each
question is also fingerprinted from canonical compact JSON.

Reproducibility identities use validated configuration values serialized as
canonical JSON, not raw YAML bytes. Reformatting YAML or adding comments does
not change an identity; changing a validated value does. Generated question
digests additionally cover the rendered prompt and source-record content.

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
zero for both correlations and remains a format failure rather than receiving a
null API score. A valid, perfectly reversed ranking can still receive `-1`. An
API failure is not a result type: it receives null scoring and makes the run
incomplete.

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
`configs/models/catalog.yaml`. Model catalog fields are display metadata
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

Ranking task leaderboards use the task's mean within-question Spearman
correlation, with Pearson correlation and valid-output rate as diagnostics.
The default all-task leaderboard macro-averages one primary `Score` from each
published task profile for model configurations that completed every task. The
aggregation consumes the task-level score without assuming that its underlying
metric is accuracy, Spearman correlation, or any other particular metric; it
does not pool questions across tasks.
The score-efficiency chart compares the primary score to cost or total tokens
and works with one or more complete model runs.

The question explorer has one page per task. It selects complete model
configurations, resolves the matching task run after a question is selected,
and renders the exact stored prompt alongside the complete response without
exposing measured reference effects in a comparison table.
Its compact `question-metadata.json` asset is deterministically derived from
committed task sources and provenance manifests. satMutMPRA supplies its
element label as `source_metadata.display_name`. This display-only
metadata is never added to model-visible prompts and does not change question
or historical result fingerprints. SGE uses the same display field for its
gene label.

Only `versions/main/` in the public bucket is official. Named lowercase-slug
versions are reviewable release candidates or disposable experiments. The
Observable Framework explorer is static and reads published artifacts directly
from the bucket; it has no database, authentication, or backend service.
