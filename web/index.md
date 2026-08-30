---
title: Leaderboard
---

```js
import {
  leaderboardRows,
  leaderboardTable
} from "./components/vepbench.js";

const explorer = await FileAttachment("data/explorer.json").json();
const rows = leaderboardRows(explorer.task_runs);
```

# Leaderboard

<div class="card">
  ${leaderboardTable(rows)}
</div>
