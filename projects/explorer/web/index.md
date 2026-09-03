---
title: Leaderboard
---

```js
import {
  formatCost,
  formatDate,
  formatInteger,
  formatPercent
} from "./components/vepbench.js";
import {
  artifactUrl,
  displayScore,
  fetchJson,
  leaderboardRowsForScope,
  orderTaskFamilies
} from "./components/benchmark-data.js";

const config = await FileAttachment("data/config.json").json();
const runsState = await fetchJson(artifactUrl(config.data_base_url, "runs.json"))
  .then((document) => ({document, error: null}))
  .catch((error) => ({document: {runs: []}, error}));
const aggregation = runsState.document.leaderboard;
const taskName = (taskFamily) => ({
  satmut_mpra: "satMutMPRA"
})[taskFamily] ?? taskFamily;
const publishedTaskFamilies = orderTaskFamilies([
  ...new Set(
    (aggregation?.evaluation_profiles ?? []).map((profile) => profile.task_family)
  )
]);
const taskOptions = [
  {task_family: null, label: "All tasks"},
  ...(
    publishedTaskFamilies.length ? publishedTaskFamilies : ["satmut_mpra"]
  ).map((taskFamily) => ({task_family: taskFamily, label: taskName(taskFamily)}))
];
const taskInput = Inputs.select(taskOptions, {
  label: "Task",
  value: taskOptions[0],
  format: (option) => option.label
});
taskInput.style.maxWidth = "22rem";
const metricOptions = [
  {key: "cost", label: "Total cost", axis_label: "Total cost (USD)"},
  {key: "tokens", label: "Total tokens", axis_label: "Total tokens"}
];
const metricInput = Inputs.select(metricOptions, {
  label: "Compare score against",
  value: metricOptions[0],
  format: (option) => option.label
});
metricInput.style.maxWidth = "22rem";
```

# Leaderboard

```js
const selectedTask = view(taskInput);
const selectedTaskFamily = selectedTask.task_family ?? (
  publishedTaskFamilies.length === 1 ? publishedTaskFamilies[0] : null
);
```

<div style="display: flex; justify-content: flex-end; margin: 0.75rem 0;">
  ${taskInput}
</div>

```js
if (runsState.error) {
  display(html`<div class="note" label="Published data unavailable">The official benchmark data could not be loaded from <code>versions/main</code>.</div>`);
} else if (selectedTask.task_family !== null && !aggregation?.evaluation_profiles?.some(
  (profile) => profile.task_family === selectedTask.task_family
)) {
  display(html`<div class="note" label="Task unavailable">The current official version does not contain the selected task's evaluation profile.</div>`);
}
```

```js
const rows = leaderboardRowsForScope(
  runsState.document.runs,
  aggregation,
  selectedTaskFamily
);
const formatScore = (value) => formatPercent(displayScore(value));
```

Showing the primary score for **${selectedTask.label}**.

```js
const tableRows = rows.map((row) => ({
  model: row.model_cell.model,
  score: displayScore(row.score),
  release_date: row.release_date,
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
  columns: ["model", "score", "release_date", "tokens", "cost"],
  header: {
    model: "Model",
    score: "Score",
    release_date: "Release date",
    tokens: "Tokens",
    cost: "Cost"
  },
  format: {
    score: scoreBar,
    release_date: formatDate,
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
    release_date: 110,
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
const selectedMetric = view(metricInput);
```

```js
const metric = selectedMetric?.key ?? metricInput.value?.key ?? "cost";
const metricLabel = metricOptions.find((option) => option.key === metric)?.axis_label
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
  ${metricInput}
</div>

```js
display(html`<div class="card" aria-label=${`${selectedTask.label} score versus ${metricLabel}`}>
  ${resize((width) => scoreEfficiencyPlot(efficiencyRows, {width}))}
</div>`);
```
