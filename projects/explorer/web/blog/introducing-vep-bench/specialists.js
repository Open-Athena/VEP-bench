import {modelName} from "../../components/benchmark-data.js";

export const specialistTasks = [
  {family: "sge", label: "Fitness"},
  {family: "satmut_mpra", label: "Expression"},
  {family: "opensplice_snv", label: "Splicing"}
];

export function specialistModelOrder(snapshot, category = "all_covered") {
  return snapshot.overall.filter((row) => row.category === category)
    .slice().sort((a, b) => b.mean_spearman_rho - a.mean_spearman_rho
      || a.configuration.model.model_id.localeCompare(b.configuration.model.model_id))
    .map((row) => row.configuration.model.model_id === "AlphaGenome / AVI" ? "AlphaGenome / AVI"
      : modelName(row.configuration.model.model_id, row.configuration.generation_parameters));
}

export function specialistRows(snapshot, category = "all_covered", statistics = null) {
  if (snapshot.status === "awaiting_inference") return [];
  if (snapshot.status !== "complete") throw new Error("Unknown specialist analysis status");
  const runs = new Map(snapshot.runs.map((run) => [run.run_id, run]));
  const key = (row) => JSON.stringify([row.task_family, row.category, row.run_id]);
  const intervals = new Map((statistics?.intervals ?? []).map((row) => [key(row), row]));
  if (statistics && intervals.size !== statistics.intervals.length) {
    throw new Error("Duplicate specialist intervals");
  }
  return snapshot.results.filter((row) => row.category === category).map((row) => {
    const run = runs.get(row.run_id);
    if (!run && row.run_id !== "specialist") throw new Error("Unknown specialist comparison run");
    const coverage = snapshot.coverage.find((c) => c.task_family === row.task_family && c.category === category);
    if (!coverage || coverage.status !== "eligible" || row.panels.length !== coverage.eligible_panels) {
      throw new Error("Specialist comparison membership differs from coverage");
    }
    const expected = coverage.panels.filter((p) => p.status === "eligible").map((p) => p.question_id).sort();
    if (JSON.stringify(row.panels.map((p) => p.question_id).sort()) !== JSON.stringify(expected)) {
      throw new Error("Specialist comparison panel IDs differ from coverage");
    }
    const interval = intervals.get(key(row));
    if (statistics && (!interval || interval.eligible_panels !== row.panels.length
      || interval.mean_spearman_rho !== row.mean_spearman_rho
      || (interval.spearman_ci_status === "estimated"
        ? !Number.isFinite(interval.spearman_ci_low) || !Number.isFinite(interval.spearman_ci_high)
          || interval.spearman_ci_low > row.mean_spearman_rho || interval.spearman_ci_high < row.mean_spearman_rho
        : !["constant_scores", "insufficient_panels"].includes(interval.spearman_ci_status)
          || interval.spearman_ci_low !== null || interval.spearman_ci_high !== null))) {
      throw new Error("Specialist interval differs from the matched scores");
    }
    return {
      ...row,
      ...(interval && {
        spearman_ci_low: interval.spearman_ci_low,
        spearman_ci_high: interval.spearman_ci_high,
        spearman_ci_status: interval.spearman_ci_status
      }),
      model: run ? modelName(run.model.model_id, run.generation_parameters) : "AlphaGenome / AVI",
      family: run?.model.family ?? "AlphaGenome / AVI",
      task_label: specialistTasks.find((t) => t.family === row.task_family)?.label ?? row.task_family,
      eligible_variants: coverage.eligible_variants,
      eligible_panels: coverage.eligible_panels
    };
  });
}

export function specialistCsv(snapshot, statistics = null) {
  const columns = ["category", "task_family", "model", "run_id", "eligible_variants",
    "eligible_panels", "mean_spearman_rho", "spearman_ci_low", "spearman_ci_high",
    "spearman_ci_status", "mean_pearson_r", "invalid_panels"];
  const rows = ["all_covered", "excluding_avi_model_selection"].flatMap((c) => specialistRows(snapshot, c, statistics));
  const escape = (v) => `"${String(v ?? "").replaceAll('"', '""')}"`;
  return [columns, ...rows.map((row) => columns.map((key) => row[key]))]
    .map((row) => row.map(escape).join(",")).join("\n") + "\n";
}
