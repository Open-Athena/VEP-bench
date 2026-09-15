import {modelName} from "../../components/benchmark-data.js";

export const specialistTasks = [
  {family: "opensplice_snv", label: "Splicing"},
  {family: "satmut_mpra", label: "Expression"},
  {family: "sge", label: "Fitness"}
];

export function specialistRows(snapshot, category = "all_covered") {
  if (snapshot.status === "awaiting_inference") return [];
  if (snapshot.status !== "complete") throw new Error("Unknown specialist analysis status");
  const runs = new Map(snapshot.runs.map((run) => [run.run_id, run]));
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
    return {
      ...row,
      model: run ? modelName(run.model.model_id, run.generation_parameters) : "AlphaGenome / AVI",
      family: run?.model.family ?? "AlphaGenome / AVI",
      task_label: specialistTasks.find((t) => t.family === row.task_family)?.label ?? row.task_family,
      eligible_variants: coverage.eligible_variants,
      eligible_panels: coverage.eligible_panels
    };
  });
}

export function specialistCsv(snapshot) {
  const columns = ["category", "task_family", "model", "run_id", "eligible_variants",
    "eligible_panels", "mean_spearman_rho", "mean_pearson_r", "invalid_panels"];
  const rows = ["all_covered", "excluding_avi_model_selection"].flatMap((c) => specialistRows(snapshot, c));
  const escape = (v) => `"${String(v ?? "").replaceAll('"', '""')}"`;
  return [columns, ...rows.map((row) => columns.map((key) => row[key]))]
    .map((row) => row.map(escape).join(",")).join("\n") + "\n";
}
