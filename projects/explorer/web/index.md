---
title: VEP-bench
---

```js
import {
  formatCost,
  formatInteger,
  formatKnowledgeCutoff,
  formatPercent
} from "./components/vepbench.js";
import {
  artifactUrl,
  displayScore,
  fetchJson,
  leaderboardRowsForScope,
  orderTaskFamilies,
  supportsOverallLeaderboard
} from "./components/benchmark-data.js";

const config = await FileAttachment("data/config.json").json();
const runsState = await fetchJson(artifactUrl(config.data_base_url, "runs.json"))
  .then((document) => ({document, error: null}))
  .catch((error) => ({document: {runs: []}, error}));
const aggregation = runsState.document.leaderboard;
const taskLabels = {
  sge: "Fitness (SGE)",
  satmut_mpra: "Expression (satMutMPRA)",
  opensplice_snv: "Splicing (OpenSplice)"
};
const taskName = (taskFamily) => taskLabels[taskFamily] ?? taskFamily;
const publishedTaskFamilies = orderTaskFamilies([
  ...new Set(
    (aggregation?.evaluation_profiles ?? []).map((profile) => profile.task_family)
  )
]);
const allTasksAvailable = supportsOverallLeaderboard(aggregation);
const taskOptions = [
  ...(allTasksAvailable
    ? [null]
    : []),
  ...(
    publishedTaskFamilies.length
      ? publishedTaskFamilies
      : ["sge", "satmut_mpra", "opensplice_snv"]
  )
];
const taskInput = Inputs.select(taskOptions, {
  label: "Task",
  value: allTasksAvailable ? null : taskOptions[0],
  format: (taskFamily) => taskFamily === null ? "All tasks" : taskName(taskFamily)
});
taskInput.style.maxWidth = "18rem";
taskInput.style.display = "inline-grid";
taskInput.style.verticalAlign = "top";
const scoreMetricOptions = [
  {key: "spearman", label: "Spearman"},
  {key: "pearson", label: "Pearson"}
];
const scoreMetricInput = Inputs.select(scoreMetricOptions, {
  label: "Metric",
  value: scoreMetricOptions[0],
  format: (option) => option.label
});
scoreMetricInput.style.maxWidth = "18rem";
scoreMetricInput.style.display = "inline-grid";
scoreMetricInput.style.marginLeft = "1rem";
scoreMetricInput.style.verticalAlign = "top";
const comparisonOptions = [
  {key: "cost", label: "Total cost", axis_label: "Total cost (USD)"},
  {key: "tokens", label: "Total tokens", axis_label: "Total tokens"}
];
const comparisonInput = Inputs.select(comparisonOptions, {
  label: "Compare score against",
  value: comparisonOptions[0],
  format: (option) => option.label
});
comparisonInput.style.maxWidth = "22rem";
```

# VEP-bench

VEP-bench is a public benchmark of language models' native ability to predict
genetic variant effects. Models answer without internet access or tools, and
every response and deterministic score can be inspected.

## Leaderboard

```js
const selectedTaskFamily = view(taskInput);
const selectedScoreMetric = view(scoreMetricInput);
```

```js
const selectedTaskLabel = selectedTaskFamily === null
  ? "All tasks"
  : taskName(selectedTaskFamily);
const scoreMetric = selectedScoreMetric?.key
  ?? scoreMetricInput.value?.key
  ?? "spearman";
const scoreMetricLabel = scoreMetricOptions.find(
  (option) => option.key === scoreMetric
)?.label ?? "Spearman";
```

```js
display(html`<nav aria-label="Leaderboard controls" style="display: flex; justify-content: flex-end; flex-wrap: wrap; margin: 0.75rem 0;">
  ${taskInput}${scoreMetricInput}
</nav>`);
```

```js
if (runsState.error) {
  display(html`<div class="note" label="Published data unavailable">The official benchmark data could not be loaded from <code>versions/main</code>.</div>`);
} else if (selectedTaskFamily !== null && !aggregation?.evaluation_profiles?.some(
  (profile) => profile.task_family === selectedTaskFamily
)) {
  display(html`<div class="note" label="Task unavailable">The current official version does not contain the selected task's evaluation profile.</div>`);
}
```

```js
const rows = leaderboardRowsForScope(
  runsState.document.runs,
  aggregation,
  selectedTaskFamily,
  scoreMetric
);
const formatScore = (value) => formatPercent(displayScore(value));
```

${selectedTaskFamily === null
  ? `Showing the macro-average ${scoreMetricLabel} correlation across tasks.`
  : `Showing the mean ${scoreMetricLabel} correlation for ${selectedTaskLabel}.`}

```js
const tableRows = rows.map((row) => ({
  model: row.model_cell.model,
  score: displayScore(row.score),
  knowledge_cutoff: row.knowledge_cutoff,
  tokens: row.tokens,
  cost: row.cost,
  family: row.family
}));
function scoreBar(value) {
  if (!Number.isFinite(value)) return "—";
  const width = Math.max(0, Math.min(1, value)) * 100;
  return html`<span class="vepbench-score-cell" style=${`--vepbench-score-width: ${width}%`}>
    <span class="vepbench-score-bar" aria-hidden="true"></span>
    <span class="vepbench-score-value">${formatScore(value)}</span>
  </span>`;
}
const leaderboardTable = Inputs.table(tableRows, {
  columns: ["model", "score", "knowledge_cutoff", "tokens", "cost"],
  header: {
    model: "Model",
    score: "Score",
    knowledge_cutoff: "Knowledge cutoff",
    tokens: "Tokens",
    cost: "Cost"
  },
  format: {
    score: scoreBar,
    knowledge_cutoff: formatKnowledgeCutoff,
    tokens: (value) => value === null ? "—" : formatInteger(value),
    cost: formatCost
  },
  align: {
    score: "right",
    tokens: "right",
    cost: "right"
  },
  width: {
    model: 240,
    score: 100,
    knowledge_cutoff: 130,
    tokens: 100,
    cost: 90
  },
  rows: Math.max(2, tableRows.length),
  sort: "score",
  reverse: true,
  select: false
});
```

```js
display(html`<div class="card">${leaderboardTable}</div>`);
```

## Score by cost and token usage

Each line connects evaluated configurations from the same model family. Use the selector to compare the selected task's score with total run cost or total token usage.

```js
const selectedComparison = view(comparisonInput);
```

```js
const metric = selectedComparison?.key ?? comparisonInput.value?.key ?? "cost";
const metricLabel = comparisonOptions.find((option) => option.key === metric)?.axis_label
  ?? "Total cost (USD)";
const efficiencyRows = tableRows
  .filter((row) => row.score !== null && row[metric] !== null)
  .toSorted((left, right) => left[metric] - right[metric]);
function scoreEfficiencyPlot(data, {width}) {
  return Plot.plot({
    width,
    height: 410,
    marginLeft: 64,
    marginBottom: 56,
    x: {
      label: metricLabel,
      grid: true,
      nice: true,
      tickFormat: metric === "cost"
        ? (value) => formatCost(value)
        : (value) => Intl.NumberFormat("en-US", {notation: "compact"}).format(value)
    },
    y: {
      label: "Score",
      grid: true,
      tickFormat: formatScore
    },
    color: {legend: true, label: "Model family"},
    marks: [
      Plot.line(data, {
        x: (row) => row[metric],
        y: "score",
        z: "family",
        stroke: "family",
        strokeWidth: 2.5
      }),
      Plot.dot(data, {
        x: (row) => row[metric],
        y: "score",
        fill: "family",
        stroke: "white",
        r: 6,
        tip: true,
        title: (row) => [
          row.model,
          `Score: ${formatScore(row.score)}`,
          `${metricLabel}: ${metric === "cost" ? formatCost(row.cost) : formatInteger(row.tokens)}`
        ].join("\n")
      })
    ]
  });
}
```

<div style="display: flex; justify-content: flex-end; margin: 0.75rem 0;">
  ${comparisonInput}
</div>

```js
display(html`<div class="card" aria-label=${`${selectedTaskLabel} score versus ${metricLabel}`}>
  ${resize((width) => scoreEfficiencyPlot(efficiencyRows, {width}))}
</div>`);
```

## Unscored model attempts

An attempt is reported here benchmark-wide when a refusal or content filter in
any task prevents a complete, rankable model result. These attempts remain
visible regardless of the task selected above and are not included in the
leaderboard.

```js
const unscoredAttempts = [
  {
    model: "Claude Fable 5.1 (medium)",
    status: "Content filtered",
    evidence: "8/8 panels; zero output tokens; not ranked (Anthropic/OpenRouter Batch, 2026-09-03)"
  },
  {
    model: "Claude Opus 5 (medium)",
    status: "Content filtered",
    evidence: "5/8 panels; run stopped and not ranked (Anthropic/OpenRouter Batch, 2026-09-03)"
  }
];
const unscoredAttemptsTable = Inputs.table(unscoredAttempts, {
  columns: ["model", "status", "evidence"],
  header: {
    model: "Model",
    status: "Status",
    evidence: "Observed evidence"
  },
  width: {
    model: 220,
    status: 150,
    evidence: 560
  },
  rows: Math.max(2, unscoredAttempts.length),
  select: false
});
```

```js
display(html`<div class="card">${unscoredAttemptsTable}</div>`);
```
