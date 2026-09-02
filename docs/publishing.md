# Publishing

Evaluation never uploads automatically. Publication is a separate,
review-before-apply workflow scoped to one version prefix in the public Hugging
Face Storage Bucket.

## Build and validate a named version

```bash
uv run --locked vepbench version-build \
  --version prompt-redesign \
  --output /tmp/vepbench-publication

uv run --locked vepbench version-validate \
  --version prompt-redesign \
  --root /tmp/vepbench-publication
```

The version builder joins each result to its exact generated task question set
and validates schemas, fingerprints, run completeness, and configuration
identity.
It uses `configs/models/catalog.json` by default for leaderboard family and
release-date metadata, and aggregates provider-reported usage into total tokens
and total USD cost per run. Every published model must have a catalog entry;
use `--model-catalog` to select another reviewed catalog. Named versions use
lowercase slugs. Only `main` is official.

For a multi-task version, repeat `--questions` once per task question set and
`--results-dir` for each curated result staging directory:

```bash
uv run --locked vepbench version-build \
  --version two-task-medium \
  --questions .vepbench/questions.jsonl \
  --questions .vepbench/clinvar-questions.jsonl \
  --results-dir .vepbench/publication-results/consequence \
  --results-dir .vepbench/publication-results/clinvar \
  --output /tmp/vepbench-publication
```

Each question file must contain exactly one task family. Result files retain
the digest and size of that task's question set. Curate staging directories so
they contain only the intended full result files, not batch chunks or
superseded runs.

## Plan and apply a bucket update

With `HF_TOKEN` set, create a non-mutating plan for exactly one version prefix:

```bash
uv run --locked vepbench bucket-plan \
  --root /tmp/vepbench-publication \
  --version prompt-redesign \
  --plan /tmp/prompt-redesign.plan.jsonl
```

Review the JSONL plan, then apply it with an exact destination confirmation:

```bash
uv run --locked vepbench bucket-apply \
  --plan /tmp/prompt-redesign.plan.jsonl \
  --confirm-destination \
    hf://buckets/open-athena/vepbench/versions/prompt-redesign
```

The plan records the expected action, digest, and size for each file. No
command recursively syncs or deletes at the bucket root.

## Promote the official version

Replacing protected `main` requires `--promote-main` on both bucket commands.
First derive a complete future `main` tree from a validated named version:

```bash
uv run --locked vepbench version-promote \
  --source-root /tmp/vepbench-publication \
  --source-version prompt-redesign \
  --output /tmp/vepbench-main

uv run --locked vepbench bucket-plan \
  --root /tmp/vepbench-main \
  --version main \
  --promote-main \
  --plan /tmp/main.plan.jsonl
```

Apply that reviewed plan with `bucket-apply --promote-main` and the confirmed
`versions/main` destination. Promotion removes the old readiness marker before
updating shared files, scopes additions and stale-file deletions to
`versions/main/`, uploads `manifest.json` last, and verifies remote paths,
sizes, and content digests.

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
   UI would misinterpret. Verify the deployed UI still renders the live legacy
   publication correctly.
3. Build and validate a named candidate locally, review its promoted `main`
   tree, and publish `versions/main`. If shared schemas changed, publish `main`
   before optionally uploading the named candidate.
4. Verify the live `runs.json` and `manifest.json`, then smoke-test the deployed
   explorer against the new official publication.

Data-first rollout is safe only when the deployed explorer already interprets
the new format correctly. The deterministic browser QA fixture is built with
the current code and schemas, so it verifies the new format but cannot by itself
prove rollout compatibility with the previously published version.

For the question explorer, **Unavailable run** means a deep-linked run ID is
not present in the current `versions/main/runs.json`. **Results unavailable**
means the selected run lacks its outcome-index metadata or the referenced
artifact could not be loaded; after a format change, check for a deployment
order mismatch first.

## Static explorer

Build the explorer locally with:

```bash
uv run --locked vepbench site --output /tmp/vepbench-site
```

The explorer is a read-only static site. It loads `versions/main/runs.json`
first, including the leaderboard's model release date, total tokens, and total
cost. The score-efficiency chart switches between cost and tokens and draws one
line per model family. The explorer fetches the question index and a compact
outcome index for the selected run when a user opens the question explorer.
This supports the result column and filters for correct, incorrect, refusal,
token-limit, and format-error outcomes while full answer content is still
loaded one compressed object at a time. Outcome indexes built before the flat
result taxonomy remain readable through their boolean correctness field.
Complete raw archives remain downloadable without requiring a backend.

For multi-task publications, `runs.json` also names the required evaluation
profiles and the aggregation method. The current provisional overall score is
an equal-weight mean of per-task exact-match accuracies and is shown only for
configurations with complete coverage of every required profile. Overall cost
and token counts are sums across those task runs.
