# Task sampling analysis: 2026-09-04

This is the preliminary analysis for [issue #62](https://github.com/Open-Athena/VEP-bench/issues/62).
Production task generators and their committed source panels are unchanged.
The user has authorized SkyPilot compute as needed and approved `SIGN + MIN`
eligibility for MPRA, retaining the exclusion of `QUAL` rows.

## Completed MPRA comparison

All 16 CADD v1.7 RegSeq VCFs were read from primary-source files and verified
against the byte sizes and SHA-256 hashes in
`tasks/satmut-mpra/config/source-pins.yaml`. The existing VCF parser checked
the records. The complete pinned crosswalk/reference verification from the
original preparation is recorded in the source manifest; this analysis did
not rerun that crosswalk or download the genome. The comparison uses the
full source populations, not the previously selected panels.

The pinned VCF headers define `MIN` as at least ten supporting barcodes and
`SIGN` as that minimum support plus the significance threshold. The `QUAL`
header describes low support and nonsignificance. `SIGN + MIN` supplies
22,017 measurements, compared with 4,332 under `SIGN` alone. The remaining
1,499 `QUAL` rows are excluded in both comparisons. These are experimental
QC categories, not variant-consequence classes.

| Eligibility | Elements with at least ten variants in all five min-max bins | Elements with at least ten variants in all five P01-P99 bins |
| --- | ---: | ---: |
| SIGN | 3 / 16 | 8 / 16 |
| SIGN + MIN | 3 / 16 | 16 / 16 |

The least populated robust bin in each expanded element contains between
25 and 67 variants. Thus five bins with ten distinct candidates per bin is
feasible for every MPRA element under the approved eligibility policy.
The historical CADD label `GP1BA` in the outputs refers to the GP1BB promoter,
as documented in the task methodology.

![Minimum bin capacity](task-sampling-2026-09-04/mpra-bin-capacity.png)

![Full score distributions](task-sampling-2026-09-04/mpra-score-distributions.png)

The histogram count axis is logarithmic so sparse tails remain visible.
The score axis contains the original signed log2 effects. The blue lines
are the four proposed internal cutpoints. Tail measurements remain eligible.
Per-population counts and allocations are in
[occupancy.tsv](task-sampling-2026-09-04/occupancy.tsv).
The [machine-readable summary](task-sampling-2026-09-04/summary.json) includes
input pins, implementation digests, cutpoints, allocations, score coverage,
and selected/eligible variant-type composition.

This supports robust endpoints for MPRA. It does not yet establish a common
policy for SGE and OpenSplice, nor does it establish the reliability of each
individual extreme measurement. Broader effect separation changes the
evaluation population; new panel correlations should not be compared as if
they used the historical questions.

## Reproducible method

`scripts/analyze_task_sampling.py` uses the prototype in
`src/vepbench/sampling.py`. The prototype is not yet called by production
generators. It compares minimum/maximum anchors with type-7 P01/P99 anchors,
placing cutpoints at `L + k*(U-L)/5` for k = 1, 2, 3, 4. Scores equal to a
cutpoint go to the bin on its right; values beyond the anchors remain in
the outer bins. Equal scores are never separated to fill quotas.

Allocation is equal subject to capacity, with remainder ties broken by
ascending bin index. Underfilled bins contribute every available allele;
the remaining slots are allocated across bins that still have capacity.
Collapsed anchors and insufficient populations are reported explicitly,
without switching to another policy. Hash selection uses seed `2026090400`,
question identity, zero-based bin index and stable allele identity. The
comparison baseline uses ten equal-population bins and five candidates per
bin on the same eligible population.

The local MPRA audit's estimated peak working set was 150 MiB; observed peak
RSS was 40,456 KiB and elapsed time was 0.57 s. Plotting used 126,316 KiB peak
RSS and 3.68 s. Both ran under the shared-node nonblocking heavy-work lock,
with the required scheduling priority, thread limits, and memory/load checks.
No cloud worker or model API was used for these MPRA results.

Install the locked workspace and optional plotting group:

```bash
uv sync --locked --all-packages --all-extras --group test --group quality --group analysis
```

Run the following on the analysis worker. Existing files in the input folder
are digest-checked; `--download` fetches missing primary inputs. For MPRA,
place files under `inputs/mpra/SatMut.all.<label>.vcf.gz` to reuse downloads.

```bash
uv run --locked python scripts/analyze_task_sampling.py \
  --inputs inputs --output analysis --tasks satmut-mpra --download
uv run --locked --group analysis python scripts/render_sampling_audit.py \
  --analysis analysis --output figures
```

`sky/analyze_task_sampling.yaml` prepares a worker for all three datasets.
Launch with a named cluster and autostop, then retrieve results before teardown:

```bash
sky launch --dryrun sky/analyze_task_sampling.yaml
sky launch -c vepbench-issue62-audit sky/analyze_task_sampling.yaml \
  --detach-run --idle-minutes-to-autostop 30
sky logs vepbench-issue62-audit
scp -r vepbench-issue62-audit:~/issue62-analysis ./analysis
sky down --yes vepbench-issue62-audit
```

The worker keeps pinned inputs outside `sky_workdir` for iteration with
`sky exec`. It does not upload caches or publish official versions. Respect
the shared-node resource rules if running any of the commands locally.

## Remaining work

SGE and OpenSplice adapters and the SkyPilot configuration are prototypes;
they have not yet completed a full-data run. The expanded SGE mapper has
offline reconstruction tests for deletions, insertions, replacements, and
duplications on both transcript orientations, including rejection of source
REF repair. Full-data execution must still validate source conventions,
exclude/report mapping limitations, and compare window selection.

The OpenSplice audit must verify deletion fields and complete mutant
reconstruction against the pinned master, assess all exon populations, and
report window reselection separately from sampling changes. CARD11's catalog
exclusion still needs source review. Neither the new public task names nor
production cache/prompt/version migrations have been decided.

At the initial run, `sky api info` reported no connected API server. The
question about an existing endpoint versus starting a local API server is
pending. This is a setup dependency, not a lack of authorization for compute.
After the remaining data checks, record the endpoint and fallback policy
decisions in issue #62 and implement/regenerate the production tasks.
