---
title: Leaderboard
---

```js
import {
  accuracy,
  formatFailures,
  formatInteger,
  formatPercent,
  leaderboardRows,
  leaderboardTable,
  runTemplateVersion
} from "./components/vepbench.js";

const explorer = await FileAttachment("data/explorer.json").json();
const rows = leaderboardRows(explorer.runs);
const currentRun = rows.find((run) => run.current_question_set) ?? rows[0];
const consequenceCount = new Set(explorer.questions[0]?.choices.map((choice) => choice.text)).size;
const historical = rows.find((run) => !run.current_question_set);
const comparisonNote = historical && currentRun ? html`<div class="note" label="Prompt format study">
  The current v${runTemplateVersion(currentRun)} prompt reduced format failures from
  <strong>${formatFailures(historical.records_data)}</strong> to
  <strong>${formatFailures(currentRun.records_data)}</strong>. Exact-match accuracy changed from
  <strong>${formatPercent(accuracy(historical.records_data))}</strong> to
  <strong>${formatPercent(accuracy(currentRun.records_data))}</strong>.
</div>` : null;
```

*Assay 01 · most severe consequence*

# Leaderboard

Model performance on 190 balanced chromosome 17 SNVs from the human GRCh38 reference genome. Every result is a committed, independently inspectable assay record.

<div class="grid grid-cols-4">
  <div class="card">
    <h2>${formatInteger(explorer.questions.length)} questions</h2>
    <p>10 per consequence class</p>
  </div>
  <div class="card">
    <h2>${consequenceCount} consequence classes</h2>
    <p>balanced development set</p>
  </div>
  <div class="card">
    <h2>1,001 bp window</h2>
    <p>variant centered at position 501</p>
  </div>
  <div class="card">
    <h2>Exact-match scoring</h2>
    <p>last valid FINAL line</p>
  </div>
</div>

## Model results

<p class="muted">${rows.length} committed runs · ranked by exact-match accuracy · API failures remain unscored</p>

${leaderboardTable(rows, Inputs)}

${comparisonNote}

## Assay configuration

<div class="grid grid-cols-2">
  <div class="card">
    <h2>Model-visible inputs</h2>
    <dl>
      <div><dt>Reference</dt><dd>Homo sapiens GRCh38</dd></div>
      <div><dt>Region</dt><dd>Chromosome 17</dd></div>
      <div><dt>Variant</dt><dd>Centered SNV in local VCF</dd></div>
      <div><dt>VEP</dt><dd>release 109.1</dd></div>
      <div><dt>Flags</dt><dd><code>--most_severe --distance 1000</code></dd></div>
    </dl>
  </div>
  <div class="card">
    <h2>Interpretation</h2>
    <dl>
      <div><dt>Questions</dt><dd>Public development set</dd></div>
      <div><dt>Annotations</dt><dd>Transcript annotations intentionally omitted</dd></div>
      <div><dt>Collapsed class</dt><dd>Intergenic, intronic, upstream, and downstream</dd></div>
      <div><dt>Historical runs</dt><dd>Retain original prompt snapshots</dd></div>
      <div><dt>Explorer</dt><dd>Static; no backend or hidden state</dd></div>
    </dl>
  </div>
</div>

[Inspect questions and responses →](./questions.html)
