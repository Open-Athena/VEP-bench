import {readFileSync} from "node:fs";
import {pathToFileURL} from "node:url";
import {join} from "node:path";

const [baseUrl, assets] = process.argv.slice(2);
const {artifactUrl, fetchJson, fetchOutcomeIndex} = await import(
  pathToFileURL(join(assets, "components/benchmark-data.js"))
);
const {sgeCutoffModels, sgeCutoffPerformance} = await import(
  pathToFileURL(join(assets, "blog/introducing-vep-bench/cutoff.js"))
);
const metadata = JSON.parse(readFileSync(0, "utf8"));
const [runs, questions] = await Promise.all([
  fetchJson(artifactUrl(baseUrl, "runs.json")),
  fetchJson(artifactUrl(baseUrl, "question-index.json"))
]);
if (runs.question_set_sha256 !== questions.question_set_sha256) {
  throw new Error("Run and question indexes describe different publications");
}
const models = sgeCutoffModels(runs);
const outcomes = new Map(await Promise.all(models.map(async ({run}) => [
  run.run_id, {document: await fetchOutcomeIndex(baseUrl, run)}
])));
console.log(JSON.stringify({
  ...sgeCutoffPerformance(models, questions.questions, metadata, outcomes),
  comparison_count: models.length,
  data_base_url: baseUrl,
  question_set_sha256: runs.question_set_sha256
}));
