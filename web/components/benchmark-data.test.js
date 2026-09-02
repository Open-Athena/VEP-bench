import assert from "node:assert/strict";
import {gzipSync} from "node:zlib";
import test from "node:test";

import {
  answerPath,
  fetchAnswer,
  fetchJson,
  fetchOutcomeIndex,
  groupCurrentRuns,
  leaderboardLineSeries,
  leaderboardRows,
  overallLeaderboardRows,
  outcomeIndexPath
} from "./benchmark-data.js";

function run({
  accuracy = 0.5,
  complete = true,
  completedAt = "2026-08-31T00:00:00Z",
  configurationKey = `cfg-${"0".repeat(64)}`,
  effort = "medium",
  evaluationProfile = "synthetic_effect:mc-effect-v1@1.0",
  family = "Test family",
  modelId = "test/model",
  releaseDate = "2026-07-09",
  runId = "test-run",
  tokens = 1200,
  cost = 0.25
} = {}) {
  return {
    answer_prefix: `answers/${runId}/`,
    completed_at: completedAt,
    configuration_key: configurationKey,
    coverage: {complete},
    generation_parameters: {reasoning: {effort}},
    evaluation_profile: evaluationProfile,
    metrics: {
      accuracy,
      format_failures: 0,
      total_tokens: tokens,
      total_cost_usd: cost
    },
    model: {
      model_id: modelId,
      family,
      release_date: releaseDate,
      upstream_provider: "Test provider"
    },
    outcome_index_path: `outcomes/${runId}.json.gz`,
    question_set_sha256: "0".repeat(64),
    question_set_size: 1,
    run_id: runId
  };
}

test("leaderboard keeps the latest complete run per model configuration", () => {
  const rows = leaderboardRows([
    run({accuracy: 0.2, completedAt: "2026-08-01T00:00:00Z", runId: "old"}),
    run({accuracy: 0.8, completedAt: "2026-08-02T00:00:00Z", runId: "new"}),
    run({complete: false, configurationKey: `cfg-${"1".repeat(64)}`, runId: "bad"})
  ]);

  assert.equal(rows.length, 1);
  assert.equal(rows[0].run.run_id, "new");
  assert.equal(rows[0].accuracy, 0.8);
  assert.equal(rows[0].model_cell.model, "model (medium)");
  assert.equal(rows[0].family, "Test family");
  assert.equal(rows[0].release_date, "2026-07-09");
  assert.equal(rows[0].tokens, 1200);
  assert.equal(rows[0].cost, 0.25);
});

test("overall leaderboard macro-averages complete task profiles", () => {
  const leaderboard = {
    aggregation_method: "task_macro_average_v0",
    evaluation_profiles: [
      {
        task_family: "clinvar",
        evaluation_profile: "clinvar:clinvar-snv-v1@1.0"
      },
      {
        task_family: "vep_most_severe_consequence",
        evaluation_profile: "vep_most_severe_consequence:vep-most-severe-v1@1.2"
      }
    ]
  };
  const rows = overallLeaderboardRows([
    run({
      accuracy: 0.75,
      configurationKey: `cfg-${"1".repeat(64)}`,
      cost: 0.5,
      evaluationProfile: "clinvar:clinvar-snv-v1@1.0",
      runId: "medium-clinvar",
      tokens: 300
    }),
    run({
      accuracy: 0.25,
      configurationKey: `cfg-${"2".repeat(64)}`,
      cost: 0.25,
      evaluationProfile: "vep_most_severe_consequence:vep-most-severe-v1@1.2",
      runId: "medium-consequence",
      tokens: 200
    }),
    run({
      accuracy: 0.9,
      configurationKey: `cfg-${"3".repeat(64)}`,
      effort: "low",
      evaluationProfile: "vep_most_severe_consequence:vep-most-severe-v1@1.2",
      runId: "low-consequence-only"
    })
  ], leaderboard);

  assert.equal(rows.length, 1);
  assert.equal(rows[0].accuracy, 0.5);
  assert.equal(rows[0].tokens, 500);
  assert.equal(rows[0].cost, 0.75);
  assert.deepEqual(
    rows[0].task_scores.map((task) => [task.task_family, task.accuracy]),
    [["clinvar", 0.75], ["vep_most_severe_consequence", 0.25]]
  );
  assert.deepEqual(rows[0].runs.map((candidate) => candidate.run_id), [
    "medium-clinvar",
    "medium-consequence"
  ]);
});

test("overall leaderboard rejects unknown aggregation metadata", () => {
  assert.deepEqual(overallLeaderboardRows([], null), []);
  assert.deepEqual(overallLeaderboardRows([], {
    aggregation_method: "question_micro_average_v0",
    evaluation_profiles: []
  }), []);
});

test("line chart data groups model families and sorts points by the selected metric", () => {
  const rows = leaderboardRows([
    run({
      accuracy: 0.8,
      configurationKey: `cfg-${"1".repeat(64)}`,
      cost: 2,
      effort: "high",
      runId: "family-a-high",
      tokens: 200
    }),
    run({
      accuracy: 0.6,
      configurationKey: `cfg-${"2".repeat(64)}`,
      cost: 1,
      effort: "low",
      modelId: "test/model-v2",
      runId: "family-a-low",
      tokens: 100
    }),
    run({
      accuracy: 0.7,
      configurationKey: `cfg-${"3".repeat(64)}`,
      cost: 1.5,
      family: "Another family",
      modelId: "test/another",
      runId: "family-b",
      tokens: null
    })
  ]);

  const costSeries = leaderboardLineSeries(rows, "cost");
  assert.deepEqual(costSeries.map((series) => series.family), [
    "Another family",
    "Test family"
  ]);
  assert.deepEqual(
    costSeries[1].points.map((point) => point.x),
    [1, 2]
  );
  const tokenSeries = leaderboardLineSeries(rows, "tokens");
  assert.deepEqual(tokenSeries.map((series) => series.family), ["Test family"]);
  assert.throws(() => leaderboardLineSeries(rows, "release_date"), /Unknown/);
});

test("question explorer exposes only complete runs", () => {
  const runs = groupCurrentRuns([
    run({runId: "zeta"}),
    run({complete: false, runId: "ignored"}),
    run({runId: "alpha"})
  ]);
  assert.deepEqual(runs.map((candidate) => candidate.run_id), ["alpha", "zeta"]);
});

test("answer path is one encoded object and rejects mismatched prefixes", () => {
  const candidate = run({runId: "demo-run"});
  assert.equal(
    answerPath(candidate, "task:question-1"),
    "answers/demo-run/task%3Aquestion-1.json.gz"
  );
  candidate.answer_prefix = "answers/another-run/";
  assert.throws(() => answerPath(candidate, "task:question-1"), /does not match/);
});

test("fetchAnswer downloads and decompresses exactly one gzip object", async () => {
  const answer = {question_id: "task:question-1", scoring: {correct: true}};
  const compressed = gzipSync(`${JSON.stringify(answer)}\n`, {mtime: 0});
  const requested = [];
  const fetcher = async (url) => {
    requested.push(url);
    return new Response(compressed, {status: 200});
  };

  const observed = await fetchAnswer(
    "https://example.test/versions/main",
    run({runId: "demo-run"}),
    "task:question-1",
    fetcher
  );

  assert.deepEqual(observed, answer);
  assert.deepEqual(requested, [
    "https://example.test/versions/main/answers/demo-run/task%3Aquestion-1.json.gz"
  ]);
});

test("fetchOutcomeIndex downloads the compact results for one run", async () => {
  const candidate = run({runId: "demo-run"});
  const outcomeIndex = {
    schema_version: "1.0",
    run_id: candidate.run_id,
    question_set_sha256: candidate.question_set_sha256,
    question_set_size: candidate.question_set_size,
    outcomes: [{question_id: "task:question-1", correct: true}]
  };
  const compressed = gzipSync(`${JSON.stringify(outcomeIndex)}\n`, {mtime: 0});
  const requested = [];
  const fetcher = async (url) => {
    requested.push(url);
    return new Response(compressed, {status: 200});
  };

  assert.deepEqual(
    await fetchOutcomeIndex("https://example.test/versions/main", candidate, fetcher),
    outcomeIndex
  );
  assert.deepEqual(requested, [
    "https://example.test/versions/main/outcomes/demo-run.json.gz"
  ]);
});

test("outcome index path rejects mismatched run metadata", () => {
  const candidate = run({runId: "demo-run"});
  candidate.outcome_index_path = "outcomes/another-run.json.gz";
  assert.throws(() => outcomeIndexPath(candidate), /does not match/);
});

test("fetchJson rejects non-success responses with the status code", async () => {
  const fetcher = async () => new Response("unavailable", {status: 503});

  await assert.rejects(
    fetchJson("https://example.test/versions/main/runs.json", fetcher),
    /HTTP 503/
  );
});

test("fetchAnswer rejects unsafe identifiers before making a request", async () => {
  let requested = false;
  const fetcher = async () => {
    requested = true;
    return new Response();
  };

  await assert.rejects(
    fetchAnswer(
      "https://example.test/versions/main",
      run({runId: "unsafe/run"}),
      "task:question-1",
      fetcher
    ),
    /Unsafe run or question ID/
  );
  assert.equal(requested, false);
});
