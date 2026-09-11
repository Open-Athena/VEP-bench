import {variantType} from "../../components/benchmark-data.js";

export const compositionTasks = [
  {family: "sge", label: "Fitness (SGE)", color: "#4267a8"},
  {family: "satmut_mpra", label: "Expression (satMutMPRA)", color: "#b45f23"},
  {family: "opensplice_snv", label: "Splicing (OpenSplice)", color: "#248474"}
];

export const compositionTypes = ["SNV", "Indel", "Multibase substitution", "Unknown"];

export function variantComposition(metadata) {
  const tasks = compositionTasks.map((task) => {
    const panels = Object.values(metadata?.by_task_family?.[task.family] ?? {});
    const variants = panels.flatMap((panel) => Object.values(panel.variants ?? {}));
    // Without the annotation snapshot, OpenSplice's genomic mapping is absent.
    // Do not present partial metadata as the full selected population.
    const available = panels.length > 0 && panels.every(
      (panel) => Object.keys(panel.variants ?? {}).length > 0
    );
    return {...task, panels: panels.length, variants, available};
  });
  const consequences = [...new Set(tasks.filter((task) => task.available).flatMap(
    (task) => task.variants.map((variant) => variant.most_severe_consequence ?? "Not annotated")
  ))].sort();
  const rows = tasks.filter((task) => task.available).flatMap((task) => {
    const counts = {type: new Map(), consequence: new Map()};
    // Count panel appearances, including an allele selected in multiple panels.
    for (const variant of task.variants) {
      const categories = {
        type: variantType(variant.genomic?.ref, variant.genomic?.alt),
        consequence: variant.most_severe_consequence ?? "Not annotated"
      };
      for (const [dimension, category] of Object.entries(categories)) {
        counts[dimension].set(category, (counts[dimension].get(category) ?? 0) + 1);
      }
    }
    return Object.entries({type: compositionTypes, consequence: consequences}).flatMap(
      ([dimension, categories]) => categories.map((category) => ({
        task_family: task.family,
        task: task.label,
        panels: task.panels,
        total: task.variants.length,
        dimension,
        category,
        count: counts[dimension].get(category) ?? 0,
        proportion: (counts[dimension].get(category) ?? 0) / task.variants.length
      }))
    );
  });
  return {
    tasks: tasks.map(({variants, ...task}) => ({...task, total: variants.length})),
    rows,
    consequences,
    types: compositionTypes.filter((type) => rows.some(
      (row) => row.dimension === "type" && row.category === type && row.count > 0
    ))
  };
}

export function compositionCsv(rows) {
  const columns = ["task_family", "task", "panels", "total", "dimension", "category", "count", "proportion"];
  const escape = (value) => `"${String(value).replaceAll('"', '""')}"`;
  return [columns, ...rows.map((row) => columns.map((column) => row[column]))]
    .map((row) => row.map(escape).join(",")).join("\n") + "\n";
}
