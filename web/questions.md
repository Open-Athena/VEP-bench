---
title: Questions
---

```js
import {
  entriesForQuestions,
  entryForAnswer,
  formatInteger,
  formatRunLabel,
  outcomeBadge,
  questionRecord
} from "./components/vepbench.js";
import {
  artifactUrl,
  fetchAnswer,
  fetchJson,
  fetchOutcomeIndex,
  groupCurrentRuns
} from "./components/benchmark-data.js";

const config = await FileAttachment("data/config.json").json();
const [runsState, questionState] = await Promise.all([
  fetchJson(artifactUrl(config.data_base_url, "runs.json"))
    .then((document) => ({document, error: null}))
    .catch((error) => ({document: {runs: []}, error})),
  fetchJson(artifactUrl(config.data_base_url, "question-index.json"))
    .then((document) => ({document, error: null}))
    .catch((error) => ({document: {questions: []}, error}))
]);
const parameters = new URLSearchParams(location.search);
const requestedQuestionId = parameters.get("question");
const requestedRunId = parameters.get("run");
const currentRuns = groupCurrentRuns(runsState.document.runs);
const requestedRun = currentRuns.find((candidate) => candidate.run_id === requestedRunId);
const missingRun = requestedRunId && !requestedRun
  ? {kind: "missing", label: `Unavailable run · ${requestedRunId}`, run_id: requestedRunId}
  : null;
const availableRunOptions = currentRuns.map((run) => ({
  kind: "run",
  label: `${formatRunLabel(run)} · ${run.run_id}`,
  run
}));
const noRuns = {kind: "empty", label: "No complete evaluation runs available"};
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
const resultLabel = (correct) => correct === true
  ? "Correct"
  : correct === false
    ? "Incorrect"
    : "Not scored";
const questionEntries = entriesForQuestions(questionState.document.questions).map((entry) => ({
  ...entry,
  task: taskName(entry.question.metadata.task_family)
}));
const requestedQuestion = questionEntries.find(
  (entry) => entry.question_id === requestedQuestionId
);
```

# Questions

Inspect a benchmark question alongside one lazily loaded response from a selected evaluation run.

```js
if (runsState.error || questionState.error) {
  display(html`<div class="note" label="Published data unavailable">The official benchmark data could not be loaded from <code>versions/main</code>.</div>`);
}
```

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
    "Incorrect"
  ], {label: "Result"})
});
controlsInput.style.display = "flex";
controlsInput.style.flexWrap = "wrap";
controlsInput.style.gap = "0.75rem";
controlsInput.style.alignItems = "end";
controlsInput.style.width = "100%";
for (const input of controlsInput.children) {
  input.style.display = "flex";
  input.style.flexDirection = "column";
  input.style.flex = "1 1 8rem";
  input.style.gap = "0.25rem";
  input.style.minWidth = "8rem";
  input.style.margin = "0";
  const control = input.lastElementChild;
  if (control) control.style.width = "100%";
}
controlsInput.children[1].style.flex = "1.5 1 15rem";
controlsInput.children[1].style.minWidth = "15rem";
```

```js
const controls = view(controlsInput);
```

```js
const runOption = controls.run;
const run = runOption.kind === "run" ? runOption.run : null;
const outcomeState = run?.outcome_index_path
  ? await fetchOutcomeIndex(config.data_base_url, run)
      .then((value) => ({value, error: null}))
      .catch((error) => ({value: null, error}))
  : {value: null, error: null};
const outcomesByQuestion = new Map(
  (outcomeState.value?.outcomes ?? []).map((outcome) => [
    outcome.question_id,
    resultLabel(outcome.correct)
  ])
);
const entriesWithResults = controls.search.map((entry) => ({
  ...entry,
  outcome: run
    ? (outcomesByQuestion.get(entry.question_id) ?? "Unavailable")
    : "Not evaluated"
}));
const visibleEntries = entriesWithResults.filter((entry) =>
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
    outcome: outcomeBadge
  },
  width: {
    question_label: 70,
    task: 210,
    variant: 150,
    answer: 280,
    outcome: 100
  },
  multiple: false,
  required: false,
  value: defaultQuestion
});
```

```js
const selected = view(questionTable);
```

${controlsInput}

${questionTable}

<p class="muted" style="margin: 0.75rem 0 0.25rem">${formatInteger(visibleEntries.length)} questions match the current filters · select one row to inspect it</p>

```js
const selectedIndex = selected
  ? questionEntries.findIndex((entry) => entry.question_id === selected.question_id)
  : -1;
const answerState = selected && run
  ? await fetchAnswer(config.data_base_url, run, selected.question_id)
      .then((value) => ({value, error: null}))
      .catch((error) => ({value: null, error}))
  : {value: null, error: null};
const rawArchiveUrl = answerState.value
  ? artifactUrl(config.data_base_url, answerState.value.raw_archive_path)
  : null;
const recordEntry = selected
  ? entryForAnswer(
      selected.question,
      selectedIndex,
      answerState.value,
      run,
      rawArchiveUrl
    )
  : null;
```

```js
if (requestedQuestionId && !requestedQuestion && !selected) {
  display(html`<div class="note" label="Question not found">No benchmark question has ID <code>${requestedQuestionId}</code>.</div>`);
}
if (runOption.kind === "missing") {
  display(html`<div class="note" label="Response not found">No complete current evaluation has run ID <code>${runOption.run_id}</code>.</div>`);
}
if (run && (!run.outcome_index_path || outcomeState.error)) {
  display(html`<div class="note" label="Results unavailable">The result column could not be loaded for this evaluation run.</div>`);
}
if (answerState.error) {
  display(html`<div class="note" label="Response unavailable">The selected answer object could not be loaded.</div>`);
}
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
