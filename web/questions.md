---
title: Questions
---

```js
import {
  entriesForQuestions,
  entryForAnswer,
  formatInteger,
  formatPercent,
  outcomeBadge,
  questionRecord
} from "./components/vepbench.js";
import {
  artifactUrl,
  fetchAnswerIfAvailable,
  fetchJson,
  fetchOutcomeIndex,
  modelSelectionRows,
  orderQuestionsForExplorer,
  runForTask
} from "./components/benchmark-data.js";

const config = await FileAttachment("data/config.json").json();
const [runsState, questionState, metadataState] = await Promise.all([
  fetchJson(artifactUrl(config.data_base_url, "runs.json"))
    .then((document) => ({document, error: null}))
    .catch((error) => ({document: {runs: []}, error})),
  fetchJson(artifactUrl(config.data_base_url, "question-index.json"))
    .then((document) => ({document, error: null}))
    .catch((error) => ({document: {questions: []}, error})),
  FileAttachment("data/question-metadata.json").json()
    .then((document) => ({document, error: null}))
    .catch((error) => ({document: {by_task_family: {}}, error}))
]);
const parameters = new URLSearchParams(location.search);
const requestedQuestionId = parameters.get("question");
const requestedRunId = parameters.get("run");
const modelRows = modelSelectionRows(
  runsState.document.runs,
  runsState.document.leaderboard
);
const requestedModelRow = modelRows.find((row) =>
  row.runs.some((run) => run.run_id === requestedRunId)
);
const missingModel = requestedRunId && !requestedModelRow
  ? {kind: "missing", label: `Unavailable model for run · ${requestedRunId}`, run_id: requestedRunId}
  : null;
const availableModelOptions = modelRows.map((row) => ({
  kind: "model",
  label: `${row.model_cell.model} · ${row.model_cell.provider} · ${formatPercent(row.accuracy)} overall`,
  row
}));
const noModels = {kind: "empty", label: "No complete model configurations available"};
const modelOptions = [
  ...(missingModel ? [missingModel] : []),
  ...availableModelOptions,
  ...(!missingModel && !availableModelOptions.length ? [noModels] : [])
];
const defaultModelOption = modelOptions.find(
  (option) => option.kind === "model" && option.row === requestedModelRow
) ?? missingModel ?? availableModelOptions[0] ?? noModels;
const taskName = (taskFamily) => ({
  clinvar: "ClinVar",
  vep_most_severe_consequence: "Consequence classification"
})[taskFamily] ?? taskFamily;
const resultLabel = (correct) => correct === true
  ? "Correct"
  : correct === false
    ? "Incorrect"
    : "Not scored";
const questionEntries = entriesForQuestions(
  orderQuestionsForExplorer(questionState.document.questions)
).map((entry) => ({
  ...entry,
  task: taskName(entry.question.metadata.task_family),
  consequence: metadataState.document.by_task_family
    ?.[entry.question.metadata.task_family]
    ?.[entry.question.provenance.source_record_id]
    ?.consequence ?? "—"
}));
const requestedQuestion = questionEntries.find(
  (entry) => entry.question_id === requestedQuestionId
);
```

# Questions

Inspect a benchmark question alongside the matching response from a selected model.

```js
if (runsState.error || questionState.error) {
  display(html`<div class="note" label="Published data unavailable">The official benchmark data could not be loaded from <code>versions/main</code>.</div>`);
}
if (metadataState.error) {
  display(html`<div class="note" label="Metadata unavailable">Question consequence metadata could not be loaded.</div>`);
}
```

```js
const controlsInput = Inputs.form({
  model: Inputs.select(modelOptions, {
    label: "Model",
    value: defaultModelOption,
    format: (option) => option.label
  }),
  search: Inputs.search(questionEntries, {
    label: "Find a question",
    placeholder: "Question ID, variant, consequence, or task…",
    columns: ["question_id", "variant", "consequence", "task"]
  }),
  task: Inputs.select([
    "All tasks",
    ...new Set(questionEntries.map((entry) => entry.task))
  ], {label: "Task"}),
  consequence: Inputs.select([
    "All consequences",
    ...[...new Set(questionEntries.map((entry) => entry.consequence))]
      .filter((consequence) => consequence !== "—")
      .sort()
  ], {label: "Consequence"}),
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
const modelOption = controls.model;
const modelRow = modelOption.kind === "model" ? modelOption.row : null;
const selectedModelRuns = modelRow?.runs ?? [];
const outcomeStates = await Promise.all(selectedModelRuns.map(async (run) => ({
  run,
  ...(run.outcome_index_path
    ? await fetchOutcomeIndex(config.data_base_url, run)
        .then((value) => ({value, error: null}))
        .catch((error) => ({value: null, error}))
    : {value: null, error: new Error("Run has no outcome index")})
})));
const outcomeStateByRun = new Map(
  outcomeStates.map((state) => [state.run.run_id, state])
);
const outcomesByQuestion = new Map(
  outcomeStates.flatMap((state) =>
    (state.value?.outcomes ?? []).map((outcome) => [
      outcome.question_id,
      resultLabel(outcome.correct)
    ])
  )
);
const entriesWithResults = controls.search.map((entry) => ({
  ...entry,
  outcome: modelRow
    ? (outcomesByQuestion.get(entry.question_id) ?? "Unavailable")
    : "Not evaluated"
}));
const visibleEntries = entriesWithResults.filter((entry) =>
  (controls.task === "All tasks" || entry.task === controls.task)
  && (
    controls.consequence === "All consequences"
    || entry.consequence === controls.consequence
  )
  && (controls.result === "All results" || entry.outcome === controls.result)
);
const defaultQuestion = requestedQuestionId
  ? (visibleEntries.find((entry) => entry.question_id === requestedQuestionId) ?? null)
  : (
      (modelRow
        ? visibleEntries.find((entry) => outcomesByQuestion.has(entry.question_id))
        : null)
      ?? visibleEntries[0]
    );
```

```js
const questionTable = Inputs.table(visibleEntries, {
  columns: ["question_label", "task", "variant", "consequence", "outcome"],
  header: {
    question_label: "Question",
    task: "Task",
    variant: "Source record",
    consequence: "Consequence",
    outcome: "Result"
  },
  format: {
    outcome: outcomeBadge
  },
  width: {
    question_label: 70,
    task: 190,
    variant: 170,
    consequence: 230,
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
const run = selected && modelRow
  ? runForTask(modelRow, selected.question.metadata.task_family)
  : null;
const outcomeState = run
  ? (outcomeStateByRun.get(run.run_id) ?? {value: null, error: null})
  : {value: null, error: null};
const answerState = selected && run
  ? await fetchAnswerIfAvailable(
      config.data_base_url,
      run,
      selected.question_id,
      outcomeState.value
    )
      .then((value) => ({value, error: null}))
      .catch((error) => ({value: null, error}))
  : {value: null, error: null};
const rawArchiveUrl = answerState.value
  ? artifactUrl(config.data_base_url, answerState.value.raw_archive_path)
  : null;
const recordEntry = selected
  ? {
      ...entryForAnswer(
        selected.question,
        selectedIndex,
        answerState.value,
        run,
        rawArchiveUrl
      ),
      consequence: selected.consequence
    }
  : null;
```

```js
if (requestedQuestionId && !requestedQuestion && !selected) {
  display(html`<div class="note" label="Question not found">No benchmark question has ID <code>${requestedQuestionId}</code>.</div>`);
}
if (modelOption.kind === "missing") {
  display(html`<div class="note" label="Model not found">No complete current model configuration contains run ID <code>${modelOption.run_id}</code>.</div>`);
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
    modelOption.kind === "missing" ? modelOption.run_id : null
  );
  if (questionId) nextParameters.set("question", questionId);
  if (runId) nextParameters.set("run", runId);
  const query = nextParameters.toString();
  const nextUrl = `${location.pathname}${query ? `?${query}` : ""}${location.hash}`;
  const currentUrl = `${location.pathname}${location.search}${location.hash}`;
  if (nextUrl !== currentUrl) history.replaceState(null, "", nextUrl);
}
```
