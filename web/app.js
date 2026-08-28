"use strict";

const state = { manifest: null, questions: [], results: [], runs: [] };
const viewNode = document.querySelector("#view");
const statusNode = document.querySelector("#status");
const dialog = document.querySelector("#detail-dialog");
const detailTitle = document.querySelector("#detail-title");
const detailBody = document.querySelector("#detail-body");

document.querySelector("#close-dialog").addEventListener("click", () => dialog.close());
dialog.addEventListener("click", (event) => {
  if (event.target === dialog) dialog.close();
});
window.addEventListener("hashchange", renderRoute);

function node(tag, options = {}, children = []) {
  const element = document.createElement(tag);
  if (options.className) element.className = options.className;
  if (options.text !== undefined) element.textContent = String(options.text);
  if (options.type) element.type = options.type;
  if (options.value !== undefined) element.value = options.value;
  if (options.href) element.href = options.href;
  for (const [key, value] of Object.entries(options.data || {})) element.dataset[key] = value;
  for (const child of Array.isArray(children) ? children : [children]) {
    if (child !== null && child !== undefined) element.append(child);
  }
  return element;
}

function parseJsonl(text, path) {
  return text.split(/\r?\n/).filter((line) => line.trim()).map((line, index) => {
    try { return JSON.parse(line); }
    catch (error) { throw new Error(`${path}:${index + 1}: ${error.message}`); }
  });
}

async function loadJson(url) {
  const response = await fetch(url);
  if (!response.ok) throw new Error(`${url}: HTTP ${response.status}`);
  return response.json();
}

async function loadJsonl(url) {
  const response = await fetch(url);
  if (!response.ok) throw new Error(`${url}: HTTP ${response.status}`);
  return parseJsonl(await response.text(), url);
}

async function loadData() {
  try {
    state.manifest = await loadJson("data/manifest.json");
    const [questions, ...runs] = await Promise.all([
      loadJsonl(state.manifest.questions.path),
      ...state.manifest.results.map((entry) => loadJsonl(entry.path)),
    ]);
    state.questions = questions;
    state.runs = runs.map((records, index) => ({ ...state.manifest.results[index], records }));
    state.results = runs.flat();
    statusNode.hidden = true;
    viewNode.hidden = false;
    renderRoute();
  } catch (error) {
    statusNode.classList.add("error");
    statusNode.textContent = `Could not load benchmark data: ${error.message}`;
  }
}

function currentRoute() {
  const route = window.location.hash.replace(/^#/, "");
  return ["overview", "runs", "questions", "matrix"].includes(route) ? route : "overview";
}

function renderRoute() {
  if (!state.manifest) return;
  const route = currentRoute();
  document.querySelectorAll("[data-view]").forEach((link) => {
    if (link.dataset.view === route) link.setAttribute("aria-current", "page");
    else link.removeAttribute("aria-current");
  });
  viewNode.replaceChildren();
  ({ overview: renderOverview, runs: renderRuns, questions: renderQuestions, matrix: renderMatrix })[route]();
}

function sectionHead(title, copy) {
  return node("div", { className: "section-head" }, [
    node("h2", { text: title }),
    node("p", { text: copy }),
  ]);
}

function scored(records = state.results) {
  return records.filter((result) => result.scoring.value !== null);
}

function accuracy(records) {
  const values = scored(records);
  if (!values.length) return null;
  return values.reduce((sum, result) => sum + result.scoring.value, 0) / values.length;
}

function formatAccuracy(value) {
  return value === null ? "—" : `${(value * 100).toFixed(1)}%`;
}

function renderOverview() {
  viewNode.append(sectionHead("Benchmark overview", "A compact view of the committed artifacts. API failures are excluded from accuracy and shown separately."));
  const models = new Set(state.results.map((result) => result.model.model_id));
  const failures = state.results.filter((result) => result.response.status === "api_error").length;
  const metrics = [
    ["Questions", state.questions.length],
    ["Runs / models", `${state.runs.length} / ${models.size}`],
    ["Scored outputs", scored().length],
    ["Overall accuracy", formatAccuracy(accuracy(state.results))],
  ];
  viewNode.append(node("div", { className: "metric-grid" }, metrics.map(([label, value]) =>
    node("div", { className: "metric" }, [node("span", { className: "metric-label", text: label }), node("strong", { className: "metric-value", text: value })])
  )));

  const panel = node("section", { className: "panel" }, node("h3", { text: "Accuracy by task family" }));
  const families = [...new Set(state.questions.map((question) => question.metadata.task_family))].sort();
  if (!state.results.length) panel.append(node("p", { className: "empty", text: "No committed evaluation runs yet." }));
  for (const family of families) {
    const records = state.results.filter((result) => result.question.task_family === family);
    const value = accuracy(records);
    panel.append(node("div", { className: "bar-row" }, [
      node("span", { text: family }),
      node("div", { className: "bar-track" }, node("div", { className: "bar-fill" })),
      node("span", { className: "bar-value", text: `${formatAccuracy(value)} · n=${scored(records).length}` }),
    ]));
    panel.lastElementChild.querySelector(".bar-fill").style.width = `${(value || 0) * 100}%`;
  }
  if (failures) panel.append(node("p", { className: "run-meta", text: `${failures} API failure(s) are unscored.` }));
  viewNode.append(panel);
}

function renderRuns() {
  viewNode.append(sectionHead("Evaluation runs", "Each card corresponds to one committed JSONL run and retains its exact model settings, usage, responses, and routing metadata."));
  if (!state.runs.length) {
    viewNode.append(node("p", { className: "empty", text: "No committed evaluation runs yet." }));
    return;
  }
  const grid = node("div", { className: "run-grid" });
  for (const run of state.runs) {
    const first = run.records[0];
    const failures = run.api_errors;
    const provider = first.model.upstream_provider || "Provider not reported";
    const card = node("article", { className: "run-card" }, [
      node("span", { className: (!run.complete || failures) ? "pill error" : "pill", text: (!run.complete || failures) ? "Incomplete" : "Complete" }),
      node("h3", { text: first.model.model_id }),
      node("p", { className: "run-meta", text: `${first.run_id} · ${provider}` }),
      node("p", { text: `${run.records.length} output(s), ${failures} API error(s)` }),
      node("div", { className: "run-score" }, [node("span", { text: "Accuracy" }), node("strong", { text: formatAccuracy(accuracy(run.records)) })]),
    ]);
    grid.append(card);
  }
  viewNode.append(grid);
}

function option(value, label) {
  return node("option", { value, text: label });
}

function selectField(label, id, options) {
  const select = node("select");
  select.id = id;
  options.forEach(([value, text]) => select.append(option(value, text)));
  const labelNode = node("label", { text: label });
  labelNode.htmlFor = id;
  return node("div", { className: "field" }, [labelNode, select]);
}

function renderQuestions() {
  viewNode.append(sectionHead("Question explorer", "Filter the public questions, inspect exact prompts and answer keys, then open any available model output and provider-exposed reasoning."));
  const families = [...new Set(state.questions.map((question) => question.metadata.task_family))].sort();
  const filters = node("div", { className: "filters" }, [
    selectField("Task family", "family-filter", [["", "All families"], ...families.map((value) => [value, value])]),
    selectField("Run", "run-filter", [["", "All runs"], ...state.runs.map((run) => [run.run_id, run.run_id])]),
    selectField("Outcome", "outcome-filter", [["", "All outcomes"], ["correct", "Correct"], ["incorrect", "Incorrect"], ["api_error", "API error"], ["not_run", "Not evaluated"]]),
  ]);
  viewNode.append(filters);
  const list = node("div", { className: "question-list" });
  viewNode.append(list);

  const refresh = () => {
    const family = document.querySelector("#family-filter").value;
    const runId = document.querySelector("#run-filter").value;
    const outcomeFilter = document.querySelector("#outcome-filter").value;
    list.replaceChildren();
    const visible = state.questions.filter((question) => {
      if (family && question.metadata.task_family !== family) return false;
      const result = resultFor(question.question_id, runId);
      return !outcomeFilter || outcome(result) === outcomeFilter;
    });
    if (!visible.length) list.append(node("p", { className: "empty", text: "No questions match these filters." }));
    for (const question of visible) {
      const result = resultFor(question.question_id, runId);
      const resultOutcome = outcome(result);
      const button = node("button", { className: "question-button", type: "button" }, [
        node("div", {}, [
          node("p", { className: "question-id", text: question.question_id }),
          node("p", { className: "question-excerpt", text: excerpt(question.prompt) }),
        ]),
        node("span", { className: `outcome ${resultOutcome}`, text: outcomeLabel(resultOutcome) }),
      ]);
      button.addEventListener("click", () => openDetail(question, result));
      list.append(button);
    }
  };
  filters.querySelectorAll("select").forEach((select) => select.addEventListener("change", refresh));
  refresh();
}

function resultFor(questionId, runId) {
  const candidates = state.results.filter((result) => result.question_id === questionId && (!runId || result.run_id === runId));
  return candidates[0] || null;
}

function outcome(result) {
  if (!result) return "not_run";
  if (result.response.status === "api_error") return "api_error";
  return result.scoring.correct ? "correct" : "incorrect";
}

function outcomeLabel(value) {
  return ({ correct: "✓ Correct", incorrect: "× Incorrect", api_error: "! API error", not_run: "— Not run" })[value];
}

function excerpt(prompt) {
  const variantLine = prompt.split("\n").find((line) => line.startsWith("Variant:"));
  return variantLine || prompt.slice(0, 160);
}

function renderMatrix() {
  viewNode.append(sectionHead("Question × model matrix", "Cells show deterministic exact-match outcomes. Each column is a run, labelled by its requested model."));
  if (!state.runs.length) {
    viewNode.append(node("p", { className: "empty", text: "No committed evaluation runs yet." }));
    return;
  }
  const table = node("table");
  const header = node("tr", {}, node("th", { className: "matrix-question", text: "Question" }));
  for (const run of state.runs) {
    const first = run.records[0];
    header.append(node("th", { className: "matrix-model" }, [node("span", { text: first.model.model_id }), node("small", { text: first.run_id })]));
  }
  table.append(node("thead", {}, header));
  const body = node("tbody");
  for (const question of state.questions) {
    const row = node("tr", {}, node("td", { className: "matrix-question" }, [node("div", { className: "question-id", text: question.question_id }), node("span", { text: excerpt(question.prompt) })]));
    for (const run of state.runs) {
      const result = run.records.find((record) => record.question_id === question.question_id) || null;
      const value = outcome(result);
      const cell = node("td", { className: `matrix-cell ${value}`, text: ({ correct: "✓", incorrect: "×", api_error: "!", not_run: "—" })[value] });
      if (result) {
        cell.tabIndex = 0;
        cell.setAttribute("role", "button");
        cell.setAttribute("aria-label", `${outcomeLabel(value)}; inspect ${question.question_id} for ${result.model.model_id}`);
        cell.addEventListener("click", () => openDetail(question, result));
        cell.addEventListener("keydown", (event) => { if (event.key === "Enter" || event.key === " ") openDetail(question, result); });
      }
      row.append(cell);
    }
    body.append(row);
  }
  table.append(body);
  viewNode.append(node("div", { className: "table-wrap" }, table));
}

function detailSection(title, content) {
  return node("section", { className: "detail-section" }, [node("h3", { text: title }), content]);
}

function openDetail(question, result) {
  detailTitle.textContent = question.question_id;
  detailBody.replaceChildren();
  detailBody.append(detailSection("Model-visible prompt", node("div", { className: "prompt", text: question.prompt })));
  const choices = node("div");
  for (const choice of question.choices) {
    const answer = choice.choice_id === question.answer_choice_id;
    choices.append(node("div", { className: answer ? "choice answer" : "choice" }, [node("strong", { text: `${choice.choice_id}.` }), node("span", { text: `${choice.text}${answer ? " — expected answer" : ""}` })]));
  }
  detailBody.append(detailSection("Choices and answer key", choices));
  if (!result) {
    detailBody.append(node("p", { className: "empty", text: "No result is available for the selected run." }));
  } else {
    const metadata = node("dl", { className: "detail-grid" });
    [["Run", result.run_id], ["Model", result.model.model_id], ["Provider", result.model.upstream_provider || "Not reported"], ["Outcome", outcomeLabel(outcome(result))], ["Parsed answer", result.scoring.parsed_answer || "None"], ["Evaluated", result.evaluated_at]].forEach(([term, value]) => metadata.append(node("div", {}, [node("dt", { text: term }), node("dd", { text: value })])));
    detailBody.append(detailSection("Evaluation", metadata));
    detailBody.append(detailSection("Model response", node("pre", { text: result.response.content || "No response content." })));
    detailBody.append(detailSection("Provider-exposed reasoning", node("pre", { text: result.response.reasoning || "No reasoning text or summary was exposed by the provider." })));
    const raw = node("details", {}, [node("summary", { text: "Raw provider response and metadata" }), node("pre", { text: JSON.stringify(result.response.raw, null, 2) })]);
    detailBody.append(detailSection("Reproducibility", node("div", {}, [
      node("p", { text: `Question SHA-256: ${result.question_sha256}` }),
      node("p", { text: `Question set SHA-256: ${result.question_set_sha256}` }),
      raw,
    ])));
  }
  dialog.showModal();
}

loadData();
