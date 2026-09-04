# Benchmark tasks

Every benchmark task has one methodology page in this directory. The root
README lists tasks only at a high level; scientific and operational details
live here so the project can grow without turning its introduction into a
task-specific manual.

## Catalog

| Task | Family | Status |
| --- | --- | --- |
| [Fitness (SGE)](sge.md) | `sge` | Public development set |
| [Expression (satMutMPRA)](satmut-mpra.md) | `satmut_mpra` | Public development set |
| [Splicing (OpenSplice)](opensplice-snv.md) | `opensplice_snv` | Public development set |

## Documentation convention

A task page should state:

- the scientific question and what a model receives;
- the answer space and scoring rule;
- source datasets, versions, provenance, and sampling method;
- the assay's earliest verified indexed-publication evidence;
- prompt and task-profile versions;
- known limitations and the intended interpretation of scores;
- reproducible preparation and validation instructions;
- links to its source, manifest, template, profile, and explorer page;
- published baseline results, when available.

Use a stable lowercase filename matching the task slug. Keep shared evaluator,
schema, publication, and development instructions in the parent documentation
rather than copying them into every task page.

See [Architecture](../architecture.md#adding-a-task) for the implementation
checklist.
