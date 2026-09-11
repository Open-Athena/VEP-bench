import assert from "node:assert/strict";
import {readFileSync} from "node:fs";
import test from "node:test";
import {variantType} from "../../components/benchmark-data.js";
import {compositionCsv, variantComposition} from "./analysis.js";

const variant = (ref, alt, consequence = "missense_variant") => ({
  genomic: {ref, alt}, most_severe_consequence: consequence
});

test("composition counts panel appearances with a separate denominator for each task", () => {
  const snv = variant("AC", "AT");
  const summary = variantComposition({by_task_family: {
    sge: {
      first: {variants: {V01: snv, V02: variant("AC", "GT", "stop_gained")}},
      second: {variants: {V01: snv, V02: variant("A", "AT", "frameshift_variant")}}
    },
    satmut_mpra: {first: {variants: {V01: snv}}},
    opensplice_snv: {first: {variants: {V01: variant("AT", "A", "intron_variant")}}}
  }});
  const count = (family, dimension, category) => summary.rows.find(
    (row) => row.task_family === family && row.dimension === dimension && row.category === category
  );
  assert.deepEqual(summary.tasks.map((task) => [task.family, task.panels, task.total]), [
    ["sge", 2, 4], ["satmut_mpra", 1, 1], ["opensplice_snv", 1, 1]
  ]);
  assert.equal(count("sge", "type", "SNV").count, 2);
  assert.equal(count("sge", "type", "SNV").proportion, 0.5);
  assert.equal(count("satmut_mpra", "type", "SNV").proportion, 1);
  assert.equal(count("sge", "type", "Indel").count, 1);
  assert.equal(count("sge", "type", "Multibase substitution").count, 1);
  assert.equal(count("satmut_mpra", "consequence", "frameshift_variant").count, 0);
  for (const family of ["sge", "satmut_mpra", "opensplice_snv"]) {
    for (const dimension of ["type", "consequence"]) {
      assert.equal(summary.rows.filter((row) => row.task_family === family && row.dimension === dimension)
        .reduce((sum, row) => sum + row.proportion, 0), 1);
    }
  }
});

test("missing annotations remain in denominators; missing panels make a task unavailable", () => {
  const summary = variantComposition({by_task_family: {
    sge: {first: {variants: {V01: variant("A", "C"), V02: {}}}},
    opensplice_snv: {first: {variants: {}}}
  }});
  assert.deepEqual(summary.tasks.map((task) => task.available), [true, false, false]);
  assert.equal(summary.rows.find((row) => row.category === "Not annotated").proportion, 0.5);
  assert.equal(summary.rows.find((row) => row.category === "Unknown").proportion, 0.5);
  assert.equal(variantComposition({}).rows.length, 0);
});

test("saved snapshot agrees with prepared source candidates", () => {
  const snapshot = JSON.parse(readFileSync(new URL("../../../data/variant-annotations.json", import.meta.url)));
  const summary = variantComposition({
    by_task_family: snapshot.by_task_family, variant_annotation: snapshot.annotation
  });
  const sources = ["sge-mavedb-2026-09-03", "satmut-mpra-cadd-v1.7", "opensplice-snv-figshare-v5"];
  for (const source of sources) {
    const records = readFileSync(new URL(`../../../../../data/sources/${source}.jsonl`, import.meta.url), "utf8")
      .trim().split("\n").map(JSON.parse);
    const task = summary.tasks.find((task) => task.family === records[0].task_family);
    assert.equal(task.panels, records.length);
    assert.equal(task.total, records.reduce((sum, record) => sum + record.candidates.length, 0));
    for (const record of records) {
      const annotated = snapshot.by_task_family[task.family][record.source_record_id];
      assert.deepEqual(Object.keys(annotated.variants).sort(), record.candidates.map((candidate) => candidate.candidate_id).sort());
      for (const candidate of record.candidates) {
        const genomic = annotated.variants[candidate.candidate_id].genomic;
        assert.equal(variantType(genomic.ref, genomic.alt), variantType(candidate.ref, candidate.alt));
      }
    }
  }
});

test("CSV includes exact counts, denominator, and escaped category names", () => {
  const row = {task_family: "sge", task: "Fitness", panels: 2, total: 4,
    dimension: "consequence", category: 'test, "quoted"', count: 1, proportion: 0.25};
  assert.equal(compositionCsv([row]).split("\n")[1],
    '"sge","Fitness","2","4","consequence","test, ""quoted""","1","0.25"');
});
