---
title: ClinVar
---

```js
import {
  entriesForQuestions,
  formatInteger,
  questionUrl
} from "../components/vepbench.js";
import {artifactUrl, fetchJson} from "../components/benchmark-data.js";

const config = await FileAttachment("../data/config.json").json();
const questionState = await fetchJson(
  artifactUrl(config.data_base_url, "question-index.json")
)
  .then((document) => ({document, error: null}))
  .catch((error) => ({document: {questions: []}, error}));
const taskQuestions = questionState.document.questions.filter(
  (question) => question.metadata.task_family === "clinvar"
);
const entries = entriesForQuestions(taskQuestions).map((entry) => ({
  ...entry,
  question_link: {
    label: entry.question_label,
    href: questionUrl(entry.question_id, null, "../questions.html")
  }
}));
```

*Assay 02 · temporal sequence-context multiple choice*

# ClinVar

```js
if (questionState.error) {
  display(html`<div class="note" label="Published data unavailable">The official question index could not be loaded from <code>versions/main</code>.</div>`);
}
```

Classify a human GRCh38 SNV as exactly **Benign** or **Pathogenic** from only a centered local sequence window and the variant alleles.

## Task design

<div class="card">
  <p><strong>${formatInteger(taskQuestions.length)} published questions</strong> from ClinVar VCV records first public in July 2026. Every question uses a 1,001 bp window centered on the variant and is scored by exact match against its last valid <code>FINAL</code> line.</p>
  <p>The cohort uses labels frozen in the August 2026 monthly ClinVar release, requires at least one review star, and is balanced between benign and pathogenic variants within every retained exact VEP consequence. Consequence is used only for matched sampling and is hidden from the model.</p>
  <dl>
    <div><dt>Task version</dt><dd>1.0</dd></div>
    <div><dt>Choices</dt><dd><code>C01</code> Benign · <code>C02</code> Pathogenic</dd></div>
    <div><dt>Questions</dt><dd>Temporal public development set</dd></div>
  </dl>
</div>

## Interpretation

The July first-public cutoff reduces direct ClinVar leakage but does not prove that a variant or its evidence was absent from model training data. Pathogenicity is not determined by local sequence alone: phenotype, inheritance, allele frequency, segregation, functional evidence, gene, transcript, ClinVar identity, and genomic coordinates are intentionally omitted. Scores are not clinical variant interpretations.

## Questions

Browse the published task questions and open one to inspect its full prompt.

```js
const filters = view(Inputs.form({
  search: Inputs.search(entries, {
    label: "Find a question",
    placeholder: "Question ID, local variant, answer, or choice…",
    columns: ["question_id", "question_label", "variant", "answer"]
  }),
  answer: Inputs.select([
    "All answers",
    ...[...new Set(entries.map((entry) => entry.answer))].sort()
  ], {label: "Reference answer"})
}));
```

```js
const visibleEntries = filters.search.filter((entry) =>
  filters.answer === "All answers" || entry.answer === filters.answer
);
```

<p class="muted">${formatInteger(visibleEntries.length)} questions match the current filters</p>

```js
const questionTable = Inputs.table(visibleEntries, {
  columns: ["question_link", "variant", "answer"],
  header: {
    question_link: "Question",
    variant: "ClinVar record",
    answer: "Reference answer"
  },
  format: {
    question_link: (value) => html`<a href=${value.href}>${value.label}</a>`
  },
  width: {
    question_link: 70,
    variant: 130,
    answer: 180
  },
  select: false
});

questionTable.style.maxWidth = "none";
display(html`<div class="card" style="box-sizing: border-box; width: 100%; max-width: none; padding: 0.75rem 1rem;">${questionTable}</div>`);
```
