# Publishing

Evaluation never uploads automatically. Publication is a separate,
review-before-apply workflow scoped to one version prefix in the public Hugging
Face Storage Bucket. Install its private workspace project; the `bucket` extra
is needed only for remote Hugging Face operations:

```bash
uv sync --locked --package vepbench-publishing --extra bucket
```

## Build and validate a named version

```bash
uv run --no-sync vepbench-publish version build \
  --config projects/publishing/config/publishing.yaml \
  --version candidate \
  --questions .vepbench/questions/satmut-mpra.jsonl \
  --results-dir .vepbench/publication-results/satmut-mpra \
  --output /tmp/vepbench-publication

uv run --no-sync vepbench-publish version validate \
  --version candidate \
  --root /tmp/vepbench-publication
```

Each question file must contain exactly one task family. Repeat `--questions`
and `--results-dir` to include additional tasks in the version. Curate result
directories to contain only intended complete runs, excluding batch chunks and
superseded runs. Every included task needs a complete run, and each configuration
may appear only once.

For an existing complete evaluation spanning several task families, export
task-specific inputs before building the version:

```bash
uv run --no-sync vepbench-publish split-results \
  --questions .vepbench/questions/combined.jsonl \
  --results .vepbench/results/combined.jsonl \
  --output .vepbench/publication-export
```

The export validates every source record, requires one completed response per
question, and preserves completed invalid answers and their zero scores. It
writes canonical task question files and assigns a task suffix to each run ID.
Only run identity and question-set membership change; prompts, provider data,
reasoning, usage, and scores are retained. Namespaced raw metadata and
`export.json` record the original run, full question-set fingerprint, and source
record hashes. Source files remain intact. This command accepts complete source
runs; task runs containing an API error use the retry export below instead.
When a provider batch spans tasks, each export retains the complete batch receipt
and cost allocation ledger, together with its task membership. Its run cost
includes only that task's allocated charges. A shared token receipt cannot fill
missing token usage for an individual task.

For a full task with one API error and one explicitly authorized unchanged retry,
use `vepbench-publish resolve-retry --original ORIGINAL --retry RETRY --output OUTPUT`.
This export retains the original failure inside the selected answer's
`usage.vepbench.retry` metadata, along with the selected source record's digest
and run ID. It rejects changes to the question, model, and generation parameters,
and never replaces a completed answer based on its score. Both original inputs
remain intact. The published run flags the retry and includes both attempts in
its cost. When a rejected item lacks individual token usage, the complete batch
receipt supplies the run's token total; unknown usage remains unknown.
Publish this task export directly with `version build`; do not pass it through
`split-results`, which changes the identities covered by retry provenance.
Retry resolution requires one task family; combined runs with retries are not
supported.

To publish an explicitly authorized selective retry experiment, use
`vepbench-publish resolve-truncations --original ORIGINAL --retry RETRIES --max-tokens LIMIT --output OUTPUT`.
Supply exactly one completed retry for **every** original answer that ended at
the token limit without a valid final answer. Only `max_tokens` may change, and
it must increase; sampling, reasoning effort, routing, model, and questions stay
fixed. The export retains every retry outcome, including another truncation or
an invalid answer, rather than selecting by score. It makes no model calls.
Omit `--retry` for a task with no eligible answers to attach the same experiment
policy across tasks. Publish these task exports directly without `split-results`.

The leaderboard identifies selective retries separately from single-attempt
runs. Its run parameters describe the initial requests; `retry_policy` records
both caps, including for tasks that needed no retries. Each result retains its
actual request parameters, and each replaced answer retains its original full
result and selected source digest in usage provenance. Cost and total tokens
include both attempts; output usage and truncation rates describe retained
responses. This measures a recovery protocol with extra inference spend, not
single-attempt performance. A successful retry does not establish that the
higher cap caused recovery, since sampled responses may differ.

The [publishing config](../projects/publishing/config/publishing.yaml) selects
the bucket and [model catalog](../configs/models/catalog.yaml). Every published
model needs a reviewed catalog entry. Knowledge-cutoff metadata must have
provider evidence or remain null; see
[Temporal provenance](architecture.md#temporal-provenance).

Named versions are review candidates or experiments; only `main` is official.
The [publisher](../projects/publishing/src/vepbench_publishing/publication.py)
and [schemas](../src/vepbench/schemas/) define artifact contents, fingerprints,
completeness checks, and configuration identity.
Use `uv run --no-sync vepbench-publish --help` for commands and options.

## Plan and apply a bucket update

With `HF_TOKEN` set, create a non-mutating plan for exactly one version prefix:

```bash
uv run --no-sync vepbench-publish bucket plan \
  --config projects/publishing/config/publishing.yaml \
  --root /tmp/vepbench-publication \
  --version candidate \
  --plan /tmp/candidate.plan.jsonl
```

Review the JSONL plan, then apply it with an exact destination confirmation:

```bash
uv run --no-sync vepbench-publish bucket apply \
  --plan /tmp/candidate.plan.jsonl \
  --confirm-destination \
    hf://buckets/open-athena/VEP-bench/versions/candidate
```

No command recursively syncs or deletes at the bucket root. The
[bucket implementation](../projects/publishing/src/vepbench_publishing/bucket.py)
defines plan validation, upload order, and remote verification.

## Promote the official version

Promotion replaces the official version. Include every intended task and run
in the candidate before promoting it. Replacing protected `main` requires
`--promote-main` on both bucket commands. First derive a complete future
`main` tree from a validated named version:

```bash
uv run --no-sync vepbench-publish version promote \
  --source-root /tmp/vepbench-publication \
  --source-version candidate \
  --output /tmp/vepbench-main

uv run --no-sync vepbench-publish bucket plan \
  --config projects/publishing/config/publishing.yaml \
  --root /tmp/vepbench-main \
  --version main \
  --promote-main \
  --plan /tmp/main.plan.jsonl
```

Apply that reviewed plan with `bucket apply --promote-main` and the confirmed
`versions/main` destination.

The saved plan also covers the bucket README and shared schemas. A named version
must match those shared files whenever a ready `main` already exists.
Consequently, a release that changes a shared schema cannot upload its named
version while the old `main` readiness marker exists. Validate the named build
locally, derive and publish the promoted `main` tree first, then optionally
upload the named archival version after the shared schemas match.

## Coordinate publication and explorer changes

GitHub Pages and the public bucket deploy independently. When an explorer
change reads a new run field or artifact, use this rollout order:

1. Keep additions backward-compatible and give the new UI a useful fallback for
   the currently published format.
2. Merge and deploy the explorer change before publishing data that the previous
   UI would misinterpret. Verify it still renders the live publication correctly.
3. Build and validate a named candidate locally, review its promoted `main`
   tree, and publish `versions/main`. If shared schemas changed, publish `main`
   before optionally uploading the named candidate.
4. Verify the live `runs.json` and `manifest.json`, then smoke-test the deployed
   explorer against the new official publication.

Data-first rollout is safe only when the deployed explorer already interprets
the new format correctly. The browser QA fixture uses current code and schemas,
so it cannot by itself prove compatibility with the previous publication.

An unavailable deep-linked run may have been removed from `main`. If results
cannot load after a format change, check that the deployed explorer and bucket
artifacts are compatible.

## Static explorer

```bash
uv sync --locked --package vepbench-explorer
npm ci --prefix projects/explorer
uv run --no-sync vepbench-site build \
  --config projects/explorer/config/site.yaml \
  --output /tmp/vepbench-site
```

See [Browser smoke QA](development.md#browser-smoke-qa) for local validation and
the [Website workflow](../.github/workflows/website.yml) for deployment.
The [explorer source](../projects/explorer/web/) defines page behavior; score
interpretation belongs in [Evaluation](evaluation.md#completion-and-failure-semantics)
and the [task methodology](tasks/README.md).

### Variant annotations

To refresh the annotations used by the site:

```bash
uv run --no-sync vepbench-site annotate \
  --config projects/explorer/config/site.yaml \
  --ensembl-url https://may2025.rest.ensembl.org \
  --cache .vepbench/ensembl-may2025
```

Commit the generated [snapshot](../projects/explorer/data/variant-annotations.json)
and retain the response cache for offline replay. Use a fresh cache directory
when refreshing against another Ensembl release. The snapshot records the
annotation source and parameters.

Genomic VEP consequences provide biological context; they may differ from the
effect measured in a reporter assay.
