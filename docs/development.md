# Development

## Setup

VEPBench uses Python 3.14, [`uv`](https://docs.astral.sh/uv/) for Python
dependencies, and npm for the Observable Framework explorer.

```bash
uv sync --locked --group dev
npm ci
uv run --locked pre-commit install
```

Do not add a parallel pip, Poetry, Pipenv, or Conda workflow. Commit both
`pyproject.toml` and `uv.lock` when Python dependencies change.

## Repository layout

| Path | Purpose |
| --- | --- |
| `src/vepbench/` | Shared builder, evaluator, publication, and task preparation code |
| `data/sources/` | Compact deterministic task sources and provenance manifests |
| `templates/` | Versioned model-visible prompt templates |
| `configs/tasks/` | Task-level evaluation settings |
| `configs/models/` | Model- and provider-specific settings |
| `schemas/` | Public question, result, and publication contracts |
| `tests/fixtures/` | Small offline-only synthetic artifacts |
| `web/` | Static Observable Framework explorer |
| `docs/tasks/` | Scientific methodology, one page per benchmark task |

See [Architecture](architecture.md) before changing shared data flow or adding
a task.

## Checks

Run the locked offline checks before opening a pull request:

```bash
uv run --locked python scripts/validate_vep_consequence_artifacts.py
uv run --locked pytest --cov=vepbench --cov-report=term-missing:skip-covered
uv run --locked ruff check .
uv run --locked ruff format --check .
uv run --locked mypy
npm test
uv run --locked pre-commit run --all-files
uv run --locked vepbench build --output /tmp/questions.jsonl
cmp benchmark/expected-manifest.json /tmp/questions.manifest.json
uv run --locked vepbench site --output /tmp/vepbench-site
```

The source-artifact validator is specific to the current consequence task; each
new task should add its own deterministic validation entry point where needed.

CI runs the offline suites, validates deterministic regeneration against the
expected manifest, compiles the explorer, and performs browser smoke QA with
fake evaluation data. A separate scheduled and post-deployment canary checks
that the live explorer can read the official bucket version.

## Browser smoke QA

The browser smoke test requires one of `google-chrome`, `chromium`,
`chromium-browser`, or `chrome-headless-shell` on `PATH`. Confirm the browser is
discoverable before running it:

```bash
command -v chromium || command -v chrome-headless-shell
bash scripts/browser_qa.sh _site browser-qa
```

On a user-managed VM, keep reusable browser binaries under a persistent path
such as `~/.local/share/vepbench/browsers/` and expose the selected executable
through `~/.local/bin`, which is normally on `PATH`. Do not rely on a browser
installation under `/tmp` because temporary files are not a stable project
dependency.

## Artifact policy

Compact prepared task sources and their manifests are committed when they are
needed for reproducible question generation. Do not edit generated sources by
hand when a preparation script can reproduce them.

`vepbench build` writes questions and a digest manifest under `.vepbench/` by
default. Generated question JSONL and production result JSONL are ignored and
are not tracked in Git. The public bucket is their canonical published home;
Git keeps generation code, compact sources, schemas, tests, and the small
`benchmark/expected-manifest.json` fingerprint.

Live or paid model calls must remain explicit local actions. Tests and CI use
fake transports and never read `OPENROUTER_API_KEY`. Small synthetic fixtures
under `tests/fixtures/` exist only for offline tests.

Implementation work is tracked in GitHub issues rather than in repository plan
or roadmap files.
