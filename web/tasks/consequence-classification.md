---
title: Consequence classification
---

```js
import {
  entriesForQuestions,
  formatInteger,
  questionUrl
} from "../components/vepbench.js";
import {artifactUrl, fetchJson} from "../components/benchmark-data.js";
import {
  ENSEMBL_CONSEQUENCE_SOURCE,
  consequenceTableRows,
  sequenceOntologyUrl
} from "../components/consequences.js";

const config = await FileAttachment("../data/config.json").json();
const questionState = await fetchJson(
  artifactUrl(config.data_base_url, "question-index.json")
)
  .then((document) => ({document, error: null}))
  .catch((error) => ({document: {questions: []}, error}));
const consequenceDiagramUrl = FileAttachment("./consequences.svg").href;
const taskFamily = "vep_most_severe_consequence";
const taskQuestions = questionState.document.questions.filter(
  (question) => question.metadata.task_family === taskFamily
);
const consequenceCount = new Set(taskQuestions[0]?.choices.map((choice) => choice.text)).size;
const consequenceRows = consequenceTableRows(taskQuestions[0]?.choices ?? []);
const coveredSourceTermCount = consequenceRows.filter((row) => row.choice_id !== null).length;

function codeCell(value) {
  const code = document.createElement("code");
  code.textContent = value;
  return code;
}

function accessionCell(value) {
  const link = document.createElement("a");
  link.href = sequenceOntologyUrl(value);
  link.target = "_blank";
  link.rel = "noreferrer";
  link.append(codeCell(value));
  return link;
}

function choiceCell(value) {
  if (value === null) {
    const empty = document.createElement("span");
    empty.className = "muted";
    empty.title = "Not included in this benchmark";
    empty.textContent = "—";
    return empty;
  }
  return codeCell(value);
}

function colorCell(value) {
  const swatch = document.createElement("span");
  swatch.setAttribute("aria-label", `Ensembl display color ${value}`);
  swatch.title = value;
  swatch.style.display = "inline-block";
  swatch.style.width = "0.85rem";
  swatch.style.height = "0.85rem";
  swatch.style.border = "1px solid color-mix(in srgb, currentColor 35%, transparent)";
  swatch.style.borderRadius = "0.2rem";
  swatch.style.background = value;
  return swatch;
}

function impactCell(value) {
  const badge = document.createElement("span");
  const palette = {
    HIGH: ["#fee2e2", "#991b1b"],
    MODERATE: ["#ffedd5", "#9a3412"],
    LOW: ["#fef9c3", "#854d0e"],
    MODIFIER: ["#e0f2fe", "#075985"]
  }[value];
  badge.textContent = value;
  badge.style.display = "inline-block";
  badge.style.padding = "0.12rem 0.42rem";
  badge.style.borderRadius = "999px";
  badge.style.fontSize = "0.72rem";
  badge.style.fontWeight = "700";
  badge.style.letterSpacing = "0.03em";
  badge.style.background = palette[0];
  badge.style.color = palette[1];
  return badge;
}
```

*Assay 01 · sequence-context multiple choice*

# Consequence classification

```js
if (questionState.error) {
  display(html`<div class="note" label="Published data unavailable">The official question index could not be loaded from <code>versions/main</code>.</div>`);
}
```

Predict the Ensembl VEP most severe consequence for a human GRCh38 SNV using only its centered local sequence window and variant alleles.

## Task design

<div class="card">
  <p><strong>${formatInteger(taskQuestions.length)} questions</strong> across ${consequenceCount} balanced consequence classes, with 3 examples per class. Each question uses a 1,001 bp window centered on the variant and is scored by exact match against its last valid <code>FINAL</code> line.</p>
  <p>Models see a chromosome 17 SNV in local VCF form, the human GRCh38 reference window, and VEP release 109.1 with <code>--most_severe --distance 1000</code>. The prompt states the deterministic 80-base FASTA line width. Transcript annotations are intentionally omitted. Intergenic, intronic, upstream, and downstream consequences are combined into one class.</p>
  <dl>
    <div><dt>Task version</dt><dd>1.2</dd></div>
    <div><dt>Questions</dt><dd>Public development set</dd></div>
    <div><dt>Explorer</dt><dd>Static; no backend or hidden state</dd></div>
  </dl>
</div>

## Consequence map

Ensembl’s diagram places consequence terms relative to transcript structure. It is a current, general reference; the benchmark questions and answers remain pinned to VEP release 109.1.

```js
const consequenceFigure = html`<figure class="card" style="box-sizing: border-box; width: 100%; max-width: none; margin: 1rem 0 2rem; padding: 1rem;">
  <a href=${consequenceDiagramUrl} target="_blank" rel="noreferrer" style="display: block;">
    <img
      src=${consequenceDiagramUrl}
      alt="Ensembl diagram showing variant consequence terms relative to gene and transcript structure"
      loading="lazy"
      style="display: block; width: 100%; max-width: 990px; height: auto; margin: 0 auto; border-radius: 0.35rem; background: white;"
    >
  </a>
  <figcaption class="muted" style="margin-top: 0.75rem; font-size: 0.82rem; line-height: 1.45;">
    Consequence diagram © EMBL-EBI / Ensembl, reproduced unmodified from the
    <a href=${ENSEMBL_CONSEQUENCE_SOURCE.page} target="_blank" rel="noreferrer">Ensembl release ${ENSEMBL_CONSEQUENCE_SOURCE.release} calculated-consequences reference</a>
    (<a href=${ENSEMBL_CONSEQUENCE_SOURCE.diagram} target="_blank" rel="noreferrer">SVG source</a>) under the
    <a href=${ENSEMBL_CONSEQUENCE_SOURCE.imageReuse} target="_blank" rel="noreferrer">Ensembl image-reuse policy</a>.
    Open the diagram for a full-size view.
  </figcaption>
</figure>`;

display(consequenceFigure);
```

## Consequence classes

The complete current Ensembl catalog is shown in severity order. ${formatInteger(coveredSourceTermCount)} of ${formatInteger(consequenceRows.length)} source terms map to this benchmark’s ${consequenceCount} answer choices; an em dash marks terms that are not currently covered. Choice **C03** combines intergenic, intronic, upstream, and downstream variants, so its four source terms appear separately.

Definitions, colors, severity order, and IMPACT labels follow Ensembl’s [current calculated-consequences reference](https://useast.ensembl.org/info/genome/variation/prediction/predicted_data.html). Severity order and IMPACT are separate Ensembl classifications; VEPBench itself scores only the exact answer choice.

```js
const visibleConsequences = view(Inputs.search(consequenceRows, {
  label: "Find a consequence",
  placeholder: "Choice, SO term, description, accession, or impact…",
  columns: [
    "choice_id",
    "choice_label",
    "term",
    "description",
    "accession",
    "impact"
  ]
}));
```

<p class="muted">${formatInteger(visibleConsequences.length)} source terms match the current search</p>

```js
const consequenceTable = Inputs.table(visibleConsequences, {
  columns: ["color", "choice_id", "term", "description", "accession", "impact"],
  header: {
    color: "",
    choice_id: "Choice",
    term: "SO term",
    description: "SO description",
    accession: "SO accession",
    impact: "IMPACT"
  },
  format: {
    color: colorCell,
    choice_id: choiceCell,
    term: codeCell,
    accession: accessionCell,
    impact: impactCell
  },
  width: {
    color: 34,
    choice_id: 70,
    term: 250,
    description: 520,
    accession: 110,
    impact: 100
  },
  select: false
});

consequenceTable.style.maxWidth = "none";
const consequenceTableCard = html`<div class="card" style="box-sizing: border-box; width: 100%; max-width: none; padding: 0.75rem 1rem;">${consequenceTable}</div>`;
display(consequenceTableCard);
```

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

```js
const questionTable = Inputs.table(visibleEntries, {
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
});

questionTable.style.maxWidth = "none";
const questionTableCard = html`<div class="card" style="box-sizing: border-box; width: 100%; max-width: none; padding: 0.75rem 1rem;">${questionTable}</div>`;
display(questionTableCard);
```
