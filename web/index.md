---
title: Leaderboard
---

```js
import {
  leaderboardRows,
  leaderboardTable
} from "./components/vepbench.js";
import {artifactUrl, fetchJson} from "./components/benchmark-data.js";

const config = await FileAttachment("data/config.json").json();
const runsState = await fetchJson(artifactUrl(config.data_base_url, "runs.json"))
  .then((document) => ({document, error: null}))
  .catch((error) => ({document: {runs: []}, error}));
const rows = leaderboardRows(runsState.document.runs);
```

# Leaderboard

```js
if (runsState.error) {
  display(html`<div class="note" label="Published data unavailable">The official benchmark data could not be loaded from <code>versions/main</code>.</div>`);
}
```

<div class="card">
  ${leaderboardTable(rows)}
</div>
