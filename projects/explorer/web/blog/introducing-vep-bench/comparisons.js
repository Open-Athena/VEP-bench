import {overallLeaderboardRows} from "../../components/benchmark-data.js";

const efforts = ["none", "minimal", "low", "medium", "high", "xhigh", "max"];
export const comparisonScopes = ["overall", "sge", "satmut_mpra", "opensplice_snv"];

function ranks(values) {
  const sorted = values.map((value, index) => ({value, index}))
    .sort((a, b) => a.value - b.value);
  const result = Array(values.length);
  for (let start = 0; start < sorted.length;) {
    let end = start + 1;
    while (end < sorted.length && sorted[end].value === sorted[start].value) end++;
    for (let i = start; i < end; i++) result[sorted[i].index] = (start + end - 1) / 2 + 1;
    start = end;
  }
  return result;
}

function pearson(x, y) {
  const mean = (v) => v.reduce((a, b) => a + b, 0) / v.length;
  const mx = mean(x), my = mean(y);
  let covariance = 0, vx = 0, vy = 0;
  for (let i = 0; i < x.length; i++) {
    covariance += (x[i] - mx) * (y[i] - my);
    vx += (x[i] - mx) ** 2;
    vy += (y[i] - my) ** 2;
  }
  return vx > 0 && vy > 0 ? Math.max(-1, Math.min(1, covariance / Math.sqrt(vx * vy))) : null;
}

export function summarizePairs(pairs) {
  const models = pairs.map((row) => row.model_id);
  if (new Set(models).size !== models.length) throw new Error("Repeated model in comparison");
  if (pairs.some((row) => !Number.isFinite(row.vep_score) || !Number.isFinite(row.external_score))) {
    throw new Error("Comparison contains a missing or nonfinite score");
  }
  const summary = {n: models.length, models, spearman: null, pearson: null, leave_one_out: []};
  if (pairs.length < 5) return {...summary, reason: "Fewer than five distinct matched models"};
  const x = pairs.map((row) => row.vep_score), y = pairs.map((row) => row.external_score);
  summary.pearson = pearson(x, y);
  summary.spearman = pearson(ranks(x), ranks(y));
  if (summary.spearman === null) return {...summary, reason: "Constant score vector"};
  // Every omission must still satisfy the five-model rule; recompute its ranks.
  if (pairs.length >= 6) summary.leave_one_out = pairs.map((row, omitted) => {
    const subset = pairs.filter((_, i) => i !== omitted);
    const rho = pearson(ranks(subset.map((r) => r.vep_score)), ranks(subset.map((r) => r.external_score)));
    return {omitted: row.model_id, n: subset.length, spearman: rho,
      material_change: rho === null || Math.sign(rho) !== Math.sign(summary.spearman)
        || Math.abs(rho - summary.spearman) >= 0.2};
  });
  return {...summary, reason: "Exploratory association"};
}

export function vepConfigurations(snapshot) {
  return overallLeaderboardRows(snapshot.runs, snapshot.leaderboard, "spearman").map((row) => ({
    model_id: row.runs[0].model.model_id,
    model_revision: row.runs[0].model.model_revision ?? null,
    family: row.family,
    effort: row.runs[0].generation_parameters.reasoning?.effort ?? null,
    generation_parameters: row.runs[0].generation_parameters,
    scores: {overall: row.score, ...Object.fromEntries(row.task_scores.map((t) => [t.task_family, t.score]))},
    run_ids: row.runs.map((r) => r.run_id),
    question_set_sha256: snapshot.question_set_sha256
  })).sort((a, b) => a.model_id.localeCompare(b.model_id) || (a.effort ?? "").localeCompare(b.effort ?? ""));
}

export function matchStatus(result, configurations) {
  if (result.exclusion) return result.exclusion;
  if (!result.model_id) return "No exact model-version match";
  const versions = configurations.filter((c) => c.model_id === result.model_id
    && c.model_revision === (result.model_revision ?? null));
  if (!versions.length) return "No complete VEP-bench overall configuration for this version";
  if (!efforts.includes(result.effort)) return "External effort not explicitly reported";
  const exact = versions.filter((c) => c.effort === result.effort);
  if (!exact.length) return "No exact effort match";
  if (exact.length > 1) return "Ambiguous VEP-bench configuration at this effort";
  return "Exact version and effort match";
}

export function compareScores(snapshot, external, {effort = null, repeat = 0} = {}) {
  const configurations = vepConfigurations(snapshot);
  const matches = external.results.map((result) => ({...result,
    match_status: matchStatus(result, configurations)}));
  const comparisons = external.comparisons.flatMap((comparison) => {
    const eligible = matches.filter((r) => r.group === comparison.group
      && r.match_status === "Exact version and effort match"
      && Number.isFinite(r.scores[comparison.metric]) && (!effort || r.effort === effort));
    const pairs = [];
    for (const model of [...new Set(eligible.map((r) => r.model_id))].sort()) {
      const candidates = eligible.filter((r) => r.model_id === model);
      const selectedEffort = candidates.map((r) => r.effort)
        .sort((a, b) => efforts.indexOf(b) - efforts.indexOf(a))[0];
      const sameEffort = candidates.filter((r) => r.effort === selectedEffort)
        .sort((a, b) => b.reported_at.localeCompare(a.reported_at) || a.id.localeCompare(b.id));
      const result = sameEffort[repeat];
      if (!result) continue;
      // A timestamp tie cannot be broken by a leaderboard rank or score.
      if (sameEffort.some((r) => r.id !== result.id && r.reported_at === result.reported_at)) {
        throw new Error(`Ambiguous external submission: ${result.id}`);
      }
      const configuration = configurations.find((c) => c.model_id === model
        && c.model_revision === (result.model_revision ?? null) && c.effort === result.effort);
      pairs.push({model_id: model, family: configuration.family, effort: result.effort,
        label: configuration.family, external_model: result.model_label,
        external_score: result.scores[comparison.metric], scores: configuration.scores,
        external_result_id: result.id, source_id: result.source_id,
        run_ids: configuration.run_ids, generation_parameters: configuration.generation_parameters});
    }
    return comparisonScopes.map((scope) => {
      const scopedPairs = pairs.filter((p) => Number.isFinite(p.scores[scope]))
        .map(({scores, ...pair}) => ({...pair, vep_score: scores[scope]}));
      return {...comparison, scope, selection: effort ? `${effort} only` : "Highest exact shared effort",
        repeat, pairs: scopedPairs, summary: summarizePairs(scopedPairs)};
    });
  });
  return {configurations, matches, comparisons};
}

export function pairedScoresCsv(comparisons) {
  const columns = ["comparison", "scope", "selection", "repeat", "model_id", "effort", "vep_score",
    "external_score", "external_result_id", "source_id", "run_ids"];
  const rows = comparisons.flatMap((c) => c.pairs.map((p) => ({...p, comparison: c.id,
    scope: c.scope, selection: c.selection, repeat: c.repeat, run_ids: p.run_ids.join(";")})));
  const quote = (value) => `"${String(value).replaceAll('"', '""')}"`;
  return [columns, ...rows.map((r) => columns.map((c) => r[c]))]
    .map((row) => row.map(quote).join(",")).join("\n") + "\n";
}
