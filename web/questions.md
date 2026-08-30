---
title: Questions
---

```js
import {
  entriesForQuestions,
  entryForResult,
  formatInteger,
  formatRunLabel,
  outcomeBadge,
  questionRecord
} from "./components/vepbench.js";
import {groupCurrentRuns} from "./components/benchmark-data.js";

const explorer = await FileAttachment("data/explorer.json").json();
const parameters = new URLSearchParams(location.search);
const requestedQuestionId = parameters.get("question");
const requestedRunId = parameters.get("run");
const currentRuns = groupCurrentRuns(explorer.task_runs);
const requestedRun = currentRuns.find((candidate) => candidate.run_id === requestedRunId);
const missingRun = requestedRunId && !requestedRun
  ? {kind: "missing", label: `Unavailable run · ${requestedRunId}`, run_id: requestedRunId}
  : null;
const availableRunOptions = currentRuns.map((run) => ({
  kind: "run",
  label: `${formatRunLabel(run)} · ${run.run_id}`,
  run
}));
const noRuns = {kind: "empty", label: "No evaluation runs available"};
const runOptions = [
  ...(missingRun ? [missingRun] : []),
  ...availableRunOptions,
  ...(!missingRun && !availableRunOptions.length ? [noRuns] : [])
];
const defaultRunOption = runOptions.find(
  (option) => option.kind === "run" && option.run === requestedRun
) ?? missingRun ?? availableRunOptions[0] ?? noRuns;
const taskName = (taskFamily) => taskFamily === "vep_most_severe_consequence"
  ? "Consequence classification"
  : taskFamily;
const questionEntries = entriesForQuestions(explorer.questions).map((entry) => ({
  ...entry,
  task: taskName(entry.question.metadata.task_family)
}));
const requestedQuestion = questionEntries.find(
  (entry) => entry.question_id === requestedQuestionId
);
```

# Questions

Inspect a benchmark question alongside a response from a selected evaluation run.

```js
const controlsInput = Inputs.form({
  run: Inputs.select(runOptions, {
    label: "Evaluation run",
    value: defaultRunOption,
    format: (option) => option.label
  }),
  search: Inputs.search(questionEntries, {
    label: "Find a question",
    placeholder: "Question ID, variant, consequence, or choice…",
    columns: ["question_id", "variant", "answer", "task"]
  }),
  task: Inputs.select([
    "All tasks",
    ...[...new Set(questionEntries.map((entry) => entry.task))].sort()
  ], {label: "Task"}),
  consequence: Inputs.select([
    "All consequences",
    ...[...new Set(questionEntries.map((entry) => entry.answer))].sort()
  ], {label: "Reference consequence"}),
  result: Inputs.select([
    "All results",
    "Correct",
    "Incorrect",
    "Format failure"
  ], {label: "Result"})
});
controlsInput.style.display = "grid";
controlsInput.style.gridTemplateColumns = "repeat(auto-fit, minmax(10rem, 1fr))";
controlsInput.style.gap = "0.75rem";
controlsInput.style.alignItems = "end";
controlsInput.style.width = "100%";
for (const input of controlsInput.children) {
  input.style.display = "flex";
  input.style.flexDirection = "column";
  input.style.gap = "0.25rem";
  input.style.minWidth = "0";
  input.style.margin = "0";
  const control = input.lastElementChild;
  if (control) control.style.width = "100%";
}
```

```js
const controls = Generators.input(controlsInput);
```

```js
const runOption = controls.run;
const run = runOption.kind === "run" ? runOption.run : null;
```

```js
const evaluatedEntries = controls.search.map((entry) => {
  const resultIndex = run
    ? run.records_data.findIndex((record) => record.question_id === entry.question_id)
    : -1;
  return resultIndex < 0
    ? entry
    : {
      ...entry,
      ...entryForResult(run.records_data[resultIndex], resultIndex, run),
      question_label: entry.question_label,
      task: entry.task
    };
});
const visibleEntries = evaluatedEntries.filter((entry) =>
  (controls.task === "All tasks" || entry.task === controls.task)
  && (controls.consequence === "All consequences" || entry.answer === controls.consequence)
  && (controls.result === "All results" || entry.outcome === controls.result)
);
const defaultQuestion = requestedQuestionId
  ? (visibleEntries.find((entry) => entry.question_id === requestedQuestionId) ?? null)
  : visibleEntries[0];
```

```js
const questionTable = Inputs.table(visibleEntries, {
  columns: ["question_label", "task", "variant", "answer", "outcome"],
  header: {
    question_label: "Question",
    task: "Task",
    variant: "Source variant",
    answer: "Reference consequence",
    outcome: "Result"
  },
  format: {
    outcome: (value) => outcomeBadge(value)
  },
  width: {
    question_label: 70,
    task: 210,
    variant: 150,
    answer: 280,
    outcome: 110
  },
  multiple: false,
  required: false,
  value: defaultQuestion
});
const selected = Generators.input(questionTable);
```

<div class="card">
  ${controlsInput}
  <p class="muted" style="margin: 0.75rem 0 0.25rem">${formatInteger(visibleEntries.length)} questions match the current filters · select one row to inspect it</p>
  ${questionTable}
</div>

```js
const recordEntry = selected
  ? {...selected, run}
  : null;
```

```js
if (requestedQuestionId && !requestedQuestion && !selected) {
  display(html`<div class="note" label="Question not found">No benchmark question has ID <code>${requestedQuestionId}</code>.</div>`);
}
```

```js
if (runOption.kind === "missing") {
  display(html`<div class="note" label="Response not found">No complete current evaluation has run ID <code>${runOption.run_id}</code>.</div>`);
}
```

```js
display(recordEntry
  ? questionRecord(recordEntry)
  : html`<div class="note" label="No question selected">Select a question row above to inspect it.</div>`
);
```

```js
{
  const nextParameters = new URLSearchParams();
  const questionId = selected?.question_id ?? (
    requestedQuestionId && !requestedQuestion ? requestedQuestionId : null
  );
  const runId = run?.run_id ?? (
    runOption.kind === "missing" ? runOption.run_id : null
  );
  if (questionId) nextParameters.set("question", questionId);
  if (runId) nextParameters.set("run", runId);
  const query = nextParameters.toString();
  const nextUrl = `${location.pathname}${query ? `?${query}` : ""}${location.hash}`;
  const currentUrl = `${location.pathname}${location.search}${location.hash}`;
  if (nextUrl !== currentUrl) history.replaceState(null, "", nextUrl);
}
```
