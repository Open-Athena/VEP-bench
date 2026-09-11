import * as Plot from "npm:@observablehq/plot@0.6.17";
import {compositionTasks} from "./analysis.js";

const percent = (value) => `${(value * 100).toFixed(1)}%`;
const integer = (value) => value.toLocaleString("en-US");

function consequenceLabel(term) {
  return term.replace(/_variant$/, "").replaceAll("_", " ")
    .replace("3 prime", "3′").replace("5 prime", "5′");
}

export function distributionFigure(composition, dimension, width) {
  const rows = composition.rows.filter((row) => row.dimension === dimension);
  const categories = dimension === "type" ? composition.types : composition.consequences;
  const taskLabels = composition.tasks.map((task) => task.label);
  const detail = (row) => `${row.task}\n${row.category}\n${integer(row.count)} / ${integer(row.total)} variants (${percent(row.proportion)})`;
  const plot = Plot.plot({
    width: Math.max(760, width - 34),
    height: categories.length * 29 + 115,
    marginLeft: dimension === "type" ? 165 : 230,
    marginRight: 36,
    marginTop: 60,
    marginBottom: 55,
    style: {fontSize: "12px"},
    ariaLabel: dimension === "type"
      ? "Variant types by task, as a percentage of selected panel variants"
      : "Most severe VEP consequences by task, as a percentage of selected panel variants",
    x: {label: "Variants within task (%)", labelAnchor: "center", grid: true, ticks: 4, insetRight: 30, tickFormat: (value) => `${Math.round(value * 100)}%`},
    y: {domain: categories, label: null, tickFormat: dimension === "type" ? undefined : consequenceLabel},
    fx: {
      domain: taskLabels,
      label: null,
      tickFormat: (label) => `${label}\nn = ${integer(composition.tasks.find((task) => task.label === label).total)}`,
      padding: 0.15
    },
    color: {domain: taskLabels, range: compositionTasks.map((task) => task.color)},
    marks: [
      Plot.ruleX([0]),
      Plot.barX(rows, {fx: "task", y: "category", x: "proportion", fill: "task", tip: true, title: detail, ariaLabel: detail}),
      Plot.text(rows, {fx: "task", y: "category", x: "proportion", text: (row) => percent(row.proportion), textAnchor: "start", dx: 4, fontSize: 10})
    ]
  });
  const exported = plot.cloneNode(true);
  exported.setAttribute("xmlns", "http://www.w3.org/2000/svg");
  exported.style.color = "#202124";
  exported.style.background = "white";
  const link = document.createElement("a");
  link.download = `vepbench-${dimension}-distribution.svg`;
  link.textContent = "Download SVG";
  link.href = `data:image/svg+xml;charset=utf-8,${encodeURIComponent(new XMLSerializer().serializeToString(exported))}`;
  const chart = document.createElement("div");
  chart.style.overflowX = "auto";
  chart.tabIndex = 0;
  chart.setAttribute("role", "region");
  chart.setAttribute("aria-label", "Variant distribution chart; scroll horizontally on narrow screens");
  // Preserve legible labels on mobile instead of shrinking the entire SVG.
  plot.style.maxWidth = "none";
  chart.append(plot);
  const caption = document.createElement("p");
  caption.style.cssText = "font-size: 0.85rem; margin-bottom: 0";
  caption.append(link);
  const figure = document.createElement("div");
  figure.className = "card";
  figure.append(chart, caption);
  return figure;
}
