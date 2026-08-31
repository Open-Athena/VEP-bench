import assert from "node:assert/strict";
import {gzipSync} from "node:zlib";
import test from "node:test";

import {
  answerPath,
  fetchAnswer,
  fetchJson,
  groupCurrentRuns,
  leaderboardRows
} from "./benchmark-data.js";

function run({
  accuracy = 0.5,
  complete = true,
  completedAt = "2026-08-31T00:00:00Z",
  configurationKey = `cfg-${"0".repeat(64)}`,
  effort = "medium",
  runId = "test-run"
} = {}) {
  return {
    answer_prefix: `answers/${runId}/`,
    completed_at: completedAt,
    configuration_key: configurationKey,
    coverage: {complete},
    generation_parameters: {reasoning: {effort}},
    metrics: {accuracy, format_failures: 0},
    model: {
      model_id: "test/model",
      upstream_provider: "Test provider"
    },
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
