# Architecture

VEP-bench separates scientific benchmark tasks from a lightweight evaluator,
private maintainer projects, and the static explorer. A task defines what a
model sees and what counts as the correct answer; the evaluator fetches or
generates questions, calls one provider, and scores deterministic outputs.

## Artifact flow

```text
task source + YAML task descriptor and prompt
            |
            v
 deterministic question JSONL
            |
            v
 task profile + model profile
            |
            v
 local resumable result JSONL
            |
            v
 validated publication version <--- model catalog
            |
            v
 Hugging Face Storage Bucket + static explorer
```

Only the model-visible prompt is sent to OpenRouter. Answer keys stay local.
Evaluation is an explicit local action; tests, CI, and static-site builds do
not make model calls.

## Prompt minimality and causal context

Model-visible prompts should contain the least information needed to define the
prediction target and the experimental conditions that affect its
interpretation. Prefer molecular inputs and assay context that are causally
connected to the measured outcome. The benchmark should test reasoning about
those mechanisms rather than recognition, lookup, or memorization.

Names, genomic coordinates, disease or phenotype labels, accessions, and
derived annotations should remain in source provenance or display metadata
unless a task's methodology establishes that they are necessary inputs to the
prediction. Minimal does not mean context-free: include details that change the
physical reporter construct, cellular state, perturbation, or meaning of the
measurement. Each task methodology should explain why every model-visible
context field is needed.

## Shared and task-specific concerns

The root [`vepbench`](../src/vepbench/) project owns question generation,
validation, OpenRouter evaluation, scoring, and resumable results. Publication
and bucket operations live in [`projects/publishing/`](../projects/publishing/);
static-site assembly and browser QA live in
[`projects/explorer/`](../projects/explorer/). This keeps the default evaluator
independent of maintainer and task-preparation dependencies.

Each task owns its scientific interpretation, source preparation, provenance,
sampling, prompt, task-level model settings, and validity checks. Keep
task-specific assumptions out of shared evaluator, publication, and explorer
code. The evaluator accepts one task family at a time; publication combines
independently pinned runs without rewriting their question-set identities.

Result snapshots retain the full question, response, and resolved non-secret
request parameters so historical results remain independently inspectable.
Configuration identities use validated values rather than YAML formatting.
Observed upstream providers remain response metadata because OpenRouter can
route an unpinned run across providers.

Only `versions/main/` in the public bucket is official. The explorer reads
published artifacts directly, with no database or backend. See
[Publishing](publishing.md) for release and rollout policy.

## Adding a task

Use a stable slug consistently and add, as applicable:

1. A deterministic source artifact under `data/sources/`, with provenance.
2. A versioned YAML prompt and task descriptor under `configs/tasks/<task-slug>/`.
3. Preparation configuration and code under `tasks/<task-slug>/` when needed.
   Tasks using generic question formats can remain config-only.
4. Offline tests for generation, invariants, and schema compatibility.
5. A methodology page under `docs/tasks/` following the
   [task documentation convention](tasks/README.md#documentation-convention).
6. An explorer task page and catalog entry when ready to publish.

Question IDs must be globally unique across tasks. Descriptor paths are
resolved relative to their YAML file; a config-only task needs no CLI edit.

## Sources of truth

Use these definitions for exact fields, defaults, validation, and algorithms:

| Concern | Authoritative source |
| --- | --- |
| Public on-disk contracts | [JSON schemas](../src/vepbench/schemas/) |
| Task and model settings | [Task descriptors and prompts](../configs/tasks/), [model profiles](../configs/models/), and [configuration loaders](../src/vepbench/config/) |
| Question generation and cross-field invariants | [Builder](../src/vepbench/questions/builder.py) and [validation](../src/vepbench/questions/validation.py) |
| Answer parsing and scoring | [Evaluator](../src/vepbench/evaluation/core.py) and [offline tests](../tests/test_evaluator.py) |
| Batch collection and cost allocation | [Batch evaluator](../src/vepbench/evaluation/batch.py) |
| Publication identity and aggregation | [Publisher](../projects/publishing/src/vepbench_publishing/publication.py) |
| Explorer aggregation and compatibility | [Browser data helpers](../projects/explorer/web/components/benchmark-data.js) and [tests](../projects/explorer/web/components/benchmark-data.test.js) |

The schema `$id` values retain their original `VEPBench` URLs as stable public
identifiers; the product rename does not change existing contract identities.

## Temporal provenance

The [model catalog](../configs/models/catalog.yaml) supplies provider-documented
knowledge cutoffs and evidence links. The
[reviewed assay metadata](../projects/explorer/config/assay-publications.yaml)
records the earliest verified public date among indexed records linked from
pinned task provenance, such as PubMed, MaveDB, or Figshare. Arbitrary project
URLs and source-control history do not qualify.

These dates support explorer display and filtering, not model input or scoring.
Unknown dates remain unknown; a model's release date is not a substitute for
its knowledge cutoff. Temporal comparisons can help assess source exposure but
cannot establish that an assay was absent from training data.
