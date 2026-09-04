# Contributor documentation

The root [README](../README.md) is an introduction for benchmark users. This
directory contains the implementation and maintenance documentation for people
working on VEP-bench.

## Guides

- [Architecture](architecture.md): design rationale, task boundaries, and links
  to authoritative code and contracts.
- [Development](development.md): local setup, checks, and artifact handling.
- [Evaluation](evaluation.md): building questions and running models through
  OpenRouter.
- [Publishing](publishing.md): building, validating, and publishing a static
  benchmark version.
- [Task construction](task-construction.md): shared allele and sampling protocol.
- [Task catalog](tasks/README.md): task documentation conventions and the list
  of implemented benchmark tasks.

Document rationale, scientific methodology, limitations, and workflows that
require judgment. Link to code, schemas, configuration, and CI for exact fields,
defaults, inventories, and validation rules instead of maintaining prose copies.
Task methodology belongs under `docs/tasks/`, one file per task; shared workflows
belong in the guides above.
