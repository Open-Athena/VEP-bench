import assert from "node:assert/strict";

const [siteUrl, debugUrl] = process.argv.slice(2);
if (!siteUrl || !debugUrl) {
  throw new Error("usage: node browser_interaction_qa.mjs SITE_URL DEBUG_URL");
}

const targets = await fetch(`${debugUrl}/json/list`).then((response) => response.json());
const page = targets.find((target) => target.type === "page");
if (!page) throw new Error("Chrome DevTools Protocol exposed no page target");

const socket = new WebSocket(page.webSocketDebuggerUrl);
await new Promise((resolve, reject) => {
  socket.addEventListener("open", resolve, {once: true});
  socket.addEventListener("error", reject, {once: true});
});

let commandId = 0;
const pending = new Map();
socket.addEventListener("message", (event) => {
  const message = JSON.parse(event.data);
  if (!message.id || !pending.has(message.id)) return;
  const {resolve, reject} = pending.get(message.id);
  pending.delete(message.id);
  if (message.error) reject(new Error(message.error.message));
  else resolve(message.result);
});

function send(method, params = {}) {
  const id = ++commandId;
  socket.send(JSON.stringify({id, method, params}));
  return new Promise((resolve, reject) => pending.set(id, {resolve, reject}));
}

async function evaluate(expression) {
  const response = await send("Runtime.evaluate", {
    expression,
    awaitPromise: true,
    returnByValue: true
  });
  if (response.exceptionDetails) {
    throw new Error(response.exceptionDetails.exception?.description ?? "browser evaluation failed");
  }
  return response.result.value;
}

async function waitFor(expression, label, timeoutMs = 10_000) {
  const deadline = Date.now() + timeoutMs;
  while (Date.now() < deadline) {
    if (await evaluate(expression)) return;
    await new Promise((resolve) => setTimeout(resolve, 100));
  }
  throw new Error(`timed out waiting for ${label}`);
}

async function navigate(path) {
  await send("Page.navigate", {url: new URL(path, siteUrl).href});
  await waitFor('document.readyState === "complete"', `${path} document load`);
}

function chooseOptionContaining(optionText) {
  return evaluate(`(() => {
    const select = [...document.querySelectorAll("select")].find((candidate) =>
      [...candidate.options].some((option) => option.textContent.includes(${JSON.stringify(optionText)}))
    );
    const option = [...(select?.options ?? [])].find(
      (candidate) => candidate.textContent.includes(${JSON.stringify(optionText)})
    );
    if (!select || !option) return false;
    select.value = option.value;
    select.dispatchEvent(new Event("input", {bubbles: true}));
    select.dispatchEvent(new Event("change", {bubbles: true}));
    return true;
  })()`);
}

await send("Page.enable");
await send("Runtime.enable");

await navigate("/index.html");
await waitFor(
  `document.querySelectorAll(".vepbench-score-cell").length === 2
    && document.querySelector('.card[aria-label^="All tasks score versus"]') !== null`,
  "two-model default all-task leaderboard"
);
assert.equal(await chooseOptionContaining("Pearson"), true);
await waitFor(
  `[...document.querySelectorAll("p")].some(
    (paragraph) => paragraph.textContent.includes("Pearson correlation")
  )`,
  "Pearson leaderboard metric"
);
assert.equal(await chooseOptionContaining("Spearman"), true);
await waitFor(
  `[...document.querySelectorAll("p")].some(
    (paragraph) => paragraph.textContent.includes("Spearman correlation")
  )`,
  "Spearman leaderboard metric"
);
assert.equal(
  await evaluate('getComputedStyle(document.querySelector(\'th[title="score"]\')).textAlign'),
  "center",
  "score column header is not centered"
);
assert.equal(await chooseOptionContaining("Expression (satMutMPRA)"), true);
await waitFor(
  `document.querySelector('.card[aria-label^="Expression (satMutMPRA) score versus"]') !== null`,
  "Expression (satMutMPRA) task scope"
);
assert.equal(await chooseOptionContaining("Total tokens"), true);
await waitFor(
  `document.querySelector('.card[aria-label="Expression (satMutMPRA) score versus Total tokens"]') !== null`,
  "token plot"
);
assert.equal(await chooseOptionContaining("Fitness (SGE)"), true);
await waitFor(
  `document.querySelector('.card[aria-label^="Fitness (SGE) score versus"]') !== null`,
  "SGE task scope"
);

const initialQuestionId = "satmut-mpra-ranking-v2:F9";
await navigate(
  `/tasks/satmut-mpra.html?question=${encodeURIComponent(initialQuestionId)}&run=browser-qa`
);
await waitFor(
  `document.querySelectorAll("table tbody tr").length > 2
    && [...document.querySelectorAll("select option")].some(
      (option) => option.textContent.includes("browser-qa-alternate")
    )`,
  "two-model question explorer"
);
assert.equal(
  await evaluate(`(() => {
    const rows = [...document.querySelectorAll('.vepbench-row-select-table tbody tr')];
    const row = rows.find((candidate) => !candidate.querySelector('input[type="radio"]')?.checked)
      ?? rows[1];
    row.click();
    return Boolean(row);
  })()`),
  true
);
await waitFor(
  `new URLSearchParams(location.search).get("question") !== ${JSON.stringify(initialQuestionId)}`,
  "row selection URL update"
);
let selectedQuestionId = await evaluate(
  'new URLSearchParams(location.search).get("question")'
);
assert.equal(
  await evaluate(`(() => {
    const selectedRow = document.querySelector(
      '.vepbench-row-select-table tbody tr:has(input[type="radio"]:checked)'
    );
    const radio = selectedRow?.querySelector('input[type="radio"]');
    return Boolean(
      selectedRow
      && radio
      && radio.getBoundingClientRect().width <= 1
      && radio.getAttribute("aria-label")?.startsWith("Select Q")
      && getComputedStyle(
        document.querySelector('.vepbench-row-select-table thead input[type="checkbox"]')
      ).display === "none"
      && getComputedStyle(selectedRow.cells[1]).backgroundColor
        !== getComputedStyle(document.body).backgroundColor
    );
  })()`),
  true,
  "selected question row is not highlighted or its radio remains visible"
);

assert.equal(
  await evaluate(`(async () => {
    const link = [...document.querySelectorAll('.vepbench-row-select-table tbody tr')]
      .find((row) => !row.querySelector('input[type="radio"]')?.checked)
      ?.querySelector("a");
    if (!link) return false;
    link.addEventListener("click", (event) => event.preventDefault(), {once: true});
    link.focus();
    link.click();
    await new Promise((resolve) => setTimeout(resolve, 50));
    return document.activeElement === link
      && new URLSearchParams(location.search).get("question")
        === ${JSON.stringify(selectedQuestionId)};
  })()`),
  true,
  "assay citation link changed the question selection or lost focus"
);

assert.equal(
  await evaluate(`(() => {
    const radio = document.querySelector(
      '.vepbench-row-select-table tbody input[type="radio"]:checked'
    );
    radio?.focus();
    return document.activeElement === radio;
  })()`),
  true,
  "selected row radio could not receive keyboard focus"
);
await send("Input.dispatchKeyEvent", {
  type: "keyDown",
  key: "ArrowDown",
  code: "ArrowDown",
  windowsVirtualKeyCode: 40,
  nativeVirtualKeyCode: 40
});
await send("Input.dispatchKeyEvent", {
  type: "keyUp",
  key: "ArrowDown",
  code: "ArrowDown",
  windowsVirtualKeyCode: 40,
  nativeVirtualKeyCode: 40
});
await waitFor(
  `new URLSearchParams(location.search).get("question") !== ${JSON.stringify(selectedQuestionId)}
    && document.activeElement?.matches('input[type="radio"]:checked')`,
  "keyboard row selection"
);
selectedQuestionId = await evaluate(
  'new URLSearchParams(location.search).get("question")'
);

assert.equal(
  await evaluate(`(() => {
    const header = document.querySelector('.vepbench-row-select-table th[title="spearman_rho"]');
    header?.click();
    return Boolean(header);
  })()`),
  true
);
await waitFor(
  `document.querySelector(
    '.vepbench-row-select-table tbody tr:has(input[type="radio"]:checked)'
  )?.querySelector('input[type="radio"]')?.getAttribute("aria-label")?.startsWith("Select Q")`,
  "highlighted row selection after sorting"
);
assert.equal(
  await evaluate('new URLSearchParams(location.search).get("question")'),
  selectedQuestionId,
  "sorting did not preserve the selected question"
);
const initialRunId = await evaluate('new URLSearchParams(location.search).get("run")');

assert.equal(await chooseOptionContaining("browser-qa-alternate"), true);
await waitFor(
  `new URLSearchParams(location.search).get("run") !== ${JSON.stringify(initialRunId)}`,
  "model selection URL update"
);
assert.equal(
  await evaluate('new URLSearchParams(location.search).get("question")'),
  selectedQuestionId,
  "model selection did not preserve the selected question"
);
assert.equal(
  await evaluate(`Boolean(document.querySelector(
    '.vepbench-row-select-table tbody tr:has(input[type="radio"]:checked)'
  ))`),
  true,
  "model selection did not preserve the highlighted row"
);

socket.close();
console.log("browser interaction QA passed");
