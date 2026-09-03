# satMutMPRA regulatory-effect ranking

This task asks a model to predict the signed reporter-activity effect of 50
variants from one saturation-mutagenesis MPRA, then evaluates the ranking and
relative numerical spacing of those predictions within the regulatory element.

- **Task family:** `satmut_mpra`
- **Question schema:** 2.0
- **Prompt version:** 1.0
- **Size:** 16 questions, one per regulatory element and 50 candidates per question
- **Primary metric:** mean within-element Spearman rank correlation
- **Secondary metrics:** mean within-element Pearson correlation and valid-output rate

## Model-visible input and output

Every prompt contains the regulatory-element name and class, assay cell line,
experimental context, the complete uppercase reference sequence in its physical
reporter-construct orientation, and a compact VCF table with `CHROM`, `POS`,
opaque candidate ID, `REF`, and `ALT`. FASTA sequence lines contain 80 bases
except the final line.

Every VCF record uses the synthetic contig `element`, with 1-based
positions relative to the first base of the displayed sequence. Genomic
chromosomes and positions are not model-visible. The prompt does not expose the
measured effect, p-value, barcode count, source `FILTER`, quantile bin, MaveDB
accession, or selection metadata.

The model predicts one finite signed log2-scale activity effect per candidate.
Positive values mean increased reporter activity and negative values mean
decreased activity. Its final line must be a strict JSON object:

```text
FINAL: {"V01": -0.42, "V02": 0.08, ...}
```

All 50 IDs must appear exactly once with no additional keys. The versioned
prompt is [`templates/satmut_mpra.json`](../../templates/satmut_mpra.json), and
the task completion ceiling is in
[`configs/tasks/satmut-mpra.yaml`](../../configs/tasks/satmut-mpra.yaml).

## Canonical sources and crosswalk

The source is the canonical 16-file RegSeq validation collection distributed
with [CADD v1.7](https://kircherlab.bihealth.org/download/CADD-development/v1.7/validation/regseq/).
Preparation pins the upstream `MD5SUMs` object and verifies the MD5 and SHA-256
of every downloaded VCF. Across the 16 files there are 23,516 rows:

| CADD `FILTER` | Rows | Use |
| --- | ---: | --- |
| `SIGN` | 4,332 | eligible sampling pool |
| `MIN` | 17,685 | provenance and crosswalk validation only |
| `QUAL` | 1,499 | provenance and crosswalk validation only |

Every CADD row is independently cross-checked against a pinned
[MaveDB](https://www.mavedb.org/) score set using source-study coordinates and
alleles, effect after CADD's decimal rounding, p-value, and barcode count. The
crosswalk is:

| CADD label | MaveDB score set | Model-visible element |
| --- | --- | --- |
| F9 | `urn:mavedb:00000015-a-1` | F9 promoter |
| GP1BA | `urn:mavedb:00000017-a-1` | GP1BB promoter |
| HBB | `urn:mavedb:00000018-a-1` | HBB promoter |
| HBG1 | `urn:mavedb:00000019-a-1` | HBG1 promoter |
| HNF4A | `urn:mavedb:00000020-a-1` | HNF4A promoter |
| IRF4 | `urn:mavedb:00000021-a-1` | IRF4 enhancer |
| IRF6 | `urn:mavedb:00000022-a-1` | IRF6 enhancer |
| LDLR | `urn:mavedb:00000023-a-2` | LDLR promoter, replicate 2 |
| MSMB | `urn:mavedb:00000024-a-1` | MSMB promoter |
| MYCrs6983267 | `urn:mavedb:00000025-a-1` | MYC rs6983267 enhancer |
| PKLR | `urn:mavedb:00000027-b-1` | PKLR promoter, 48 h |
| SORT1 | `urn:mavedb:00000029-a-1` | SORT1 enhancer, replicate 1 |
| TCF7L2 | `urn:mavedb:00000030-a-1` | TCF7L2 enhancer |
| TERT | `urn:mavedb:00000031-a-1` | TERT promoter, HEK293T |
| ZFAND3 | `urn:mavedb:00000033-a-1` | ZFAND3 enhancer |
| ZRSh13 | `urn:mavedb:00000034-a-1` | ZRS enhancer, Hoxd13 |

`GP1BA` is the historical CADD filename; the matched MaveDB target and prompt
correctly call the assayed sequence the GP1BB promoter. The exact mapping,
upstream digests, retrieval date, MaveDB modification dates, and validation
counts are retained in the
[`source manifest`](../../data/sources/satmut-mpra-cadd-v1.7.manifest.json).

## Reference validation and one excluded discrepancy

The model-visible sequence is the pinned MaveDB target sequence, representing
the physical reporter-construct orientation. The Methods and Supplementary
Table 18 of the
[source study](https://doi.org/10.1038/s41467-019-11526-w) document the
restriction-site overhangs used for directional cloning. The study treats the
reverse-orientation SORT1 library as a separate experiment, which this task
excludes.
These sequences and eligible alleles are validated against the GRCh38 primary
assembly from
[`marin-dna/human-genome`](https://huggingface.co/datasets/marin-dna/human-genome)
at revision `11b9433582981bb929af333bc6422f10a8fd71b4`. GRCh38 is used only for
validation and genomic provenance, not as the display-orientation policy.
`configs/sources/satmut-mpra-v1.json` pins every
CADD file, the CADD checksum manifest, the reference file size and LFS SHA-256,
and both MaveDB payloads for every score set. Preparation verifies the
reference's Hub commit, linked digest, and size with a metadata request and
aborts if any downloaded upstream payload differs.
The manifest records the canonical snapshot retrieval date and verified size
and digest of every input.

The ZRS reporter-construct sequence has `A` at its terminal position
7:156791604, whereas the pinned GRCh38 primary assembly has `T`. The prompt
retains the construct `A`. The CADD file consequently contains
one deletion, `7:156791603:CA:C`, whose REF also disagrees with GRCh38 (`CT`).
That row has `FILTER=QUAL`, is ineligible by construction, and is never sampled.
Preparation allows only this exact, source-pinned exception and records both
the sequence and allele discrepancy in the manifest. Any eligible REF mismatch,
or any other mismatch, aborts preparation.

## Deterministic panel construction

Each element's eligible `FILTER=SIGN` variants are sorted by measured effect,
then divided into ten equal-population rank bins. When a count is not divisible
by ten, the first remainder bins receive one additional row. Five variants per
bin are selected by SHA-256 ordering using seed `2026090200`; the resulting 50
variants are sorted by genomic `CHROM`, `POS`, `REF`, and `ALT`, then assigned
opaque IDs `V01` through `V50`. Genomic coordinates and alleles also break
effect ties before binning; only element-relative coordinates are rendered in
the generated question. The generic ranking builder sorts the visible VCF by
`CHROM`, `POS`, `REF`, and `ALT`.

This design guarantees five candidates from each effect quantile while keeping
the prompt free of measured effects and bin labels. Sampling algorithm version
`sha256_rank_quantile_v1`, its parameters, and each selected
variant's private bin are recorded in the compact source and manifest.

## Parsing and scoring

The evaluator uses the last well-formed `FINAL: {JSON object}` line. It rejects
duplicate, missing, or extra candidate IDs; booleans, strings, nulls, or other
nonnumeric values; and non-finite numbers. A malformed later line is ignored
when an earlier well-formed final object exists. A syntactically valid object
with invalid content is a format failure.

For valid output, Spearman uses average ranks for ties and Pearson uses the raw
predicted and measured effects. If either input vector is constant, the
corresponding correlation is defined as zero. Invalid completed output receives
zero for both correlations and is included in the 16-question mean; zero
represents no usable ranking signal without equating malformed output to a
valid, perfectly reversed ranking. Invalid output also lowers valid-output
rate. API failures remain null and make the run incomplete. The task score is
the arithmetic mean of the 16 per-element Spearman values, with mean Pearson
and valid-output rate reported alongside it.

## Artifacts, cache, and regeneration

The committed
[`source JSONL`](../../data/sources/satmut-mpra-cadd-v1.7.jsonl) contains the 16
selected panels plus private audit fields. The manifest records the full
23,516-row population checks, target-sequence digests, deterministic selection
configuration, and the compact source digest. The reusable processed cache
contains all 4,332 eligible rows and element metadata, not the redistributable
upstream VCF or MaveDB payloads. It is content-addressed under:

```text
hf://buckets/open-athena/VEP-bench/data_prep/satmut-mpra/v1/<cache-key>/
```

Data files are uploaded first and the digest-bearing `manifest.json` completion
marker last. Existing complete or incomplete prefixes are never overwritten.
Retrieval timestamps are excluded from the cache key. A complete matching cache
is downloaded, digest-checked, and reconstructed before any CADD or MaveDB
payload is requested. The cache is separate from official `versions/`
publication artifacts, so a later panel size or sampling policy can reuse the
validated eligible pool.

To prepare on a SkyPilot worker, upload the cache, copy back the compact
artifacts, validate them, and build questions:

```bash
bash scripts/run_prepare_satmut_mpra_sky.sh
```

For the small canonical source, preparation can also run locally. It uploads
the cache by default; use `--skip-cache-upload` for a read-only dry run:

```bash
uv run --locked --group prepare python scripts/prepare_satmut_mpra.py
uv run --locked python scripts/validate_satmut_mpra_artifacts.py
uv run --locked vepbench build \
  --source data/sources/satmut-mpra-cadd-v1.7.jsonl \
  --template templates/satmut_mpra.json \
  --output /tmp/satmut-mpra-questions.jsonl
cmp benchmark/satmut-mpra-expected-manifest.json \
  /tmp/satmut-mpra-questions.manifest.json
```

## Interpretation and limitations

Spearman measures within-panel ordering, not absolute calibration, and Pearson
is sensitive to numerical scale and outliers. Five samples per effect quantile
produce broad effect coverage but do not reproduce the natural frequency of
effects in the complete assay. A 50-candidate panel is a sampled view of each
element, not an exhaustive score of all variants.

Reporter assays measure activity in specific constructs, cell lines, and
experimental conditions. Their effects need not transfer directly to native
chromatin, another tissue, or clinical phenotype. The questions and answers
are public development data and may be present in model training corpora.
