---
title: Leaderboard
---

```js
import {
  leaderboardRows,
  leaderboardTable
} from "./components/vepbench.js";

const explorer = await FileAttachment("data/explorer.json").json();
const rows = leaderboardRows(explorer.runs);
```

# Leaderboard

Model performance across VEPBench tasks. Each row is a committed, independently inspectable evaluation result against the latest task version.

<p class="muted">${rows.length} committed ${rows.length === 1 ? "run" : "runs"} · ranked by exact-match accuracy · API failures remain unscored</p>

${leaderboardTable(rows, Inputs)}
