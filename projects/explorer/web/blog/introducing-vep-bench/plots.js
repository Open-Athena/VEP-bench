import * as Plot from "npm:@observablehq/plot@0.6.17";

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
