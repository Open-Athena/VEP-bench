---
title: Splicing (OpenSplice)
---

```js
import {
  assayFirstIndexedLink,
  cutoffRelationBadge,
  enhanceTableRowSelection,
  entriesForQuestions,
  entryForAnswer,
  formatCorrelation,
  formatInteger,
  knowledgeCutoffNote,
  outcomeBadge,
  questionRecord
} from "../components/vepbench.js";
import {
  artifactUrl,
  assayCutoffRelation,
  defaultQuestionForExplorer,
  fetchAnswerIfAvailable,
  fetchJson,
  fetchOutcomeIndex,
  modelSelectionRows,
  orderQuestionsForExplorer,
  rankingOutcomeMetrics,
  resultTypeLabel,
  runForTask
} from "../components/benchmark-data.js";

const config = await FileAttachment("../data/config.json").json();
const [runsState, questionState, metadataState] = await Promise.all([
  fetchJson(artifactUrl(config.data_base_url, "runs.json"))
    .then((document) => ({document, error: null}))
    .catch((error) => ({document: {runs: []}, error})),
  fetchJson(artifactUrl(config.data_base_url, "question-index.json"))
    .then((document) => ({document, error: null}))
    .catch((error) => ({document: {questions: []}, error})),
  FileAttachment("../data/question-metadata.json").json()
    .then((document) => ({document, error: null}))
    .catch((error) => ({document: {by_task_family: {}}, error}))
]);
const parameters = new URLSearchParams(location.search);
const requestedQuestionId = parameters.get("question");
const requestedRunId = parameters.get("run");
const taskFamily = "opensplice_snv";
const taskQuestions = orderQuestionsForExplorer(
  questionState.document.questions
).filter((question) => question.metadata.task_family === taskFamily);
const modelRows = modelSelectionRows(
  runsState.document.runs,
  runsState.document.leaderboard
).filter((row) => runForTask(row, taskFamily));
const requestedModelRow = modelRows.find((row) =>
  row.runs.some((run) => run.run_id === requestedRunId)
);
const missingModel = requestedRunId && !requestedModelRow
  ? {kind: "missing", label: `Unavailable model for run · ${requestedRunId}`, run_id: requestedRunId}
  : null;
const availableModelOptions = modelRows.map((row) => ({
  kind: "model",
  label: `${row.model_cell.model} · ${row.model_cell.provider} · ${
    runForTask(row, taskFamily)?.metrics?.mean_spearman_rho?.toFixed(3)
      ?? "—"
  } Spearman ρ`,
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
const resultLabel = (outcome) => outcome?.result_type !== undefined
  ? resultTypeLabel(outcome.result_type, outcome.correct)
  : outcome?.valid === true
    ? "Valid prediction"
    : outcome?.valid === false
      ? "Format failure"
      : "Not scored";
const questionEntries = entriesForQuestions(taskQuestions).map((entry) => {
  const displayMetadata = (
    metadataState.document.by_task_family
      ?.[taskFamily]
      ?.[entry.question.provenance.source_record_id]
  );
  return {
    ...entry,
    element: displayMetadata?.element ?? "—",
    assay_first_indexed: displayMetadata?.assay_first_indexed ?? null
  };
});
const requestedQuestion = questionEntries.find(
  (entry) => entry.question_id === requestedQuestionId
);
const knownQuestionIds = new Set(questionEntries.map((entry) => entry.question_id));
```

# Splicing (OpenSplice)

```js
if (runsState.error || questionState.error) {
  display(html`<div class="note" label="Published data unavailable">The official benchmark data could not be loaded from <code>versions/main</code>.</div>`);
}
if (metadataState.error) {
  display(html`<div class="note" label="Metadata unavailable">Question display metadata could not be loaded.</div>`);
}
```

Predict signed changes in alternative-exon inclusion for SNVs in complete
three-exon minigene cassettes, using exact construct sequence and assay context.

**${formatInteger(taskQuestions.length)} published exon panels** each contain 50 measured SNVs sampled as five candidates from each of ten effect-rank bins. Every prompt uses opaque candidate IDs and excludes source identity, outcomes, selection labels, genomic coordinates, and specialized predictor outputs. The primary score is mean within-exon Spearman correlation; mean Pearson correlation reports numerical agreement, valid-output rate reports strict JSON compliance, and invalid completed outputs contribute zero while remaining identifiable as format failures.

Exons were deliberately selected for large measured 5th-to-95th-percentile
effect range, and each panel is quantile-balanced. The task emphasizes effect
discrimination rather than the natural distribution of exon architectures or
effect sizes. Scores describe exon inclusion in a specific HEK293T minigene
reporter; they are not direct estimates of native-tissue splicing or clinical
pathogenicity.

Source data come from [Quarantani et al. (2026)](https://doi.org/10.64898/2026.05.22.727141) and the [OpenSplice Figshare v5 dataset](https://doi.org/10.6084/m9.figshare.32337414.v5).

## Questions

Inspect the exact prompt given to a model alongside its complete response.

```js
const controlsInput = Inputs.form({
  model: Inputs.select(modelOptions, {
    label: "Model",
    value: defaultModelOption,
    format: (option) => option.label
  }),
  search: Inputs.search(questionEntries, {
    label: "Find a question",
    placeholder: "Question ID or exon…",
    columns: ["question_id", "variant", "element"]
  }),
  element: Inputs.select([
    "All exons",
    ...[...new Set(questionEntries.map((entry) => entry.element))]
      .filter((element) => element !== "—")
      .sort()
  ], {label: "Exon"}),
  result: Inputs.select([
    "All results",
    "Valid prediction",
    "Format failure"
  ], {label: "Result"}),
  cutoff: Inputs.select([
    "All cutoff relations",
    "Before cutoff",
    "After cutoff",
    "Unknown"
  ], {label: "Cutoff relation"})
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
const selectedTaskRun = modelRow ? runForTask(modelRow, taskFamily) : null;
const knowledgeCutoff = selectedTaskRun?.model?.knowledge_cutoff ?? null;
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
      outcome
    ])
  )
);
const entriesWithResults = controls.search.map((entry) => {
  const outcome = outcomesByQuestion.get(entry.question_id);
  const cutoffRelation = assayCutoffRelation(entry.assay_first_indexed, knowledgeCutoff);
  return {
    ...entry,
    assay_first_indexed: entry.assay_first_indexed
      ? {
          ...entry.assay_first_indexed,
          cutoff_relation: cutoffRelation,
          knowledge_cutoff: knowledgeCutoff
        }
      : null,
    cutoff_relation: cutoffRelation,
    ...rankingOutcomeMetrics(outcome),
    outcome: modelRow
      ? (outcome ? resultLabel(outcome) : "Unavailable")
      : "Not evaluated"
  };
});
const visibleEntries = entriesWithResults.filter((entry) =>
  (
    controls.element === "All exons"
    || entry.element === controls.element
  )
  && (controls.result === "All results" || entry.outcome === controls.result)
  && (
    controls.cutoff === "All cutoff relations"
    || entry.cutoff_relation === controls.cutoff
  )
);
const currentQuestionId = new URLSearchParams(location.search).get("question");
const defaultQuestion = defaultQuestionForExplorer(visibleEntries, {
  currentQuestionId,
  knownQuestionIds,
  evaluatedQuestionIds: new Set(outcomesByQuestion.keys()),
  preferEvaluated: Boolean(modelRow)
});
```

```js
const questionTable = Inputs.table(visibleEntries, {
  columns: [
    "question_label",
    "element",
    "assay_first_indexed",
    "cutoff_relation",
    "spearman_rho",
    "pearson_r",
    "outcome"
  ],
  header: {
    question_label: "Question",
    element: "Exon",
    assay_first_indexed: "Assay first indexed",
    cutoff_relation: "Cutoff relation",
    spearman_rho: "Spearman ρ",
    pearson_r: "Pearson r",
    outcome: "Result"
  },
  format: {
    assay_first_indexed: assayFirstIndexedLink,
    cutoff_relation: cutoffRelationBadge,
    spearman_rho: formatCorrelation,
    pearson_r: formatCorrelation,
    outcome: outcomeBadge
  },
  width: {
    question_label: 70,
    element: 120,
    assay_first_indexed: 150,
    cutoff_relation: 115,
    spearman_rho: 90,
    pearson_r: 90,
    outcome: 115
  },
  multiple: false,
  required: false,
  value: defaultQuestion
});
enhanceTableRowSelection(questionTable);
```

```js
const selected = view(questionTable);
```

${controlsInput}

${knowledgeCutoffNote(selectedTaskRun)}

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
      element: selected.element
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
