import MarkdownIt from "npm:markdown-it@14.1.0";

const markdown = new MarkdownIt({
  html: false,
  breaks: true,
  linkify: true,
  typographer: false
});

export {
  accuracy,
  formatFailures,
  formatRunLabel,
  leaderboardRows,
  runCorrect,
  scored
} from "./benchmark-data.js";

export function formatInteger(value) {
  return Number(value ?? 0).toLocaleString("en-US");
}

export function formatPercent(value) {
  return value === null ? "—" : `${(value * 100).toFixed(1)}%`;
}

export function questionUrl(questionId, runId = null, path = "./questions.html") {
  const parameters = new URLSearchParams({question: questionId});
  if (runId) parameters.set("run", runId);
  return `${path}?${parameters}`;
}

function accuracyMeter(value) {
  const wrapper = document.createElement("span");
  wrapper.style.display = "inline-flex";
  wrapper.style.alignItems = "center";
  wrapper.style.gap = "0.6rem";
  wrapper.style.width = "100%";
  wrapper.style.minWidth = "0";
  wrapper.style.whiteSpace = "nowrap";
  const label = document.createElement("strong");
  label.textContent = formatPercent(value);
  label.style.flex = "0 0 4.5rem";
  label.style.textAlign = "right";
  const meter = document.createElement("meter");
  meter.min = 0;
  meter.max = 1;
  meter.value = value;
  meter.style.flex = "1 1 0";
  meter.style.width = "0";
  meter.style.minWidth = "0";
  meter.style.height = "1rem";
  meter.setAttribute("aria-label", `${formatPercent(value)} exact-match accuracy`);
  wrapper.append(label, meter);
  return wrapper;
}

export function leaderboardTable(rows) {
  const table = document.createElement("div");
  table.setAttribute("role", "table");
  table.setAttribute("aria-label", "VEPBench leaderboard");
  table.style.width = "100%";
  table.style.maxWidth = "100%";
  table.style.minWidth = "0";
  table.style.overflow = "hidden";

  const gridColumns = "minmax(0, 2fr) minmax(0, 3fr)";
  const makeRow = () => {
    const row = document.createElement("div");
    row.setAttribute("role", "row");
    row.style.display = "grid";
    row.style.gridTemplateColumns = gridColumns;
    row.style.alignItems = "center";
    row.style.columnGap = "1rem";
    row.style.minWidth = "0";
    row.style.padding = "0.4rem 0.75rem";
    row.style.borderBottom = "1px solid var(--theme-foreground-faintest, #ddd)";
    return row;
  };

  const header = makeRow();
  const modelHeader = document.createElement("strong");
  modelHeader.setAttribute("role", "columnheader");
  modelHeader.textContent = "Model";
  const scoreHeader = document.createElement("strong");
  scoreHeader.setAttribute("role", "columnheader");
  scoreHeader.textContent = "Score";
  scoreHeader.style.textAlign = "center";
  header.append(modelHeader, scoreHeader);
  table.append(header);

  for (const rowData of rows) {
    const row = makeRow();
    const modelCell = document.createElement("span");
    modelCell.setAttribute("role", "cell");
    modelCell.style.minWidth = "0";
    modelCell.style.overflow = "hidden";
    modelCell.style.textOverflow = "ellipsis";
    modelCell.style.whiteSpace = "nowrap";
    modelCell.title = `${rowData.model_cell.model} · ${rowData.model_cell.provider}`;
    const model = document.createElement("strong");
    model.textContent = rowData.model_cell.model;
    const provider = document.createElement("small");
    provider.textContent = rowData.model_cell.provider;
    modelCell.append(model, " · ", provider);

    const scoreCell = document.createElement("span");
    scoreCell.setAttribute("role", "cell");
    scoreCell.style.minWidth = "0";
    scoreCell.style.overflow = "hidden";
    scoreCell.append(accuracyMeter(rowData.accuracy));
    row.append(modelCell, scoreCell);
    table.append(row);
  }

  return table;
}

function choiceText(question, choiceId) {
  return question.choices.find((choice) => choice.choice_id === choiceId)?.text ?? "—";
}

export function resultOutcome(result) {
  if (result.scoring.parse_error !== null) return "Format failure";
  return result.scoring.correct ? "Correct" : "Incorrect";
}

export function entryForResult(result, index, run) {
  return {
    question_id: result.question_id,
    question_label: `Q${String(index + 1).padStart(3, "0")}`,
    variant: result.question.provenance.source_record_id,
    answer: choiceText(result.question, result.question.answer_choice_id),
    prediction: choiceText(result.question, result.scoring.parsed_answer),
    outcome: resultOutcome(result),
    question: result.question,
    result,
    run
  };
}

export function entriesForRun(run) {
  return run.records_data.map((result, index) => entryForResult(result, index, run));
}

export function entriesForQuestions(questions) {
  return questions.map((question, index) => ({
    question_id: question.question_id,
    question_label: `Q${String(index + 1).padStart(3, "0")}`,
    variant: question.provenance.source_record_id,
    answer: choiceText(question, question.answer_choice_id),
    prediction: "—",
    outcome: "Not evaluated",
    question,
    result: null,
    run: null
  }));
}

export function outcomeBadge(value) {
  const badge = document.createElement("span");
  const color = value === "Correct"
    ? "green"
    : value === "Format failure"
      ? "yellow"
      : value === "Not evaluated"
        ? ""
        : "red";
  if (color) badge.className = color;
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
    element("h2", null, "Question"),
    element("span", "muted", entry.question_label)
  );
  const reference = element(
    "p",
    "muted",
    `Reference answer: ${question.answer_choice_id} · ${entry.answer}`
  );
  const questionBody = markdownNode(question.prompt);
  questionBody.className = "vepbench-record-content";
  questionColumn.append(questionHeader, reference, questionBody);

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
  const prediction = element(
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
  root.append(footer);
  return root;
}
