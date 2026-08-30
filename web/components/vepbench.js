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

export function formatRunLabel(run) {
  const model = run.records_data[0]?.model.model_id ?? "unknown model";
  const provider = run.records_data[0]?.model.upstream_provider ?? "provider not reported";
  return `${model} · ${provider}`;
}

function taskForRun(run) {
  const questionId = run.records_data[0]?.question_id ?? "";
  if (questionId.startsWith("vep-most-severe-v1:")) {
    return {
      id: "consequence-classification",
      name: "Consequence classification",
      path: "./tasks/consequence-classification.html"
    };
  }
  return {id: "unknown", name: "Unclassified task", path: "./tasks.html"};
}

function modelName(modelId) {
  const name = modelId.split("/").at(-1) ?? modelId;
  if (name === "gpt-5.6-luna") return "GPT-5.6 Luna";
  return name;
}

export function leaderboardRows(runs) {
  const ranked = runs
    .filter((run) => run.current_question_set)
    .map((run) => {
      return {
        ...run,
        task: taskForRun(run),
        model_cell: {
          model: modelName(run.records_data[0]?.model.model_id ?? "unknown"),
          provider: run.records_data[0]?.model.upstream_provider ?? "not reported"
        },
        correct: `${runCorrect(run)}/${scored(run.records_data).length}`,
        accuracy: accuracy(run.records_data),
        format_failures: formatFailures(run.records_data),
        inspect: run.run_id
      };
    })
    .sort((a, b) =>
      a.task.name.localeCompare(b.task.name)
      || (b.accuracy ?? -1) - (a.accuracy ?? -1)
      || a.run_id.localeCompare(b.run_id)
    );
  let taskId = null;
  let rank = 0;
  return ranked.map((run) => {
    rank = run.task.id === taskId ? rank + 1 : 1;
    taskId = run.task.id;
    return {...run, rank};
  });
}

function link(label, href) {
  const anchor = document.createElement("a");
  anchor.href = href;
  anchor.textContent = label;
  return anchor;
}

export function leaderboardTable(rows, Inputs) {
  return Inputs.table(rows, {
    columns: ["task", "rank", "model_cell", "correct", "accuracy", "inspect"],
    header: {
      task: "Task",
      rank: "Rank",
      model_cell: "Model / provider",
      correct: "Correct",
      accuracy: "Accuracy",
      inspect: "Records"
    },
    format: {
      task: (value) => link(value.name, value.path),
      rank: (value) => String(value).padStart(2, "0"),
      model_cell: (value) => {
        const wrapper = document.createElement("span");
        const strong = document.createElement("strong");
        strong.textContent = value.model;
        const small = document.createElement("small");
        small.textContent = value.provider;
        wrapper.append(strong, " · ", small);
        return wrapper;
      },
      accuracy: formatPercent,
      inspect: (runId) => {
        const run = rows.find((candidate) => candidate.run_id === runId);
        return link("Open", `${run.task.path}?run=${encodeURIComponent(runId)}`);
      }
    },
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
  return run.records_data.map((result, index) => ({
    question_id: result.question_id,
    question_label: `Q${String(index + 1).padStart(3, "0")}`,
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

function markdownNode(source) {
  const node = element("div");
  node.innerHTML = markdown.render(source ?? "");
  for (const anchor of node.querySelectorAll("a")) anchor.rel = "noreferrer";
  return node;
}

function definitionList(rows) {
  const list = element("dl");
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
  const {question, result} = entry;
  const root = element("article");
  const header = element("div", "card");
  const heading = element("div");
  heading.append(
    element("em", null, "Selected assay record"),
    element("h2", null, entry.question_label),
    element("p", null, question.provenance.source_record_id)
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
  root.append(recordCard("Record metadata", metadata));
  root.append(recordCard("Model-visible prompt", markdownNode(question.prompt)));

  const responseBody = result.response.content
    ? markdownNode(result.response.content)
    : element("p", "muted", "No completed response content.");
  root.append(recordCard("Observed response", responseBody));

  if (result.response.reasoning) {
    root.append(disclosure(
      "Provider-exposed reasoning",
      markdownNode(result.response.reasoning)
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
