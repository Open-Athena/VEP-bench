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
  fetchJson,
  leaderboardRowsForScope
} from "./components/benchmark-data.js";

const config = await FileAttachment("data/config.json").json();
const runsState = await fetchJson(artifactUrl(config.data_base_url, "runs.json"))
  .then((document) => ({document, error: null}))
  .catch((error) => ({document: {runs: []}, error}));
const aggregation = runsState.document.leaderboard;
const taskFamily = "satmut_mpra";
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
if (runsState.error) {
  display(html`<div class="note" label="Published data unavailable">The official benchmark data could not be loaded from <code>versions/main</code>.</div>`);
} else if (!aggregation?.evaluation_profiles?.some(
  (profile) => profile.task_family === taskFamily
)) {
  display(html`<div class="note" label="satMutMPRA unavailable">The current official version does not contain a satMutMPRA evaluation profile.</div>`);
}
```

```js
const rows = leaderboardRowsForScope(
  runsState.document.runs,
  aggregation,
  taskFamily
);
const formatScore = (value) => value === null ? "—" : value.toFixed(3);
```

Showing mean within-element Spearman rho for **satMutMPRA**. Pearson correlation and valid-output rate are separate diagnostics.

```js
const tableRows = rows.map((row) => ({
  model: row.model_cell.model,
  score: row.score,
  pearson: row.pearson,
  valid_output_rate: row.valid_output_rate,
  release_date: row.release_date,
  tokens: row.tokens,
  cost: row.cost,
  family: row.family
}));
function scoreBar(value) {
  if (!Number.isFinite(value)) return "—";
  const normalized = (value + 1) / 2;
  const width = Math.max(0, Math.min(1, normalized)) * 100;
  return html`<span class="vepbench-score-cell" style=${`--vepbench-score-width: ${width}%`}>
    <span class="vepbench-score-bar" aria-hidden="true"></span>
    <span class="vepbench-score-value">${formatScore(value)}</span>
  </span>`;
}
const leaderboardTable = Inputs.table(tableRows, {
  columns: ["model", "score", "pearson", "valid_output_rate", "release_date", "tokens", "cost"],
  header: {
    model: "Model",
    score: "Score",
    pearson: "Pearson r",
    valid_output_rate: "Valid outputs",
    release_date: "Release date",
    tokens: "Tokens",
    cost: "Cost"
  },
  format: {
    score: scoreBar,
    pearson: (value) => value === null ? "—" : value.toFixed(3),
    valid_output_rate: formatPercent,
    release_date: formatDate,
    tokens: (value) => value === null ? "—" : formatInteger(value),
    cost: formatCost
  },
  align: {
    score: "right",
    pearson: "right",
    valid_output_rate: "right",
    tokens: "right",
    cost: "right"
  },
  width: {
    model: 240,
    score: 100,
    pearson: 100,
    valid_output_rate: 105,
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

Each line connects evaluated configurations from the same model family. Use the selector to compare the satMutMPRA score with total run cost or total token usage.

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
      label: "Mean Spearman ρ",
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
display(html`<div class="card" aria-label=${`satMutMPRA score versus ${metricLabel}`}>
  ${resize((width) => scoreEfficiencyPlot(efficiencyRows, {width}))}
</div>`);
```
