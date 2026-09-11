import * as Plot from "npm:@observablehq/plot@0.6.17";
import {modelFamilyColors} from "../../components/model-colors.js";

const percent = (value) => `${(value * 100).toFixed(1)}%`;
const integer = (value) => value.toLocaleString("en-US");

function consequenceLabel(term) {
  return term.replace(/_variant$/, "").replaceAll("_", " ")
    .replace("3 prime", "3′").replace("5 prime", "5′");
}

export function distributionFigure(composition, dimension, width) {
  const rows = composition.rows.filter((row) => row.dimension === dimension);
  const categories = dimension === "type" ? composition.types : composition.consequences;
  const detail = (row) => `${row.task}\n${row.category}\n${integer(row.count)} / ${integer(row.total)} variants (${percent(row.proportion)})`;
  const plotWidth = Math.max(760, width - 34);
  const height = categories.length * 29 + 115;
  const labelWidth = dimension === "type" ? 155 : 220;
  const panelWidth = (plotWidth - labelWidth) / composition.tasks.length;
  const plot = document.createElementNS("http://www.w3.org/2000/svg", "svg");
  plot.setAttribute("width", plotWidth);
  plot.setAttribute("height", height);
  plot.setAttribute("viewBox", `0 0 ${plotWidth} ${height}`);
  plot.setAttribute("aria-label", dimension === "type"
    ? "Variant types by task, with independently scaled percentage axes"
    : "Most severe VEP consequences by task, with independently scaled percentage axes");
  // Separate Plot instances give each task its own automatic x scale.
  composition.tasks.forEach((task, index) => {
    const data = rows.filter((row) => row.task_family === task.family);
    const panel = Plot.plot({
      width: panelWidth + (index === 0 ? labelWidth : 0),
      height,
      marginLeft: index === 0 ? labelWidth + 10 : 10,
      marginRight: 38,
      marginTop: 60,
      marginBottom: 55,
      style: {fontSize: "12px", overflow: "visible"},
      ariaLabel: `${task.label}: ${dimension} distribution`,
      x: {label: "Variants (%)", labelAnchor: "center", grid: true, ticks: 4, tickFormat: (value) => `${Math.round(value * 100)}%`},
      y: {
        domain: categories,
        axis: index === 0 ? "left" : null,
        label: null,
        tickFormat: dimension === "type" ? undefined : consequenceLabel
      },
      marks: [
        Plot.ruleX([0]),
        Plot.barX(data, {y: "category", x: "proportion", fill: task.color, tip: true, title: detail, ariaLabel: detail}),
        Plot.text(data, {y: "category", x: "proportion", text: (row) => percent(row.proportion), textAnchor: "start", dx: 4, fontSize: 10}),
        Plot.text([task], {frameAnchor: "top", dy: -35, text: (task) => `${task.label}\nn = ${integer(task.total)}`, fontSize: 12})
      ]
    });
    panel.setAttribute("x", index === 0 ? 0 : labelWidth + index * panelWidth);
    plot.append(panel);
  });
  const chart = document.createElement("div");
  chart.style.overflowX = "auto";
  chart.tabIndex = 0;
  chart.setAttribute("role", "region");
  chart.setAttribute("aria-label", "Variant distribution chart; scroll horizontally on narrow screens");
  // Preserve legible labels on mobile instead of shrinking the entire SVG.
  plot.style.maxWidth = "none";
  chart.append(plot);
  const figure = document.createElement("div");
  figure.className = "card";
  figure.append(chart);
  return figure;
}

export function cutoffFigure(summaries, width) {
  const rows = summaries.filter((row) => row.before_n || row.after_n);
  const points = rows.flatMap((row) => [
    {row, relation: "Before cutoff", score: row.before_mean, n: row.before_n, low: row.before_ci_low, high: row.before_ci_high},
    {row, relation: "After cutoff", score: row.after_mean, n: row.after_n, low: row.after_ci_low, high: row.after_ci_high}
  ]).filter((point) => point.n > 0);
  const labels = new Map(rows.map((row) => [row.run_id,
    `${row.model}\nCutoff ${row.knowledge_cutoff} · n = ${row.before_n} / ${row.after_n}`]));
  const detail = ({row, relation, score, n, low, high}) =>
    `${row.model}\n${relation}: ${score.toFixed(3)} Spearman ρ\n95% CI: ${low == null || high == null ? "not estimable" : `${low.toFixed(3)}–${high.toFixed(3)}`}\n${n} gene panels\nKnowledge cutoff: ${row.knowledge_cutoff}`;
  const plot = Plot.plot({
    width: Math.max(660, width - 34),
    height: rows.length * 64 + 90,
    marginLeft: 225,
    marginRight: 100,
    marginTop: 30,
    marginBottom: 55,
    style: {fontSize: "12px", background: "white", color: "#222"},
    ariaLabel: "SGE performance before and after each model's knowledge cutoff, with 95% confidence intervals",
    x: {label: "Mean within-gene Spearman ρ", labelAnchor: "center", grid: true},
    y: {domain: rows.map((row) => row.run_id), label: null, tickSize: 0, tickFormat: (id) => labels.get(id)},
    color: {domain: ["Before cutoff", "After cutoff"], range: ["#4267a8", "#b45f23"]},
    marks: [
      Plot.text(rows, {frameAnchor: "right", y: "run_id", dx: 15, textAnchor: "start", text: (row) =>
        row.p_value === null ? "p —" : `p ${row.p_value < 0.001 ? "<0.001" : row.p_value.toFixed(3)}${row.p_value <= 0.05 ? " *" : ""}`
      }),
      ...["Before cutoff", "After cutoff"].flatMap((relation, i) => {
        const group = points.filter((point) => point.relation === relation);
        const intervals = group.filter((point) => point.low != null && point.high != null);
        const dy = i ? 8 : -8;
        return [
          Plot.ruleY(intervals, {
            x1: "low", x2: "high", y: (point) => point.row.run_id, dy,
            stroke: "relation", strokeWidth: 2, ariaLabel: `${relation}: 95% confidence interval`
          }),
          Plot.dot(group, {
            x: "score", y: (point) => point.row.run_id, dy, fill: "relation", stroke: "white", r: 6,
            symbol: i ? "diamond" : "circle", tip: true, title: detail, ariaLabel: detail
          })
        ];
      })
    ]
  });
  plot.style.maxWidth = "none";
  const chart = document.createElement("div");
  chart.style.overflowX = "auto";
  chart.tabIndex = 0;
  chart.setAttribute("role", "region");
  chart.setAttribute("aria-label", "SGE cutoff comparison; scroll horizontally on narrow screens");
  chart.append(plot);
  const figure = document.createElement("div");
  figure.className = "card";
  const legend = document.createElement("p");
  legend.textContent = "● Before cutoff    ◆ After cutoff · bars: 95% CI · n = before / after · p tests before > after · * p ≤ 0.05";
  figure.append(legend, chart);
  return figure;
}

export function comparisonFigure(comparison, families, width) {
  const color = modelFamilyColors(families);
  const figure = document.createElement("div");
  figure.className = "card";
  const caption = document.createElement("p");
  caption.textContent = `${comparison.label} · ${comparison.subset} · ${comparison.summary.points} matched configurations · ${comparison.summary.n} distinct models. `
    + (comparison.summary.spearman === null ? `${comparison.summary.reason}; descriptive only.`
      : `Exploratory Spearman ρ = ${comparison.summary.spearman.toFixed(3)}; Pearson r = ${comparison.summary.pearson.toFixed(3)}.`);
  figure.append(caption);
  if (!comparison.pairs.length) return figure;
  const detail = (row) => `${row.label} (${row.effort})\nVEP-bench: ${row.vep_score.toFixed(4)}\n`
    + `${comparison.metric_label}: ${row.external_score.toFixed(4)}\nHarness: ${comparison.harness}`;
  const values = comparison.pairs.map((r) => r.vep_score);
  const midpoint = (Math.min(...values) + Math.max(...values)) / 2;
  const labels = [...new Map(comparison.pairs.map((row) => [row.model_id, row])).values()];
  const effortSymbols = {none: "cross", minimal: "star", low: "circle", medium: "square",
    high: "triangle", xhigh: "diamond", max: "wye"};
  const shownEfforts = Object.keys(effortSymbols).filter((effort) => comparison.pairs.some((r) => r.effort === effort));
  const symbol = {domain: shownEfforts, range: shownEfforts.map((effort) => effortSymbols[effort]), label: "Reasoning effort"};
  const labelOptions = {x: "vep_score", y: "external_score", text: "label",
    fontSize: 11, dy: -14, lineWidth: 18};
  const chart = Plot.plot({
    width: Math.max(320, width - 34), height: 380, marginTop: 50, marginBottom: 55, marginLeft: 72, marginRight: 35,
    ariaLabel: `Overall VEP-bench versus ${comparison.label}; ${comparison.summary.points} configurations from ${comparison.summary.n} distinct models`,
    x: {label: "Overall VEP-bench score (mean Spearman)", grid: true, nice: true, ticks: 5},
    y: {label: comparison.metric_label, grid: true, nice: true, ticks: 5}, color, symbol,
    marks: [
      Plot.line(comparison.pairs, {x: "vep_score", y: "external_score", stroke: "family",
        z: "model_id", strokeOpacity: 0.35, strokeWidth: 1.5}),
      Plot.dot(comparison.pairs, {x: "vep_score", y: "external_score", fill: "family", symbol: "effort", r: 5,
        stroke: "white", tip: true, title: detail, ariaLabel: detail}),
      Plot.text(labels.filter((r) => r.vep_score <= midpoint), {...labelOptions, textAnchor: "start", dx: 7}),
      Plot.text(labels.filter((r) => r.vep_score > midpoint), {...labelOptions, textAnchor: "end", dx: -7, dy: 16})
    ]
  });
  figure.append(chart);
  const visibleFamilies = [...new Set(comparison.pairs.map((row) => row.family))].sort();
  figure.append(Plot.legend({color: {type: "categorical", domain: visibleFamilies,
    range: visibleFamilies.map((family) => chart.scale("color").apply(family)), label: "Model family"}}));
  figure.append(Plot.legend({symbol}));
  return figure;
}
