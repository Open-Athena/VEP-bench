import assert from "node:assert/strict";
import {readFile, writeFile} from "node:fs/promises";
import {join} from "node:path";
import {gunzipSync} from "node:zlib";
import {stratumModelOrder, stratumRows} from "../web/blog/introducing-vep-bench/strata-analysis.js";

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

const websiteModelColors = JSON.parse(await readFile(
  new URL("../web/components/model-family-colors.json", import.meta.url)
));
function checkModelColors(families, colors) {
  assert.deepEqual(colors, families.map((family) => websiteModelColors[family] ?? "#767676"),
    "every model plot uses the fixed website palette");
}

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
  checkModelColors(scales[0].colorDomain, scales[0].colorRange);
  assert.deepEqual(await evaluate(`(() => {
    const section = document.querySelector(${JSON.stringify(selector)});
    return [...section.querySelectorAll('.card svg')].map((plot) =>
      plot.querySelectorAll('g[aria-label="dot"] circle').length
    );
  })()`), [3, 3], "cost and token plots must retain every effort configuration");
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
assert.equal(await evaluate(`(() => {
  const bars = [...document.querySelectorAll('.vepbench-leaderboard-chart g[aria-label="bar"] rect')];
  return bars.some((bar) => bar.getAttribute('aria-label').includes('browser-qa (high)'))
    && !bars.some((bar) => bar.getAttribute('aria-label').includes('browser-qa (low)'));
})()`), true, "only the bar chart must select each model's highest effort");
await waitFor(
  `document.querySelectorAll('[aria-label^="Output limits and usage;"] tbody tr').length === 1`,
  "execution summary for only the model with a recorded output-limit stop"
);
assert.equal(await evaluate(`(() => {
  const table = document.querySelector('[aria-label^="Output limits and usage;"]');
  const unscored = document.querySelector('#unscored-model-attempts');
  return Boolean(unscored.compareDocumentPosition(table) & Node.DOCUMENT_POSITION_FOLLOWING)
    && table.textContent.includes("browser-qa-alternate")
    && ["Output limit", "Output tokens", "Largest output", "Formatting failures", "Truncated"].every(
      (label) => [...table.querySelectorAll('th')].some((cell) => cell.textContent.trim() === label)
    );
})()`), true, "execution summary must omit clean models and expose failure and usage columns");
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
    const executionClip = await evaluate(`(() => {
      const bounds = document.querySelector('[aria-label^="Output limits and usage;"]')
        .getBoundingClientRect();
      return {x: bounds.x + scrollX, y: bounds.y + scrollY,
        width: bounds.width, height: bounds.height, scale: 1};
    })()`);
    const executionScreenshot = await send("Page.captureScreenshot", {
      clip: executionClip, captureBeyondViewport: true
    });
    await writeFile(join(outputDir, `execution-${width}.png`), Buffer.from(executionScreenshot.data, "base64"));
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
await waitFor(
  `document.querySelector('[aria-label^="Output limits and usage;"]')?.textContent
    .includes("No recorded failures for the selected models and tasks.")`,
  "failure section clears when the selected task has no failures"
);
assert.equal(await evaluate(
  `document.querySelectorAll('[aria-label^="Output limits and usage;"] tbody tr').length`
), 0, "models with no failures in the selected task must be omitted");

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

await navigate("/blog/introducing-vep-bench.html");
const stratumSnapshot = JSON.parse(gunzipSync(await readFile(
  new URL("../web/blog/introducing-vep-bench/strata-2026-09-11.json.gz", import.meta.url)
)));
const expectedStratumModels = stratumModelOrder(stratumSnapshot);
const stratumIntervals = JSON.parse(await readFile(
  new URL("../web/blog/introducing-vep-bench/strata-2026-09-11.intervals.json", import.meta.url), "utf8"
));
const expectedStratumRows = stratumRows(stratumSnapshot, stratumIntervals);
const stratumPlot = 'svg[aria-label="Within-panel Spearman correlations by variant stratum and task"]';
const stratumGroups = [
  ["sge", "allele_type", 18], ["sge", "consequence", 12],
  ["satmut_mpra", "allele_type", 6], ["satmut_mpra", "consequence", 18],
  ["opensplice_snv", "allele_type", 12], ["opensplice_snv", "consequence", 6]
];
async function checkStratumPlots() {
  await waitFor(`document.querySelectorAll(${JSON.stringify(stratumPlot + ' g[aria-label="dot"] circle')})
    .length === 72 && document.querySelectorAll(${JSON.stringify(stratumPlot)}).length === 2`,
  "two grouped stratum figures");
  assert.equal(await evaluate(`document.querySelectorAll('select').length`), 0, "blog has no data selectors");
  assert.equal(await evaluate(`document.querySelectorAll('figure.vepbench-stratum-figure').length`), 2);
  for (const axis of ["allele_type", "consequence"]) {
    const figure = `figure[data-stratum-axis="${axis}"]`;
    assert.equal(await evaluate(`document.querySelectorAll(${JSON.stringify(figure + ' svg[data-task-family]')}).length`), 3);
    assert.equal(await evaluate(`document.querySelectorAll(${JSON.stringify(figure + ' [aria-label="Model (reasoning effort) legend"]')}).length`), 1);
    assert.equal(await evaluate(`document.querySelector(${JSON.stringify(figure)}).textContent.includes('Mean panel Spearman ρ')`), true);
    assert.equal(await evaluate(`document.querySelector(${JSON.stringify(figure)}).textContent.includes('Insufficient coverage')`), true);
    assert.deepEqual(await evaluate(`[...document.querySelectorAll(${JSON.stringify(figure + ' [aria-label="Model (reasoning effort) legend"] > span')})]
      .map((item) => item.textContent)`), expectedStratumModels, "legend follows the overall leaderboard");
    const scales = await evaluate(`[...document.querySelectorAll(${JSON.stringify(figure + ' svg[data-task-family]')})].map((plot) => ({
      task: plot.dataset.taskFamily, x: plot.scale('x').domain, y: plot.scale('y').domain,
      families: plot.scale('color').domain, colors: plot.scale('color').range
    }))`);
    const shared = scales.map(({y, families, colors}) => ({y, families, colors}));
    assert.deepEqual(shared[0], shared[1], "tasks share category positions and model colors");
    assert.deepEqual(shared[0], shared[2], "tasks share category positions and model colors");
    assert.equal(new Set(scales.map((scale) => JSON.stringify(scale.x))).size, 3,
      "each task uses its own data range");
    for (const scale of scales) {
      checkModelColors(scale.families, scale.colors);
      const scores = expectedStratumRows.filter((row) => row.task_family === scale.task && row.axis === axis)
        .flatMap((row) => [row.mean_spearman_rho, row.spearman_ci_low, row.spearman_ci_high])
        .filter(Number.isFinite);
      const minimum = Math.min(...scores), maximum = Math.max(...scores);
      assert.ok(scale.x[0] <= minimum && scale.x[1] >= maximum, "axis includes every signed score and interval");
      assert.ok(scale.x[1] - scale.x[0] <= 2 * (maximum - minimum), "axis fits the task data");
      if (minimum > 0.5) assert.ok(scale.x[0] > 0, "annotation positions must not force a zero baseline");
    }
  }
  for (const [task, axis, dots] of stratumGroups) {
    const group = `[data-stratum-axis="${axis}"] svg[data-task-family="${task}"]`;
    assert.equal(await evaluate(`document.querySelectorAll(${JSON.stringify(group)}).length`), 1);
    assert.equal(await evaluate(`document.querySelectorAll(${JSON.stringify(group + ' g[aria-label="dot"] circle')}).length`), dots);
    const expectedIntervals = expectedStratumRows.filter((row) => row.task_family === task
      && row.axis === axis && row.spearman_ci_status === "estimated");
    assert.equal(await evaluate(`document.querySelectorAll(${JSON.stringify(group + ' g[aria-description="95% confidence intervals"] line')}).length`), expectedIntervals.length);
    assert.equal(await evaluate(`[...document.querySelectorAll(${JSON.stringify(group + ' g[aria-label="dot"] circle')})]
      .every((dot) => dot.getAttribute('aria-label').includes('95% t CI:'))`), true);
    const orders = await evaluate(`(() => {
      const groups = new Map();
      for (const dot of document.querySelectorAll(${JSON.stringify(group + ' g[aria-label="dot"] circle')})) {
        const [model, category] = dot.getAttribute('aria-label').split(String.fromCharCode(10));
        if (!groups.has(category)) groups.set(category, []);
        groups.get(category).push({model, y: Number(dot.getAttribute('cy'))});
      }
      return [...groups.values()].map((dots) => dots.sort((a, b) => a.y - b.y).map((dot) => dot.model));
    })()`);
    for (const order of orders) assert.deepEqual(order, expectedStratumModels,
      "dots follow the overall leaderboard from top to bottom within each category");
  }
  assert.equal(await evaluate(`document.querySelectorAll('.observablehq--error').length`), 0);
}
for (const width of [1440, 390]) {
  await send("Emulation.setDeviceMetricsOverride", {
    width, height: 1100, deviceScaleFactor: 1, mobile: false
  });
  await waitFor(`document.body.scrollWidth <= innerWidth`, `stratum page fits viewport ${width}`);
  await checkStratumPlots();
  for (const axis of ["allele_type", "consequence"]) {
    assert.equal(await evaluate(`(() => {
      const scroll = document.querySelector('figure[data-stratum-axis="${axis}"] [role="region"]');
      return scroll.scrollWidth > scroll.clientWidth + 1;
    })()`), width === 390, "task columns fit desktop and scroll within the figure on mobile");
  }
  if (outputDir) {
    for (const axis of ["allele_type", "consequence"]) {
      const clip = await evaluate(`(() => {
        const bounds = document.querySelector('figure[data-stratum-axis="${axis}"]').getBoundingClientRect();
        return {x: bounds.x + scrollX, y: bounds.y + scrollY,
          width: bounds.width, height: bounds.height, scale: 1};
      })()`);
      const screenshot = await send("Page.captureScreenshot", {clip, captureBeyondViewport: true});
      await writeFile(join(outputDir, `variant-strata-${axis}-${width}.png`), Buffer.from(screenshot.data, "base64"));
    }
  }
}
await send("Emulation.setDeviceMetricsOverride", {
  width: 1440, height: 1100, deviceScaleFactor: 1, mobile: false
});
await saveDom("variant-strata.dom.html");
// The post must remain independent of the live/fixture leaderboard, and input
// generators must be consumed in separate reactive cells before filtering data.
await navigate("/blog/introducing-vep-bench.html");
await waitFor(`document.querySelectorAll('svg[aria-label*="VEP-bench versus"]').length === 5`,
  "frozen external comparison plots");
assert.equal(await evaluate(`document.querySelectorAll('svg[aria-label*="Intelligence Index"] circle').length`), 4);
assert.equal(await chooseOptionContaining("Sensitivity: medium effort"), true);
await waitFor(`[...document.querySelectorAll('svg[aria-label*="Intelligence Index"] text')]
  .some((node) => node.textContent.includes('(medium)'))`, "exact medium effort comparison");
assert.equal(await chooseOptionContaining("SciCode (AA)"), true);
await waitFor(`Boolean(document.querySelector('svg[aria-label*="SciCode"]'))`, "AA component selection");
assert.equal(await chooseOptionContaining("Splicing (exploratory)"), true);
await waitFor(`Boolean(document.querySelector('svg[aria-label*="Splicing VEP-bench versus SciCode"]'))`,
  "task-specific external comparison");
assert.equal(await chooseOptionContaining("Sensitivity: low effort"), true);
await waitFor(`document.querySelectorAll('svg[aria-label*="VEP-bench versus"]').length === 2
  && document.querySelectorAll('svg[aria-label*="GeneBench-Pro"] circle').length === 2`,
  "exact low-effort biology comparison");
assert.equal(await evaluate(`document.querySelectorAll('svg[aria-label*="SciCode"] circle').length`), 4);
assert.deepEqual(await evaluate(`[...document.querySelectorAll('.observablehq--error')].map((node) => node.textContent)`), []);

socket.close();
console.log("browser interaction QA passed");
