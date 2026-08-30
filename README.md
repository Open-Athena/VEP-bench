# VEPBench

VEPBench is an early-stage, public benchmark for measuring the native genetic
variant effect prediction capabilities of language models. Models receive no
internet access or tools during evaluation.

The first version is deliberately small and measures current capabilities
rather than aiming to be a contamination-resistant benchmark:

- questions are generated deterministically from structured variant records and
  versioned prompt templates;
- all questions use multiple-choice answers and exact-match scoring;
- evaluations run locally through OpenRouter;
- public questions, answer keys, responses, scores, and provider-exposed
  reasoning are stored in this repository; and
- a static GitHub Pages site makes results inspectable without a backend.

The public explorer is deployed at
[openathena.ai/VEPBench](https://openathena.ai/VEPBench/).

## Current task

The committed development set contains 190 chromosome 17 SNVs. Each question
asks for the Ensembl VEP most severe consequence from a 1,001-base window of
the human GRCh38 reference genome and a centered local VCF record. See the
[complete example prompt](EXAMPLE_PROMPT.md).

Labels come from
[`songlab/hg38-variant-consequences`](https://huggingface.co/datasets/songlab/hg38-variant-consequences),
pinned to revision `eb3022cc6797b9369cca16af72ff3c4197df343a`. They were
generated with VEP release 109.1 and the flags `--most_severe --distance 1000`;
`consequence_cre` is not used. Reference sequence comes from
[`marin-dna/human-genome`](https://huggingface.co/datasets/marin-dna/human-genome),
pinned to revision `11b9433582981bb929af333bc6422f10a8fd71b4`.

There are 19 final choices and exactly 10 questions per choice. Intronic,
intergenic, upstream-gene, and downstream-gene consequences are collapsed into
one choice because the prompt omits genomic coordinates and transcript
annotations. Its 10 questions are deterministically composed of three
`intergenic_variant`, three `intron_variant`, two `upstream_gene_variant`, and
two `downstream_gene_variant` examples. The other chr17 consequences remain
separate. The full preparation configuration, raw counts, source composition,
and artifact digest are recorded in the
[source manifest](data/sources/chr17-vep-consequences.manifest.json).

Every reference window is uppercased and restricted to A/C/G/T. The original
chromosome and genomic position remain in provenance for auditing but are not
included in the model-visible prompt. Instead, the prompt names the sequence
contig `window` and places the SNV at local position 501. It explicitly states:

```text
Reference genome: human GRCh38
VEP version: release 109.1
VEP flags: --most_severe --distance 1000
```

This input is still underdetermined relative to a real VEP run: transcript
annotations are deliberately absent. The task therefore measures inference
from local sequence context and model priors, not exact reconstruction of VEP
from all of VEP's inputs.

## Workflow

```text
structured variant records + versioned templates
                       |
                       v
                 questions.jsonl
                       |
                       v
              local OpenRouter run
                       |
                       v
              results/<run-id>.jsonl
                       |
                       v
          static GitHub Pages explorer
```

Only model-visible prompts cross the API boundary. Answer keys remain local and
are applied by the deterministic scorer after each response returns. Paid model
calls run only from an explicitly invoked local command, never from tests or CI.

The Observable Framework explorer opens on the leaderboard and provides a
searchable, filterable table of individual prompts and responses, available
provider-exposed reasoning, and exact-match results. Globally unique question
IDs support durable links such as
`questions.html?question=<question-id>&run=<run-id>` without generating a page
per question or response. The explorer does not require a database or backend.

The task profile owns the shared 128,000-token completion ceiling. Luna model
profiles contain model-specific settings only; the low, medium, and high profiles
set their corresponding reasoning efforts. The explorer labels runs by reasoning
effort so comparisons remain distinguishable even when they use the same model
ID.

The `openai-gpt-5.6-luna-medium-flex.yaml` model profile requests OpenAI's Flex
service tier through OpenRouter. Flex uses the ordinary request API but receives
the same 50% token discount as batch processing in exchange for higher latency
and lower availability. OpenRouter's asynchronous Batch API remains the CLI
default; use the Flex profile with `--direct` when Luna's advertised batch route
is unavailable.

## Data contracts

- [Generated question schema](schemas/question.schema.json)
- [Evaluation result schema](schemas/result.schema.json)

Generated questions are sorted by `question_id` and written as UTF-8 JSONL with
LF line endings. A question-set fingerprint is the lowercase SHA-256 digest of
the complete file. A per-question fingerprint is computed from compact JSON
with recursively sorted keys and no trailing newline.

Every multiple-choice prompt asks the model to end with a line containing only:

```text
FINAL: <choice-id>
```

The production VEP prompt also gives a concrete `FINAL: C07` formatting example
and explicitly prohibits adding the consequence name, punctuation, or other text
to that line. The scorer uses the last well-formed `FINAL:` line and compares its
choice ID exactly. A missing or unknown choice ID scores zero as a parse error.
API errors have a null score and make the run incomplete instead of counting as
scientific errors.

Each result snapshots the complete generated question alongside the raw
provider response. Its question fingerprint is recomputed from that embedded
snapshot, so historical runs remain valid and inspectable after the current
question set changes. Results also record the originating question-set size;
complete runs are accepted only when the embedded snapshots reproduce the set
digest. Provider-exposed reasoning is preserved when available and is otherwise
null; it is not presented as guaranteed access to a model's private chain of
thought.

JSON Schema cannot enforce that `answer_choice_id` occurs exactly once in
`choices`. The builder and tests must enforce this cross-field invariant, unique
choice IDs, and agreement between the structured choices and rendered prompt.

## Development

Python environments and dependencies are managed with
[`uv`](https://docs.astral.sh/uv/). Install the locked development environment,
rebuild the committed questions, and run the offline checks with:

```bash
uv sync --locked --group dev
npm ci
uv run --locked vepbench build
uv run --locked python scripts/validate_vep_consequence_artifacts.py
uv run --locked pytest
uv run --locked ruff check .
uv run --locked vepbench site --output /tmp/vepbench-site
```

The committed `data/sources/chr17-vep-consequences.jsonl`, its manifest, and
`benchmark/questions.jsonl` are generated; do not edit them directly. The first
real baseline is committed as
[`results/gpt-5.6-luna-medium-parallel-20260829.jsonl`](results/gpt-5.6-luna-medium-parallel-20260829.jsonl).
It contains all 190 OpenAI GPT-5.6 Luna responses with no API errors. Strict
exact-match scoring gives 15/190 (7.9%); 72 responses failed the required final
line format and therefore correctly score zero. That run used prompt template
version 1.0 and is retained as a historical result.

The current prompt v1.1 evaluation is committed as
[`results/gpt-5.6-luna-medium-prompt-v1.1-20260830.jsonl`](results/gpt-5.6-luna-medium-prompt-v1.1-20260830.jsonl).
It also contains all 190 responses with no API errors. Exact-match scoring gives
28/190 (14.7%), with one format failure and no length-limited completions. The
format-only prompt revision therefore reduced invalid final lines from 72 to 1;
the explorer shows only evaluations against the latest task version, while the
older result remains committed for historical audit. Small synthetic artifacts
under `tests/fixtures/` exist only for offline unit tests.

### Rebuilding the source data

Production preparation is an explicit networked operation and is not run in CI.
It needs an authenticated Hugging Face token in `HF_TOKEN` or the standard
local Hugging Face token cache, AWS credentials available to SkyPilot, and
substantially more memory than a normal development machine. Run:

```bash
bash scripts/run_prepare_vep_consequence_sky.sh
```

The launcher creates a named on-demand EC2 instance with automatic teardown,
passes `HF_TOKEN` as a SkyPilot secret, downloads only the pinned 383 MB chr17
Parquet file, and loads its five required columns with Polars. That file
contains 248,760,612 rows after decoding, so the checked-in SkyPilot task asks
for 256 GiB of RAM for the grouped candidate selection. It copies back only
the compact source JSONL and manifest, validates them locally, rebuilds the
questions, and terminates the instance.

Sampling is reproducible: preparation uses seed `2026082800` and a versioned
integer rank over `(chrom, pos, ref, alt)`, retains a bounded candidate pool per
source consequence, and sorts final records by source record ID. Reference
windows that are not exactly 1,001 uppercase A/C/G/T bases or whose center does
not match REF are skipped with deterministic backfill. Any underfilled class is
a hard error.

To run a reproducible evaluation, export an OpenRouter key locally and select a
versioned task profile and model profile. The task profile owns settings shared
across all model runs, including the maximum completion-token budget; model
profiles contain only model-specific parameters. Question paths, output paths,
run IDs, and secrets remain run-specific:

```bash
export OPENROUTER_API_KEY=...
uv run --locked vepbench evaluate \
  --task-profile configs/tasks/vep-most-severe-consequence.yaml \
  --model-profile configs/models/openai-gpt-5.6-luna-medium.yaml
```

For direct low- and high-reasoning comparison runs, use:

```bash
uv run --locked vepbench evaluate --direct \
  --task-profile configs/tasks/vep-most-severe-consequence.yaml \
  --model-profile configs/models/openai-gpt-5.6-luna-low.yaml

uv run --locked vepbench evaluate --direct \
  --task-profile configs/tasks/vep-most-severe-consequence.yaml \
  --model-profile configs/models/openai-gpt-5.6-luna-high.yaml
```

Evaluation submits the whole question set through OpenRouter's asynchronous Batch
API by default and records local state under `.vepbench/batches/`. Refresh a
submitted batch with `vepbench batch-status --state <state.json>`, then materialize
its canonical scored JSONL with `vepbench batch-collect --state <state.json>`.
Use `--direct` when a model has no live batch endpoint; it uses eight concurrent
requests by default while still writing results in deterministic question order.
Set `--concurrency 1` for a strictly sequential diagnostic. For an ad hoc model,
`--model provider/model-id` remains available with defaults of `temperature: 0.0`
and `max_tokens: 4096`. CLI generation arguments override profile values. Every
fully resolved non-secret request parameter is copied into each result record for
reproducibility even when its source of truth is the task profile.

On 2026-08-29, OpenRouter advertised a Luna batch model but its live Batch API
rejected both the documented base model ID and the `:batch` slug as lacking a
batch endpoint. The committed Luna baseline therefore used `--direct
--concurrency 16`; native batch submission remains the default for models whose
batch endpoint is live.

Direct evaluation is non-streaming and bounded-parallel. The command refuses to
overwrite an existing run, writes results in deterministic question order,
flushes one result at a time, and exits non-zero when an API error leaves the run
scientifically incomplete. It never sends answer keys to the provider. No test
or GitHub Actions workflow reads the API key or makes model calls.

GitHub Actions validates the generated fixtures, tests every committed result,
and compiles the Observable Pages artifact from `web/`, `benchmark/`, and
`results/`.
Implementation work is tracked in GitHub issues rather than as a roadmap here.

VEPBench is a public development set for now. If it becomes a formal benchmark,
a separate unpublished held-out set can be introduced without changing this
public workflow. A future ranking task can add a versioned question schema and a
deterministic Spearman-correlation scorer while keeping the same runner and
result-file layout.
