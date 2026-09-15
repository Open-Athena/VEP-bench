import {createHash} from "node:crypto";

const sha = (bytes) => createHash("sha256").update(bytes).digest("hex");

export function refreshVepSnapshot(previous, manifestBytes, runsBytes, date) {
  const manifest = JSON.parse(manifestBytes), runs = JSON.parse(runsBytes);
  const descriptor = manifest.artifacts.runs;
  if (sha(runsBytes) !== descriptor.artifact_sha256 || runsBytes.length !== descriptor.artifact_bytes
      || runs.question_set_sha256 !== manifest.question_set_sha256
      || runs.question_set_size !== manifest.question_set_size) {
    throw new Error("VEP runs do not match the frozen publication manifest");
  }
  if (!/^\d{4}-\d{2}-\d{2}$/.test(date ?? "")
      || new Date(`${date}T00:00:00Z`).toISOString().slice(0, 10) !== date) {
    throw new Error("Expected a valid snapshot date (YYYY-MM-DD)");
  }
  if (previous.sources.filter((source) => source.id === "vep").length !== 1) {
    throw new Error("Expected exactly one VEP source to refresh");
  }
  return {...previous, snapshot_date: date, sources: previous.sources.map((source) => source.id !== "vep"
    ? source : {...source, retrieved_at: date, downloaded_sha256: sha(manifestBytes),
      locator: "Public publication verified against the post's frozen manifest",
      evidence: {runs: descriptor, question_set_sha256: manifest.question_set_sha256,
        question_set_size: manifest.question_set_size}})};
}
