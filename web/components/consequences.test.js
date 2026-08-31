import assert from "node:assert/strict";
import test from "node:test";

import {consequenceTableRows, sequenceOntologyUrl} from "./consequences.js";

const choices = [
  ["C01", "3_prime_UTR_variant"],
  ["C02", "5_prime_UTR_variant"],
  ["C03", "coding_sequence_variant"],
  ["C04", "incomplete_terminal_codon_variant"],
  ["C05", "intergenic_variant / intron_variant / upstream_gene_variant / downstream_gene_variant"],
  ["C06", "mature_miRNA_variant"],
  ["C07", "missense_variant"],
  ["C08", "non_coding_transcript_exon_variant"],
  ["C09", "splice_acceptor_variant"],
  ["C10", "splice_donor_5th_base_variant"],
  ["C11", "splice_donor_region_variant"],
  ["C12", "splice_donor_variant"],
  ["C13", "splice_polypyrimidine_tract_variant"],
  ["C14", "splice_region_variant"],
  ["C15", "start_lost"],
  ["C16", "stop_gained"],
  ["C17", "stop_lost"],
  ["C18", "stop_retained_variant"],
  ["C19", "synonymous_variant"]
].map(([choice_id, text]) => ({choice_id, text}));

test("consequence rows cover every benchmark source term in severity order", () => {
  const rows = consequenceTableRows(choices);

  assert.equal(rows.length, 41);
  assert.deepEqual(
    rows.slice(0, 5).map((row) => row.term),
    [
      "transcript_ablation",
      "splice_acceptor_variant",
      "splice_donor_variant",
      "stop_gained",
      "frameshift_variant"
    ]
  );
  assert.deepEqual(
    [...new Set(rows.map((row) => row.choice_id).filter(Boolean))].sort(),
    choices.map((choice) => choice.choice_id).sort()
  );
  assert.deepEqual(
    rows.filter((row) => row.choice_id === "C05").map((row) => row.term).sort(),
    [
      "downstream_gene_variant",
      "intergenic_variant",
      "intron_variant",
      "upstream_gene_variant"
    ]
  );
  assert.equal(rows.filter((row) => row.choice_id !== null).length, 22);
  assert.deepEqual(
    rows.find((row) => row.term === "frameshift_variant"),
    {
      term: "frameshift_variant",
      description: "A sequence variant that disrupts the translational reading frame because the number of inserted or deleted nucleotides is not a multiple of three",
      accession: "SO:0001589",
      impact: "HIGH",
      color: "#9400d3",
      choice_id: null,
      choice_label: "Not included in this benchmark",
      severity_order: 5
    }
  );
});

test("SO accessions link to the same term pages used by Ensembl", () => {
  assert.equal(
    sequenceOntologyUrl("SO:0001574"),
    "http://www.sequenceontology.org/miso/current_svn/term/SO:0001574"
  );
  assert.throws(() => sequenceOntologyUrl("not-an-accession"));
});
