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
  --version prompt-redesign \
  --questions .vepbench/questions/satmut-mpra.jsonl \
  --results-dir .vepbench/publication-results/satmut-mpra \
  --output /tmp/vepbench-publication

uv run --no-sync vepbench-publish version validate \
  --version prompt-redesign \
  --root /tmp/vepbench-publication
```

The version builder joins each result to its exact generated task question set
and validates schemas, fingerprints, run completeness, and configuration
identity.
The publishing config selects the reviewed bucket and model catalog, resolving
its catalog path relative to the YAML file. The catalog supplies leaderboard
family and release-date metadata. `--model-catalog` can select another reviewed
catalog for an experiment. Publication aggregates provider-reported usage into
total tokens and total USD cost per run. Every published model must have a
catalog entry. Named versions use lowercase slugs. Only `main` is official.

For satMutMPRA, name the question set and curated result staging directory explicitly:

```bash
uv run --no-sync vepbench-publish version build \
  --config projects/publishing/config/publishing.yaml \
  --version satmut-mpra-prompt-v1-1 \
  --questions .vepbench/questions/satmut-mpra.jsonl \
  --results-dir .vepbench/publication-results/satmut-mpra \
  --output /tmp/vepbench-publication
```

SGE is published as its own independently pinned ranking run:

```bash
uv run --no-sync vepbench-publish version build \
  --config projects/publishing/config/publishing.yaml \
  --version sge-prompt-v1 \
  --questions .vepbench/questions/sge.jsonl \
  --results-dir .vepbench/publication-results/sge \
  --output /tmp/vepbench-publication
```

The question file must contain exactly one task family. Result files retain
the digest and size of that question set. Curate the staging directory so it
contains only the intended full result files, not batch chunks or
superseded runs.

## Plan and apply a bucket update

With `HF_TOKEN` set, create a non-mutating plan for exactly one version prefix:

```bash
uv run --no-sync vepbench-publish bucket plan \
  --config projects/publishing/config/publishing.yaml \
  --root /tmp/vepbench-publication \
  --version prompt-redesign \
  --plan /tmp/prompt-redesign.plan.jsonl
```

Review the JSONL plan, then apply it with an exact destination confirmation:

```bash
uv run --no-sync vepbench-publish bucket apply \
  --plan /tmp/prompt-redesign.plan.jsonl \
  --confirm-destination \
    hf://buckets/open-athena/VEP-bench/versions/prompt-redesign
```

The plan records the expected action, digest, and size for each file. No
command recursively syncs or deletes at the bucket root.

## Promote the official version

Replacing protected `main` requires `--promote-main` on both bucket commands.
First derive a complete future `main` tree from a validated named version:

```bash
uv run --no-sync vepbench-publish version promote \
  --source-root /tmp/vepbench-publication \
  --source-version prompt-redesign \
  --output /tmp/vepbench-main

uv run --no-sync vepbench-publish bucket plan \
  --config projects/publishing/config/publishing.yaml \
  --root /tmp/vepbench-main \
  --version main \
  --promote-main \
  --plan /tmp/main.plan.jsonl
```

Apply that reviewed plan with `bucket apply --promote-main` and the confirmed
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
uv sync --locked --package vepbench-explorer
npm ci --prefix projects/explorer
uv run --no-sync vepbench-site build \
  --config projects/explorer/config/site.yaml \
  --output /tmp/vepbench-site
```

The explorer is a read-only static site. It loads `versions/main/runs.json`
first, including the leaderboard's model release date, total tokens, and total
cost. The score-efficiency chart switches between cost and tokens and draws one
line per model family. Leaderboard score bars and chart values are presented as
percentages; published score values remain in their canonical representation,
and negative correlations are displayed as 0%. The explorer fetches the
question index and a compact
outcome index for the selected run when a user opens the question view on its
task page.
This supports valid-output and format-failure filters while full answer content
is loaded one compressed object at a time. The question pane renders the exact
stored model prompt; it does not display a measured-versus-predicted effect
table. Complete raw archives remain downloadable without requiring a backend.

`runs.json` names each task's evaluation profile and primary metric. Both
satMutMPRA and SGE use mean within-question Spearman; Pearson correlation and
valid-output rate are also published. A single complete model run is sufficient
for a working task leaderboard; additional model configurations add rows
without changing the publication contract.
