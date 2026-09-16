import assert from "node:assert/strict";
import test from "node:test";
import {specialistRows, specialistCsv, specialistModelOrder, specialistTasks} from "./specialists.js";

const snapshot = () => ({
  status: "complete",
  runs: [{run_id: "llm", model: {model_id: "openai/test", family: "OpenAI"}, generation_parameters: {}}],
  coverage: [{task_family: "sge", category: "all_covered", status: "eligible",
    eligible_variants: 3, eligible_panels: 1,
    panels: [{question_id: "gene", status: "eligible", candidate_ids: ["V1", "V2", "V3"]}]}],
  results: ["llm", "specialist"].map((run_id) => ({task_family: "sge", category: "all_covered",
    run_id, mean_spearman_rho: 0.5, mean_pearson_r: 0.4, invalid_panels: 0,
    panels: [{question_id: "gene"}]}))
});

test("a pending analysis never displays synthetic scores", () => {
  assert.deepEqual(specialistRows({status: "awaiting_inference", results: []}), []);
});

test("specialist rows retain matched counts, task labels and model families", () => {
  const rows = specialistRows(snapshot());
  assert.equal(rows.length, 2);
  assert.equal(rows[0].family, "OpenAI");
  assert.equal(rows[1].model, "AlphaGenome / AVI");
  assert.equal(rows[1].task_label, "Fitness");
  assert.equal(rows[1].eligible_variants, 3);
  assert.match(specialistCsv(snapshot()), /AlphaGenome \/ AVI/);
});

test("mismatched panels fail instead of plotting a misleading comparison", () => {
  const data = snapshot();
  data.results[1].panels[0].question_id = "other";
  assert.throws(() => specialistRows(data), /panel IDs/);
});

const intervals = (data) => ({intervals: data.results.map((row) => ({
  task_family: row.task_family, category: row.category, run_id: row.run_id,
  eligible_panels: row.panels.length, mean_spearman_rho: row.mean_spearman_rho,
  spearman_ci_low: 0.2, spearman_ci_high: 0.8, spearman_ci_status: "estimated"
}))});

test("matched intervals appear in rows and the CSV, including unavailable intervals", () => {
  const data = snapshot();
  const stats = intervals(data);
  stats.intervals[1] = {...stats.intervals[1], spearman_ci_low: null,
    spearman_ci_high: null, spearman_ci_status: "constant_scores"};
  const rows = specialistRows(data, "all_covered", stats);
  assert.equal(rows[0].spearman_ci_low, 0.2);
  assert.equal(rows[0].spearman_ci_high, 0.8);
  assert.equal(rows[1].spearman_ci_status, "constant_scores");
  assert.equal(rows[1].spearman_ci_low, null);
  assert.match(specialistCsv(data, stats), /"spearman_ci_low","spearman_ci_high"/);
});

test("missing, stale or malformed intervals fail before plotting", () => {
  for (const change of [
    (stats) => stats.intervals.pop(),
    (stats) => stats.intervals.push(stats.intervals[0]),
    (stats) => stats.intervals[0].mean_spearman_rho = 0.1,
    (stats) => stats.intervals[0].eligible_panels = 2,
    (stats) => stats.intervals[0].spearman_ci_low = 0.6,
    (stats) => stats.intervals[0].spearman_ci_high = Infinity,
    (stats) => stats.intervals[0].spearman_ci_status = "unknown"
  ]) {
    const data = snapshot(), stats = intervals(data);
    change(stats);
    assert.throws(() => specialistRows(data, "all_covered", stats), /interval/i);
  }
});

test("models follow overall matched performance and tasks use the benchmark order", () => {
  const overall = [
    ["a-model", 0.4, "all_covered"], ["z-model", 0.8, "all_covered"],
    ["AlphaGenome / AVI", 0.6, "all_covered"], ["excluded", 0.9, "excluding_avi_model_selection"]
  ].map(([model_id, mean_spearman_rho, category]) => ({category, mean_spearman_rho,
    configuration: {model: {model_id}, generation_parameters: {}}}));
  assert.deepEqual(specialistModelOrder({overall}), ["z-model", "AlphaGenome / AVI", "a-model"]);
  assert.deepEqual(specialistTasks.map((task) => task.label), ["Fitness", "Expression", "Splicing"]);
});
