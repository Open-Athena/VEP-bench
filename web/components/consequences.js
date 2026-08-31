// Ensembl release 116 consequence metadata. The array order follows Ensembl
// severity order; benchmark coverage is added separately by consequenceTableRows.
// https://github.com/Ensembl/ensembl-webcode/blob/release/116/modules/EnsEMBL/Web/Document/HTML/ConsequenceTable.pm
// Copyright [1999-2015] Wellcome Trust Sanger Institute and EMBL-EBI;
// Copyright [2016-2024] EMBL-EBI. Licensed under Apache License 2.0.
export const ENSEMBL_CONSEQUENCE_SOURCE = Object.freeze({
  release: "116",
  page: "https://useast.ensembl.org/info/genome/variation/prediction/predicted_data.html",
  diagram: "https://github.com/Ensembl/public-plugins/blob/release/116/ensembl/htdocs/info/genome/variation/prediction/consequences.svg",
  imageReuse: "https://www.ensembl.org/info/about/legal/image_reuse.html"
});

const SEQUENCE_ONTOLOGY_TERM_BASE =
  "http://www.sequenceontology.org/miso/current_svn/term/";

export const CONSEQUENCE_DEFINITIONS = Object.freeze([
  {
    term: "transcript_ablation",
    description: "A feature ablation whereby the deleted region includes a transcript feature",
    accession: "SO:0001893",
    impact: "HIGH",
    color: "#ff0000"
  },
  {
    term: "splice_acceptor_variant",
    description: "A splice variant that changes the 2 base region at the 3' end of an intron",
    accession: "SO:0001574",
    impact: "HIGH",
    color: "#ff581a"
  },
  {
    term: "splice_donor_variant",
    description: "A splice variant that changes the 2 base region at the 5' end of an intron",
    accession: "SO:0001575",
    impact: "HIGH",
    color: "#ff581a"
  },
  {
    term: "stop_gained",
    description: "A sequence variant whereby at least one base of a codon is changed, resulting in a premature stop codon, leading to a shortened transcript",
    accession: "SO:0001587",
    impact: "HIGH",
    color: "#ff0000"
  },
  {
    term: "frameshift_variant",
    description: "A sequence variant that disrupts the translational reading frame because the number of inserted or deleted nucleotides is not a multiple of three",
    accession: "SO:0001589",
    impact: "HIGH",
    color: "#9400d3"
  },
  {
    term: "stop_lost",
    description: "A sequence variant where at least one base of the terminator codon is changed, resulting in an elongated transcript",
    accession: "SO:0001578",
    impact: "HIGH",
    color: "#ff0000"
  },
  {
    term: "start_lost",
    description: "A codon variant that changes at least one base of the canonical start codon",
    accession: "SO:0002012",
    impact: "HIGH",
    color: "#ffd700"
  },
  {
    term: "transcript_amplification",
    description: "A feature amplification of a region containing a transcript",
    accession: "SO:0001889",
    impact: "HIGH",
    color: "#ff69b4"
  },
  {
    term: "feature_elongation",
    description: "A sequence variant that extends a genomic feature with regard to the reference sequence",
    accession: "SO:0001907",
    impact: "HIGH",
    color: "#7f7f7f"
  },
  {
    term: "feature_truncation",
    description: "A sequence variant that reduces a genomic feature with regard to the reference sequence",
    accession: "SO:0001906",
    impact: "HIGH",
    color: "#7f7f7f"
  },
  {
    term: "inframe_insertion",
    description: "An in-frame non-synonymous variant that inserts bases into the coding sequence",
    accession: "SO:0001821",
    impact: "MODERATE",
    color: "#ff69b4"
  },
  {
    term: "inframe_deletion",
    description: "An in-frame non-synonymous variant that deletes bases from the coding sequence",
    accession: "SO:0001822",
    impact: "MODERATE",
    color: "#ff69b4"
  },
  {
    term: "missense_variant",
    description: "A sequence variant that changes one or more bases, resulting in a different amino acid sequence while preserving its length",
    accession: "SO:0001583",
    impact: "MODERATE",
    color: "#ffd700"
  },
  {
    term: "protein_altering_variant",
    description: "A sequence variant predicted to change the protein encoded by the coding sequence",
    accession: "SO:0001818",
    impact: "MODERATE",
    color: "#ff0080"
  },
  {
    term: "splice_donor_5th_base_variant",
    description: "A sequence variant that causes a change at the 5th base pair after the start of the intron in the orientation of the transcript",
    accession: "SO:0001787",
    impact: "LOW",
    color: "#ff7f50"
  },
  {
    term: "splice_region_variant",
    description: "A sequence variant within the splice-site region, either 1–3 bases from the exon or 3–8 bases into the intron",
    accession: "SO:0001630",
    impact: "LOW",
    color: "#ff7f50"
  },
  {
    term: "splice_donor_region_variant",
    description: "A sequence variant between the 3rd and 6th base after the splice junction at the 5' end of an intron",
    accession: "SO:0002170",
    impact: "LOW",
    color: "#ff7f50"
  },
  {
    term: "splice_polypyrimidine_tract_variant",
    description: "A sequence variant in the polypyrimidine tract at the 3' end of an intron, from acceptor −3 to acceptor −17",
    accession: "SO:0002169",
    impact: "LOW",
    color: "#ff7f50"
  },
  {
    term: "incomplete_terminal_codon_variant",
    description: "A sequence variant where at least one base of the final codon of an incompletely annotated transcript is changed",
    accession: "SO:0001626",
    impact: "LOW",
    color: "#ff00ff"
  },
  {
    term: "start_retained_variant",
    description: "A sequence variant where at least one base in the start codon is changed, but the start remains",
    accession: "SO:0002019",
    impact: "LOW",
    color: "#76ee00"
  },
  {
    term: "stop_retained_variant",
    description: "A sequence variant where at least one base in the terminator codon is changed, but the terminator remains",
    accession: "SO:0001567",
    impact: "LOW",
    color: "#76ee00"
  },
  {
    term: "synonymous_variant",
    description: "A sequence variant where there is no resulting change to the encoded amino acid",
    accession: "SO:0001819",
    impact: "LOW",
    color: "#76ee00"
  },
  {
    term: "coding_sequence_variant",
    description: "A sequence variant that changes the coding sequence",
    accession: "SO:0001580",
    impact: "MODIFIER",
    color: "#458b00"
  },
  {
    term: "mature_miRNA_variant",
    description: "A transcript variant located within the sequence of the mature miRNA",
    accession: "SO:0001620",
    impact: "MODIFIER",
    color: "#458b00"
  },
  {
    term: "5_prime_UTR_variant",
    description: "A UTR variant of the 5' UTR",
    accession: "SO:0001623",
    impact: "MODIFIER",
    color: "#7ac5cd"
  },
  {
    term: "3_prime_UTR_variant",
    description: "A UTR variant of the 3' UTR",
    accession: "SO:0001624",
    impact: "MODIFIER",
    color: "#7ac5cd"
  },
  {
    term: "non_coding_transcript_exon_variant",
    description: "A sequence variant that changes non-coding exon sequence in a non-coding transcript",
    accession: "SO:0001792",
    impact: "MODIFIER",
    color: "#32cd32"
  },
  {
    term: "intron_variant",
    description: "A transcript variant occurring within an intron",
    accession: "SO:0001627",
    impact: "MODIFIER",
    color: "#02599c"
  },
  {
    term: "NMD_transcript_variant",
    description: "A variant in a transcript that is the target of nonsense-mediated decay",
    accession: "SO:0001621",
    impact: "MODIFIER",
    color: "#ff4500"
  },
  {
    term: "non_coding_transcript_variant",
    description: "A transcript variant of a non-coding RNA gene",
    accession: "SO:0001619",
    impact: "MODIFIER",
    color: "#32cd32"
  },
  {
    term: "coding_transcript_variant",
    description: "A transcript variant of a protein-coding gene",
    accession: "SO:0001968",
    impact: "MODIFIER",
    color: "#458b00"
  },
  {
    term: "upstream_gene_variant",
    description: "A sequence variant located 5' of a gene",
    accession: "SO:0001631",
    impact: "MODIFIER",
    color: "#a2b5cd"
  },
  {
    term: "downstream_gene_variant",
    description: "A sequence variant located 3' of a gene",
    accession: "SO:0001632",
    impact: "MODIFIER",
    color: "#a2b5cd"
  },
  {
    term: "TFBS_ablation",
    description: "A feature ablation whereby the deleted region includes a transcription factor binding site",
    accession: "SO:0001895",
    impact: "MODIFIER",
    color: "#a52a2a"
  },
  {
    term: "TFBS_amplification",
    description: "A feature amplification of a region containing a transcription factor binding site",
    accession: "SO:0001892",
    impact: "MODIFIER",
    color: "#a52a2a"
  },
  {
    term: "TF_binding_site_variant",
    description: "A sequence variant located within a transcription factor binding site",
    accession: "SO:0001782",
    impact: "MODIFIER",
    color: "#a52a2a"
  },
  {
    term: "regulatory_region_ablation",
    description: "A feature ablation whereby the deleted region includes a regulatory region",
    accession: "SO:0001894",
    impact: "MODIFIER",
    color: "#a52a2a"
  },
  {
    term: "regulatory_region_amplification",
    description: "A feature amplification of a region containing a regulatory region",
    accession: "SO:0001891",
    impact: "MODIFIER",
    color: "#a52a2a"
  },
  {
    term: "regulatory_region_variant",
    description: "A sequence variant located within a regulatory region",
    accession: "SO:0001566",
    impact: "MODIFIER",
    color: "#a52a2a"
  },
  {
    term: "intergenic_variant",
    description: "A sequence variant located in the intergenic region, between genes",
    accession: "SO:0001628",
    impact: "MODIFIER",
    color: "#636363"
  },
  {
    term: "sequence_variant",
    description: "A non-exact copy of a sequence feature or genome exhibiting one or more sequence alterations",
    accession: "SO:0001060",
    impact: "MODIFIER",
    color: "#636363"
  }
]);

export function consequenceTableRows(choices) {
  const choiceByTerm = new Map();
  for (const choice of choices ?? []) {
    for (const term of choice.text.split(" / ")) {
      choiceByTerm.set(term, choice);
    }
  }

  return CONSEQUENCE_DEFINITIONS.map((definition, severityIndex) => {
    const choice = choiceByTerm.get(definition.term);
    return {
      ...definition,
      choice_id: choice?.choice_id ?? null,
      choice_label: choice?.text ?? "Not included in this benchmark",
      severity_order: severityIndex + 1
    };
  });
}

export function sequenceOntologyUrl(accession) {
  if (!/^SO:\d{7}$/.test(accession)) {
    throw new Error(`invalid Sequence Ontology accession: ${accession}`);
  }
  return `${SEQUENCE_ONTOLOGY_TERM_BASE}${accession}`;
}
