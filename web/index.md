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
```

<div class="page-kicker">ASSAY 01 · MOST SEVERE CONSEQUENCE</div>

# Leaderboard

Model performance on 190 balanced chromosome 17 SNVs from the human GRCh38 reference genome. Every result is a committed, independently inspectable assay record.

<div class="grid grid-cols-4 assay-metrics">
  <div class="card">
    <h2>Questions</h2>
    <span class="big-number">${formatInteger(explorer.questions.length)}</span>
    <p>10 per consequence class</p>
  </div>
  <div class="card">
    <h2>Consequence classes</h2>
    <span class="big-number">${consequenceCount}</span>
    <p>balanced development set</p>
  </div>
  <div class="card">
    <h2>Sequence window</h2>
    <span class="big-number">1,001 bp</span>
    <p>variant centered at position 501</p>
  </div>
  <div class="card">
    <h2>Scoring</h2>
    <span class="big-number">Exact</span>
    <p>last valid FINAL line</p>
  </div>
</div>

## Model results

<div class="section-note">${rows.length} committed runs · ranked by exact-match accuracy · API failures remain unscored</div>

<div class="card table-card">
  ${leaderboardTable(rows, Inputs)}
</div>

${historical && currentRun ? html`<div class="note comparison-note" label="Prompt format study">
  The current v${runTemplateVersion(currentRun)} prompt reduced format failures from
  <strong>${formatFailures(historical.records_data)}</strong> to
  <strong>${formatFailures(currentRun.records_data)}</strong>. Exact-match accuracy changed from
  <strong>${formatPercent(accuracy(historical.records_data))}</strong> to
  <strong>${formatPercent(accuracy(currentRun.records_data))}</strong>.
</div>` : null}

## Assay configuration

<div class="grid grid-cols-2 method-grid">
  <div class="card">
    <h2>Model-visible inputs</h2>
    <dl class="method-list">
      <div><dt>Reference</dt><dd>Homo sapiens GRCh38</dd></div>
      <div><dt>Region</dt><dd>Chromosome 17</dd></div>
      <div><dt>Variant</dt><dd>Centered SNV in local VCF</dd></div>
      <div><dt>VEP</dt><dd>release 109.1</dd></div>
      <div><dt>Flags</dt><dd><code>--most_severe --distance 1000</code></dd></div>
    </dl>
  </div>
  <div class="card">
    <h2>Interpretation</h2>
    <dl class="method-list">
      <div><dt>Questions</dt><dd>Public development set</dd></div>
      <div><dt>Annotations</dt><dd>Transcript annotations intentionally omitted</dd></div>
      <div><dt>Collapsed class</dt><dd>Intergenic, intronic, upstream, and downstream</dd></div>
      <div><dt>Historical runs</dt><dd>Retain original prompt snapshots</dd></div>
      <div><dt>Explorer</dt><dd>Static; no backend or hidden state</dd></div>
    </dl>
  </div>
</div>

<a class="primary-link" href="./questions">Inspect questions and responses →</a>
