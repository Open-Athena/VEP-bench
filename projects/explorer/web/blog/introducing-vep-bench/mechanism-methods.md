---
title: "MSH6 and LDLR explanation case studies: reference and protocol"
---

# MSH6 and LDLR explanation case studies

This supplementary experiment asks GPT-6 Astra at max reasoning effort to justify
its predictions for one deliberately selected panel: OpenSplice MSH6 exon 7
(`E01`, `ENSE00003677325`). All 50 benchmark alleles are retained. Selection was
based on mechanistic interpretability after reading the paper and measurements,
before inspecting this panel's model explanations. It is not a representative
performance sample or a case selected for model failure.
On September 17, the user also requested the older LDLR promoter example below,
retaining its full 50-allele panel before model-trace inspection.

## Complete prompts and responses

The exact final responses, including their original Markdown and all 50 final
predictions, are committed with this article. Expand either response to read it
here. The downloads also include the exact prompts, baseline answers, and data
needed to recompute both correlations. The separate provider-exposed summaries
are partial records, not complete or verified accounts of internal reasoning.

```js
import MarkdownIt from "npm:markdown-it@14.1.0";
const mechanismCases = [
  {name: "MSH6", prompt: FileAttachment("./mechanism-evidence/msh6-prompt.txt"),
    response: FileAttachment("./mechanism-evidence/msh6-response.txt"),
    summary: FileAttachment("./mechanism-evidence/msh6-reasoning-summary.txt"),
    baseline: FileAttachment("./mechanism-evidence/msh6-baseline-response.txt"),
    comparison: FileAttachment("./mechanism-evidence/msh6-comparison.json")},
  {name: "LDLR", prompt: FileAttachment("./mechanism-evidence/ldlr-prompt.txt"),
    response: FileAttachment("./mechanism-evidence/ldlr-response.txt"),
    summary: FileAttachment("./mechanism-evidence/ldlr-reasoning-summary.txt"),
    baseline: FileAttachment("./mechanism-evidence/ldlr-baseline-response.txt"),
    comparison: FileAttachment("./mechanism-evidence/ldlr-comparison.json")}
];
for (const example of mechanismCases) {
  display(html`<p><strong>${example.name}:</strong>
    <a href=${await example.prompt.url()} download>Prompt</a> ·
    <a href=${await example.response.url()} data-mechanism-response=${example.name} download>Complete response</a> ·
    <a href=${await example.baseline.url()} download>Baseline response</a> ·
    <a href=${await example.summary.url()} download>Provider-exposed summary</a> ·
    <a href=${await example.comparison.url()} download>Comparison data</a></p>`);
  const responseDetails = document.createElement("details");
  const responseSummary = document.createElement("summary");
  responseSummary.textContent = `Read the complete ${example.name} explanation response`;
  const responseCard = document.createElement("div");
  responseCard.className = "card vepbench-record-content";
  responseCard.setAttribute("aria-label", `Complete ${example.name} explanation response`);
  responseCard.innerHTML = new MarkdownIt({html: false}).render(await example.response.text());
  responseDetails.append(responseSummary, responseCard);
  display(responseDetails);
}
display(html`<p><a href=${await FileAttachment("./mechanism-evidence/manifest.json").url()} download>Download the evidence manifest</a>
  with file sizes and SHA-256 hashes.</p>`);
```

## Reference interpretation, fixed before model explanations

The [OpenSplice preprint](https://www.biorxiv.org/content/10.64898/2026.05.22.727141v1)
shows the mutational landscape in **Fig. 2f** and the regulatory map in
**Fig. 6a, group 6**. The Results paragraph beginning “Similar diversity is also
observed among highly included exons” interprets MSH6 exon 7 specifically.
The authors describe a 90-nt exon with roughly 97% wild-type inclusion and a
sparse regulatory architecture: 78.4% neutral and 21.6% enhancer-state sequence
across the 185-nt native insert. They locate an upstream enhancer-state segment
at insert positions 47–50, a 21-nt acceptor-region segment, and a 15-nt segment
across the exon end and donor. The reported states summarize substitution and
short-deletion effects; they do not identify bound proteins. The 21-nt deletions
below were not used to construct those maps. No direct branchpoint or
RNA-binding-protein validation for MSH6's upstream segment was identified in
the retrieved main text. Calling that segment a proven branchpoint would exceed
the available evidence.

The assay uses a FAS three-exon minigene in HEK293T cells. The supplied sequence
is in construct orientation. The 185-nt native insert occupies cassette bases
147–331; the tested exon is 217–306. Add 146 to native-insert coordinates to
obtain cassette coordinates. Thus the upstream 47–50 segment maps to 193–196,
the acceptor AG to 215–216, and the donor GT to 307–308. Junction recognition
is a sequence-grounded interpretation; exact effects and the regulatory-state
map require measurements.

| Allele | Sequence feature in the supplied cassette | Measured ΔPSI (percentage points) | Reference interpretation |
| --- | --- | ---: | --- |
| V19, G216A | Terminal G of acceptor AG | −87.466 | Large inclusion loss is consistent with disruption of the canonical acceptor. |
| V44, G307C | First G of donor GT | −97.003 | Near-total loss is consistent with disruption of the canonical donor. |
| V41, G306A | Final exonic base immediately before donor | −26.389 | A weaker junction effect than V44; an identical near-null prediction would miss the measured gradation. |
| V24, deletion 241–261 | Exon bases 25–45 | +0.297 | Substantial interior deletion is tolerated in this reporter. |
| V28, deletion 261–281 | Exon bases 45–65 | +0.488 | Substantial interior deletion is tolerated in this reporter. |
| V30, deletion 276–296 | Exon bases 60–80 | +0.415 | Substantial interior deletion is tolerated in this reporter. |

Deletion intervals describe removed bases, excluding each normalized VCF anchor.
Tiny positive measurements are described as tolerance, not evidence of a
specific inclusion-enhancing mechanism. These illustrations were fixed before
model inspection; the scored experiment still contains all 50 alleles.
Measurements come from the pinned benchmark
[source panel](https://github.com/Open-Athena/VEP-bench/blob/71df7e3e780cb50bff1292ea029f37a21280425f/data/sources/opensplice-snv-figshare-v5.jsonl).

The model can locate the junctions, read alleles and deletion lengths, and use
the supplied cell and reporter context. It is not given the gene name, wild-type
PSI, measured effects, paper, map labels, or these illustrative candidates.
Specific protein binding, branchpoint usage, altered RNA structure, and cryptic
splice-product identities would need additional evidence; motif plausibility
alone does not establish them. Reporter results also do not establish an effect
in endogenous tissues or a clinical consequence.

## LDLR secondary reference, fixed before model explanations

[Kircher et al. (2019)](https://www.nature.com/articles/s41467-019-11526-w),
Fig. 1d and the LDLR Results section, describe effects clustered at SP1 and
SREBP1/SREBP2 sites. The established sterol-responsive sequence `ATCACCCCAC`
occurs at reporter positions **161–170** in the pinned LDLR panel. The
[primary functional study](https://pmc.ncbi.nlm.nih.gov/articles/PMC6430766/)
identifies that sequence in its reporter-construct Methods. The five selected
substitutions within it all reduce activity:

| Allele | Reporter edit | Measured log2 activity effect |
| --- | --- | ---: |
| V27 | T162A | −2.070 |
| V28 | A164C | −2.798 |
| V29 | A164T | −1.797 |
| V30 | C165A | −2.252 |
| V31 | A169G | −2.669 |

These measurements are consistent with disrupting a positive regulatory element;
they do not demonstrate altered protein binding separately for every allele.
Sequence localization and effect direction are assessable from the model's
explanation. Specific TF occupancy, affinity changes, and precise effects require
additional evidence. The benchmark supplies the complete insert in construct
orientation and an episomal reporter in HepG2 cells, measured after 24 hours
with no added treatment. It does not supply gene identity or a sterol-depletion
condition. Predictions premised on an unprovided treatment will be identified.

The source is the study's LDLR replicate 2, as recorded in the pinned
[satMutMPRA source](https://github.com/Open-Athena/VEP-bench/blob/71df7e3e780cb50bff1292ea029f37a21280425f/data/sources/satmut-mpra-cadd-v1.7.jsonl).
All 50 alleles remain in the scored panel. Both the 2019 study and the underlying
mechanism predate Astra's cutoff, so this secondary case supplies mechanistic
interpretability without the primary case's later-release rationale.

## Availability evidence

Astra's documented cutoff is
[April 30, 2026](https://developers.openai.com/api/docs/models/gpt-6-astra).
The [publisher version record](https://api.biorxiv.org/details/biorxiv/10.64898/2026.05.22.727141/na/json)
dates the first preprint to May 23, 2026, before the
[Figshare initial release](https://api.figshare.com/v2/articles/32337414/versions)
on May 24. The September 16 verification retained the publisher XML and version
record and traced the study's predecessor-release evidence; see the
[provenance audit](https://github.com/Open-Athena/VEP-bench/blob/71df7e3e780cb50bff1292ea029f37a21280425f/docs/assay-provenance.md).
The selected source uses Figshare v5. Earliest-supplement versus v5 byte identity
for these exact 50 measurements has not been established, and the supplementary
TSV was not independently checked. A post-cutoff first verified study date is
evidence about public availability, not proof of absence from training. General
splice-site knowledge predates this assay.

## Explanation request and comparison

The experiment replaces only the optional explanation sentence in the original
prompt. It requests substantive sequence-grounded biological justifications,
candidate IDs, proposed mechanisms, exceptions, alternatives, and uncertainty,
without a fixed word limit. It permits established biological knowledge but asks
the model not to substitute assumed gene identities or remembered experimental
results for a justification grounded in the supplied input. It also explicitly
permits saying that the input does not support a mechanistic explanation. This
instruction guides the stated justification; it cannot establish whether recall
influenced the prediction. The sequence, geometry, VCF, prediction target, and
required final JSON remain unchanged. A distinct template version and question
digest identify this supplementary prompt. The full question snapshot retains
reference measurements for scoring; only its prompt goes to the model.

Each panel used `openai/gpt-6-astra`, max effort, seed 20260829,
and the existing 128,000-token output ceiling including reasoning. Sampling
controls are omitted. These settings follow the requested effort and existing
benchmark controls; they are not a provider-endorsed biology recipe. The ceiling
is a resource limit, not a requested explanation length.

The experiment has a **separate $15 user-authorized budget**, unrelated
to issue #72. The OpenAI route is pinned with fallbacks disabled and all request
parameters required. Prompt and completion prices are capped at $10 and $50 per
million tokens, with no per-request fee. The recorded preflight bounds both
requests together even before an anticipated 50% batch discount. There are no
automatic retries. Exact request bodies, price assumptions, and submission
receipts are retained beside the result files.

The September 17 batch returned malformed upstream results for both requests,
with no usable model output and $0 charged. A September 18 direct attempt was
interrupted locally during the first request, without a saved response or cost
receipt. The user then clarified that both panels should run directly. The
restart reserves the interrupted request's full possible cost and checks the
remaining budget before each request. Prompts and inference settings remain
unchanged; the existing streaming transport is used to keep the connection alive.
All attempts and the transport changes are recorded separately from the frozen
selection. A missing response is not treated as a free request.

The completed MSH6 direct response cost $2.381404. Its receipt left insufficient
room for LDLR's full standard-price ceiling while retaining the interrupted
request's $6.45879 reserve, so LDLR was withheld locally. The subsequent LDLR
request pins OpenAI's `openai/flex` direct endpoint and caps prices at $5/$25 per
million input/output tokens. The prompt, max reasoning effort, seed, and
128,000-token ceiling are unchanged. Including that new request's ceiling, the
conservative total is $12.065004. The route snapshot and all earlier records
remain beside the experiment artifacts.

The September 15 publication's strongest completed configuration was Astra High
(overall Spearman 0.598976), as recorded in the original freeze. The September 18
publication includes the user's completed Astra Max baseline, now strongest at
0.610478. This later availability does not change the selected panels. The
matching baseline runs are `gpt-6-astra-max-opensplice-snv-20260916` and
`gpt-6-astra-max-satmut-mpra-20260916`, both served by OpenAI with max effort,
seed 20260829, and a 128,000-token ceiling. The supplementary experiment adds
provider-routing price restrictions and uses direct requests. No duplicate
baseline is requested; provider and inference-setting differences are retained
in the comparison artifacts.

The comparison distinguishes sequence localization and allele/orientation
handling, predicted direction and magnitude, proposed causal links, unsupported
specificity, contradictions, and unresolved claims. Explanations are stated
justifications, not verified descriptions of internal reasoning. Agreement with
the paper is not mechanistic proof. Existing deterministic Spearman and Pearson
scoring applies to all 50 predictions. A single completion per condition cannot
establish a benefit from requesting explanations or separate prompt effects
from sampling variability. Supplementary results do not enter the leaderboard.

## Primary result: MSH6

The explanation completion returned valid predictions for all 50 alleles without
truncation. Spearman was **0.803453**, compared with **0.799818** in the matching
Astra Max baseline; Pearson was **0.713871**, compared with **0.740322**. These
small, mixed changes from one completion per condition do not establish an
improvement from requesting an explanation.

The six illustrations fixed before model inspection show why the overall score
does not settle the mechanistic interpretation:

| Allele | Baseline prediction | Explanation prediction | Measured ΔPSI |
| --- | ---: | ---: | ---: |
| V19, G216A | −21 | −10 | −87.466 |
| V44, G307C | −72 | −72 | −97.003 |
| V41, G306A | −45 | −25 | −26.389 |
| V24, deletion 241–261 | −24 | −16 | +0.297 |
| V28, deletion 261–281 | +5 | +8 | +0.488 |
| V30, deletion 276–296 | +4 | +6 | +0.415 |

The explanation correctly localizes the canonical acceptor and donor, distinguishes
the final exonic donor base from the intronic GT, and describes V44's mutant donor
as CT rather than GC. Its graded V41 prediction closely matches the measured
effect. It also excludes retained VCF anchors when describing deletion intervals.

For V19 it reconstructs the newly created AG216–217 and proposes a shifted
acceptor, predicting only −10 points despite the measured −87.466. This is a
plausible sequence hypothesis with an unsuccessful prediction, not evidence that
the alternative junction forms. The model explicitly identifies uncertainty about
whether shifted products count toward PSI. A post-response check of the paper's
**Methods, “Read counting with DiMSum”**, finds that isoforms were classified by
exact matching to expected inclusion or skipping sequences, with expected exonic
deletions incorporated into the inclusion sequence. Arbitrary boundary shifts
therefore cannot simply be assumed to rescue the scored inclusion target. Our
prompt gives the geometry and PSI target but omits this exact-match counting
rule. This target ambiguity limits attribution of the discrepancy to biological
reasoning alone; the frozen experiment is not changed after seeing the result.

For the interior, the model proposes enhancer loss for V24 and silencer loss
for V28/V30, tying these to short SR-associated, RBFOX-like, and PTBP-like motifs.
It explicitly calls binding and activity unproven. The measured near-neutral
effects do not support the predicted magnitudes or establish those mechanisms.
The paper's largely tolerant interior remains the stronger empirical account.
Neither these measurements nor the model's caveats establish which proteins bind.

The completed response used 47,597 output tokens, of which the provider reports
42,838 as reasoning tokens, plus 1,527 input tokens. The $2.381404 direct charge
is separate from the unresolved interrupted attempt. The baseline's per-panel
cost is allocated from a batch receipt, so a cost difference cannot be attributed
solely to the explanation instruction.

## Secondary result: LDLR

The direct Flex completion returned valid predictions for all 50 alleles without
truncation. Spearman was **0.498629**, compared with **0.472678** in the matching
Astra Max baseline; Pearson was **0.450955**, compared with **0.607670**. The
explanation therefore gives a slightly better ordering but weaker correspondence
between the numerical effect sizes. One completion per condition does not
establish a prompt effect, and the service routes differ.

The model correctly identifies `ATCACCCCAC` at 161–170 and predicts decreased
activity for all five preselected substitutions within it. This agrees with the
direction of the measurements and the paper's SREBP-site interpretation. It does
not establish altered occupancy for any individual allele.

| Allele | Baseline prediction | Explanation prediction | Measured log2 effect |
| --- | ---: | ---: | ---: |
| V27, T162A | −0.90 | −0.95 | −2.070 |
| V28, A164C | −1.60 | −1.65 | −2.798 |
| V29, A164T | −1.50 | −1.90 | −1.797 |
| V30, C165A | −1.55 | −2.10 | −2.252 |
| V31, A169G | −1.50 | −1.60 | −2.669 |

The model acknowledges that motif-core recognition supports effect direction more
strongly than fine ordering. The ordering of V28 and V29 is nevertheless reversed:
it predicts A164T to be more damaging than A164C, whereas A164C has the larger
measured loss. The C/T-rich flanking elements are also correctly localized, with
Sp-family binding presented as a hypothesis requiring evidence.

### What the established LDLR elements reveal

This expanded audit was added **after inspecting the responses**, on September 18.
It does not change the five preselected illustrations or the 50 scored variants.
The reference is now more specific than a search for plausible short motifs:
[satMutMPRA Fig. 1d and Supplementary Table 12](https://www.nature.com/articles/s41467-019-11526-w)
connect the activity landscape to previously studied promoter elements and alleles.
The [GPN-Star paper, Fig. 4D](https://pmc.ncbi.nlm.nih.gov/articles/PMC12458161/)
labels four blocks **FP1, SREBP1, SREBP2, and SP1** and examines dependencies
within and between them.

The nomenclature needs care. The [satMutMPRA supplement, Table 12, pp. 92–93](https://media.springernature.com/original/springer-static/esm/art%3A10.1038%2Fs41467-019-11526-w/MediaObjects/41467_2019_11526_MOESM1_ESM.pdf)
defines the numbered labels as SRE repeats (with a duplicated “SREBP1” in the
caption). They should not be treated as proof that SREBP-1 and SREBP-2 bind
different sites. Classic functional studies identify a distal **Sp1 site in
repeat 1**, the **SRE within repeat 2**, and a proximal **Sp1 site in repeat 3**.
For example, [Peeters et al.](https://pubmed.ncbi.nlm.nih.gov/9610768/) found that
a repeat-1 deletion abolished Sp1 binding in a footprint assay and reduced
reporter activity to about 10% of normal. Astra's Sp-family hypothesis for the
upstream C/T-rich sequence is therefore compatible with established biology,
despite the figure's SREBP1 label.

The following mapping uses the submitted insert's **1-based, inclusive**
coordinates. Genomic GRCh38 position is reporter position + 11,089,230;
the corresponding NM_000527.4 upstream coordinate is position − 319.
The latter is relative to translation, not a transcription-start coordinate.
These offsets are checked against every selected source allele; V34, for example,
is reporter C177T = chr19:11089407 C>T = c.-142C>T.
FP1 uses the experimentally footprinted sequence. The other three intervals
are the precise cores discussed by Astra, within the known regulatory modules;
they do not define complete binding footprints or classify their flanks as inert.

| Literature element | Reporter sequence interval | Selected variants | What Astra recognized |
| --- | --- | --- | --- |
| FP1 | 81–100, `GAGCTTCACGGGTTAAAAAG` | V07–V12 | Does not identify the footprint; treats G81A/T86A as without a supported direction and the A/T-rich tract as nearly neutral. |
| Repeat 1, “SREBP1” figure label | 126–136, `CTCCTCCTCTT` | V18–V24 | Locates the C/T-rich core and proposes Sp-family cooperation, but assigns much smaller losses than measured. |
| Repeat 2 / SRE, “SREBP2” figure label | 161–170, `ATCACCCCAC` | V27–V31 | Correctly recognizes the canonical SRE and shared negative direction. |
| Repeat 3 / SP1 | 177–187, `CTCCTCCCCCT` | V34–V38 | Locates the core but underweights its importance, especially when proposing compensating ETS recruitment. |

**FP1 is a missed regulatory element, not merely a missing name.**
[Mehta et al.](https://pubmed.ncbi.nlm.nih.gov/8969230/) footprinted this exact
20-base sequence, tested substitutions in reporters, and observed sequence-specific
nuclear-protein binding. Their induction experiments involved sterol depletion;
we do not transfer that treatment or its effect size to this untreated MPRA,
nor infer the identity of the bound protein. The independent benchmark
measurements nevertheless show losses for all six selected FP1 variants.
V07 (G81A) is the most damaging allele in our entire 50-variant panel at
**−3.784**, but both baseline (−0.03) and explanation (0.00) effectively miss it.
V09–V12, the four substitutions in the tract Astra discusses at 93–99,
measure −1.692 to −2.083 despite predictions of only −0.04 to −0.10.
Declining to call this a canonical TATA box is reasonable; treating that lack
of a TATA match as insufficient evidence for a substantial effect misses FP1.

**Recognition of the SRE is better than recognition of the whole promoter.**
Every selected variant in each of these four intervals reduces activity.
The means below include every selected allele in each interval, including the
C128 deletion in repeat 1; the deletion's retained VCF anchor is excluded when
assigning its position. These are descriptive summaries of this deliberately
selected panel, not new benchmark scores or evidence of a prompt effect.

| Interval | Alleles | Mean measured | Mean baseline | Mean explanation |
| --- | ---: | ---: | ---: | ---: |
| FP1 | 6 | −2.078 | −0.032 | −0.050 |
| Repeat 1 core | 7 | −2.408 | −0.820 | −0.364 |
| SRE core | 5 | −2.317 | −1.410 | −1.640 |
| SP1 / repeat 3 core | 5 | −2.691 | −0.570 | −0.480 |
| All other sampled positions | 27 | −0.229 | −0.097 | −0.071 |

The explanation singles out the SRE as its strongest loss region, although the
sampled FP1 and Sp1-associated variants can be equally or more damaging.
It also moves the repeat-1 and repeat-3 means closer to zero than the baseline.
That pattern helps explain the weaker Pearson correlation without claiming a
causal effect of the explanation instruction. “Other” includes regulatory flanks
and does not mean biologically unimportant sequence.

```js
import * as Plot from "npm:@observablehq/plot@0.6.17";
const ldlrElementAudit = await FileAttachment("./mechanism-evidence/ldlr-elements.json").json();
const ldlrSeries = [
  {key: "reference_score", label: "Measured", color: "#238b6b"},
  {key: "baseline", label: "Baseline", color: "#5278b8"},
  {key: "explanation", label: "Explanation", color: "#9951a7"}
];
display(resize((width) => {
  const charts = document.createElement("div");
  charts.setAttribute("aria-label", "LDLR element comparison");
  for (const series of ldlrSeries) {
    charts.append(Plot.plot({
      width, height: 185, marginLeft: 64, marginTop: 22,
      ariaLabel: `LDLR ${series.label.toLowerCase()} effects by reporter position`,
      style: {background: "transparent"},
      x: {domain: [1, 318], label: "Reporter position (1-based)", ticks: 8},
      y: {domain: [-4.1, 1.05], label: `${series.label} (log2)`, grid: true},
      marks: [
        Plot.rect(ldlrElementAudit.annotation.elements, {
          x1: (d) => d.start - 0.5, x2: (d) => d.end + 0.5,
          y1: -4.1, y2: 1.05, fill: "currentColor", fillOpacity: 0.07
        }),
        Plot.ruleY([0], {stroke: "currentColor", strokeOpacity: 0.4}),
        Plot.text(ldlrElementAudit.annotation.elements, {
          x: (d) => (d.start + d.end) / 2, y: 0.8,
          text: (d) => ({fp1: "FP1", repeat1: "R1", sre: "SRE", sp1: "SP1"}[d.id]),
          fontSize: 10, fill: "currentColor"
        }),
        Plot.dot(ldlrElementAudit.variants, {
          x: "changed_position", y: series.key, fill: series.color, r: 3, tip: true,
          title: (d) => `${d.candidate_id}: ${d.ref}>${d.alt} at ${d.pos}\n${series.label}: ${d[series.key].toFixed(3)}\n${d.element}`
        })
      ]
    }));
  }
  return charts;
}));
display(html`<p class="muted">All 50 alleles on shared axes; variants at the same position can overlap.
  Shading marks the four intervals above. Hover for individual values.
  <a href=${await FileAttachment("./mechanism-evidence/ldlr-elements.json").url()} download>Download the complete element audit</a>.</p>`);
```

### Cooperation changes the interpretation of motif gains

[Sanchez et al.](https://pubmed.ncbi.nlm.nih.gov/7836375/) experimentally showed
that SREBP and Sp1 cooperate, that Sp1 orientation matters, and that other
tested TF sites could not simply replace the adjacent Sp1 site. An intact SRE
therefore does not imply preserved promoter activity after damage to a cooperating
element. This is a more constrained reference model than adding independent
benefits for every newly created short motif.

The following **post-response illustrations** examine the complete group for
which the explanation proposes beneficial ETS-site changes. They were not part
of the preselected five motif substitutions above, and do not change the scored
50-allele panel:

| Allele | Proposed favorable ETS change | Explanation prediction | Measured log2 effect |
| --- | --- | ---: | ---: |
| V18, C126T | New reverse-strand core | +0.15 | −1.709 |
| V34, C177T | New reverse-strand core | +0.15 | −3.090 |
| V36, C182T | New core offsets C-rich-site loss | −0.10 | −3.518 |
| V40, A191G | New forward-strand core | +0.25 | +0.504 |
| V43, deletion of G243 | Improved flank of an existing core | +0.35 | +0.755 |

The new short cores are present in the reconstructed sequences, but their
presence does not establish productive factor binding. V18/V34 have the wrong
predicted sign, and the proposed compensation in V36 misses a large measured
loss. V40/V43 agree in direction; that agreement does not demonstrate the proposed
ETS mechanism. The explanation itself cautions that binding, cooperation, and
competition require experimental evidence. These are uncertain mechanistic
assignments and unsuccessful predictions, rather than fabricated sequence motifs.

V34 is particularly informative because the literature tests **the same allele**.
Table 12 identifies c.-142C>T in the SP1 region and reports −3.09 for LDLR.2,
matching our pinned value. In the older transcription-start numbering this is
−49C>T: [Mozas et al.](https://pubmed.ncbi.nlm.nih.gov/11792717/) measured an
80% reduction in HepG2 reporter activity and severely impaired Sp1 binding in
gel-shift assays. Those older measurements use a different construct and are not
interchangeable with our numerical target, but they independently support
loss of an established activator site. Astra notices the new `TTCC`, yet its
proposed +0.15 net effect fails to account for that documented loss mechanism.
There is no evidence here that ETS binds the new sequence or rescues activity.

### What GPN-Star adds, and what it cannot establish

GPN-Star's Fig. 4D and Supplementary Fig. 14 provide a useful complementary
analysis: the model's nucleotide-dependency map resolves the four named regions
and connections between them, including the SREBP2-labelled region and SP1.
It changes the question from “does this short motif look plausible?” to “has the
model captured the interacting regulatory architecture?” Astra's final answer
mentions cooperation but misses FP1 and gives unsupported compensation substantial
weight at known activator elements. This is our interpretation of its stated
explanation, not an observation of its internal computation.

The GPN-Star map is **model-derived**, not a new binding or double-mutant assay.
Its MPRA track also reuses the Kircher measurements: the authors filter by barcode
support and significance, average absolute allele effects by position, fill missing
positions with zero, and smooth over five positions. The
[archived LDLR notebook](https://github.com/songlab-cal/gpn/blob/30dee6cf45849dfdcfc043ca8baf44fd6ba51d74/analysis/gpn-star/interpretation/workflow/notebooks/LDLR.ipynb)
preserves that construction. The track therefore neither supplies independent
experimental replication nor tests signed predictions for our 50 alleles.
We use it to motivate checking elements and interactions, not to claim a
quantitative GPN-Star-versus-Astra performance comparison or experimental epistasis.

Assay handling is generally careful: the model retains deletion anchors,
recognizes that no separate minimal promoter is present, and does not assume
sterol depletion. It declines to assert TATA-box, upstream-ORF, or methylation
mechanisms from insufficient context. We checked its 17 stated reference-sequence
features against the supplied insert, as well as the five ETS-related mutant
contexts above. These checks establish sequence consistency, not factor occupancy.

The final answer is grounded in sequence positions, but the provider-exposed
summary explicitly refers to the “classic LDL receptor promoter.” The prompt's
mechanistic framing therefore does not demonstrate removal of recognition or
recall. This partial provider summary is not a verified account of internal
reasoning, and cannot establish how much recognition affected the predictions.
The study and underlying mechanism predate the model's cutoff.

The response used 41,624 output tokens, including 37,639 reported reasoning
tokens, plus 1,076 input tokens. Its reported cost was **$1.04732125**. Together,
the two completed explanation responses cost **$3.42872525**; the original failed
batch cost $0, and the interrupted direct request's charge remains unresolved.
Keeping its full $6.45879 reserve gives a conservative total of **$9.88751525**,
below the authorized $15. No additional completions are planned.

## Reproduction

From the repository root, with the evaluator installed:

```bash
uv run --locked python projects/blog-analysis/scripts/mechanism_case.py prepare-pair \
  --output .vepbench/experiments/issue97-msh6-ldlr-20260917
```

Preparation is offline and refuses to overwrite the experiment directory. It
verifies the selected question against the frozen digest and saves both question
snapshots, the exact proposed request, and the selection record before inference.
Production records use the existing evaluator and retain complete provider
payloads, usage, reasoning, failures, and costs outside Git. The small, exact
readable responses and comparison extracts are committed beside this article.
Preparing these files does not submit a request.

The explicit `run --experiment PATH` subcommand submits the two-request batch
once, reading only `OPENROUTER_API_KEY` for authentication. Use the existing
`vepbench batch status` and `vepbench batch collect` commands with the saved
`batch-state.json` to retrieve it. An existing execution plan prevents repeat
submission even if no final result was collected.

The `recover-direct --experiment PATH` subcommand is reserved for the reviewed
unbilled batch failure. It checks the receipt and preserved failures, reproduces
the original request bodies, and reserves a single direct attempt before sending
anything. An existing recovery plan blocks repetition even if no response was
saved; unknown or billed outcomes require a separate budget review.

The reviewed September 18 restart uses `resume-interrupted-direct`. It preserves
the interrupted attempt, records each new request before contacting the provider,
and bounds total spending including the interrupted request's unresolved charge.
It refuses to repeat an existing restart plan.

After both result files are available, reproduce the deterministic comparison:

```bash
uv run --locked python projects/blog-analysis/scripts/mechanism_case.py compare \
  --experiment .vepbench/experiments/issue97-msh6-ldlr-20260917 \
  --panel msh6 \
  --baseline PATH_TO_BASELINE_RESULTS.jsonl \
  --results PATH_TO_EXPLANATION_RESULTS.jsonl \
  --output .vepbench/experiments/issue97-msh6-ldlr-20260917/msh6-comparison.json
```

Repeat with `--panel ldlr` and a distinct output path for the secondary case.

The committed evidence is generated from those saved records, without model calls:

```bash
uv run --locked python projects/blog-analysis/scripts/mechanism_case.py export-evidence \
  --experiment .vepbench/experiments/issue97-msh6-ldlr-20260917 \
  --baseline .vepbench/experiments/issue97-msh6-ldlr-20260917/baseline-20260918/results.jsonl \
  --output /tmp/vepbench-mechanism-evidence
```

Use a fresh output directory; the exporter refuses replacement. The committed
evidence is sufficient for offline score and element-audit replay via
`uv run --locked pytest projects/blog-analysis/tests/test_mechanism_case.py`.
This checks the exact prompts, file hashes, all predictions, both correlations,
allele mappings and interval summaries without access to private local records.
The annotation intervals are defined in
`projects/blog-analysis/config/ldlr-elements.json`; no literature labels were
added to the model prompt after inference. Full provider payloads, opaque provider
data, and attempt receipts remain archived separately from these readable extracts.
