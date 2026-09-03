# Development

## Setup

VEP-bench uses Python 3.14 and one private
[`uv`](https://docs.astral.sh/uv/) workspace. A default sync installs only the
model evaluator:

```bash
uv sync --locked
uv run --no-sync vepbench --help
```

Install a focused environment when working on another area:

```bash
# Static explorer and its QA helpers
uv sync --locked --package vepbench-explorer --extra qa --group test
npm ci --prefix projects/explorer

# Publication and optional Hugging Face bucket operations
uv sync --locked --package vepbench-publishing --extra bucket --group test

# satMutMPRA source preparation
uv sync --locked --package vepbench-task-satmut-mpra --group test

# SGE source preparation and its offline tests
uv sync --locked --package vepbench-task-sge --group test

# Repository-wide Python quality tools
uv sync --locked --all-packages --all-extras --group quality
uv run --no-sync pre-commit install
```

Workspace projects are installed from the checkout and are not published to
PyPI. Do not add a parallel pip, Poetry, Pipenv, or Conda workflow. Commit both
`pyproject.toml` and `uv.lock` when dependencies change.

## Repository layout

| Path | Purpose |
| --- | --- |
| `src/vepbench/` | Lightweight evaluator, CLI, configuration, artifact, question, and bundled-schema code |
| `projects/explorer/` | Private static-site Python and Node project |
| `projects/publishing/` | Private publication and bucket project |
| `tasks/satmut-mpra/` | Private satMutMPRA preparation project and YAML configuration |
| `tasks/sge/` | Private SGE catalog, coordinate, consequence, and panel preparation project |
| `data/sources/` | Compact deterministic task sources and provenance manifests |
| `configs/tasks/` | Versioned YAML task descriptors and model-visible prompts |
| `configs/models/` | Model- and provider-specific YAML settings |
| `src/vepbench/schemas/` | Public JSON question, result, and publication contracts bundled with the evaluator |
| `tests/fixtures/` | Small offline-only synthetic artifacts |
| `docs/tasks/` | Scientific methodology, one page per benchmark task |

Large remote preparation jobs may reuse processed, pre-sampling intermediates
from `data_prep/` in the same Hugging Face bucket. That namespace is isolated
from the official `versions/` publication tree and is not mirrored into Git.

See [Architecture](architecture.md) before changing shared data flow or adding
a task.

## Checks

Install all workspace projects and run the complete locked offline checks with:

```bash
uv sync --locked --all-packages --all-extras --group test --group quality
uv run --no-sync pytest --cov=vepbench --cov-report=term-missing:skip-covered
uv run --no-sync pre-commit run --all-files
uv run --no-sync mypy
uv run --no-sync vepbench-satmut-mpra validate
uv run --no-sync vepbench questions build \
  --task configs/tasks/satmut-mpra/task.yaml \
  --output /tmp/satmut-mpra-questions.jsonl
cmp benchmark/satmut-mpra-expected-manifest.json \
  /tmp/satmut-mpra-questions.manifest.json
uv run --no-sync vepbench-sge validate
uv run --no-sync vepbench questions build \
  --task configs/tasks/sge/task.yaml \
  --output /tmp/sge-questions.jsonl
cmp benchmark/sge-expected-manifest.json \
  /tmp/sge-questions.manifest.json
npm test --prefix projects/explorer
uv run --no-sync vepbench-site build \
  --config projects/explorer/config/site.yaml \
  --output /tmp/vepbench-site
```

CI has separate Test, Quality, and Website workflows. Their route jobs always
report a result, while expensive jobs run only for relevant changed paths. The
evaluator test uses an isolated minimal sync and asserts that workspace-only
dependencies are absent. A separate scheduled and post-deployment canary
checks that the live explorer can read the official bucket version.

## Browser smoke QA

The browser smoke test requires one of `google-chrome`, `chromium`,
`chromium-browser`, or `chrome-headless-shell` on `PATH`:

```bash
command -v chromium || command -v chrome-headless-shell
bash projects/explorer/scripts/browser_qa.sh _site browser-qa
```

On a user-managed VM, keep reusable browser binaries under a persistent path
such as `~/.local/share/vepbench/browsers/` and expose the selected executable
through `~/.local/bin`, which is normally on `PATH`. Do not rely on a browser
installation under `/tmp` because temporary files are not a stable project
dependency.

## Artifact policy

Compact prepared task sources and their manifests are committed when they are
needed for reproducible question generation. Do not edit generated sources by
hand when a preparation command can reproduce them.

`vepbench questions build` writes questions and a digest manifest under
`.vepbench/questions/` by default. `vepbench questions fetch` caches verified
published questions there under a content-digest filename. Generated question
JSONL and production result JSONL are ignored and are not tracked in Git. The
public bucket is their canonical published home; Git keeps generation code,
compact sources, schemas, tests, and the small
task-specific expected-manifest fingerprints under `benchmark/`.

The full SGE source build must use the checked-in SkyPilot entry point because
its pinned chromosome consequence objects exceed the shared VM's memory and
I/O limits. The compact generated source and manifest are copied back and
validated locally; tests never download MaveDB, cdot, reference, GTF, or
consequence inputs.

Live or paid model calls must remain explicit local actions. Tests and CI use
fake transports and never read `OPENROUTER_API_KEY`. Small synthetic fixtures
under `tests/fixtures/` exist only for offline tests.

Implementation work is tracked in GitHub issues rather than in repository plan
or roadmap files.
