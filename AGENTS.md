# Repository instructions

These instructions apply to the entire VEPBench repository.

## Product boundaries

- Keep the MVP limited to generated multiple-choice questions, deterministic
  exact-match scoring, one OpenRouter integration, committed JSONL artifacts,
  and a static results explorer.
- Do not introduce a database, backend service, authentication, submissions
  system, provider abstraction, Hugging Face dependency, LLM judge, batch
  inference, or statistical framework unless a scoped issue explicitly requires
  it.
- Treat the committed questions and answers as a public development set. Do not
  add contamination defenses or hidden-test infrastructure without an explicit
  project decision.

## Python tooling

- Use `uv` for Python environment and dependency management.
- Run Python tools and project commands with `uv run`.
- Run offline checks with `uv run --locked pytest` and
  `uv run --locked ruff check .`.
- Commit `pyproject.toml` and `uv.lock` when Python dependencies are introduced.
- Do not add parallel `pip`, Poetry, Pipenv, or Conda setup instructions.

## Cost and secrets

- Never make paid or live model API calls from tests or CI.
- Keep evaluation an explicit local action. Tests must use an injected fake or
  offline mock transport.
- Read the OpenRouter credential only from `OPENROUTER_API_KEY`. Never write
  credentials into configuration, output, logs, fixtures, or version control.
- GitHub Actions may validate artifacts and deploy the static site, but must not
  receive an OpenRouter secret or run evaluations.

## Data invariants

- Treat `schemas/question.schema.json` and `schemas/result.schema.json` as the
  public on-disk contracts.
- Version a schema when a change is not backward-compatible, and update its
  examples and tests in the same change.
- Question generation must be deterministic. Sort records by `question_id` and
  write UTF-8 JSONL with LF line endings.
- Enforce cross-field invariants that JSON Schema cannot express: choice IDs are
  unique, `answer_choice_id` identifies exactly one choice, and the rendered
  prompt agrees with the structured choices.
- Parse multiple-choice answers only from the last well-formed
  `FINAL: <choice-id>` line. Do not add heuristic or model-based grading.
- Preserve the complete provider response, final content, nullable
  provider-exposed reasoning, usage, finish reason, and non-secret request
  parameters in result records.
- Record API failures with a null score and mark the run incomplete. A completed
  response with a missing or invalid final answer receives an exact-match score
  of zero and a parse error.
- Pin every result to the complete question-set digest and size, plus its
  individual question digest. Keep the full generated question in the result
  snapshot so historical runs remain independently inspectable.

## Change discipline

- Keep changes focused on the active issue and prefer the smallest implementation
  that satisfies its acceptance criteria.
- Apply the `agent-generated` label to every GitHub issue or pull request created
  by an agent. Create the label first if the repository does not already have it.
- Add offline tests for deterministic generation, parsing, scoring, and schema
  compatibility whenever those behaviors change.
- Do not hand-edit generated benchmark or result artifacts when a generator can
  reproduce the change.
