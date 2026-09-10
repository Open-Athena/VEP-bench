import assert from "node:assert/strict";
import {writeFile} from "node:fs/promises";
import {join} from "node:path";

const [siteUrl, debugUrl, outputDir] = process.argv.slice(2);
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
  await saveDom("interaction-failure.dom.html");
  throw new Error(`timed out waiting for ${label}`);
}

async function navigate(path) {
  await send("Page.navigate", {url: new URL(path, siteUrl).href});
  await waitFor('document.readyState === "complete"', `${path} document load`);
}

async function saveDom(name) {
  if (outputDir) {
    await writeFile(join(outputDir, name), await evaluate('document.documentElement.outerHTML'));
  }
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

async function checkEfficiencyPlots(taskLabel) {
  const selector = `section[aria-label="${taskLabel} score comparisons"]`;
  await waitFor(`(() => {
    const section = document.querySelector(${JSON.stringify(selector)});
    const plots = [...(section?.querySelectorAll('.card svg') ?? [])];
    return plots.length === 2 && plots.every((plot) => typeof plot.scale === 'function');
  })()`, `${taskLabel} cost and token plots`);
  const scales = await evaluate(`(() => {
    const section = document.querySelector(${JSON.stringify(selector)});
    return [...section.querySelectorAll('.card svg')].map((plot) => ({
      scoreDomain: plot.scale('y').domain,
      colorDomain: plot.scale('color').domain,
      colorRange: plot.scale('color').range
    }));
  })()`);
  assert.deepEqual(scales[0], scales[1], "plots must share score and color scales");
  assert.deepEqual(await evaluate(`(() => {
    const color = document.querySelector('.vepbench-leaderboard-chart svg').scale('color');
    return {domain: color.domain, range: color.range};
  })()`), {domain: scales[0].colorDomain, range: scales[0].colorRange},
  "ranked bars and efficiency plots must use the same family colors");
  assert.equal(
    await evaluate('document.querySelectorAll(\'[aria-label="Model family legend"]\').length'),
    1,
    "both plots must use one legend"
  );
}

await navigate("/index.html");
await waitFor(
  `document.querySelectorAll('.vepbench-leaderboard-chart g[aria-label="bar"] rect').length === 2
    && document.querySelector('.card[aria-label^="All tasks score versus"]') !== null`,
  "two-model default all-task leaderboard"
);
await checkEfficiencyPlots("All tasks");
const rankedBars = await evaluate(`(() => {
  const plot = document.querySelector('.vepbench-leaderboard-chart svg');
  return {
    domain: plot.scale('y').domain,
    bars: [...plot.querySelectorAll('g[aria-label="bar"] rect')].map((bar) => ({
      x: Number(bar.getAttribute('x')),
      height: Number(bar.getAttribute('height')),
      description: bar.getAttribute('aria-label')
    }))
  };
})()`);
assert.equal(rankedBars.domain[0], 0, "ranked bars must have a zero baseline");
assert.ok(rankedBars.bars[0].x < rankedBars.bars[1].x);
assert.ok(rankedBars.bars[0].height >= rankedBars.bars[1].height,
  "models must be ranked from highest score to lowest");
assert.ok(rankedBars.bars.every((bar) =>
  ["Score:", "Cost:", "Tokens:", "Knowledge cutoff:"].every(
    (label) => bar.description.includes(label)
  )
), "each bar must expose the model's supporting metrics");
assert.equal(
  await evaluate(`document.querySelectorAll(
    '.vepbench-model-label [role="img"][aria-label="Anthropic"]'
  ).length`),
  2,
  "unscored models must display their organization icons"
);
assert.equal(await evaluate(`(async () => {
  const icons = [...document.querySelectorAll('.vepbench-organization-icon')];
  return icons.length > 0 && (await Promise.all(icons.map(async (icon) => {
    const mask = getComputedStyle(icon).maskImage;
    if (!mask.startsWith('url("')) return false;
    const image = new Image();
    image.src = mask.slice(5, -2);
    await image.decode();
    return image.naturalWidth > 0 && icon.getBoundingClientRect().width > 0;
  }))).every(Boolean);
})()`), true, "organization icons must load from bundled assets");
for (const width of [1440, 390]) {
  await send("Emulation.setDeviceMetricsOverride", {
    width, height: 1100, deviceScaleFactor: 1, mobile: false
  });
  await waitFor(`(() => {
    const section = document.querySelector('section[aria-label="All tasks score comparisons"]');
    const cards = [...section.querySelectorAll('.card')];
    const [left, right] = cards.map((card) => card.getBoundingClientRect());
    const arranged = ${width} > 640
      ? Math.abs(left.top - right.top) < 1 && right.left >= left.right
      : right.top >= left.bottom && Math.abs(left.left - right.left) < 1;
    return arranged && cards.every((card) => {
      const plot = card.querySelector('svg');
      return plot && Number(plot.getAttribute('width')) <= card.clientWidth
        && card.getBoundingClientRect().right <= innerWidth;
    });
  })()`, `comparison layout at viewport width ${width}`);
  assert.equal(await evaluate(`(() => {
    const plot = document.querySelector('.vepbench-leaderboard-chart svg');
    return Math.abs(plot.getBoundingClientRect().width - Number(plot.getAttribute('width'))) < 1
      && document.body.scrollWidth <= innerWidth;
  })()`), true, "ranked chart must preserve readable sizing within its scroll container");
  if (outputDir) {
    const clip = await evaluate(`(() => {
      const bounds = document.querySelector('section[aria-label="All tasks score comparisons"]')
        .getBoundingClientRect();
      return {x: bounds.x + scrollX, y: bounds.y + scrollY,
        width: bounds.width, height: bounds.height, scale: 1};
    })()`);
    const screenshot = await send("Page.captureScreenshot", {clip, captureBeyondViewport: true});
    await writeFile(join(outputDir, `efficiency-${width}.png`), Buffer.from(screenshot.data, "base64"));
  }
}
await send("Emulation.setDeviceMetricsOverride", {
  width: 1440, height: 1100, deviceScaleFactor: 1, mobile: false
});
await saveDom("leaderboard.dom.html");
assert.equal(await chooseOptionContaining("Pearson"), true);
await waitFor(
  `[...document.querySelectorAll("p")].some(
    (paragraph) => paragraph.textContent.includes("Pearson correlation")
  )`,
  "Pearson leaderboard metric"
);
await checkEfficiencyPlots("All tasks");
assert.equal(await chooseOptionContaining("Spearman"), true);
await waitFor(
  `[...document.querySelectorAll("p")].some(
    (paragraph) => paragraph.textContent.includes("Spearman correlation")
  )`,
  "Spearman leaderboard metric"
);
assert.equal(await chooseOptionContaining("Expression (satMutMPRA)"), true);
await waitFor(
  `document.querySelector('.card[aria-label^="Expression (satMutMPRA) score versus"]') !== null`,
  "Expression (satMutMPRA) task scope"
);
await checkEfficiencyPlots("Expression (satMutMPRA)");
assert.equal(await chooseOptionContaining("Fitness (SGE)"), true);
await waitFor(
  `document.querySelector('.card[aria-label^="Fitness (SGE) score versus"]') !== null`,
  "SGE task scope"
);
await checkEfficiencyPlots("Fitness (SGE)");

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
const plotReady = `document.querySelector(
  '.vepbench-prediction-plot svg[aria-label="Predicted versus measured variant effects"]'
) !== null`;
await waitFor(plotReady, "variant scatter plot");
await saveDom("question.dom.html");
for (const width of [1440, 390]) {
  await send("Emulation.setDeviceMetricsOverride", {
    width, height: 1100, deviceScaleFactor: 1, mobile: false
  });
  await waitFor(`(() => {
    const container = document.querySelector('.vepbench-prediction-plot');
    const plot = [...container.querySelectorAll('figure, svg')].find(
      (node) => typeof node.scale === 'function'
    );
    if (!plot) return false;
    const svg = container.querySelector('svg[aria-label="Predicted versus measured variant effects"]');
    const x = plot.scale('x').range;
    const y = plot.scale('y').range;
    return Math.abs(Math.abs(x[1] - x[0]) - Math.abs(y[1] - y[0])) < 0.1
      && Math.abs(svg.getBoundingClientRect().width - container.clientWidth) < 1;
  })()`, `square plotting area at viewport width ${width}`);
}
await send("Emulation.setDeviceMetricsOverride", {
  width: 1440, height: 1100, deviceScaleFactor: 1, mobile: false
});
await waitFor(`(() => {
  const container = document.querySelector('.vepbench-prediction-plot');
  const svg = container?.querySelector('svg[aria-label="Predicted versus measured variant effects"]');
  return svg && Math.abs(Number(svg.getAttribute('width')) - container.clientWidth) < 1;
})()`, "resized variant scatter plot");
await evaluate('new Promise((resolve) => requestAnimationFrame(() => requestAnimationFrame(resolve)))');
const point = await evaluate(`(() => {
  const node = document.querySelector('.vepbench-prediction-plot [aria-label^="V01;"]');
  node.scrollIntoView({block: 'center'});
  const bounds = node.getBoundingClientRect();
  return {x: bounds.x + bounds.width / 2, y: bounds.y + bounds.height / 2};
})()`);
await send("Input.dispatchMouseEvent", {type: "mouseMoved", ...point});
await waitFor(`(() => {
  const text = document.querySelector('.vepbench-prediction-plot')?.textContent ?? '';
  return text.includes('GRCh38') && text.includes('chrX:139530464') && text.includes('T>G');
})()`, "native Observable hover with genomic allele");
if (outputDir) {
  const clip = await evaluate(`(() => {
    const bounds = document.querySelector('.vepbench-prediction-plot').getBoundingClientRect();
    return {x: bounds.x + scrollX, y: bounds.y + scrollY,
      width: bounds.width, height: bounds.height, scale: 1};
  })()`);
  const screenshot = await send("Page.captureScreenshot", {clip, captureBeyondViewport: true});
  await writeFile(join(outputDir, "variant-scatter.png"), Buffer.from(screenshot.data, "base64"));
}
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
