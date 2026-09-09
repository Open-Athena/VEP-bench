import * as Plot from "npm:@observablehq/plot@0.6.17";
import MarkdownIt from "npm:markdown-it@14.1.0";
import {resize} from "observablehq:stdlib";

import {
  predictionComparisonRows,
  resultTypeForAnswer,
  resultTypeLabel
} from "./benchmark-data.js";

const markdown = new MarkdownIt({
  html: false,
  breaks: true,
  linkify: true,
  typographer: false
});

export function formatInteger(value) {
  return Number(value ?? 0).toLocaleString("en-US");
}

export function formatPercent(value) {
  return value === null ? "—" : `${(value * 100).toFixed(1)}%`;
}

export function formatDate(value) {
  if (typeof value !== "string") return "—";
  const match = /^(\d{4}-\d{2}-\d{2})/.exec(value);
  return match ? match[1] : "—";
}

export function formatCorrelation(value) {
  return Number.isFinite(value) ? value.toFixed(3) : "—";
}

export function formatKnowledgeCutoff(value) {
  if (value === null || value === undefined) return "Not disclosed";
  if (typeof value !== "string") return "—";
  if (/^\d{4}-\d{2}-\d{2}$/.test(value)) return value;
  if (!/^\d{4}-\d{2}$/.test(value)) return "—";
  const parsed = new Date(`${value}-01T00:00:00Z`);
  if (Number.isNaN(parsed.valueOf())) return "—";
  return new Intl.DateTimeFormat("en-US", {
    month: "long",
    year: "numeric",
    timeZone: "UTC"
  }).format(parsed);
}

export function assayFirstIndexedLink(value) {
  const label = formatDate(value?.date);
  if (label === "—" || typeof value?.url !== "string") return label;
  const link = document.createElement("a");
  link.href = value.url;
  link.rel = "noreferrer";
  link.target = "_blank";
  link.textContent = label;
  const relation = value?.cutoff_relation;
  const relationClass = {
    "Before cutoff": "before",
    "After cutoff": "after",
    Unknown: "unknown"
  }[relation];
  if (relationClass) link.className = `vepbench-assay-date vepbench-assay-date-${relationClass}`;
  const kind = typeof value.kind === "string" ? value.kind.replaceAll("_", " ") : null;
  const cutoff = typeof value.knowledge_cutoff === "string"
    ? `model cutoff ${formatKnowledgeCutoff(value.knowledge_cutoff)}`
    : "model cutoff not disclosed";
  link.title = [value.registry, kind, relation, cutoff].filter(Boolean).join(" · ");
  return link;
}

export function cutoffRelationBadge(value) {
  const badge = document.createElement("span");
  const suffix = {
    "Before cutoff": "before",
    "After cutoff": "after",
    Unknown: "unknown"
  }[value] ?? "unknown";
  badge.className = `vepbench-cutoff-badge vepbench-cutoff-${suffix}`;
  badge.textContent = value ?? "Unknown";
  return badge;
}

export function knowledgeCutoffNote(run) {
  const note = document.createElement("p");
  note.className = "muted";
  note.style.margin = "0.5rem 0";
  const cutoff = run?.model?.knowledge_cutoff;
  const sourceUrl = run?.model?.knowledge_cutoff_url;
  if (typeof cutoff !== "string") {
    note.textContent = "Knowledge cutoff not disclosed; cutoff relations remain Unknown.";
    return note;
  }
  note.append("Knowledge cutoff: ");
  if (typeof sourceUrl === "string") {
    const link = document.createElement("a");
    link.href = sourceUrl;
    link.rel = "noreferrer";
    link.target = "_blank";
    link.textContent = formatKnowledgeCutoff(cutoff);
    link.title = "Provider model documentation";
    note.append(link);
  } else {
    note.append(formatKnowledgeCutoff(cutoff));
  }
  note.append(
    cutoff.length === 7
      ? ". Later months are green; earlier months are amber; dates in the cutoff month are gray and Unknown."
      : ". Green assay dates are after it; amber dates are on or before it."
  );
  return note;
}

export function formatCost(value) {
  if (value === null) return "—";
  const maximumFractionDigits = value > 0 && value < 0.01 ? 4 : 2;
  return value.toLocaleString("en-US", {
    style: "currency",
    currency: "USD",
    minimumFractionDigits: 2,
    maximumFractionDigits
  });
}

function choiceText(question, choiceId) {
  return question.choices.find((choice) => choice.choice_id === choiceId)?.text ?? "—";
}

export function resultOutcome(result) {
  if (!result) return "Not evaluated";
  if (result.scoring.metric === "rank_correlation") {
    return result.scoring.parse_error !== null ? "Format failure" : "Valid prediction";
  }
  return resultTypeLabel(resultTypeForAnswer(result), result.scoring.correct);
}

export function entryForAnswer(question, index, result, run, rawArchiveUrl = null) {
  const ranking = question.task_type === "ranking";
  return {
    question_id: question.question_id,
    question_label: `Q${String(index + 1).padStart(3, "0")}`,
    variant: question.provenance.source_record_id,
    answer: ranking
      ? `${question.candidates.length} candidate variants`
      : choiceText(question, question.answer_choice_id),
    prediction: ranking
      ? (result?.scoring.parsed_answer
          ? `${Object.keys(result.scoring.parsed_answer).length} numeric predictions`
          : "—")
      : choiceText(question, result?.scoring.parsed_answer),
    outcome: resultOutcome(result),
    question,
    result,
    run,
    rawArchiveUrl
  };
}

export function entriesForQuestions(questions) {
  return questions.map((question, index) => ({
    question_id: question.question_id,
    question_label: `Q${String(index + 1).padStart(3, "0")}`,
    variant: question.provenance.source_record_id,
    answer: question.task_type === "ranking"
      ? `${question.candidates.length} candidate variants`
      : choiceText(question, question.answer_choice_id),
    prediction: "—",
    outcome: "Select to load",
    question,
    result: null,
    run: null
  }));
}

export function outcomeBadge(value) {
  const badge = document.createElement("span");
  const outcome = {
    Correct: "correct",
    Incorrect: "incorrect",
    Refusal: "refusal",
    "Token limit": "token-limit",
    "Format error": "format-error",
    "Format failure": "format-failure",
    "Valid prediction": "correct"
  }[value] ?? "";
  if (outcome) badge.className = `vepbench-outcome-badge vepbench-outcome-${outcome}`;
  badge.textContent = value;
  return badge;
}

export function enhanceTableRowSelection(table) {
  table.classList.add("vepbench-row-select-table");

  const labelRows = () => {
    for (const row of table.querySelectorAll("tbody tr")) {
      const radio = row.querySelector('input[type="radio"]');
      if (!radio || radio.hasAttribute("aria-label")) continue;
      const label = row.cells[1]?.textContent.trim();
      radio.setAttribute("aria-label", label ? `Select ${label}` : "Select row");
    }
  };

  labelRows();
  new MutationObserver(labelRows).observe(table, {childList: true, subtree: true});

  table.addEventListener("click", (event) => {
    if (event.target.closest("a, button, input, select, textarea, [role=button]")) return;
    const row = event.target.closest("tbody tr");
    if (!row || !table.contains(row)) return;
    const radio = row.querySelector('input[type="radio"]');
    if (!radio) return;
    radio.focus({preventScroll: true});
    radio.click();
  });

  return table;
}

function element(tag, className, text) {
  const node = document.createElement(tag);
  if (className) node.className = className;
  if (text !== undefined && text !== null) node.textContent = text;
  return node;
}

function markdownNode(source) {
  const node = element("div");
  node.innerHTML = markdown.render(source ?? "");
  for (const anchor of node.querySelectorAll("a")) anchor.rel = "noreferrer";
  return node;
}

function predictionComparisonPlot(rows) {
  const consequences = [...new Set(rows.map((row) => row.consequence))].sort();
  const variantTypes = ["SNV", "Indel", "Multibase substitution", "Unknown"];
  const symbols = ["circle", "triangle", "square", "diamond"];
  const presentTypes = variantTypes.filter((type) => rows.some((row) => row.variant_type === type));
  const container = element("div", "vepbench-prediction-plot");
  container.style.maxWidth = "440px";
  container.style.marginInline = "auto";
  container.append(resize((width) => Plot.plot({
    width,
    // Equal inner dimensions after reserving space for the axis labels.
    height: width - 8,
    marginLeft: 54,
    marginRight: 20,
    marginTop: 20,
    marginBottom: 46,
    ariaLabel: "Predicted versus measured variant effects",
    ariaDescription: (
      `${rows.length} variants. The dashed line shows the fitted linear trend.`
    ),
    x: {grid: true, label: "Measured effect", nice: true},
    y: {grid: true, label: "Predicted effect", nice: true},
    color: {
      type: "categorical",
      domain: consequences,
      range: consequences.map(consequenceColor),
      legend: true,
      tickFormat: (term) => term.replace(/_variant$/, "").replaceAll("_", " "),
      label: "VEP consequence"
    },
    symbol: {
      domain: presentTypes,
      range: presentTypes.map((type) => symbols[variantTypes.indexOf(type)]),
      legend: true,
      fill: "currentColor",
      stroke: "none"
    },
    marks: [
      Plot.linearRegressionY(rows, {
        ci: 0,
        x: "measured",
        y: "predicted",
        stroke: "currentColor",
        strokeDasharray: "4,4",
        strokeOpacity: 0.35,
        strokeWidth: 1.5
      }),
      Plot.dot(rows, {
        x: "measured",
        y: "predicted",
        fill: "consequence",
        symbol: "variant_type",
        fillOpacity: 0.82,
        stroke: "var(--theme-background)",
        strokeWidth: 1.25,
        r: 4.5,
        tip: true,
        ariaLabel: (row) => (
          `${row.candidate_id}; ${row.variant_type}; ${row.consequence}; `
          + `measured ${row.measured}; predicted ${row.predicted}`
        ),
        title: (row) => [
          row.candidate_id,
          `Type: ${row.variant_type}`,
          `VEP consequence: ${row.consequence.replaceAll("_", " ")}`,
          row.genomic
            ? `${row.genomic.assembly} · chr${row.genomic.chrom}:${row.genomic.pos} `
              + `${row.genomic.ref}>${row.genomic.alt}`
            : "Genomic coordinates unavailable",
          `Measured: ${row.measured}`,
          `Predicted: ${row.predicted}`
        ].join("\n")
      })
    ]
  })));
  return container;
}

// Fixed colors keep the same consequence recognizable across panels and models.
function consequenceColor(term) {
  const colors = {
    transcript_ablation: "#7f0000",
    splice_acceptor_variant: "#d73027",
    splice_donor_variant: "#f46d43",
    stop_gained: "#a50026",
    frameshift_variant: "#b2188b",
    stop_lost: "#8c510a",
    start_lost: "#bf812d",
    inframe_insertion: "#762a83",
    inframe_deletion: "#9970ab",
    missense_variant: "#e69f00",
    protein_altering_variant: "#cc79a7",
    splice_region_variant: "#009e73",
    splice_donor_5th_base_variant: "#006d2c",
    splice_donor_region_variant: "#238b45",
    splice_polypyrimidine_tract_variant: "#41ab5d",
    synonymous_variant: "#0072b2",
    stop_retained_variant: "#3182bd",
    start_retained_variant: "#6baed6",
    "5_prime_UTR_variant": "#008b8b",
    "3_prime_UTR_variant": "#56b4e9",
    non_coding_transcript_exon_variant: "#66a61e",
    intron_variant: "#666666",
    NMD_transcript_variant: "#7570b3",
    non_coding_transcript_variant: "#a6761d",
    upstream_gene_variant: "#8c6d31",
    downstream_gene_variant: "#b15928",
    regulatory_region_variant: "#1b9e77",
    TF_binding_site_variant: "#d95f02",
    intergenic_variant: "#999999",
    "Not annotated": "#999999"
  };
  return colors[term] ?? "#6a51a3";
}

export function questionRecord(entry) {
  const {question, result, run} = entry;
  const root = element("article");
  root.style.width = "100%";
  root.style.minWidth = "0";

  const comparison = element("div", "grid grid-cols-2");
  comparison.style.width = "100%";
  comparison.style.minWidth = "0";
  comparison.style.gridAutoRows = "1fr";
  comparison.style.alignItems = "stretch";

  const questionColumn = element("section", "card vepbench-record-card");
  questionColumn.style.minWidth = "0";
  const questionHeader = element("div");
  questionHeader.style.display = "flex";
  questionHeader.style.alignItems = "baseline";
  questionHeader.style.justifyContent = "space-between";
  questionHeader.style.gap = "1rem";
  questionHeader.append(
    element("h2", null, "Prompt given to model"),
    element("span", "muted", entry.question_label)
  );
  const reference = question.task_type === "ranking"
    ? element("p", "muted", `Reference panel: ${entry.answer}`)
    : element(
        "p",
        "muted",
        `Reference answer: ${question.answer_choice_id} · ${entry.answer}`
      );
  const consequence = element(
    "p",
    "muted",
    `VEP consequence: ${entry.consequence ?? "—"}`
  );
  const questionBody = markdownNode(question.prompt);
  questionBody.className = "vepbench-record-content";
  questionColumn.append(questionHeader, reference);
  if (question.task_type !== "ranking") questionColumn.append(consequence);
  questionColumn.append(questionBody);

  const responseBody = result?.response.content
    ? markdownNode(result.response.content)
    : element(
      "p",
      "muted",
      result
        ? "No completed response content."
        : run
          ? "This evaluation run does not contain a response for this question."
          : "No complete evaluation runs are available."
    );

  const answerColumn = element(
    "section",
    "card vepbench-record-card vepbench-answer-card"
  );
  answerColumn.style.minWidth = "0";
  const answerHeader = element("div");
  answerHeader.style.display = "flex";
  answerHeader.style.alignItems = "baseline";
  answerHeader.style.justifyContent = "space-between";
  answerHeader.style.gap = "1rem";
  answerHeader.append(
    element("h2", null, "Answer"),
    outcomeBadge(entry.outcome)
  );
  const prediction = question.task_type === "ranking"
    ? element(
        "p",
        "muted",
        result?.scoring.metric === "rank_correlation"
          ? `Spearman ρ: ${result.scoring.spearman_rho?.toFixed(3) ?? "—"} · `
            + `Pearson r: ${result.scoring.pearson_r?.toFixed(3) ?? "—"} · ${entry.prediction}`
          : "No parsed ranking prediction."
      )
    : element(
        "p",
        "muted",
        `Parsed prediction: ${result?.scoring.parsed_answer ?? "—"} · ${entry.prediction}`
      );
  const comparisonRows = predictionComparisonRows(question, result, entry.display_metadata);
  const answerSections = element("div", "vepbench-answer-sections");
  if (question.task_type === "ranking") {
    const plotSection = element("section", "vepbench-prediction-section");
    const plotHeader = element("div", "vepbench-section-heading");
    plotHeader.append(
      element("h3", null, "Predictions vs. measurements"),
      comparisonRows.length
        ? element("span", "muted", `${comparisonRows.length} variants`)
        : element("span", "muted", "Plot unavailable")
    );
    plotSection.append(plotHeader);
    if (comparisonRows.length) {
      plotSection.append(predictionComparisonPlot(comparisonRows));
      plotSection.append(element(
        "p",
        "muted vepbench-plot-caption",
        "Color shows the most severe VEP consequence; shape shows the variant type. "
        + "The dashed line shows the fitted linear trend."
      ));
    } else {
      plotSection.append(element(
        "p",
        "muted vepbench-plot-empty",
        "A comparison plot is available when the response contains valid numeric predictions."
      ));
    }
    answerSections.append(plotSection);
  }

  const responseSection = element("section", "vepbench-response-section");
  responseSection.append(element("h3", null, "Model response"));
  const answerBody = element("div", "vepbench-record-content");
  answerBody.append(responseBody);

  const reasoningSection = element("section");
  reasoningSection.style.borderTop = "1px solid var(--theme-foreground-faintest)";
  reasoningSection.style.marginTop = "1.25rem";
  reasoningSection.style.paddingTop = "0.75rem";
  reasoningSection.append(element("h3", null, "Reasoning"));
  const reasoningBody = result?.response.reasoning
    ? markdownNode(result.response.reasoning)
    : element("p", "muted", "No provider-exposed reasoning was supplied.");
  reasoningSection.append(reasoningBody);
  answerBody.append(reasoningSection);
  responseSection.append(answerBody);
  answerSections.append(responseSection);
  answerColumn.append(answerHeader, prediction, answerSections);

  comparison.append(questionColumn, answerColumn);
  root.append(comparison);

  const footer = element("p", "muted");
  footer.style.fontSize = "0.85rem";
  footer.style.margin = "0.75rem 0 0";
  footer.append(
    "Question ID ",
    element("code", null, question.question_id)
  );
  if (run) {
    footer.append(" · Run ", element("code", null, run.run_id));
  }
  if (entry.rawArchiveUrl) {
    const archive = element("a", null, "Download complete run archive");
    archive.href = entry.rawArchiveUrl;
    archive.rel = "noreferrer";
    archive.target = "_blank";
    footer.append(" · ", archive);
  }
  root.append(footer);
  return root;
}
