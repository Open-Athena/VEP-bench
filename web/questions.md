---
title: Questions
---

```js
import {
  entriesForRun,
  formatInteger,
  formatRunLabel,
  outcomeBadge,
  questionRecord,
  runCorrect,
  runTemplateVersion
} from "./components/vepbench.js";

const explorer = await FileAttachment("data/explorer.json").json();
const orderedRuns = [...explorer.runs].sort((a, b) =>
  Number(b.current_question_set) - Number(a.current_question_set)
  || b.run_id.localeCompare(a.run_id)
);
const requestedRunId = new URLSearchParams(location.search).get("run");
const defaultRun = orderedRuns.find((candidate) => candidate.run_id === requestedRunId)
  ?? orderedRuns.find((candidate) => candidate.current_question_set)
  ?? orderedRuns[0];
const runInput = Inputs.select(orderedRuns, {
  label: "Evaluation run",
  value: defaultRun,
  format: formatRunLabel
});
const run = Generators.input(runInput);
const entries = entriesForRun(run);
const searchInput = Inputs.search(entries, {
  label: "Find a question",
  placeholder: "Question ID, variant, consequence, or choice…",
  columns: ["question_id", "variant", "answer", "prediction", "outcome"]
});
const searchedEntries = Generators.input(searchInput);
const outcomeOptions = ["All outcomes", "Correct", "Incorrect", "Format failure", "API error"];
const outcomeInput = Inputs.select(outcomeOptions, {label: "Outcome"});
const outcomeFilter = Generators.input(outcomeInput);
const consequenceOptions = [
  "All consequences",
  ...[...new Set(entries.map((entry) => entry.answer))].sort()
];
const consequenceInput = Inputs.select(consequenceOptions, {label: "Reference consequence"});
const consequenceFilter = Generators.input(consequenceInput);
const visibleEntries = searchedEntries.filter((entry) =>
  (outcomeFilter === "All outcomes" || entry.outcome === outcomeFilter)
  && (consequenceFilter === "All consequences" || entry.answer === consequenceFilter)
);
const tableInput = Inputs.table(visibleEntries, {
  columns: ["question_id", "variant", "answer", "prediction", "outcome"],
  header: {
    question_id: "Question",
    variant: "Source variant",
    answer: "Reference consequence",
    prediction: "Model prediction",
    outcome: "Outcome"
  },
  format: {outcome: outcomeBadge},
  width: {question_id: 235, variant: 150, answer: 250, prediction: 250, outcome: 115},
  align: {outcome: "left"},
  layout: "fixed",
  rows: 14,
  multiple: false,
  required: false,
  value: visibleEntries[0]
});
const selected = Generators.input(tableInput);
```

*Assay records · prompts and responses*

# Questions

Choose a committed evaluation run, search or filter its question records, then select one row to inspect the full model-visible prompt and observed response.

<div class="grid grid-cols-4">
  <div class="card"><h2>${run.current_question_set ? "Current" : "Historical"} prompt</h2><p>version ${runTemplateVersion(run)}</p></div>
  <div class="card"><h2>${formatInteger(run.records)} responses</h2><p>of ${formatInteger(run.questions_expected)} expected</p></div>
  <div class="card"><h2>${runCorrect(run)} correct</h2><p>deterministic exact match</p></div>
  <div class="card"><h2>${run.api_errors} API errors</h2><p>errors remain unscored</p></div>
</div>

<div class="grid grid-cols-4">
  ${runInput}
  ${searchInput}
  ${outcomeInput}
  ${consequenceInput}
</div>

<p class="muted">${formatInteger(visibleEntries.length)} records match the current filters · select a row using its checkbox</p>

${tableInput}

${selected ? questionRecord(selected) : html`<div class="note" label="No record selected">Select a question row above to inspect it.</div>`}
