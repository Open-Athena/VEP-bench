# Public assay evidence and cutoff comparisons

The September 16, 2026 audit corrected the explorer's dates and the introduction's
SGE cutoff comparison. Recent MaveDB records included revisions and expansions
of older public datasets. Treating their publication dates as first availability
produced an incorrect 9-before/7-after split. Using the same frozen predictions,
the reviewed split is 13 before, 2 after and 1 mixed for all five reported model
cutoffs. The corrected blog explicitly records this change.

## What a date establishes

The [reviewed metadata](../projects/explorer/config/assay-publications.yaml)
records the earliest **verified evidence** of a scored assay covering the
selected panel. It is an upper bound on first availability, not a demonstrated
first indexing date. Search-engine crawl dates, manuscript submission dates,
private file creation dates and mutable Git commit dates cannot establish public
availability. Trace papers and preprints, data availability statements, public
archives and repository predecessor chains; do not stop at links in the current
MaveDB record. Match the experiment, cell system, measurement and selected region,
not just the gene name.

Earlier experimental scores count as prior assay evidence even if later revised.
This policy asks whether relevant experimental information was already public,
not whether exact benchmark answers appeared verbatim. Historical preprints are
study-level evidence; the audit does not claim score identity for those studies.
The direct file comparisons below establish identity or revision separately.
Raw sequencing deposits and conference abstracts are recorded as leads, but do
not substitute for verified scored-assay evidence.

When only part of a panel has earlier evidence, retain both the earlier date and
the date of full-panel evidence. If they span the model cutoff, label the panel
**Mixed availability** and exclude it from both date-group means and tests.
Exact cutoff days belong to the earlier group. A date in an unspecified cutoff
month remains unknown. An unknown date cannot establish a wholly later panel.
The internal `assay_first_indexed` key is retained for compatibility; the display
label is **Public assay evidence**.

These rules do not identify model training examples. A later evidence date is
not evidence that a model lacked the data. The two remaining later SGE panels
also differ biologically from the earlier genes, so a performance gap cannot
isolate training exposure. The very small later group limits the comparison.

## Direct score-release audit

The [source manifest](../projects/blog-analysis/config/assay-provenance-sources.json)
pins public metadata and score-file bytes. The
[generated audit](../projects/explorer/web/blog/introducing-vep-bench/assay-provenance-audit.json)
compares every selected variant against each reviewed release, retaining missing
identities, original scores and pinned scores. Counts below refer to our selected
50 variants per panel, not the entire source experiment.

| Panel | Verified release and selected-variant coverage |
| --- | --- |
| BAP1 | [Zenodo release, 2024-01-11](https://doi.org/10.5281/zenodo.10489733): the linked v1.0.0 workbook has all 50 scores unchanged. |
| BARD1 | [MaveDB 00001250-a-1, 2025-10-21](https://api.mavedb.org/api/v1/score-sets/urn%3Amavedb%3A00001250-a-1): all 50 unchanged. [IGVF IGVFFI2616FRGT](https://data.igvf.org/tabular-files/IGVFFI2616FRGT/) repeats them on 2025-11-18. |
| CTCF | [IGVFFI7008EHEH, 2025-06-24](https://data.igvf.org/tabular-files/IGVFFI7008EHEH/): 31 variants, all scores revised. [IGVFFI6548CGAB, 2025-11-18](https://data.igvf.org/tabular-files/IGVFFI6548CGAB/): all 50 unchanged. [MaveDB 00001262-a-1, 2026-01-10](https://api.mavedb.org/api/v1/score-sets/urn%3Amavedb%3A00001262-a-1) also has all 50 unchanged. |
| PALB2 | [IGVFFI9974PZRX, 2025-06-24](https://data.igvf.org/tabular-files/IGVFFI9974PZRX/): 24 variants with revised scores. [IGVFFI5011VHRR, 2025-11-18](https://data.igvf.org/tabular-files/IGVFFI5011VHRR/): all 50 unchanged, as in [MaveDB 00001259-a-1, 2026-01-10](https://api.mavedb.org/api/v1/score-sets/urn%3Amavedb%3A00001259-a-1). |
| RAD51D | [IGVFFI9138TFXQ, 2025-06-24](https://data.igvf.org/tabular-files/IGVFFI9138TFXQ/): 30 variants. [IGVFFI2272LOUM, 2025-11-18](https://data.igvf.org/tabular-files/IGVFFI2272LOUM/) and [MaveDB 00001260-a-1, 2026-01-10](https://api.mavedb.org/api/v1/score-sets/urn%3Amavedb%3A00001260-a-1): all 50 variants. Every earlier score differs from the pinned score. |
| SFPQ | [IGVFFI3125FMNW, 2025-06-23](https://data.igvf.org/tabular-files/IGVFFI3125FMNW/): V01–V03 only, with revised scores. [IGVFFI1833BDXW, 2025-11-18](https://data.igvf.org/tabular-files/IGVFFI1833BDXW/) and [MaveDB 00001265-a-1, 2026-01-13](https://api.mavedb.org/api/v1/score-sets/urn%3Amavedb%3A00001265-a-1): the same three with unchanged scores. The pinned a-2 release on 2026-06-08 covers all 50. |
| XRCC2 | [IGVFFI1361XVSO, 2025-06-23](https://data.igvf.org/tabular-files/IGVFFI1361XVSO/): no selected variants. [IGVFFI1536JYYY, 2025-11-18](https://data.igvf.org/tabular-files/IGVFFI1536JYYY/): 46 variants, 43 unchanged scores. [MaveDB 00001264-a-1, 2026-01-10](https://api.mavedb.org/api/v1/score-sets/urn%3Amavedb%3A00001264-a-1): all 50 unchanged. |

For IGVF, the date comes from the individual file's `release_timestamp`, not the
analysis set's release or the file's creation. The June 2025 analysis sets had
additional files released in November. Their replacement sets were released in
July 2026; the newer SFPQ and RAD51D files were also first released in July.

The tabular comparison uses GRCh38 alleles, removing redundant padding and
recognizing unanchored deletions. It does not perform a genome-wide liftover or
repeat left-alignment. The BAP1 workbook is matched using identical, versioned
transcript HGVS identifiers, which resolves alternate genomic representations
of deletions in repeats. No score normalization is applied. Exact equality and
a separate absolute tolerance of 1e-6 are reported; duplicate matches fail.

The previous PALB2 citation, [Boonen et al.](https://www.nature.com/articles/s41467-025-67252-z),
describes site-saturation screens and an SGE validation restricted to exon 10.
It is not the source of this University of Washington full-panel SGE dataset.
The earlier IGVF file matches every selected score and supplies direct provenance.

## Papers, preprints and remaining release dates

First-version preprint dates were checked against the publishers' public API
(`api.biorxiv.org/details/{server}/{doi}/na/json`) and the corresponding study.
A DOI's embedded date can differ from its posted date.

| Assay | Corrected evidence | Previously reported |
| --- | --- | --- |
| BRCA1 | [Preprint, 2018-04-05](https://www.biorxiv.org/content/10.1101/294520v1) | 2018-09-12 |
| BRCA2 | [Huang/Couch HAP1 preprint, 2023-12-15](https://www.biorxiv.org/content/10.1101/2023.12.14.571597v1) | 2025-01-08 |
| DDX3X | [Preprint, 2022-06-14](https://www.medrxiv.org/content/10.1101/2022.06.10.22276179v1) | 2023-12-06 |
| TP53 | [Preprint, 2023-03-10](https://www.biorxiv.org/content/10.1101/2023.03.10.531074v1) | 2025-01-07 |
| VHL | [Preprint, 2023-06-10](https://www.biorxiv.org/content/10.1101/2023.06.10.542698v1) | 2024-04-25 |
| satMutMPRA | [Preprint, 2018-12-23](https://www.biorxiv.org/content/10.1101/505362v1) | 2019-08-08 |
| OpenSplice | [Preprint, 2026-05-23](https://www.biorxiv.org/content/10.64898/2026.05.22.727141v1) | Figshare initial release, 2026-05-24 |

The [BARD1 preprint](https://pmc.ncbi.nlm.nih.gov/articles/PMC12637766/) was posted
2025-11-06, after its first MaveDB release. The February 2026 consortium preprint,
[A scalable approach to resolving variants of uncertain significance](https://pmc.ncbi.nlm.nih.gov/articles/PMC12918978/),
reports the six UW genes above and links the earlier repositories. The audit
compared repository files directly. It did not fully compare that paper's large
supplement archive, so it does not establish absence of additional earlier
SFPQ measurements. Mixed availability prevents treating that panel as wholly
later on the basis of incomplete earlier coverage.

The dates for [CARD11](https://pubmed.ncbi.nlm.nih.gov/33202260/) (2020-11-16) and
[RAD51C](https://pubmed.ncbi.nlm.nih.gov/39299233/) (2024-09-18) remain the verified
scored-study dates. CARD11's [GEO series](https://www.ncbi.nlm.nih.gov/geo/query/acc.cgi?acc=GSE152129)
was public on 2020-07-01, but the earlier sequencing deposit was not established
as an earlier release of the growth-score table. The MPRA GEO series was public
on 2019-02-15, after its preprint. OSF creation in November 2018 does not establish
when that project or its score files became public.

No earlier scored source was verified for
[SBDS](https://api.mavedb.org/api/v1/score-sets/urn%3Amavedb%3A00001284-a-1)
(2026-08-24) or
[TINF2](https://api.mavedb.org/api/v1/score-sets/urn%3Amavedb%3A00001286-a-1)
(2026-08-28) through predecessor metadata, study searches and IGVF records.
The [SBDS IGVF file](https://api.data.igvf.org/tabular-files/IGVFFI9790OCYB/?format=json)
was created in March 2026 but released publicly on August 26. Neither an early
private upload nor an undated laboratory project page establishes prior public
scores. These are bounded audit findings, not an exhaustive exclusion of public
sources, nonidentical assays or model training material.

## Reproducing the correction

Use the shared-node resource guard when running analyses locally. The score
audit streams source files and needs well below 500 MiB; upstream files belong
in a disposable cache. `--download` retrieves only the files in the reviewed
manifest and rejects changed bytes; omitting it replays cached files offline.

```bash
uv run --locked python -m vepbench_blog_analysis.assay_provenance \
  --manifest projects/blog-analysis/config/assay-provenance-sources.json \
  --panels data/sources/sge-mavedb-2026-09-03.jsonl \
  --cache /tmp/assay-provenance-cache --download \
  --output /tmp/assay-provenance-audit.json
```

To change date-group membership, recollect from the publication root verified by
the frozen manifest. `--input` only replays statistics from already grouped
scores; it does not apply newer metadata.

```bash
uv run --locked vepbench-blog-sge-cutoff \
  --publication /path/to/publication-root \
  --manifest projects/explorer/web/blog/introducing-vep-bench/specialist-2026-09-15.manifest.json \
  --output projects/explorer/web/blog/introducing-vep-bench/cutoff-analysis.json
```

The saved analysis records hashes of both the reviewed date metadata and the
variant audit. Offline snapshot tests reject stale combinations, alongside the
existing frozen outcome checks. Public questions, source score pins and model
results remain unchanged by this display and reporting correction.
