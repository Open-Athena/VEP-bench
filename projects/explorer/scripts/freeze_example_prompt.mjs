// Extract the blog example from the question index verified against its frozen publication.
// Usage: node projects/explorer/scripts/freeze_example_prompt.mjs QUESTION_INDEX_JSON
import {createHash} from "node:crypto";
import {readFileSync, writeFileSync} from "node:fs";

if (!process.argv[2]) throw new Error("Expected the downloaded question-index.json path");
const directory = new URL("../web/blog/introducing-vep-bench/", import.meta.url);
const manifest = JSON.parse(readFileSync(new URL("specialist-2026-09-17.manifest.json", directory)));
const bytes = readFileSync(process.argv[2]);
const index = JSON.parse(bytes);
const descriptor = manifest.artifacts.question_index;
if (bytes.length !== descriptor.artifact_bytes
    || createHash("sha256").update(bytes).digest("hex") !== descriptor.artifact_sha256
    || index.question_set_sha256 !== manifest.question_set_sha256) {
  throw new Error("Question index does not match the blog's frozen publication");
}

// MSH6 exon 7 was selected for the explanation case study in issue #97.
const question = index.questions.find((entry) => entry.question_id === "opensplice-snv-ranking-v2:E01");
if (!question || typeof question.prompt !== "string" || question.candidates.length !== 50) {
  throw new Error("Expected the complete 50-variant E01 prompt");
}
writeFileSync(new URL("msh6-e7-prompt.txt", directory), `${question.prompt}\n`);
