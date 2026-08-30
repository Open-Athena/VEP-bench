# Example VEPBench prompt

This is the complete first prompt from `benchmark/questions.jsonl`, shown exactly as the model receives it. The answer is intentionally not included.

Predict the Ensembl VEP most severe consequence for the SNV using only the local sequence context below.

**Reference genome:** human GRCh38
**VEP version:** release 109.1
**VEP flags:** `--most_severe --distance 1000`

```fasta
>window
CTTGAAACTAGCAGACAGATCTCAGAAGTGAATCTGTGATCTGGAATGTTAAGTGCTTTACCTACAGGTCCTAGTTGAAT
TTGCCCAATGATCAATCAGGTACAATTCTCATTATTACACAGCTAGTAATTGGCAGAGCAGGAGTTTGAATCCAGGTCTG
TCTCATTCCAAAGTTCACTTCCTTTCACGGTCTTTTGGTGCTCTTTCATGTGTCAGTTAAGTTTAAAGTGCAGCTATTTT
TCATAGACCCTTTCCATAAGTGACTACAGGCATGCTATATGACTGCAGCAACAAGCTTGCTTGTTGGAAACTTTTTTGGT
ACAATTTTTCTGCTAACTCACATCAAAGATCTCAAAGCCAGCAATGTCCAAGACCCCAATGAAGTACTGCCTGGGCTGCT
TGGTGTCCAGCTGCTGGTTGATGCGGGTGACCATCCACAAGAACATCTTATCGTAGACAGCTTTGGCCAGAGCACCCACT
GCATTGTACACCTTCACAGATAAAGTTTGTTGGTGTTATTAAAGACATGTCATGAAGGCCTGGGATGTGTGTGATTCATT
GAGGTCATGCACTTACCTGCTGCACAGTTTGACCTTTGGTGACATACTCATTGCCGACCTTGACCCTAGGGTAGCAGAGG
GCTTTGAGCAGATCTGCAGAGTTCAGATTTTGGAGATAGGCTGCCTTGTCAGCAACTGCAGAAACATAATTCAGATACCT
AATATGACTTACTCTGGGACCCACAGTTCAAAGGGGCATAGTATTGAGTAAATAACTTTGTAGAAAGTCTGTGTGTGGGT
ACATCTGAGTATGTCCATCAGGAGCTCAGCTGTAAAGAAGACTTGAATTTTGGAATGGACATTTTTGCCTATATTCTCTC
ATTAAACCCAGATGGAGATTCATTTGGTACCTTCAGTGCCATCTGGCTCAGCTTGCTCCTCACGCTGCTTTTGCTTGAAT
TTCATGTTCCCATAATGCATCACAGCCCCTGTGAGCTTATA
```

```vcf
##fileformat=VCFv4.3
##contig=<ID=window,length=1001>
#CHROM	POS	ID	REF	ALT	QUAL	FILTER	INFO
window	501	.	T	C	.	PASS	.
```

What is the Ensembl VEP most severe consequence for this SNV?

Choices:
C01. 3_prime_UTR_variant
C02. 5_prime_UTR_variant
C03. coding_sequence_variant
C04. incomplete_terminal_codon_variant
C05. intergenic_variant / intron_variant / upstream_gene_variant / downstream_gene_variant
C06. mature_miRNA_variant
C07. missense_variant
C08. non_coding_transcript_exon_variant
C09. splice_acceptor_variant
C10. splice_donor_5th_base_variant
C11. splice_donor_region_variant
C12. splice_donor_variant
C13. splice_polypyrimidine_tract_variant
C14. splice_region_variant
C15. start_lost
C16. stop_gained
C17. stop_lost
C18. stop_retained_variant
C19. synonymous_variant

You may explain your reasoning.

Your final line must contain only the word `FINAL`, a colon, a space, and the choice ID.
Example: `FINAL: C07`
Do not include the consequence name, a period, or any other text on that line.
