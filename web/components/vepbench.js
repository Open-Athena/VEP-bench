import MarkdownIt from "npm:markdown-it@14.1.0";
import {leaderboardLineSeries} from "./benchmark-data.js";

const markdown = new MarkdownIt({
  html: false,
  breaks: true,
  linkify: true,
  typographer: false
});

export {
  formatRunLabel,
  leaderboardLineSeries,
  leaderboardRows,
  overallLeaderboardRows
} from "./benchmark-data.js";

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
  table.style.overflowX = "auto";
  table.style.overflowY = "hidden";

  const gridColumns = "minmax(12rem, 2fr) minmax(11rem, 2fr) 7rem 7rem 6rem";
  const makeRow = () => {
    const row = document.createElement("div");
    row.setAttribute("role", "row");
    row.style.display = "grid";
    row.style.gridTemplateColumns = gridColumns;
    row.style.alignItems = "center";
    row.style.columnGap = "0.75rem";
    row.style.minWidth = "46rem";
    row.style.padding = "0.4rem 0.75rem";
    row.style.borderBottom = "1px solid var(--theme-foreground-faintest, #ddd)";
    return row;
  };

  const header = makeRow();
  const modelHeader = document.createElement("strong");
  modelHeader.setAttribute("role", "columnheader");
  modelHeader.textContent = "Model";
  const releaseHeader = document.createElement("strong");
  releaseHeader.setAttribute("role", "columnheader");
  releaseHeader.textContent = "Release date";
  const tokensHeader = document.createElement("strong");
  tokensHeader.setAttribute("role", "columnheader");
  tokensHeader.textContent = "Tokens";
  tokensHeader.style.textAlign = "right";
  const costHeader = document.createElement("strong");
  costHeader.setAttribute("role", "columnheader");
  costHeader.textContent = "Cost";
  costHeader.style.textAlign = "right";
  const scoreHeader = document.createElement("strong");
  scoreHeader.setAttribute("role", "columnheader");
  scoreHeader.textContent = rows.some((row) => row.task_scores) ? "Overall score" : "Score";
  scoreHeader.style.textAlign = "center";
  header.append(modelHeader, scoreHeader, releaseHeader, tokensHeader, costHeader);
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
    if (rowData.task_scores) {
      const taskNames = {
        clinvar: "ClinVar",
        vep_most_severe_consequence: "Consequence"
      };
      const breakdown = document.createElement("small");
      breakdown.style.display = "block";
      breakdown.style.color = "var(--theme-foreground-muted, #666)";
      breakdown.textContent = rowData.task_scores.map((task) =>
        `${taskNames[task.task_family] ?? task.task_family} ${formatPercent(task.accuracy)}`
      ).join(" · ");
      modelCell.append(breakdown);
    }

    const releaseCell = document.createElement("span");
    releaseCell.setAttribute("role", "cell");
    releaseCell.textContent = formatDate(rowData.release_date);
    releaseCell.style.fontVariantNumeric = "tabular-nums";

    const tokensCell = document.createElement("span");
    tokensCell.setAttribute("role", "cell");
    tokensCell.textContent = rowData.tokens === null ? "—" : formatInteger(rowData.tokens);
    tokensCell.style.textAlign = "right";
    tokensCell.style.fontVariantNumeric = "tabular-nums";

    const costCell = document.createElement("span");
    costCell.setAttribute("role", "cell");
    costCell.textContent = formatCost(rowData.cost);
    costCell.style.textAlign = "right";
    costCell.style.fontVariantNumeric = "tabular-nums";

    const scoreCell = document.createElement("span");
    scoreCell.setAttribute("role", "cell");
    scoreCell.style.minWidth = "0";
    scoreCell.style.overflow = "hidden";
    scoreCell.append(accuracyMeter(rowData.accuracy));
    row.append(modelCell, scoreCell, releaseCell, tokensCell, costCell);
    table.append(row);
  }

  return table;
}

const CHART_COLORS = [
  "#3b82f6",
  "#ef4444",
  "#10b981",
  "#f59e0b",
  "#8b5cf6",
  "#06b6d4",
  "#ec4899",
  "#64748b"
];

const CHART_DASHES = [null, "8 4", "2 3", "10 3 2 3", "12 3", "4 3", "1 3", "6 2 1 2", "14 4 3 4"];

function svgNode(name, attributes = {}) {
  const node = document.createElementNS("http://www.w3.org/2000/svg", name);
  for (const [key, value] of Object.entries(attributes)) node.setAttribute(key, String(value));
  return node;
}

function compactNumber(value) {
  return Intl.NumberFormat("en-US", {
    notation: value >= 1000 ? "compact" : "standard",
    maximumFractionDigits: 1
  }).format(value);
}

function chartMetricLabel(metric) {
  return metric === "cost" ? "Total cost (USD)" : "Total tokens";
}

function formatChartMetric(value, metric) {
  return metric === "cost" ? formatCost(value) : formatInteger(Math.round(value));
}

export function leaderboardLineChart(rows) {
  const root = document.createElement("section");
  root.setAttribute("aria-label", "Score efficiency by model family");
  root.style.width = "100%";
  root.style.minWidth = "0";

  const controls = document.createElement("div");
  controls.style.display = "flex";
  controls.style.alignItems = "center";
  controls.style.justifyContent = "flex-end";
  controls.style.gap = "0.6rem";
  controls.style.marginBottom = "0.75rem";
  const label = document.createElement("label");
  label.textContent = "Compare score against";
  label.style.fontSize = "0.85rem";
  label.style.color = "var(--theme-foreground-muted, #666)";
  const select = document.createElement("select");
  select.setAttribute("aria-label", "Horizontal chart metric");
  for (const [value, text] of [["cost", "Total cost"], ["tokens", "Total tokens"]]) {
    const option = document.createElement("option");
    option.value = value;
    option.textContent = text;
    select.append(option);
  }
  label.htmlFor = "vepbench-chart-metric";
  select.id = label.htmlFor;
  controls.append(label, select);

  const host = document.createElement("div");
  host.style.width = "100%";
  host.style.minHeight = "26rem";
  host.style.overflowX = "auto";
  const legend = document.createElement("div");
  legend.style.display = "flex";
  legend.style.flexWrap = "wrap";
  legend.style.justifyContent = "center";
  legend.style.gap = "0.5rem 1rem";
  legend.style.minHeight = "1.5rem";
  legend.style.fontSize = "0.85rem";
  root.append(controls, host, legend);

  let metric = "cost";
  let lastWidth = 0;
  const render = (observedWidth = host.clientWidth) => {
    const width = Math.max(560, Math.floor(observedWidth || 760));
    const height = 410;
    const margin = {top: 18, right: 22, bottom: 58, left: 66};
    const chartWidth = width - margin.left - margin.right;
    const chartHeight = height - margin.top - margin.bottom;
    const series = leaderboardLineSeries(rows, metric);
    const points = series.flatMap((entry) => entry.points);
    host.replaceChildren();
    legend.replaceChildren();

    if (points.length === 0) {
      const empty = document.createElement("p");
      empty.className = "muted";
      empty.style.margin = "0";
      empty.style.padding = "8rem 1rem";
      empty.style.textAlign = "center";
      empty.textContent = `${chartMetricLabel(metric)} is not available for the published runs.`;
      host.append(empty);
      host.style.minHeight = "22rem";
      return;
    }
    host.style.minHeight = "26rem";

    const maxX = Math.max(...points.map((point) => point.x));
    const xDomainMax = maxX > 0 ? maxX * 1.06 : 1;
    const xPosition = (value) => margin.left + (value / xDomainMax) * chartWidth;
    const yPosition = (value) => margin.top + (1 - value) * chartHeight;
    const svg = svgNode("svg", {
      viewBox: `0 0 ${width} ${height}`,
      width,
      height,
      role: "img",
      "aria-labelledby": "vepbench-line-chart-title vepbench-line-chart-description"
    });
    svg.style.display = "block";
    svg.style.maxWidth = "none";
    svg.style.color = "var(--theme-foreground, currentColor)";
    const title = svgNode("title", {id: "vepbench-line-chart-title"});
    title.textContent = `Score versus ${chartMetricLabel(metric)} by model family`;
    const description = svgNode("desc", {id: "vepbench-line-chart-description"});
    description.textContent = "Each line joins evaluated configurations from the same model family.";
    svg.append(title, description);

    for (let index = 0; index <= 4; index += 1) {
      const accuracy = index / 4;
      const y = yPosition(accuracy);
      svg.append(svgNode("line", {
        x1: margin.left,
        x2: width - margin.right,
        y1: y,
        y2: y,
        stroke: "var(--theme-foreground-faintest, #ddd)",
        "stroke-width": 1
      }));
      const tick = svgNode("text", {
        x: margin.left - 10,
        y: y + 4,
        "text-anchor": "end",
        fill: "var(--theme-foreground-muted, #666)",
        "font-size": 12
      });
      tick.textContent = `${Math.round(accuracy * 100)}%`;
      svg.append(tick);
    }

    for (let index = 0; index <= 4; index += 1) {
      const value = (xDomainMax * index) / 4;
      const x = xPosition(value);
      svg.append(svgNode("line", {
        x1: x,
        x2: x,
        y1: margin.top,
        y2: height - margin.bottom,
        stroke: "var(--theme-foreground-faintest, #ddd)",
        "stroke-width": 1
      }));
      const tick = svgNode("text", {
        x,
        y: height - margin.bottom + 21,
        "text-anchor": "middle",
        fill: "var(--theme-foreground-muted, #666)",
        "font-size": 12
      });
      tick.textContent = metric === "cost" ? formatCost(value) : compactNumber(value);
      svg.append(tick);
    }

    const yLabel = svgNode("text", {
      x: 16,
      y: margin.top + chartHeight / 2,
      transform: `rotate(-90 16 ${margin.top + chartHeight / 2})`,
      "text-anchor": "middle",
      fill: "var(--theme-foreground-muted, #666)",
      "font-size": 13
    });
    yLabel.textContent = "Score";
    const xLabel = svgNode("text", {
      x: margin.left + chartWidth / 2,
      y: height - 10,
      "text-anchor": "middle",
      fill: "var(--theme-foreground-muted, #666)",
      "font-size": 13
    });
    xLabel.textContent = chartMetricLabel(metric);
    svg.append(yLabel, xLabel);

    series.forEach((entry, index) => {
      const color = CHART_COLORS[index % CHART_COLORS.length];
      const dash = CHART_DASHES[index % CHART_DASHES.length];
      if (entry.points.length > 1) {
        const path = entry.points
          .map((point, pointIndex) =>
            `${pointIndex === 0 ? "M" : "L"}${xPosition(point.x)},${yPosition(point.accuracy)}`
          )
          .join(" ");
        const pathNode = svgNode("path", {
          d: path,
          fill: "none",
          stroke: color,
          "stroke-width": 2.5,
          "stroke-linejoin": "round",
          "stroke-linecap": "round"
        });
        if (dash) pathNode.setAttribute("stroke-dasharray", dash);
        svg.append(pathNode);
      }
      for (const point of entry.points) {
        const circle = svgNode("circle", {
          cx: xPosition(point.x),
          cy: yPosition(point.accuracy),
          r: 5,
          fill: color,
          stroke: "var(--theme-background, #fff)",
          "stroke-width": 2,
          tabindex: 0,
          "aria-label": `${entry.family}, ${point.row.model_cell.model}: ${formatPercent(point.accuracy)}, ${formatChartMetric(point.x, metric)}`
        });
        const tooltip = svgNode("title");
        tooltip.textContent = [
          point.row.model_cell.model,
          point.row.model_cell.provider,
          `Score: ${formatPercent(point.accuracy)}`,
          `${chartMetricLabel(metric)}: ${formatChartMetric(point.x, metric)}`
        ].join("\n");
        circle.append(tooltip);
        svg.append(circle);
      }

      const item = document.createElement("span");
      item.style.display = "inline-flex";
      item.style.alignItems = "center";
      item.style.gap = "0.4rem";
      const swatch = svgNode("svg", {width: 20, height: 8, viewBox: "0 0 20 8", "aria-hidden": true});
      const swatchLine = svgNode("line", {
        x1: 0,
        x2: 20,
        y1: 4,
        y2: 4,
        stroke: color,
        "stroke-width": 2.5,
        "stroke-linecap": "round"
      });
      if (dash) swatchLine.setAttribute("stroke-dasharray", dash);
      swatch.append(swatchLine);
      item.append(swatch, entry.family);
      legend.append(item);
    });

    host.append(svg);
  };

  select.addEventListener("change", () => {
    metric = select.value;
    render(lastWidth || host.clientWidth);
  });
  render();
  if (typeof ResizeObserver === "function") {
    const observer = new ResizeObserver((entries) => {
      const width = entries[0]?.contentRect.width ?? 0;
      if (width > 0 && Math.abs(width - lastWidth) >= 1) {
        lastWidth = width;
        render(width);
      }
    });
    observer.observe(host);
  }
  return root;
}

function choiceText(question, choiceId) {
  return question.choices.find((choice) => choice.choice_id === choiceId)?.text ?? "—";
}

export function resultOutcome(result) {
  if (!result) return "Not evaluated";
  if (result.scoring.parse_error !== null) return "Format failure";
  return result.scoring.correct ? "Correct" : "Incorrect";
}

export function entryForAnswer(question, index, result, run, rawArchiveUrl = null) {
  return {
    question_id: question.question_id,
    question_label: `Q${String(index + 1).padStart(3, "0")}`,
    variant: question.provenance.source_record_id,
    answer: choiceText(question, question.answer_choice_id),
    prediction: choiceText(question, result?.scoring.parsed_answer),
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
    answer: choiceText(question, question.answer_choice_id),
    prediction: "—",
    outcome: "Select to load",
    question,
    result: null,
    run: null
  }));
}

export function outcomeBadge(value) {
  const badge = document.createElement("span");
  const outcome = value === "Correct"
    ? "correct"
    : value === "Incorrect"
      ? "incorrect"
      : value === "Format failure"
        ? "format-failure"
        : "";
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
