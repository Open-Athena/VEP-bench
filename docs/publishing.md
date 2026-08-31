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

The version builder joins results to the exact generated question set and
validates schemas, fingerprints, run completeness, and configuration identity.
Named versions use lowercase slugs. Only `main` is official.

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

## Static explorer

Build the explorer locally with:

```bash
uv run --locked vepbench site --output /tmp/vepbench-site
```

The explorer is a read-only static site. It loads `versions/main/runs.json`
first, fetches the question index when a user opens the question explorer, and
loads one compressed answer object at a time. Complete raw archives remain
downloadable without requiring a backend.
