---
title: Consequence classification
---

```js
import {
  entriesForQuestions,
  formatInteger,
  questionUrl
} from "../components/vepbench.js";

const explorer = await FileAttachment("../data/explorer.json").json();
const taskFamily = "vep_most_severe_consequence";
const taskQuestions = explorer.questions.filter(
  (question) => question.metadata.task_family === taskFamily
);
const consequenceCount = new Set(taskQuestions[0]?.choices.map((choice) => choice.text)).size;
```

*Assay 01 · sequence-context multiple choice*

# Consequence classification

Predict the Ensembl VEP most severe consequence for a human GRCh38 SNV using only its centered local sequence window and variant alleles.

## Task design

<div class="card">
  <p><strong>${formatInteger(taskQuestions.length)} questions</strong> across ${consequenceCount} balanced consequence classes, with 10 examples per class. Each question uses a 1,001 bp window centered on the variant and is scored by exact match against its last valid <code>FINAL</code> line.</p>
  <p>Models see a chromosome 17 SNV in local VCF form, the human GRCh38 reference window, and VEP release 109.1 with <code>--most_severe --distance 1000</code>. Transcript annotations are intentionally omitted. Intergenic, intronic, upstream, and downstream consequences are combined into one class.</p>
  <dl>
    <div><dt>Task version</dt><dd>1.1</dd></div>
    <div><dt>Questions</dt><dd>Public development set</dd></div>
    <div><dt>Explorer</dt><dd>Static; no backend or hidden state</dd></div>
  </dl>
</div>

## Questions

Browse the current task questions and open one to inspect it in detail.

```js
const entries = entriesForQuestions(taskQuestions).map(
  (entry) => ({
    ...entry,
    question_link: {
      label: entry.question_label,
      href: questionUrl(entry.question_id, null, "../questions.html")
    }
  })
);
```

```js
const filters = view(Inputs.form({
  search: Inputs.search(entries, {
    label: "Find a question",
    placeholder: "Question ID, variant, consequence, or choice…",
    columns: [
      "question_id",
      "question_label",
      "variant",
      "answer"
    ]
  }),
  consequence: Inputs.select([
    "All consequences",
    ...[...new Set(entries.map((entry) => entry.answer))].sort()
  ], {label: "Reference consequence"})
}));
```

```js
const visibleEntries = filters.search.filter((entry) =>
  filters.consequence === "All consequences" || entry.answer === filters.consequence
);
```

<p class="muted">${formatInteger(visibleEntries.length)} questions match the current filters</p>

${Inputs.table(visibleEntries, {
  columns: ["question_link", "variant", "answer"],
  header: {
    question_link: "Question",
    variant: "Source variant",
    answer: "Reference consequence"
  },
  format: {
    question_link: (value) => html`<a href=${value.href}>${value.label}</a>`
  },
  width: {
    question_link: 70,
    variant: 130,
    answer: 260
  },
  select: false
})}
