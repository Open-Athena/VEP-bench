---
title: Fitness (SGE)
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
  questionDisplayMetadata,
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
const taskFamily = "sge";
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
  const displayMetadata = questionDisplayMetadata(
    entry.question, metadataState.document
  );
  return {
    ...entry,
    gene: displayMetadata?.element ?? "—",
    assay_first_indexed: displayMetadata?.assay_first_indexed ?? null
  };
});
const requestedQuestion = questionEntries.find(
  (entry) => entry.question_id === requestedQuestionId
);
const knownQuestionIds = new Set(questionEntries.map((entry) => entry.question_id));
```

# Fitness (SGE)

```js
if (runsState.error || questionState.error) {
  display(html`<div class="note" label="Published data unavailable">The official benchmark data could not be loaded from <code>versions/main</code>.</div>`);
}
if (metadataState.error) {
  display(html`<div class="note" label="Metadata unavailable">Question display metadata could not be loaded.</div>`);
}
```

Predict continuous functional damage for assayed variants in endogenous-locus saturation genome editing screens, using the gene, assay mechanism, and local exon sequence.

**${formatInteger(taskQuestions.length)} published gene panels** each contain 50 variants from one exon and exactly 100 unmarked flanking bases on each side. Version-2 panels sample five score-space bins across the full eligible allele population, with sparse-bin slots redistributed. The primary score is mean within-gene Spearman correlation; mean Pearson correlation reports numerical agreement, valid-output rate reports strict JSON compliance, and invalid completed outputs contribute zero while remaining identifiable as format failures.

Spearman measures ordering within each gene, not cross-assay calibration. SGE effects depend on the cellular system, engineered background, selection, timing, and treatment; they are not clinical classifications. One exon window omits distant gene and splice context, while score-space sampling does not reproduce the natural variant distribution.

Source data come from published saturation genome editing score sets in [MaveDB](https://www.mavedb.org/), cited as [Rubin et al. (2025)](https://doi.org/10.1186/s13059-025-03476-y).

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
    placeholder: "Question ID or gene…",
    columns: ["question_id", "variant", "gene"]
  }),
  gene: Inputs.select([
    "All genes",
    ...[...new Set(questionEntries.map((entry) => entry.gene))]
      .filter((gene) => gene !== "—")
      .sort()
  ], {label: "Gene"}),
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
  (controls.gene === "All genes" || entry.gene === controls.gene)
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
    "gene",
    "assay_first_indexed",
    "cutoff_relation",
    "spearman_rho",
    "pearson_r",
    "outcome"
  ],
  header: {
    question_label: "Question",
    gene: "Gene",
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
    gene: 120,
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
      element: selected.gene
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
