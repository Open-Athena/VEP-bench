---
title: satMutMPRA
---

```js
import {
  entriesForQuestions,
  formatInteger,
  questionUrl
} from "../components/vepbench.js";
import {
  artifactUrl,
  fetchJson,
  orderQuestionsForExplorer
} from "../components/benchmark-data.js";

const config = await FileAttachment("../data/config.json").json();
const questionState = await fetchJson(
  artifactUrl(config.data_base_url, "question-index.json")
)
  .then((document) => ({document, error: null}))
  .catch((error) => ({document: {questions: []}, error}));
const orderedQuestions = orderQuestionsForExplorer(questionState.document.questions);
const taskQuestions = orderedQuestions.filter(
  (question) => question.metadata.task_family === "satmut_mpra"
);
const entries = entriesForQuestions(orderedQuestions)
  .filter((entry) => entry.question.metadata.task_family === "satmut_mpra")
  .map((entry) => ({
    ...entry,
    question_link: {
      label: entry.question_label,
      href: questionUrl(entry.question_id, null, "../questions.html")
    },
    candidates: entry.question.candidates.length
  }));
```

*Assay 03 · quantitative regulatory-effect ranking*

# satMutMPRA

```js
if (questionState.error) {
  display(html`<div class="note" label="Published data unavailable">The official question index could not be loaded from <code>versions/main</code>.</div>`);
}
```

Predict signed reporter-activity effects for variants in saturation-mutagenesis MPRAs using the full assayed regulatory-element sequence in reporter-construct orientation and its assay context.

## Task design

<div class="card">
  <p><strong>${formatInteger(taskQuestions.length)} published element panels</strong>, each containing 50 variants sampled as five candidates from each of ten measured-effect quantiles. Every panel uses opaque candidate IDs and contains no measured effect or selection label in its prompt.</p>
  <p>The primary score is mean within-element Spearman correlation. Mean Pearson correlation reports numerical agreement, valid-output rate reports strict JSON compliance, and invalid completed outputs contribute zero while remaining identifiable as format failures.</p>
  <dl>
    <div><dt>Task version</dt><dd>1.1 · question schema 2.0</dd></div>
    <div><dt>Output</dt><dd><code>FINAL: {"V01": number, ...}</code></dd></div>
    <div><dt>Questions</dt><dd>Public development set</dd></div>
  </dl>
</div>

## Interpretation

Spearman measures ordering within the sampled panel, not absolute calibration. Reporter effects are specific to an assay construct, cell line, and experimental condition and need not transfer to native chromatin or clinical phenotype. Quantile-balanced sampling broadens effect coverage but does not reproduce the natural effect distribution.

## Questions

Browse the published element panels and open one to inspect its full prompt.

```js
const filters = view(Inputs.form({
  search: Inputs.search(entries, {
    label: "Find an element",
    placeholder: "Question ID or regulatory element…",
    columns: ["question_id", "variant"]
  })
}));
```

<p class="muted">${formatInteger(filters.search.length)} element panels match the current filter</p>

```js
const questionTable = Inputs.table(filters.search, {
  columns: ["question_link", "variant", "candidates"],
  header: {
    question_link: "Question",
    variant: "Regulatory element",
    candidates: "Candidates"
  },
  format: {
    question_link: (value) => html`<a href=${value.href}>${value.label}</a>`
  },
  width: {
    question_link: 80,
    variant: 220,
    candidates: 100
  },
  select: false
});

questionTable.style.maxWidth = "none";
display(html`<div class="card" style="box-sizing: border-box; width: 100%; max-width: none; padding: 0.75rem 1rem;">${questionTable}</div>`);
```
