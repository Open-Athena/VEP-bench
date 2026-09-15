import * as Plot from "npm:@observablehq/plot@0.6.17";
import {modelFamilyScale} from "../../components/benchmark-data.js";

export function matchedCorrelationPlot(rows, {width, colors}) {
  const models = [...new Set(rows.map((row) => row.model))].sort();
  const detail = (row) => `${row.model}\n${row.task_label}\n`
    + `Spearman ρ: ${row.mean_spearman_rho.toFixed(3)}\n`
    + `${row.eligible_variants} matched variants in ${row.eligible_panels} panels\n`
    + `${row.invalid_panels} invalid panels (scored zero)`;
  return Plot.plot({
    width: Math.max(740, width), height: models.length * 22 + 90,
    marginLeft: 180, marginBottom: 45,
    ariaLabel: "Specialist and LLM correlations on identical variants within each task",
    x: {domain: [-1, 1], ticks: 5, grid: true, label: "Mean within-panel Spearman ρ"},
    y: {domain: models, label: null},
    fx: {domain: ["Splicing", "Expression", "Fitness"], label: null},
    color: modelFamilyScale(rows.map((row) => row.family), colors),
    marks: [
      Plot.ruleX([0], {strokeOpacity: 0.4}),
      Plot.dot(rows, {fx: "task_label", y: "model", x: "mean_spearman_rho",
        fill: "family", r: 4.5, tip: true, title: detail, ariaLabel: detail})
    ]
  });
}
