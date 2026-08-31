const SAFE_ID = /^[A-Za-z0-9][A-Za-z0-9._:-]*$/;

function modelName(modelId, generationParameters) {
  const name = modelId.split("/").at(-1) ?? modelId;
  const displayName = name === "gpt-5.6-luna" ? "GPT 5.6 Luna" : name;
  const effort = generationParameters?.reasoning?.effort;
  return effort ? `${displayName} (${effort})` : displayName;
}

export function formatRunLabel(run) {
  const model = modelName(run?.model?.model_id ?? "unknown model", run?.generation_parameters);
  const provider = run?.model?.upstream_provider ?? "provider not reported";
  return `${model} · ${provider}`;
}

export function leaderboardRows(runs) {
  const latestByConfiguration = new Map();
  for (const run of runs.filter((candidate) => candidate.coverage.complete)) {
    const previous = latestByConfiguration.get(run.configuration_key);
    if (!previous
      || Date.parse(run.completed_at) > Date.parse(previous.completed_at)
      || (run.completed_at === previous.completed_at && run.run_id > previous.run_id)) {
      latestByConfiguration.set(run.configuration_key, run);
    }
  }
  return [...latestByConfiguration.values()]
    .map((run) => ({
      run,
      model_cell: {
        model: modelName(run.model.model_id, run.generation_parameters),
        provider: run.model.upstream_provider ?? "not reported"
      },
      accuracy: run.metrics.accuracy,
      format_failures: run.metrics.format_failures
    }))
    .sort((a, b) =>
      (b.accuracy ?? -1) - (a.accuracy ?? -1)
      || a.model_cell.model.localeCompare(b.model_cell.model)
      || a.model_cell.provider.localeCompare(b.model_cell.provider)
    );
}

export function groupCurrentRuns(runs) {
  return runs
    .filter((run) => run.coverage.complete)
    .toSorted((left, right) => left.run_id.localeCompare(right.run_id));
}

export function artifactUrl(baseUrl, path) {
  const base = new URL(baseUrl.endsWith("/") ? baseUrl : `${baseUrl}/`);
  return new URL(path, base).href;
}

export async function fetchJson(url, fetcher = fetch) {
  const response = await fetcher(url);
  if (!response.ok) throw new Error(`Unable to load ${url}: HTTP ${response.status}`);
  return response.json();
}

export function answerPath(run, questionId) {
  if (!SAFE_ID.test(run.run_id) || !SAFE_ID.test(questionId)) {
    throw new Error("Unsafe run or question ID");
  }
  if (run.answer_prefix !== `answers/${run.run_id}/`) {
    throw new Error("Run answer prefix does not match its ID");
  }
  return `${run.answer_prefix}${encodeURIComponent(questionId)}.json.gz`;
}

export async function fetchAnswer(baseUrl, run, questionId, fetcher = fetch) {
  const url = artifactUrl(baseUrl, answerPath(run, questionId));
  const response = await fetcher(url);
  if (!response.ok) throw new Error(`Unable to load answer: HTTP ${response.status}`);
  if (typeof DecompressionStream !== "function") {
    throw new Error("This browser does not support gzip decompression");
  }
  const decompressed = response.body.pipeThrough(new DecompressionStream("gzip"));
  return new Response(decompressed).json();
}
