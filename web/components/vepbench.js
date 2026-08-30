import MarkdownIt from "npm:markdown-it@14.1.0";

const markdown = new MarkdownIt({
  html: false,
  breaks: true,
  linkify: true,
  typographer: false
});

export function scored(records) {
  return records.filter((record) => record.scoring.value !== null);
}

export function runCorrect(run) {
  return run.records_data.filter((record) => record.scoring.correct === true).length;
}

export function accuracy(records) {
  const scoredRecords = scored(records);
  if (!scoredRecords.length) return null;
  return scoredRecords.filter((record) => record.scoring.correct === true).length / scoredRecords.length;
}

export function formatFailures(records) {
  return records.filter((record) => record.scoring.parse_error !== null).length;
}

export function formatInteger(value) {
  return Number(value ?? 0).toLocaleString("en-US");
}

export function formatPercent(value) {
  return value === null ? "—" : `${(value * 100).toFixed(1)}%`;
}

export function formatCost(value) {
  return value ? `$${value.toFixed(3)}` : "—";
}

export function runTemplateVersion(run) {
  return run.records_data[0]?.question.provenance.template_version ?? "?";
}

export function formatRunLabel(run) {
  const model = run.records_data[0]?.model.model_id ?? "unknown model";
  const state = run.current_question_set ? "current" : "historical";
  return `${model} · prompt v${runTemplateVersion(run)} · ${state}`;
}

function runUsage(run) {
  return run.records_data.reduce((total, record) => {
    const usage = record.usage ?? {};
    total.tokens += usage.total_tokens ?? 0;
    total.cost += usage.cost ?? 0;
    return total;
  }, {tokens: 0, cost: 0});
}

export function leaderboardRows(runs) {
  return runs
    .map((run) => {
      const usage = runUsage(run);
      return {
        ...run,
        model_cell: {
          model: run.records_data[0]?.model.model_id ?? "unknown",
          provider: run.records_data[0]?.model.upstream_provider ?? "not reported"
        },
        prompt_set: `v${runTemplateVersion(run)} · ${run.current_question_set ? "current" : "historical"}`,
        correct: `${runCorrect(run)}/${scored(run.records_data).length}`,
        accuracy: accuracy(run.records_data),
        format_failures: formatFailures(run.records_data),
        tokens: usage.tokens,
        cost: usage.cost,
        inspect: run.run_id
      };
    })
    .sort((a, b) => (b.accuracy ?? -1) - (a.accuracy ?? -1) || a.run_id.localeCompare(b.run_id))
    .map((run, index) => ({...run, rank: index + 1}));
}

function link(label, href) {
  const anchor = document.createElement("a");
  anchor.href = href;
  anchor.textContent = label;
  anchor.className = "table-link";
  return anchor;
}

export function leaderboardTable(rows, Inputs) {
  return Inputs.table(rows, {
    columns: ["rank", "model_cell", "prompt_set", "correct", "accuracy", "format_failures", "api_errors", "tokens", "cost", "inspect"],
    header: {
      rank: "Rank",
      model_cell: "Model / provider",
      prompt_set: "Prompt set",
      correct: "Correct",
      accuracy: "Accuracy",
      format_failures: "Format",
      api_errors: "API",
      tokens: "Tokens",
      cost: "Cost",
      inspect: "Records"
    },
    format: {
      rank: (value) => String(value).padStart(2, "0"),
      model_cell: (value) => {
        const wrapper = document.createElement("span");
        wrapper.className = "model-cell";
        const strong = document.createElement("strong");
        strong.textContent = value.model;
        const small = document.createElement("small");
        small.textContent = value.provider;
        wrapper.append(strong, small);
        return wrapper;
      },
      accuracy: formatPercent,
      tokens: formatInteger,
      cost: formatCost,
      inspect: (runId) => link("Open", `./questions.html?run=${encodeURIComponent(runId)}`)
    },
    align: {rank: "left", correct: "right", accuracy: "right", format_failures: "right", api_errors: "right", tokens: "right", cost: "right"},
    width: {rank: 55, model_cell: 245, prompt_set: 125, correct: 85, accuracy: 90, format_failures: 75, api_errors: 55, tokens: 95, cost: 75, inspect: 70},
    layout: "fixed",
    rows: Math.max(2, rows.length),
    select: false
  });
}

function choiceText(question, choiceId) {
  return question.choices.find((choice) => choice.choice_id === choiceId)?.text ?? "—";
}

export function resultOutcome(result) {
  if (result.response.status === "api_error") return "API error";
  if (result.scoring.parse_error !== null) return "Format failure";
  return result.scoring.correct ? "Correct" : "Incorrect";
}

export function entriesForRun(run) {
  return run.records_data.map((result) => ({
    question_id: result.question_id,
    variant: result.question.provenance.source_record_id,
    answer: choiceText(result.question, result.question.answer_choice_id),
    prediction: choiceText(result.question, result.scoring.parsed_answer),
    outcome: resultOutcome(result),
    question: result.question,
    result,
    run
  }));
}

export function outcomeBadge(value) {
  const badge = document.createElement("span");
  const color = value === "Correct" ? "green" : value === "Format failure" ? "yellow" : "red";
  badge.className = color;
  badge.textContent = value;
  return badge;
}

function element(tag, className, text) {
  const node = document.createElement(tag);
  if (className) node.className = className;
  if (text !== undefined && text !== null) node.textContent = text;
  return node;
}

function markdownNode(source, className) {
  const node = element("div", className);
  node.innerHTML = markdown.render(source ?? "");
  for (const anchor of node.querySelectorAll("a")) anchor.rel = "noreferrer";
  return node;
}

function definitionList(rows) {
  const list = element("dl", "record-metadata");
  for (const [term, value] of rows) {
    const row = element("div");
    row.append(element("dt", null, term), element("dd", null, value));
    list.append(row);
  }
  return list;
}

function recordCard(title, body, className = "") {
  const card = element("section", `card ${className}`.trim());
  card.append(element("h2", null, title), body);
  return card;
}

function disclosure(summary, content, className = "") {
  const details = element("details", className);
  details.append(element("summary", null, summary), content);
  return details;
}

export function questionRecord(entry) {
  const {question, result, run} = entry;
  const root = element("article");
  const header = element("div", "card");
  const heading = element("div");
  heading.append(
    element("em", null, "Selected assay record"),
    element("h2", null, question.question_id),
    element("p", null, `${question.provenance.source_record_id} · prompt v${runTemplateVersion(run)}`)
  );
  header.append(heading, outcomeBadge(entry.outcome));
  root.append(header);

  const metadata = definitionList([
    ["Reference answer", `${question.answer_choice_id} · ${entry.answer}`],
    ["Parsed prediction", `${result.scoring.parsed_answer ?? "—"} · ${entry.prediction}`],
    ["Score", result.scoring.value === null ? "unscored" : String(result.scoring.value)],
    ["Finish reason", result.response.finish_reason ?? "—"],
    ["Model", result.model.model_id],
    ["Provider", result.model.upstream_provider ?? "not reported"],
    ["Evaluated", result.evaluated_at],
    ["Question digest", result.question_sha256]
  ]);
  root.append(recordCard("Record metadata", metadata, "metadata-card"));
  root.append(recordCard("Model-visible prompt", markdownNode(question.prompt, "rendered-markdown prompt-markdown"), "prompt-card"));

  const responseBody = result.response.content
    ? markdownNode(result.response.content, "rendered-markdown response-markdown")
    : element("p", "muted", "No completed response content.");
  root.append(recordCard("Observed response", responseBody, "response-card"));

  if (result.response.reasoning) {
    root.append(disclosure(
      "Provider-exposed reasoning",
      markdownNode(result.response.reasoning, "rendered-markdown reasoning-markdown")
    ));
  }

  const raw = element("pre");
  raw.append(element("code", null, JSON.stringify(result.response.raw, null, 2)));
  root.append(disclosure("Raw provider response", raw));

  const parameters = element("pre");
  parameters.append(element("code", null, JSON.stringify({
    generation_parameters: result.generation_parameters,
    usage: result.usage,
    latency_seconds: result.response.latency_seconds,
    run_id: result.run_id,
    question_set_sha256: result.question_set_sha256
  }, null, 2)));
  root.append(disclosure("Request and usage metadata", parameters));
  return root;
}
