import * as Plot from "npm:@observablehq/plot@0.6.17";
import {modelFamilyScale} from "../../components/benchmark-data.js";
import {specialistTasks} from "./specialists.js";

export function matchedCorrelationPlot(rows, {width, colors, modelOrder}) {
  const models = modelOrder;
  if (new Set(models).size !== models.length || rows.some((row) => !models.includes(row.model))) {
    throw new Error("Specialist model order does not cover the comparison");
  }
  const labelWidth = 180;
  const plotWidth = Math.max(820, width);
  const panelWidth = (plotWidth - labelWidth) / specialistTasks.length;
  const height = models.length * 25 + 95;
  const color = modelFamilyScale(rows.map((row) => row.family), colors);
  const detail = (row) => `${row.model}\n${row.task_label}\n`
    + `Spearman ρ: ${row.mean_spearman_rho.toFixed(3)}\n`
    + (row.spearman_ci_status === "estimated"
      ? `95% t CI: ${row.spearman_ci_low.toFixed(3)} to ${row.spearman_ci_high.toFixed(3)}\n`
      : "95% t CI: not estimated\n")
    + `${row.eligible_variants} matched variants in ${row.eligible_panels} panels\n`
    + `${row.invalid_panels} invalid panels (scored zero)`;
  const plot = document.createElementNS("http://www.w3.org/2000/svg", "svg");
  plot.setAttribute("width", plotWidth);
  plot.setAttribute("height", height);
  plot.setAttribute("viewBox", `0 0 ${plotWidth} ${height}`);
  plot.setAttribute("aria-label", "Specialist and LLM correlations on identical variants within each task");
  plot.style.maxWidth = "none";
  specialistTasks.forEach((task, index) => {
    const data = rows.filter((row) => row.task_family === task.family);
    const scores = data.flatMap((row) => [row.mean_spearman_rho, row.spearman_ci_low, row.spearman_ci_high])
      .filter(Number.isFinite);
    const panel = Plot.plot({
      width: panelWidth + (index === 0 ? labelWidth : 0), height,
      marginLeft: index === 0 ? labelWidth : 12, marginRight: 18,
      marginTop: 55, marginBottom: 35,
      ariaLabel: `${task.label}: matched within-panel Spearman correlations`,
      x: {nice: true, ticks: 4, grid: true, label: null},
      y: {domain: models, label: null, axis: index === 0 ? "left" : null, tickSize: 0},
      color,
      marks: [
        ...(Math.min(...scores) <= 0 && Math.max(...scores) >= 0
          ? [Plot.ruleX([0], {strokeOpacity: 0.4})] : []),
        Plot.ruleY(data.filter((row) => row.spearman_ci_status === "estimated"), {
          x1: "spearman_ci_low", x2: "spearman_ci_high", y: "model",
          stroke: "family", strokeWidth: 1.8,
          ariaDescription: "95% confidence intervals", ariaLabel: detail
        }),
        Plot.dot(data, {y: "model", x: "mean_spearman_rho",
          fill: "family", r: 4.5, tip: true, title: detail, ariaLabel: detail}),
        Plot.text([task], {frameAnchor: "top", dy: -30, text: "label", fontSize: 12, fontWeight: 600}),
        Plot.text(data.slice(0, 1), {frameAnchor: "top", dy: -13,
          text: (row) => `${row.eligible_variants} variants · ${row.eligible_panels} panels`,
          fontSize: 10, fillOpacity: 0.75})
      ]
    });
    panel.dataset.taskFamily = task.family;
    panel.setAttribute("x", index === 0 ? 0 : labelWidth + index * panelWidth);
    plot.append(panel);
  });
  return plot;
}
