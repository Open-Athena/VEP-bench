import assert from "node:assert/strict";
import test from "node:test";
import {specialistRows, specialistCsv} from "./specialists.js";

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
