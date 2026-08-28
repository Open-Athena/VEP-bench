"use strict";

const state = { manifest: null, questions: [], results: [], runs: [] };
const viewNode = document.querySelector("#view");
const statusNode = document.querySelector("#status");
const dialog = document.querySelector("#detail-dialog");
const detailKind = document.querySelector("#detail-kind");
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

function taskFamily(question) {
  return question.metadata.task_family;
}

function evaluationDate(records) {
  const values = records.map((record) => record.evaluated_at).filter(Boolean).sort();
  if (!values.length) return "Unknown";
  const dates = [...new Set(values.map((value) => value.slice(0, 10)))];
  return dates.length === 1 ? dates[0] : `${dates[0]} – ${dates[dates.length - 1]}`;
}

function coverageLabel(run) {
  const percentage = run.questions_expected
    ? (100 * run.questions_covered / run.questions_expected).toFixed(1)
    : "0.0";
  return `${run.questions_covered}/${run.questions_expected} (${percentage}%)`;
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
  const families = [...new Set([
    ...state.questions.map(taskFamily),
    ...state.results.map((result) => taskFamily(result.question)),
  ])].sort();
  if (!state.results.length) panel.append(node("p", { className: "empty", text: "No committed evaluation runs yet." }));
  for (const family of families) {
    const records = state.results.filter((result) => taskFamily(result.question) === family);
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
      node("dl", { className: "run-facts" }, [
        fact("Coverage", coverageLabel(run)),
        fact("Evaluated", evaluationDate(run.records)),
        fact("API errors", failures),
      ]),
      node("div", { className: "run-score" }, [node("span", { text: "Accuracy" }), node("strong", { text: formatAccuracy(accuracy(run.records)) })]),
    ]);
    card.tabIndex = 0;
    card.setAttribute("role", "button");
    card.addEventListener("click", () => openRunDetail(run));
    card.addEventListener("keydown", (event) => {
      if (event.key === "Enter" || event.key === " ") openRunDetail(run);
    });
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

function fact(term, value) {
  return node("div", {}, [node("dt", { text: term }), node("dd", { text: value })]);
}

function questionsEqual(left, right) {
  return JSON.stringify(left) === JSON.stringify(right);
}

function entriesForRun(run) {
  const entries = state.questions.map((question) => {
    const result = run.records.find((record) =>
      record.question_id === question.question_id
      && questionsEqual(record.question, question)
    ) || null;
    return { question, result, run };
  });
  for (const result of run.records) {
    const isCurrent = state.questions.some((question) =>
      question.question_id === result.question_id
      && questionsEqual(question, result.question)
    );
    if (!isCurrent) entries.push({ question: result.question, result, run });
  }
  return entries;
}

function renderQuestions() {
  viewNode.append(sectionHead("Question explorer", "Filter the public questions, inspect exact prompts and answer keys, then open any available model output and provider-exposed reasoning."));
  const families = [...new Set([
    ...state.questions.map(taskFamily),
    ...state.results.map((result) => taskFamily(result.question)),
  ])].sort();
  const filters = node("div", { className: "filters" }, [
    selectField("Task family", "family-filter", [["", "All families"], ...families.map((value) => [value, value])]),
    selectField("Run", "run-filter", [["", "All runs"], ...state.runs.map((run) => [run.run_id, run.run_id])]),
    selectField("Outcome", "outcome-filter", [["", "All outcomes"], ["correct", "Correct"], ["incorrect", "Incorrect"], ["parse_failure", "Parse failure"], ["api_error", "API error"], ["not_run", "Not evaluated"]]),
  ]);
  viewNode.append(filters);
  const list = node("div", { className: "question-list" });
  viewNode.append(list);

  const refresh = () => {
    const family = document.querySelector("#family-filter").value;
    const runId = document.querySelector("#run-filter").value;
    const outcomeFilter = document.querySelector("#outcome-filter").value;
    list.replaceChildren();
    const selectedRun = state.runs.find((run) => run.run_id === runId);
    let entries;
    if (selectedRun) entries = entriesForRun(selectedRun);
    else if (state.runs.length) entries = state.runs.flatMap(entriesForRun);
    else entries = state.questions.map((question) => ({ question, result: null, run: null }));
    const visible = entries.filter(({ question, result }) => {
      if (family && taskFamily(question) !== family) return false;
      return !outcomeFilter || outcome(result) === outcomeFilter;
    }).sort((left, right) =>
      left.question.question_id.localeCompare(right.question.question_id)
      || (left.run?.run_id || "").localeCompare(right.run?.run_id || "")
    );
    if (!visible.length) list.append(node("p", { className: "empty", text: "No questions match these filters." }));
    for (const { question, result, run } of visible) {
      const resultOutcome = outcome(result);
      const button = node("button", { className: "question-button", type: "button" }, [
        node("div", {}, [
          node("p", { className: "question-id", text: question.question_id }),
          node("p", { className: "question-excerpt", text: excerpt(question.prompt) }),
          node("p", { className: "run-meta", text: run ? `${run.run_id} · ${result?.model.model_id || "not evaluated"}` : "No runs committed" }),
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

function outcome(result) {
  if (!result) return "not_run";
  if (result.response.status === "api_error") return "api_error";
  if (result.scoring.parse_error) return "parse_failure";
  return result.scoring.correct ? "correct" : "incorrect";
}

function outcomeLabel(value) {
  return ({ correct: "✓ Correct", incorrect: "× Incorrect", parse_failure: "? Parse failure", api_error: "! API error", not_run: "— Not run", version_mismatch: "◇ Different question version" })[value];
}

function excerpt(prompt) {
  const variantLine = prompt.split("\n").find((line) => line.startsWith("Variant:"));
  return variantLine || prompt.slice(0, 160);
}

function renderMatrix() {
  viewNode.append(sectionHead("Question × model matrix", "Cells show deterministic exact-match outcomes. A diamond marks a result from a different version of the same question ID."));
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
      const result = run.records.find((record) =>
        record.question_id === question.question_id
        && questionsEqual(record.question, question)
      ) || null;
      const historicalResult = result ? null : run.records.find((record) =>
        record.question_id === question.question_id
      ) || null;
      const value = historicalResult ? "version_mismatch" : outcome(result);
      const cell = node("td", { className: `matrix-cell ${value}`, text: ({ correct: "✓", incorrect: "×", parse_failure: "?", api_error: "!", not_run: "—", version_mismatch: "◇" })[value] });
      const inspectable = result || historicalResult;
      if (inspectable) {
        cell.tabIndex = 0;
        cell.setAttribute("role", "button");
        cell.setAttribute("aria-label", `${outcomeLabel(value)}; inspect ${question.question_id} for ${inspectable.model.model_id}`);
        cell.addEventListener("click", () => openDetail(question, inspectable));
        cell.addEventListener("keydown", (event) => { if (event.key === "Enter" || event.key === " ") openDetail(question, inspectable); });
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
  if (result) question = result.question;
  detailKind.textContent = "Question detail";
  detailTitle.textContent = question.question_id;
  detailBody.replaceChildren();
  detailBody.append(detailSection("Model-visible prompt", node("div", { className: "prompt", text: question.prompt })));
  const choices = node("div");
  for (const choice of question.choices) {
    const answer = choice.choice_id === question.answer_choice_id;
    const selected = result && choice.choice_id === result.scoring.parsed_answer;
    const annotations = [answer ? "expected" : null, selected ? "selected" : null].filter(Boolean);
    choices.append(node("div", { className: `choice${answer ? " answer" : ""}${selected ? " selected" : ""}` }, [
      node("strong", { text: `${choice.choice_id}.` }),
      node("span", { text: `${choice.text}${annotations.length ? ` — ${annotations.join(", ")}` : ""}` }),
    ]));
  }
  detailBody.append(detailSection("Choices and answer key", choices));
  if (!result) {
    detailBody.append(node("p", { className: "empty", text: "No result is available for the selected run." }));
  } else {
    const metadata = node("dl", { className: "detail-grid" });
    [["Run", result.run_id], ["Model", result.model.model_id], ["Provider", result.model.upstream_provider || "Not reported"], ["Outcome", outcomeLabel(outcome(result))], ["Parsed answer", result.scoring.parsed_answer || "None"], ["Evaluated", result.evaluated_at]].forEach(([term, value]) => metadata.append(fact(term, value)));
    detailBody.append(detailSection("Evaluation", metadata));
    if (result.scoring.parse_error) detailBody.append(node("p", { className: "callout error", text: result.scoring.parse_error }));
    if (result.error) detailBody.append(detailSection("API error", node("pre", { text: JSON.stringify(result.error, null, 2) })));
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

function openRunDetail(run) {
  const first = run.records[0];
  detailKind.textContent = "Model detail";
  detailTitle.textContent = `${first.model.model_id} · ${run.run_id}`;
  detailBody.replaceChildren();

  const metadata = node("dl", { className: "detail-grid" }, [
    fact("Run", run.run_id),
    fact("Provider", first.model.upstream_provider || "Not reported"),
    fact("Coverage", coverageLabel(run)),
    fact("Evaluated", evaluationDate(run.records)),
    fact("Overall accuracy", formatAccuracy(accuracy(run.records))),
    fact("Question set", run.current_question_set ? "Current" : "Historical"),
  ]);
  detailBody.append(detailSection("Run summary", metadata));

  const familyPanel = node("div");
  const families = [...new Set(run.records.map((record) => taskFamily(record.question)))].sort();
  for (const family of families) {
    const records = run.records.filter((record) => taskFamily(record.question) === family);
    familyPanel.append(node("div", { className: "bar-row" }, [
      node("span", { text: family }),
      node("div", { className: "bar-track" }, node("div", { className: "bar-fill" })),
      node("span", { className: "bar-value", text: `${formatAccuracy(accuracy(records))} · n=${scored(records).length}` }),
    ]));
    familyPanel.lastElementChild.querySelector(".bar-fill").style.width = `${(accuracy(records) || 0) * 100}%`;
  }
  detailBody.append(detailSection("Accuracy by task family", familyPanel));

  const responses = node("div", { className: "response-list" });
  for (const result of run.records) {
    const item = node("details", { className: "response-item" }, [
      node("summary", {}, [
        node("span", { text: result.question_id }),
        node("span", { className: `outcome ${outcome(result)}`, text: outcomeLabel(outcome(result)) }),
      ]),
      node("div", { className: "response-body" }, [
        node("h4", { text: "Model-visible prompt" }),
        node("div", { className: "prompt", text: result.question.prompt }),
        node("p", { text: `Expected: ${result.question.answer_choice_id} · Selected: ${result.scoring.parsed_answer || "None"}` }),
        result.scoring.parse_error ? node("p", { className: "callout error", text: result.scoring.parse_error }) : null,
        result.error ? node("pre", { text: JSON.stringify(result.error, null, 2) }) : null,
        node("h4", { text: "Model response" }),
        node("pre", { text: result.response.content || "No response content." }),
        node("h4", { text: "Provider-exposed reasoning" }),
        node("pre", { text: result.response.reasoning || "No reasoning text or summary was exposed by the provider." }),
        node("details", {}, [
          node("summary", { text: "Raw provider response and metadata" }),
          node("pre", { text: JSON.stringify(result.response.raw, null, 2) }),
        ]),
      ]),
    ]);
    responses.append(item);
  }
  detailBody.append(detailSection("Every question response", responses));
  dialog.showModal();
}

loadData();
