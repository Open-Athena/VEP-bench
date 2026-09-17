---
title: VEP-bench
---

```js
import {leaderboardBarPlot} from "./components/leaderboard-plot.js";
import {
  formatCost,
  formatInteger,
  formatKnowledgeCutoff,
  formatPercent
} from "./components/vepbench.js";
import {
  artifactUrl,
  displayScore,
  executionSummaryForRow,
  fetchJson,
  hasExecutionFailures,
  highestEffortRows,
  leaderboardRowsForScope,
  modelFamilyScale,
  orderTaskFamilies,
  supportsOverallLeaderboard
} from "./components/benchmark-data.js";

const config = await FileAttachment("data/config.json").json();
const modelFamilyColors = await FileAttachment("components/model-family-colors.json").json();
const organizationIcons = {
  openai: {name: "OpenAI", url: await FileAttachment("icons/organizations/openai.svg").url()},
  "z-ai": {name: "Z.ai", url: await FileAttachment("icons/organizations/zai.svg").url()},
  anthropic: {name: "Anthropic", url: await FileAttachment("icons/organizations/anthropic.svg").url()},
  google: {name: "Google", url: await FileAttachment("icons/organizations/google.svg").url()},
  meta: {name: "Meta", url: await FileAttachment("icons/organizations/meta.svg").url()},
  deepseek: {name: "DeepSeek", url: await FileAttachment("icons/organizations/deepseek.svg").url()}
};
function modelLabel(model, organizationId) {
  const organization = organizationIcons[organizationId];
  if (!organization) return model;
  return html`<span class="vepbench-model-label">
    <span class="vepbench-organization-icon" role="img" aria-label=${organization.name}
      title=${organization.name} style=${`--organization-icon: url("${organization.url}")`}></span>
    ${model}
  </span>`;
}
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
```

# VEP-bench

VEP-bench is a public benchmark of language models' native ability to predict
genetic variant effects. Models answer without internet access or tools, and
every response and deterministic score can be inspected.

Our [AlphaGenome and AVI comparison](./blog/introducing-vep-bench.html#comparison-with-alpha-genome-and-avi)
compares specialist predictions with LLM answers on the same covered variants:
1,000/1,000 for splicing, 800/800 for expression, and 487/800 for fitness.
Splicing and expression include indels; the AVI fitness comparison covers SNVs.

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
const barRows = highestEffortRows(rows);
const formatScore = (value) => formatPercent(displayScore(value));
```

${selectedTaskFamily === null
  ? `Showing the macro-average ${scoreMetricLabel} correlation across tasks.`
  : `Showing the mean ${scoreMetricLabel} correlation for ${selectedTaskLabel}.`}

Each model appears in the bar chart at its highest available reasoning effort
with complete results for this view. “All tasks” requires the same configuration
across every task. Equal-effort configurations use the latest results; selection
does not depend on score.
Stopped and otherwise incomplete evaluations are listed under
[Unscored model attempts](#unscored-model-attempts).

```js
const plotRow = (row) => ({
  key: (row.run ?? row.runs[0]).configuration_key,
  model: row.model_cell.model,
  score: displayScore(row.score),
  knowledge_cutoff: row.knowledge_cutoff,
  tokens: row.tokens,
  cost: row.cost,
  family: row.family,
  retry_count: row.retry_count,
  retry_policy: row.retry_policy,
  question_count: (row.runs ?? [row.run]).reduce((total, run) => total + run.question_set_size, 0),
  organization: (row.run ?? row.runs[0]).model.model_id.split("/")[0]
});
const leaderboardData = barRows.map(plotRow);
const allConfigurationData = rows.map(plotRow);
const modelFamilyColor = modelFamilyScale(
  allConfigurationData.map((row) => row.family), modelFamilyColors
);
function modelDetails(row) {
  return [
    row.model,
    `Score: ${formatScore(row.score)}`,
    `Cost: ${formatCost(row.cost)}`,
    `Tokens: ${row.tokens === null ? "—" : formatInteger(row.tokens)}`,
    `Knowledge cutoff: ${formatKnowledgeCutoff(row.knowledge_cutoff)}`,
    ...(row.retry_policy
      ? [`${row.retry_count} of ${row.question_count} initial responses were truncated without a valid answer. Each was retried once at ${formatInteger(row.retry_policy.retry_max_tokens)} tokens, up from ${formatInteger(row.retry_policy.initial_max_tokens)}. Scores retain every retry outcome, including failures; cost includes both attempts.`]
      : row.retry_count ? [`${row.retry_count} of ${row.question_count} questions required unchanged retries after API errors. Scores and benchmark cost use completed model responses.`] : [])
  ].join("\n");
}
function leaderboardPlot({width}) {
  return leaderboardBarPlot(leaderboardData, {
    width, color: modelFamilyColor, organizationIcons, formatScore, modelDetails,
    ariaLabel: `${selectedTaskLabel} leaderboard by ${scoreMetricLabel} score`
  });
}
```

Scroll horizontally to see all models. Hover over a bar for cost, token usage, and knowledge cutoff.

```js
display(html`<div class="card vepbench-leaderboard-chart" tabindex="0"
  role="region" aria-label="Ranked model scores; scroll horizontally to see all models">
  ${resize((width) => leaderboardPlot({width}))}
</div>`);
```

## Score by cost and token usage

Compare the selected task's score with benchmark cost and total token usage across
all available reasoning efforts and configurations. Both plots share model colors
and the score scale; each line connects configurations from the same family.
Tokens include input and generated output, including reasoning.

```js
const efficiencyMetrics = [
  {key: "cost", title: "Score by cost", label: "Total cost (USD)"},
  {key: "tokens", title: "Score by token usage", label: "Total tokens"}
];
const efficiencyRows = allConfigurationData
  .filter((row) => row.score !== null && (row.cost !== null || row.tokens !== null));
const efficiencyScoreDomain = efficiencyRows.length
  ? [
    Math.min(...efficiencyRows.map((row) => row.score)),
    Math.max(...efficiencyRows.map((row) => row.score))
  ]
  : undefined;
function scoreEfficiencyPlot({key: metric, label: metricLabel}, {width}) {
  const data = efficiencyRows
    .filter((row) => row[metric] !== null)
    .toSorted((left, right) => left[metric] - right[metric]);
  return Plot.plot({
    width,
    height: 410,
    marginLeft: 64,
    marginBottom: 56,
    x: {
      label: metricLabel,
      grid: true,
      nice: true,
      ticks: Math.max(2, Math.floor(width / 100)),
      tickFormat: metric === "cost"
        ? (value) => formatCost(value)
        : (value) => Intl.NumberFormat("en-US", {notation: "compact"}).format(value)
    },
    y: {
      label: "Score",
      domain: efficiencyScoreDomain,
      grid: true,
      tickFormat: formatScore
    },
    color: modelFamilyColor,
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

```js
display(html`<section aria-label=${`${selectedTaskLabel} score comparisons`}>
  <div aria-label="Model family legend">${Plot.legend({color: modelFamilyColor})}</div>
  <div class="grid grid-cols-2">
    ${efficiencyMetrics.map((metric) => html`<div class="card" aria-label=${`${selectedTaskLabel} score versus ${metric.label}`}>
      <h3>${metric.title}</h3>
      ${resize((width) => scoreEfficiencyPlot(metric, {width}))}
    </div>`)}
  </div>
</section>`);
```

## Unscored model attempts

An attempt is reported here benchmark-wide when refusals, content filtering, or
an early stop leave the evaluation without a complete, rankable model result.
These attempts remain
visible regardless of the task selected above and are not included in the
leaderboard. Recorded responses may have individual scores, but an incomplete
evaluation has no overall score or rank.

```js
const unscoredAttempts = [
  {
    model: "Claude Fable 5.1 (medium)",
    organization: "anthropic",
    status: "Content filtered",
    evidence: "8/8 panels; zero output tokens; not ranked (Anthropic/OpenRouter Batch, 2026-09-03)"
  },
  {
    model: "Claude Opus 5 (medium)",
    organization: "anthropic",
    status: "Content filtered",
    evidence: "5/8 panels; run stopped and not ranked (Anthropic/OpenRouter Batch, 2026-09-03)"
  },
  {
    model: "DeepSeek V4.1 Flash (max)",
    organization: "deepseek",
    status: "Stopped early",
    evidence: "20/52 responses recorded; 9 reached 384K tokens without a final answer; not ranked (DeepSeek/OpenRouter, 2026-09-17)"
  }
];
const unscoredAttemptsTable = Inputs.table(unscoredAttempts, {
  columns: ["model", "status", "evidence"],
  header: {
    model: "Model",
    status: "Status",
    evidence: "Observed evidence"
  },
  format: {
    model: (model) => modelLabel(
      model, unscoredAttempts.find((attempt) => attempt.model === model)?.organization
    )
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

**DeepSeek V4.1 Flash (max), September 17, 2026.** We stopped this evaluation to
avoid further spend after 9 of 16 Fitness (SGE) responses exhausted the
384,000-token output allowance without a final answer; the other 7 were valid.
Requests used DeepSeek's own provider route through OpenRouter, with maximum
reasoning effort and provider fallback disabled. The allowance includes reasoning
and final output and was the selected route's advertised maximum, rather than an
OpenRouter-wide limit.

The 20 recorded responses include all 16 fitness questions, 3 of 16 expression
questions, and 1 of 20 splicing questions, including the initial pilot. Four
additional requests were interrupted in flight and have no recorded outcome;
they are not counted as model failures. Completed response cost was $2.48.
At least one truncated
response degenerated into repeated single letters. The cause is unconfirmed,
and these observations do not establish performance on the unanswered questions
or on other providers or reasoning settings. No retries were made.

The saved responses are retained in the
[September 17 audit publication](https://huggingface.co/buckets/open-athena/VEP-bench/tree/versions/sept17-evaluation-audit).

## Output limits and usage

Across all reasoning efforts, only configurations with recorded formatting
failures, refusals, output-limit stops, or retried failures in the selected tasks
appear below. Configurations with no recorded failures are omitted; incomplete
attempts remain in the section above.

- **Valid answers** follow the required answer format, regardless of prediction
  accuracy. For ranking tasks, the last well-formed `FINAL: {JSON object}` line
  must contain every candidate ID exactly once, with finite numeric predictions.
- **Formatting failures** have no usable final answer: for example, a missing
  `FINAL:` line, JSON split across lines, missing or duplicate candidate IDs, or
  nonnumeric predictions. These responses receive zero for both correlations
  and remain in the score average. A refusal or output-limit stop can also leave
  no usable answer, so this rate does not identify the underlying cause.
- **Truncated** counts responses the provider reports as stopped at an output
  limit, even when they contain a valid final answer. It is based on the reported
  stop reason, not a comparison of token usage with the configured ceiling.
  A model can therefore have 100% valid answers and a nonzero truncation rate;
  formatting failures and truncation can also overlap.

Output tokens include reasoning. Answer rates and output figures describe the
scored responses. Benchmark cost includes input and output for completed model
responses, including completed token-limit attempts before selective retries;
serving errors are excluded. Total tokens include all recorded attempts. Retries
keep recovered failures visible here. Selective retries replace every initially
invalid truncated answer once at the larger listed limit; all retry outcomes,
including failures, are retained. Other settings stay the same.
Unavailable measurements appear as “—”.

```js
const executionData = rows.filter(hasExecutionFailures).map(executionSummaryForRow);
const formatMeasuredTokens = (value) => value === null ? "—" : formatInteger(value);
const executionTable = Inputs.table(executionData, {
  columns: ["model", "questions", "retries", "valid_rate", "format_failure_rate",
    "truncation_rate", "output_limits", "output_tokens", "max_output_tokens", "total_tokens", "cost"],
  header: {
    model: "Model",
    questions: "Questions",
    retries: "Questions retried",
    valid_rate: "Valid answers",
    format_failure_rate: "Formatting failures",
    truncation_rate: "Truncated",
    output_limits: "Output limit",
    output_tokens: "Output tokens",
    max_output_tokens: "Largest output",
    total_tokens: "Total tokens",
    cost: "Cost (USD)"
  },
  format: {
    model: (model) => modelLabel(
      model, executionData.find((row) => row.model === model)?.organization
    ),
    questions: formatInteger,
    retries: formatInteger,
    valid_rate: formatPercent,
    format_failure_rate: formatPercent,
    truncation_rate: formatPercent,
    output_limits: (limits) => limits === null ? "—" : limits.map(formatInteger).join(", "),
    output_tokens: formatMeasuredTokens,
    max_output_tokens: formatMeasuredTokens,
    total_tokens: formatMeasuredTokens,
    cost: formatCost
  },
  width: {model: 235, questions: 90, valid_rate: 110, format_failure_rate: 150, truncation_rate: 100,
    output_limits: 120, output_tokens: 125, max_output_tokens: 130, total_tokens: 125, cost: 100},
  rows: Math.max(1, executionData.length),
  select: false
});
display(html`<div class="card" role="region" aria-label="Output limits and usage; scroll horizontally to see all columns">${executionData.length
  ? executionTable
  : html`<p>No recorded failures for the selected models and tasks.</p>`}</div>`);
```
