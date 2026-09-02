---
title: Leaderboard
---

```js
import {
  leaderboardLineChart,
  leaderboardRows,
  leaderboardTable,
  overallLeaderboardRows
} from "./components/vepbench.js";
import {artifactUrl, fetchJson} from "./components/benchmark-data.js";

const config = await FileAttachment("data/config.json").json();
const runsState = await fetchJson(artifactUrl(config.data_base_url, "runs.json"))
  .then((document) => ({document, error: null}))
  .catch((error) => ({document: {runs: []}, error}));
const aggregation = runsState.document.leaderboard;
const rows = aggregation
  ? overallLeaderboardRows(runsState.document.runs, aggregation)
  : leaderboardRows(runsState.document.runs);
```

# Leaderboard

```js
if (runsState.error) {
  display(html`<div class="note" label="Published data unavailable">The official benchmark data could not be loaded from <code>versions/main</code>.</div>`);
} else if (aggregation) {
  display(html`<p>The provisional overall score is the unweighted mean of exact-match accuracy across every published task profile. Each task contributes equally, regardless of its number of questions, and a model configuration appears only after it has a complete run on every task.</p>`);
} else {
  display(html`<div class="note" label="Single-task publication">The current official version predates multi-task overall scoring, so these are individual run scores.</div>`);
}
```

<div class="card">
  ${leaderboardTable(rows)}
</div>

## Score by cost and token usage

Each line connects evaluated configurations from the same model family. Use the selector to compare exact-match score with total run cost or total token usage.

<div class="card">
  ${leaderboardLineChart(rows)}
</div>
