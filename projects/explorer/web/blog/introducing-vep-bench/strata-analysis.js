import {modelName, overallLeaderboardRows} from "../../components/benchmark-data.js";

export function stratumModelOrder(snapshot) {
  const selectedRuns = new Set(snapshot.runs.map((run) => run.run_id));
  const selectedModels = new Set(snapshot.runs.map((run) => modelName(run.model.model_id, run.generation_parameters)));
  const models = overallLeaderboardRows(snapshot.leaderboard.runs, snapshot.leaderboard.leaderboard, "spearman")
    .filter((row) => row.runs.every((run) => selectedRuns.has(run.run_id)))
    .map((row) => row.model_cell.model);
  if (models.length !== selectedModels.size || models.some((model) => !selectedModels.has(model))) {
    throw new Error("Selected models lack a complete overall leaderboard ranking");
  }
  if (snapshot.specialist) models.push(snapshot.specialist.name);
  return models;
}

const resultKey = (row) => JSON.stringify([row.task_family, row.axis, row.category, row.run_id]);

export function stratumRows(snapshot, statistics) {
  const intervals = new Map(statistics?.intervals.map((row) => [resultKey(row), row]));
  if (statistics && (statistics.statistics.confidence_level !== 0.95
    || intervals.size !== statistics.intervals.length || intervals.size !== snapshot.results.length)) {
    throw new Error("Confidence intervals do not match the frozen analysis");
  }
  const runs = new Map(snapshot.runs.map((run) => [run.run_id, run]));
  return snapshot.results.flatMap((result) => {
    const coverage = snapshot.coverage.find((row) => row.task_family === result.task_family
      && row.axis === result.axis && row.category === result.category);
    if (!coverage || coverage.status !== "eligible") return [];
    const run = runs.get(result.run_id);
    if (!run && result.run_id !== "specialist") throw new Error("Unknown analysis run");
    const interval = intervals.get(resultKey(result));
    if (statistics && (!interval || interval.eligible_panels !== coverage.eligible_panels
      || interval.mean_spearman_rho !== result.mean_spearman_rho)) {
      throw new Error("Confidence interval membership or mean differs from the frozen scores");
    }
    return [{
      ...result,
      ...(interval ?? {}),
      model: run ? modelName(run.model.model_id, run.generation_parameters) : snapshot.specialist.name,
      family: run?.model.family ?? snapshot.specialist.name,
      eligible_panels: coverage.eligible_panels,
      eligible_variants: coverage.eligible_variants
    }];
  });
}

export function stratumCsv(snapshot, statistics) {
  const scores = stratumRows(snapshot, statistics);
  const rows = snapshot.coverage.flatMap((coverage) => {
    const matched = scores.filter((row) => row.task_family === coverage.task_family
      && row.axis === coverage.axis && row.category === coverage.category);
    return (matched.length ? matched : [{}]).map((score) => ({...coverage, ...score}));
  });
  const columns = ["task_family", "axis", "category", "status", "variant_count", "eligible_variants",
    "eligible_panels", "total_panels", "excluded_panels", "unsupported_variants", "below_cutoff_variants",
    "model", "run_id", "mean_spearman_rho", "mean_pearson_r", "invalid_panels",
    "spearman_ci_low", "spearman_ci_high", "spearman_ci_status"];
  const escape = (value) => `"${String(value ?? "").replaceAll('"', '""')}"`;
  return [columns, ...rows.map((row) => columns.map((key) => row[key]))]
    .map((row) => row.map(escape).join(",")).join("\n") + "\n";
}
