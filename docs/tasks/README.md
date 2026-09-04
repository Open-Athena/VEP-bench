# Benchmark tasks

Every benchmark task has one methodology page in this directory.

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
- source datasets, provenance, and sampling method;
- the assay's earliest verified indexed-publication evidence;
- known limitations and the intended interpretation of scores;
- reproducible preparation and validation instructions;
- links to versioned sources, manifests, prompts, profiles, and published results.

Use a stable lowercase filename matching the task slug. Keep shared evaluator,
schema, publication, and development instructions in the parent documentation
rather than copying them into every task page. Link to configuration and
manifests for exact versions, seeds, source mappings, and audit counts. Keep the
scientific rationale for selection and unusual source exceptions in prose.

See [Architecture](../architecture.md#adding-a-task) for the implementation
checklist.
