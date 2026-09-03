# Contributor documentation

The root [README](../README.md) is an introduction for benchmark users. This
directory contains the implementation and maintenance documentation for people
working on VEP-bench.

## Guides

- [Architecture](architecture.md): shared benchmark concepts, data contracts,
  artifact flow, and the boundary between the framework and individual tasks.
- [Development](development.md): local setup, repository layout, checks, and
  artifact handling.
- [Evaluation](evaluation.md): building questions and running models through
  OpenRouter.
- [Publishing](publishing.md): building, validating, and publishing a static
  benchmark version.
- [Task catalog](tasks/README.md): task documentation conventions and the list
  of implemented benchmark tasks.

Task methodology belongs under `docs/tasks/`, one Markdown file per task. Shared
mechanics belong in the guides above. This keeps the root README stable as new
tasks are added.
