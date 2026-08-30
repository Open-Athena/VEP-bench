export function scored(records) {
  return records.filter((record) => record.scoring.value !== null);
}

export function runCorrect(run) {
  return run.records_data.filter((record) => record.scoring.correct === true).length;
}

export function accuracy(records) {
  const scoredRecords = scored(records);
  if (!scoredRecords.length) return null;
  return scoredRecords.filter((record) => record.scoring.correct === true).length / scoredRecords.length;
}

export function formatFailures(records) {
  return records.filter((record) => record.scoring.parse_error !== null).length;
}

function modelName(modelId, generationParameters) {
  const name = modelId.split("/").at(-1) ?? modelId;
  const displayName = name === "gpt-5.6-luna" ? "GPT 5.6 Luna" : name;
  const effort = generationParameters?.reasoning?.effort;
  return effort ? `${displayName} (${effort})` : displayName;
}

export function formatRunLabel(run) {
  const record = run.records_data[0];
  const model = modelName(
    record?.model.model_id ?? "unknown model",
    record?.generation_parameters
  );
  const provider = record?.model.upstream_provider ?? "provider not reported";
  return `${model} · ${provider}`;
}

function evaluatedAt(run) {
  return run.records_data.reduce((latest, record) => {
    const timestamp = Date.parse(record.evaluated_at);
    return Number.isFinite(timestamp) ? Math.max(latest, timestamp) : latest;
  }, 0);
}

function compareTaskRuns(left, right) {
  return evaluatedAt(left) - evaluatedAt(right)
    || left.run_id.localeCompare(right.run_id);
}

export function leaderboardRows(runs, questions = []) {
  const eligibleRuns = runs.filter(
    (run) => run.current_task_version && run.complete
  );
  const currentTaskFamilies = new Set(
    questions.length
      ? questions.map((question) => question.metadata.task_family)
      : eligibleRuns.map((run) => run.task_family)
  );
  const groups = new Map();
  for (const run of eligibleRuns) {
    const record = run.records_data[0];
    const modelId = record?.model.model_id ?? "unknown";
    const provider = record?.model.upstream_provider ?? "not reported";
    const effort = record?.generation_parameters?.reasoning?.effort ?? null;
    const key = JSON.stringify([modelId, provider, effort]);
    const group = groups.get(key) ?? {
      model_cell: {
        model: modelName(modelId, record?.generation_parameters),
        provider
      },
      task_runs: new Map()
    };
    const existing = group.task_runs.get(run.task_family);
    if (!existing || compareTaskRuns(run, existing) > 0) {
      group.task_runs.set(run.task_family, run);
    }
    groups.set(key, group);
  }

  return Array.from(groups.values())
    .filter((group) => (
      currentTaskFamilies.size > 0
      && currentTaskFamilies.size === group.task_runs.size
      && [...currentTaskFamilies].every((task) => group.task_runs.has(task))
    ))
    .map((group) => ({
      model_cell: group.model_cell,
      records_data: [...currentTaskFamilies]
        .sort()
        .flatMap((task) => group.task_runs.get(task).records_data)
    }))
    .map((group) => ({
      ...group,
      accuracy: accuracy(group.records_data),
      format_failures: formatFailures(group.records_data)
    }))
    .sort((a, b) =>
      (b.accuracy ?? -1) - (a.accuracy ?? -1)
      || a.model_cell.model.localeCompare(b.model_cell.model)
      || a.model_cell.provider.localeCompare(b.model_cell.provider)
    );
}

export function groupCurrentRuns(taskRuns) {
  const groups = new Map();
  for (const candidate of taskRuns.filter(
    (run) => run.current_task_version && run.complete
  )) {
    const group = groups.get(candidate.run_id) ?? {
      run_id: candidate.run_id,
      records_data: []
    };
    group.records_data.push(...candidate.records_data);
    groups.set(candidate.run_id, group);
  }
  return [...groups.values()].sort((a, b) => a.run_id.localeCompare(b.run_id));
}
