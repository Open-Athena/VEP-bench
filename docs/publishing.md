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
