import assert from "node:assert/strict";
import test from "node:test";

import {groupCurrentRuns, leaderboardRows} from "./benchmark-data.js";

function question(taskFamily) {
  return {metadata: {task_family: taskFamily}};
}

function record({correct, evaluatedAt, modelId, questionId}) {
  return {
    evaluated_at: evaluatedAt,
    generation_parameters: {reasoning: {effort: "medium"}},
    model: {
      model_id: modelId,
      upstream_provider: "Test provider"
    },
    question_id: questionId,
    scoring: {
      correct,
      parse_error: null,
      value: correct ? 1 : 0
    }
  };
}

function taskRun({complete = true, current = true, runId, taskFamily, records}) {
  return {
    complete,
    current_task_version: current,
    records_data: records,
    run_id: runId,
    task_family: taskFamily
  };
}

test("leaderboard requires full task coverage and keeps the latest task run", () => {
  const fullModel = "test/full-model";
  const partialModel = "test/partial-model";
  const runs = [
    taskRun({
      runId: "full-alpha-old",
      taskFamily: "alpha",
      records: [record({
        correct: false,
        evaluatedAt: "2026-08-01T00:00:00Z",
        modelId: fullModel,
        questionId: "alpha-old"
      })]
    }),
    taskRun({
      runId: "full-alpha-new",
      taskFamily: "alpha",
      records: [record({
        correct: true,
        evaluatedAt: "2026-08-02T00:00:00Z",
        modelId: fullModel,
        questionId: "alpha-new"
      })]
    }),
    taskRun({
      runId: "full-beta",
      taskFamily: "beta",
      records: [record({
        correct: true,
        evaluatedAt: "2026-08-01T00:00:00Z",
        modelId: fullModel,
        questionId: "beta"
      })]
    }),
    taskRun({
      runId: "partial-alpha",
      taskFamily: "alpha",
      records: [record({
        correct: true,
        evaluatedAt: "2026-08-03T00:00:00Z",
        modelId: partialModel,
        questionId: "partial-alpha"
      })]
    })
  ];

  const rows = leaderboardRows(runs, [question("alpha"), question("beta")]);

  assert.equal(rows.length, 1);
  assert.equal(rows[0].model_cell.model, "full-model (medium)");
  assert.equal(rows[0].accuracy, 1);
  assert.deepEqual(
    rows[0].records_data.map((entry) => entry.question_id),
    ["alpha-new", "beta"]
  );
});

test("question runs recombine current task slices sharing one run id", () => {
  const sharedRecord = (questionId) => record({
    correct: true,
    evaluatedAt: "2026-08-01T00:00:00Z",
    modelId: "test/model",
    questionId
  });
  const runs = [
    taskRun({
      runId: "mixed-run",
      taskFamily: "alpha",
      records: [sharedRecord("alpha-question")]
    }),
    taskRun({
      runId: "mixed-run",
      taskFamily: "beta",
      records: [sharedRecord("beta-question")]
    }),
    taskRun({
      complete: false,
      runId: "incomplete-run",
      taskFamily: "alpha",
      records: [sharedRecord("ignored-question")]
    })
  ];

  const grouped = groupCurrentRuns(runs);

  assert.deepEqual(grouped.map((run) => run.run_id), ["mixed-run"]);
  assert.deepEqual(
    grouped[0].records_data.map((entry) => entry.question_id),
    ["alpha-question", "beta-question"]
  );
});
