import * as Plot from "npm:@observablehq/plot@0.6.17";
import {modelFamilyScale} from "./benchmark-data.js";

const categoryLabel = (category) => category.replace(/_variant$/, "").replaceAll("_", " ")
  .replace("3 prime", "3′").replace("5 prime", "5′");

export function stratumCorrelationPlot(rows, {width, colors, tasks, axis, coverage, modelOrder}) {
  const observed = new Set(rows.map((row) => row.category));
  const categories = axis === "allele_type"
    ? ["SNV", "Insertion", "Deletion", "Other/complex", "Unknown"].filter((category) => observed.has(category))
    : [...observed].sort();
  const modelFamilies = new Map(rows.map((row) => [row.model, row.family]));
  const models = modelOrder.filter((model) => modelFamilies.has(model));
  const families = models.map((model) => modelFamilies.get(model));
  const palette = modelFamilyScale(families, colors);
  const familyScale = {
    ...palette,
    domain: families, range: families.map((family) => palette.range[palette.domain.indexOf(family)])
  };
  const modelLabels = new Map(rows.map((row) => [row.family, row.model]));
  const band = models.length + 3;
  const labelWidth = 135;
  const plotWidth = Math.max(820, width - 34);
  const panelWidth = (plotWidth - labelWidth) / tasks.length;
  const height = categories.length * band * 15 + 100;
  const detail = (row) => `${row.model}\n${categoryLabel(row.category)}\n`
    + `Spearman ρ: ${row.mean_spearman_rho.toFixed(3)}\n`
    + (row.spearman_ci_status === "estimated"
      ? `95% t CI: ${row.spearman_ci_low.toFixed(3)} to ${row.spearman_ci_high.toFixed(3)}\n`
      : "95% t CI: not estimated\n")
    + `${row.eligible_variants} variants in ${row.eligible_panels} panels\n`
    + `${row.invalid_panels} invalid panels (scored zero)`;
  const figure = document.createElement("figure");
  figure.className = "card vepbench-stratum-figure";
  figure.style.marginInline = "0";
  figure.style.maxWidth = "none";
  figure.dataset.stratumAxis = axis;
  figure.setAttribute("aria-label", `${axis === "allele_type" ? "Allele type" : "Functional consequence"}: Spearman correlations across three tasks`);
  const legend = Plot.legend({color: {
    ...familyScale, label: "Model (reasoning effort)",
    domain: familyScale.domain.map((family) => modelLabels.get(family))
  }});
  legend.setAttribute("aria-label", "Model (reasoning effort) legend");
  figure.append(legend);

  const plot = document.createElementNS("http://www.w3.org/2000/svg", "svg");
  plot.setAttribute("width", plotWidth);
  plot.setAttribute("height", height);
  plot.setAttribute("viewBox", `0 0 ${plotWidth} ${height}`);
  plot.setAttribute("aria-label", "Within-panel Spearman correlations by variant stratum and task");
  plot.style.maxWidth = "none";
  tasks.forEach((task, index) => {
    const data = rows.filter((row) => row.task_family === task.family);
    const scores = data.flatMap((row) => [row.mean_spearman_rho, row.spearman_ci_low, row.spearman_ci_high])
      .filter(Number.isFinite);
    const groups = categories.map((category, categoryIndex) => ({
      ...coverage.find((row) => row.task_family === task.family && row.axis === axis && row.category === category),
      category, row: categoryIndex * band
    }));
    const panel = Plot.plot({
      width: panelWidth + (index === 0 ? labelWidth : 0), height,
      marginLeft: index === 0 ? labelWidth + 10 : 10,
      marginRight: 18, marginTop: 65, marginBottom: 35,
      style: {fontSize: "11px", overflow: "visible"},
      ariaLabel: `${task.label}: within-panel Spearman correlations`,
      x: {nice: true, grid: true, ticks: 4, label: null},
      y: {
        domain: [categories.length * band - 1, -2], label: null,
        axis: index === 0 ? "left" : null, tickSize: 0,
        ticks: groups.map((group) => group.row + (models.length - 1) / 2),
        tickFormat: (value) => categoryLabel(categories[Math.floor(value / band)])
      },
      color: familyScale,
      marks: [
        Plot.ruleY(groups.slice(1), {y: (group) => group.row - 2, strokeOpacity: 0.12}),
        ...(Math.min(...scores) <= 0 && Math.max(...scores) >= 0
          ? [Plot.ruleX([0], {strokeOpacity: 0.5})] : []),
        Plot.ruleY(data.filter((row) => row.spearman_ci_status === "estimated"), {
          x1: "spearman_ci_low", x2: "spearman_ci_high",
          y: (row) => categories.indexOf(row.category) * band + models.indexOf(row.model),
          stroke: "family", strokeWidth: 1.8,
          ariaDescription: "95% confidence intervals", ariaLabel: detail
        }),
        Plot.dot(data, {
          x: "mean_spearman_rho",
          y: (row) => categories.indexOf(row.category) * band + models.indexOf(row.model),
          fill: "family", r: 4.5, tip: true, title: detail, ariaLabel: detail
        }),
        Plot.text(groups.filter((group) => group.status === "eligible"), {
          frameAnchor: "middle", y: (group) => group.row - 1.25,
          text: (group) => `${group.eligible_variants} variants · ${group.eligible_panels} panels`,
          fontSize: 10, fill: "currentColor", fillOpacity: 0.75
        }),
        Plot.text(groups.filter((group) => group.status !== "eligible"), {
          frameAnchor: "middle", y: (group) => group.row + (models.length - 1) / 2,
          text: () => "Insufficient coverage", fontSize: 10, fill: "currentColor", fillOpacity: 0.6
        }),
        Plot.text([task], {frameAnchor: "top", dy: -36, text: "label", fontSize: 12, fontWeight: 600})
      ]
    });
    panel.dataset.taskFamily = task.family;
    panel.setAttribute("x", index === 0 ? 0 : labelWidth + index * panelWidth);
    plot.append(panel);
  });
  const scroll = document.createElement("div");
  scroll.style.overflowX = "auto";
  scroll.tabIndex = 0;
  scroll.setAttribute("role", "region");
  scroll.setAttribute("aria-label", "Task subplots; scroll horizontally on narrow screens");
  scroll.append(plot);
  const caption = document.createElement("figcaption");
  caption.textContent = "Mean panel Spearman ρ and 95% t CI · Independent task scales · Models in overall leaderboard order";
  caption.style.textAlign = "center";
  caption.style.maxWidth = "none";
  figure.append(scroll, caption);
  return figure;
}
