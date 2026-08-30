---
title: Consequence classification
---

```js
import {
  entriesForRun,
  formatInteger,
  formatRunLabel,
  outcomeBadge,
  questionRecord,
  runCorrect
} from "../components/vepbench.js";

const explorer = await FileAttachment("../data/explorer.json").json();
const orderedRuns = explorer.runs
  .filter((candidate) => candidate.current_question_set)
  .sort((a, b) => a.run_id.localeCompare(b.run_id));
const requestedRunId = new URLSearchParams(location.search).get("run");
const defaultRun = orderedRuns.find((candidate) => candidate.run_id === requestedRunId)
  ?? orderedRuns[0];
const consequenceCount = new Set(explorer.questions[0]?.choices.map((choice) => choice.text)).size;
```

*Assay 01 · sequence-context multiple choice*

# Consequence classification

Predict the Ensembl VEP most severe consequence for a human GRCh38 SNV using only its centered local sequence window and variant alleles.

## Task design

<div class="grid grid-cols-4">
  <div class="card">
    <h2>${formatInteger(explorer.questions.length)} questions</h2>
    <p>10 per consequence class</p>
  </div>
  <div class="card">
    <h2>${consequenceCount} consequence classes</h2>
    <p>balanced development set</p>
  </div>
  <div class="card">
    <h2>1,001 bp window</h2>
    <p>variant centered at position 501</p>
  </div>
  <div class="card">
    <h2>Exact-match scoring</h2>
    <p>last valid FINAL line</p>
  </div>
</div>

<div class="grid grid-cols-2">
  <div class="card">
    <h2>Model-visible inputs</h2>
    <dl>
      <div><dt>Reference</dt><dd>Homo sapiens GRCh38</dd></div>
      <div><dt>Region</dt><dd>Chromosome 17</dd></div>
      <div><dt>Variant</dt><dd>Centered SNV in local VCF</dd></div>
      <div><dt>VEP</dt><dd>release 109.1</dd></div>
      <div><dt>Flags</dt><dd><code>--most_severe --distance 1000</code></dd></div>
    </dl>
  </div>
  <div class="card">
    <h2>Interpretation</h2>
    <dl>
      <div><dt>Task version</dt><dd>1.1</dd></div>
      <div><dt>Questions</dt><dd>Public development set</dd></div>
      <div><dt>Annotations</dt><dd>Transcript annotations intentionally omitted</dd></div>
      <div><dt>Collapsed class</dt><dd>Intergenic, intronic, upstream, and downstream</dd></div>
      <div><dt>Explorer</dt><dd>Static; no backend or hidden state</dd></div>
    </dl>
  </div>
</div>

## Results and records

Choose a committed evaluation run, search or filter its task records, then select one row to inspect the full model-visible prompt and observed response.

```js
const run = view(Inputs.select(orderedRuns, {
  label: "Evaluation run",
  value: defaultRun,
  format: formatRunLabel
}));
```

```js
const entries = entriesForRun(run);
```

<div class="grid grid-cols-4">
  <div class="card"><h2>${runCorrect(run)} correct</h2><p>of ${formatInteger(run.questions_expected)} questions</p></div>
  <div class="card"><h2>${((runCorrect(run) / run.questions_expected) * 100).toFixed(1)}% accuracy</h2><p>deterministic exact match</p></div>
  <div class="card"><h2>${run.records_data.filter((record) => record.scoring.parse_error !== null).length} format failures</h2><p>completed but invalid final answer</p></div>
  <div class="card"><h2>${run.api_errors} API errors</h2><p>errors remain unscored</p></div>
</div>

```js
const filters = view(Inputs.form({
  search: Inputs.search(entries, {
    label: "Find a question",
    placeholder: "Question ID, variant, consequence, or choice…",
    columns: ["question_id", "variant", "answer", "prediction", "outcome"]
  }),
  outcome: Inputs.select(
    ["All outcomes", "Correct", "Incorrect", "Format failure", "API error"],
    {label: "Outcome"}
  ),
  consequence: Inputs.select([
    "All consequences",
    ...[...new Set(entries.map((entry) => entry.answer))].sort()
  ], {label: "Reference consequence"})
}));
```

```js
const visibleEntries = filters.search.filter((entry) =>
  (filters.outcome === "All outcomes" || entry.outcome === filters.outcome)
  && (filters.consequence === "All consequences" || entry.answer === filters.consequence)
);
```

<p class="muted">${formatInteger(visibleEntries.length)} records match the current filters · select a row using its checkbox</p>

```js
const selected = view(Inputs.table(visibleEntries, {
  columns: ["question_id", "variant", "answer", "prediction", "outcome"],
  header: {
    question_id: "Question",
    variant: "Source variant",
    answer: "Reference consequence",
    prediction: "Model prediction",
    outcome: "Outcome"
  },
  format: {outcome: outcomeBadge},
  multiple: false,
  required: false,
  value: visibleEntries[0]
}));
```

${selected ? questionRecord(selected) : html`<div class="note" label="No record selected">Select a question row above to inspect it.</div>`}
