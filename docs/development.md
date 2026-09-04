# Development

## Setup

VEP-bench uses one private `uv` workspace. The Python version, dependencies,
workspace members, and tool settings are defined in
[`pyproject.toml`](../pyproject.toml). A default sync installs only the evaluator:

```bash
uv sync --locked
uv run --no-sync vepbench --help
```

For repository-wide development:

```bash
uv sync --locked --all-packages --all-extras --group test --group quality
uv run --no-sync pre-commit install
```

For a focused environment, select a workspace package with
`uv sync --locked --package <package-name> --group test`. The
[evaluation](evaluation.md), [publishing](publishing.md), and
[task](tasks/README.md) guides include their specific setup commands.
Explorer work also needs `npm ci --prefix projects/explorer`.

## Checks

After installing the development environment:

```bash
uv run --locked pytest
uv run --locked ruff check .
uv run --no-sync pre-commit run --all-files
uv run --no-sync mypy
npm test --prefix projects/explorer
```

The workflows are the source of truth for CI environments and check sequences:

- [Test](../.github/workflows/test.yml): isolated evaluator, publication, and
  task tests, plus regeneration of expected question fingerprints.
- [Quality](../.github/workflows/quality.yml): formatting, lint, and type checks;
  hook definitions live in [the pre-commit config](../.pre-commit-config.yaml).
- [Website](../.github/workflows/website.yml): static build and browser QA.
- [Live canary](../.github/workflows/live-canary.yml): deployed explorer and
  official bucket compatibility.

Task pages include offline validation commands for their committed artifacts.
See [Publishing](publishing.md#static-explorer) for a local explorer build.

## Browser smoke QA

With a built site and a supported Chrome or Chromium executable on `PATH`, run:

```bash
uv sync --locked --package vepbench-explorer --extra qa
bash projects/explorer/scripts/browser_qa.sh /tmp/vepbench-site browser-qa
```

The [QA script](../projects/explorer/scripts/browser_qa.sh) defines supported
executable names and options. On a user-managed VM, keep reusable browser
binaries under a persistent path such as `~/.local/share/vepbench/browsers/`
and expose the executable through `~/.local/bin`. A `/tmp` browser installation
is not a stable project dependency.

## Artifact policy

Git keeps generation code, compact prepared sources and provenance under
[`data/sources/`](../data/sources/), and expected question fingerprints under
[`benchmark/`](../benchmark/). Regenerate artifacts instead of editing them by
hand. Generated questions and production results stay local until publication;
the public bucket is their canonical published home.

Expensive preparation can reuse content-addressed processed intermediates under
the bucket's `data_prep/` namespace. These task-owned caches are separate from
official `versions/` artifacts and must not contain raw upstream data that can
be fetched from its authoritative archive. Task pages link to preparation code
and remote entry points; full SGE preparation exceeds the shared VM's memory
and I/O limits and must run remotely.

Tests and CI use offline fixtures and fake transports. Live or paid model calls
must remain explicit local actions. Track implementation work in GitHub issues
rather than repository plan or roadmap files.
