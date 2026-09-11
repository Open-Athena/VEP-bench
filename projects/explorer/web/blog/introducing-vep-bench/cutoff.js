import {
  assayCutoffRelation,
  highestEffortRows,
  leaderboardRowsForScope,
  questionDisplayMetadata,
  rankingOutcomeMetrics
} from "../../components/benchmark-data.js";

export function sgeCutoffModels(document) {
  return highestEffortRows(
    leaderboardRowsForScope(document.runs, document.leaderboard, "sge", "spearman")
  )
    .filter(({run}) => assayCutoffRelation("2000-01-01", run.model.knowledge_cutoff) !== "Unknown");
}

export function sgeCutoffPerformance(models, questions, metadata, outcomeStates) {
  const panels = questions.filter((question) => question.metadata?.task_family === "sge");
  const summaries = [];
  const scores = [];
  const unavailable = [];
  for (const {run, model_cell} of models) {
    const state = outcomeStates.get(run.run_id);
    const document = state?.document;
    const outcomes = new Map((Array.isArray(document?.outcomes) ? document.outcomes : [])
      .map((outcome) => [outcome.question_id, outcome]));
    // Never show a partial download as a change in the panel composition.
    if (state?.error || !panels.length || !Array.isArray(document?.outcomes) || document?.run_id !== run.run_id
      || document.question_set_sha256 !== run.question_set_sha256
      || document.question_set_size !== panels.length
      || run.question_set_size !== panels.length
      || document.outcomes.length !== panels.length || outcomes.size !== panels.length
      || panels.some((panel) => {
        const score = rankingOutcomeMetrics(outcomes.get(panel.question_id)).spearman_rho;
        return score === null || score < -1 || score > 1;
      })) {
      unavailable.push(model_cell.model);
      continue;
    }
    const identity = {
      model: model_cell.model,
      model_id: run.model.model_id,
      reasoning_effort: run.generation_parameters?.reasoning?.effort ?? null,
      provider: model_cell.provider,
      run_id: run.run_id,
      question_set_sha256: run.question_set_sha256,
      knowledge_cutoff: run.model.knowledge_cutoff,
      knowledge_cutoff_url: run.model.knowledge_cutoff_url
    };
    const records = panels.map((question) => {
      const display = questionDisplayMetadata(question, metadata);
      const outcome = outcomes.get(question.question_id);
      return {
        ...identity,
        question_id: question.question_id,
        gene: display?.element ?? question.provenance?.source_record_id,
        assay_date: display?.assay_first_indexed?.date ?? null,
        assay_url: display?.assay_first_indexed?.url ?? null,
        relation: assayCutoffRelation(display?.assay_first_indexed, identity.knowledge_cutoff),
        spearman_rho: rankingOutcomeMetrics(outcome).spearman_rho,
        valid: outcome.valid
      };
    });
    const group = (relation) => {
      const subset = records.filter((record) => record.relation === relation);
      return {
        n: subset.length,
        mean: subset.length ? subset.reduce((sum, record) => sum + record.spearman_rho, 0) / subset.length : null,
        format_failures: subset.filter((record) => record.valid === false).length
      };
    };
    const before = group("Before cutoff");
    const after = group("After cutoff");
    summaries.push({
      ...identity,
      before_n: before.n,
      before_mean: before.mean,
      before_format_failures: before.format_failures,
      after_n: after.n,
      after_mean: after.mean,
      after_format_failures: after.format_failures,
      excluded_n: records.filter((record) => record.relation === "Unknown").length,
      difference: before.n && after.n ? before.mean - after.mean : null
    });
    scores.push(...records);
  }
  return {summaries, scores, unavailable};
}

export function cutoffCsv(rows) {
  if (!rows.length) return "";
  const columns = Object.keys(rows[0]);
  const escape = (value) => `"${String(value ?? "").replaceAll('"', '""')}"`;
  return [columns, ...rows.map((row) => columns.map((column) => row[column]))]
    .map((row) => row.map(escape).join(",")).join("\n") + "\n";
}
