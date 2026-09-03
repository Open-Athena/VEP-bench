import MarkdownIt from "npm:markdown-it@14.1.0";

import {resultTypeForAnswer, resultTypeLabel} from "./benchmark-data.js";

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

export function questionUrl(questionId, runId = null, path = "./questions.html") {
  const parameters = new URLSearchParams({question: questionId});
  if (runId) parameters.set("run", runId);
  return `${path}?${parameters}`;
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

  const answerColumn = element("section", "card vepbench-record-card");
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
  answerColumn.append(answerHeader, prediction, answerBody);

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
