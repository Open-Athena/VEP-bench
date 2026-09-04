"""Audit pinned primary populations without modifying production task artifacts.

Run on a SkyPilot worker for all tasks. Each adapter processes one source at a
time; OpenSplice partitions compact records by exon before comparing samplers.
"""

from __future__ import annotations

import argparse
import csv
import gzip
import hashlib
import json
import math
import platform
import urllib.parse
import urllib.request
from collections import Counter
from pathlib import Path

from vepbench.artifacts import canonical_json
from vepbench.sampling import ScoredAllele, quantile, sample_score_bins, sampling_digest

ROOT = Path(__file__).resolve().parents[1]
SEED = "2026090400"


def pinned_file(path: Path, pin: dict, url: str, download: bool) -> Path:
    if not path.exists():
        if not download:
            raise FileNotFoundError(f"Missing pinned input: {path}; use --download")
        path.parent.mkdir(parents=True, exist_ok=True)
        temporary = path.with_suffix(path.suffix + ".partial")
        print(f"Downloading {path.name}: {pin['bytes']} bytes", flush=True)
        request = urllib.request.Request(url, headers={"User-Agent": "VEP-bench/0.1"})
        with urllib.request.urlopen(request, timeout=900) as source, temporary.open("wb") as out:
            while chunk := source.read(1024 * 1024):
                out.write(chunk)
        temporary.replace(path)
    digest = hashlib.sha256()
    with path.open("rb") as source:
        while chunk := source.read(1024 * 1024):
            digest.update(chunk)
    if path.stat().st_size != pin["bytes"] or digest.hexdigest() != pin["sha256"]:
        raise ValueError(f"Pinned input identity mismatch: {path}")
    return path


def variant_kind(ref: str, alt: str) -> str:
    while ref and alt and ref[-1] == alt[-1]:
        ref, alt = ref[:-1], alt[:-1]
    while ref and alt and ref[0] == alt[0]:
        ref, alt = ref[1:], alt[1:]
    if not ref and not alt:
        return "no_change"
    if not alt:
        return f"deletion_{len(ref)}nt"
    if not ref:
        return f"insertion_{len(alt)}nt"
    if len(ref) == len(alt) == 1:
        return "SNV"
    return f"replacement_{len(ref)}to{len(alt)}nt"


def reconstruct_opensplice_allele(row: dict, reference: str) -> tuple[int, str, str]:
    """Decode deposited RNA/deletion notation and verify the complete construct."""
    from vepbench_opensplice_snv.task import normalize_dna

    start, end, length = int(row["start"]), int(row["end"]), int(row["length"])
    wt = normalize_dna(row["wt"], label="wt", allow_empty=True)
    if row["mut_type"] == f"∆{length}nt" and row["mut"] == row["mut_type"]:
        mut = ""
    elif row["mut_type"] == "sub":
        mut = normalize_dna(row["mut"], label="mut")
    else:
        raise ValueError(f"{row['variant_id']}: unsupported source allele notation")
    observed = normalize_dna(row["nt_seq"], label="nt_seq")
    if not 1 <= start <= end <= len(reference):
        raise ValueError(f"{row['variant_id']}: source interval outside construct")
    if end - start + 1 != length or reference[start - 1 : end] != wt:
        raise ValueError(f"{row['variant_id']}: source REF/interval mismatch")
    if reference[: start - 1] + mut + reference[end:] != observed:
        raise ValueError(f"{row['variant_id']}: reconstructed mutant mismatch")
    return start, wt, mut


def compare_population(key: str, rows: list[dict], output: Path, **metadata) -> dict:
    alleles = [ScoredAllele(row["key"], row["score"]) for row in rows]
    if len({a.key for a in alleles}) != len(alleles):
        raise ValueError(f"{key}: duplicate allele identities")
    by_key = {row["key"]: row for row in rows}
    scores = sorted(a.score for a in alleles)
    result = {
        "key": key,
        "count": len(rows),
        "variant_types": dict(sorted(Counter(row["variant_type"] for row in rows).items())),
        "unique_scores": len(set(scores)),
        **metadata,
        "policies": {},
    }
    if not scores:
        return result
    result["score_quantiles"] = {
        str(p): quantile(scores, p) for p in (0, 0.01, 0.05, 0.5, 0.95, 0.99, 1)
    }
    result["robust_range"] = quantile(scores, 0.95) - quantile(scores, 0.05)
    selections = {}
    for policy in ("minmax", "p01_p99"):
        try:
            panel = sample_score_bins(alleles, question_key=key, seed=SEED, anchor_policy=policy)
        except ValueError as exc:
            result["policies"][policy] = {"error": str(exc)}
            continue
        result["policies"][policy] = {
            "anchors": panel.anchors,
            "cutpoints": panel.cutpoints,
            "population_counts": panel.population_counts,
            "allocations": panel.allocations,
            "exact_five_by_ten": min(panel.population_counts) >= 10,
        }
        selections[policy] = panel.selected
    if len(alleles) >= 50:
        ordered = sorted(alleles, key=lambda a: (a.score, a.key))
        size, remainder = divmod(len(ordered), 10)
        start = 0
        selected = []
        for index in range(10):
            end = start + size + (index < remainder)
            pool = sorted(
                ordered[start:end], key=lambda a: (sampling_digest(SEED, key, index, a.key), a.key)
            )
            selected.extend((a, index) for a in pool[:5])
            start = end
        selections["rank_quantile"] = selected
        result["policies"]["rank_quantile"] = {"bins": 10, "samples_per_bin": 5}
    for policy, selected in selections.items():
        selected_scores = sorted(a.score for a, _ in selected)
        result["policies"][policy].update(
            {
                "selected_range": [selected_scores[0], selected_scores[-1]],
                "selected_p95_minus_p05": quantile(selected_scores, 0.95)
                - quantile(selected_scores, 0.05),
                "selected_variant_types": dict(
                    sorted(Counter(by_key[a.key]["variant_type"] for a, _ in selected).items())
                ),
                "selected": [
                    {"key": a.key, "score": a.score, "bin": index} for a, index in selected
                ],
            }
        )
    # Compact full populations permit plotting and independent selection checks.
    with (output / "populations.jsonl").open("a") as handle:
        handle.write(canonical_json({"key": key, "rows": rows}) + "\n")
    return result


def analyze_mpra(inputs: Path, output: Path, download: bool) -> dict:
    from vepbench_satmut_mpra.configuration import CONFIG
    from vepbench_satmut_mpra.task import ELEMENT_SPECS, parse_cadd_vcf

    report = {"task": "satmut_mpra", "inputs": {}, "populations": [], "filters": {}}
    base = CONFIG.values["upstream"]["cadd_base_url"]
    for spec in ELEMENT_SPECS:
        pin = CONFIG.pins["cadd"][spec.cadd_filename]
        path = pinned_file(
            inputs / "mpra" / spec.cadd_filename,
            pin,
            f"{base.rstrip('/')}/{spec.cadd_filename}",
            download,
        )
        report["inputs"][spec.cadd_filename] = pin
        payload = path.read_bytes()
        variants, counts = parse_cadd_vcf(payload, label=spec.cadd_label)
        headers = [
            line
            for line in gzip.decompress(payload).decode().splitlines()
            if line.startswith("##FILTER=")
        ]
        report["filters"][spec.cadd_label] = {"counts": counts, "source_headers": headers}
        for pool_name, filters in (("SIGN", {"SIGN"}), ("SIGN_MIN", {"SIGN", "MIN"})):
            rows = [
                {
                    "key": v.key_text,
                    "score": v.effect,
                    "variant_type": variant_kind(v.ref, v.alt),
                    "barcodes": v.barcode_count,
                    "p_value": v.p_value,
                    "source_filter": v.source_filter,
                }
                for v in variants
                if v.source_filter in filters
            ]
            report["populations"].append(
                compare_population(
                    f"satmut_mpra:{spec.cadd_label}:{pool_name}",
                    rows,
                    output,
                    unit=spec.cadd_label,
                    eligibility=pool_name,
                    validation="pinned VCF/parser checks; existing pinned crosswalk provenance",
                )
            )
    return report


def analyze_opensplice(inputs: Path, output: Path, download: bool) -> dict:
    from vepbench_opensplice_snv.configuration import CONFIG
    from vepbench_opensplice_snv.task import normalize_dna, parse_exon_metadata

    paths = {}
    for name in ("exon_metadata", "master"):
        pin = CONFIG.pins["files"][name]
        paths[name] = pinned_file(
            inputs / "opensplice" / pin["filename"],
            pin,
            f"https://ndownloader.figshare.com/files/{pin['file_id']}",
            download,
        )
    with paths["exon_metadata"].open() as handle:
        exons = parse_exon_metadata(handle, label="pinned exon metadata")
    report = {
        "task": "opensplice_snv",
        "inputs": {name: CONFIG.pins["files"][name] for name in paths},
        "populations": [],
        "exclusions": Counter(),
        "source_types": Counter(),
        "rows": 0,
    }
    partition = output / "opensplice-partitions"
    partition.mkdir(exist_ok=True)
    for previous in partition.glob("*.jsonl"):
        previous.unlink()
    handles = {}
    try:
        with gzip.open(paths["master"], "rt", newline="") as source:
            for row in csv.DictReader(source, delimiter="\t"):
                report["rows"] += 1
                report["source_types"][f"{row['mut_type']}|{row['length']}"] += 1
                if row["measured"].lower() != "true":
                    report["exclusions"]["not_measured"] += 1
                    continue
                exon = exons.get(row["ensembl_exon_id"])
                if exon is None:
                    report["exclusions"]["missing_exon_metadata"] += 1
                    continue
                try:
                    numbers = {
                        field: float(row[field])
                        for field in ("delta_psi", "psi_r1", "psi_r2", "psi_r3")
                    }
                    if not all(map(math.isfinite, numbers.values())):
                        raise ValueError
                except ValueError:
                    report["exclusions"]["nonfinite_measurement_or_replicate"] += 1
                    continue
                start, wt, mut = reconstruct_opensplice_allele(row, exon.wt_seq)
                observed = normalize_dna(row["nt_seq"], label="nt_seq")
                compact = {
                    "key": hashlib.sha256(observed.encode()).hexdigest(),
                    "source_id": row["variant_id"],
                    "score": numbers["delta_psi"],
                    "variant_type": variant_kind(wt, mut),
                    "gene": row["gene"],
                    "exon_id": row["ensembl_exon_id"],
                    "start": start,
                    "ref": wt,
                    "alt": mut,
                    "replicates": [numbers[f"psi_r{i}"] for i in (1, 2, 3)],
                }
                exon_id = row["ensembl_exon_id"]
                if exon_id not in handles:
                    handles[exon_id] = (partition / f"{exon_id}.jsonl").open("w")
                handles[exon_id].write(canonical_json(compact) + "\n")
                if report["rows"] % 100000 == 0:
                    print(f"OpenSplice processed {report['rows']} rows", flush=True)
    finally:
        for handle in handles.values():
            handle.close()
    current = json.loads(
        (ROOT / "data/sources/opensplice-snv-figshare-v5.manifest.json").read_text()
    )
    selected = {
        row["ensembl_exon_id"]
        for row in current["population"]["exon_selection"]
        if row["selected_rank"] is not None
    }
    for path in sorted(partition.glob("*.jsonl")):
        rows = [json.loads(line) for line in path.read_text().splitlines()]
        if len({row["gene"] for row in rows}) != 1:
            raise ValueError(f"{path.stem}: conflicting gene assignments")
        # Distinct deposited IDs must not duplicate an assayed mutant sequence.
        for pool_name, population in (
            ("SNV", [r for r in rows if r["variant_type"] == "SNV"]),
            ("all_alleles", rows),
        ):
            report["populations"].append(
                compare_population(
                    f"opensplice:{path.stem}:{pool_name}",
                    population,
                    output,
                    unit=path.stem,
                    gene=rows[0]["gene"],
                    eligibility=pool_name,
                    current_selected=path.stem in selected,
                    validation="construct reconstruction; finite replicates; no predictor gate",
                )
            )
    report["exclusions"] = dict(report["exclusions"])
    report["source_types"] = dict(report["source_types"])
    return report


def map_sge_allele(hgvs_text: str, spec, transcript, genome) -> tuple[int, str, str]:
    """Map explicit HGVS edits without allowing source REF repair by the mapper."""
    import pyhgvs
    from pyhgvs.variants import normalize_variant
    from vepbench_sge.task import reverse_complement

    if spec.coordinate_mode == "target_coding_hgvs":
        if not hgvs_text.startswith("n."):
            raise ValueError("unexpected target coordinate syntax")
        hgvs_text = f"{spec.transcript}:c.{hgvs_text[2:]}"
    h = pyhgvs.HGVSName(hgvs_text)
    if h.kind == "c":
        if h.transcript != spec.transcript:
            raise ValueError("unexpected transcript")
        first = pyhgvs.cdna_to_genomic_coord(transcript, h.cdna_start)
        last = pyhgvs.cdna_to_genomic_coord(transcript, h.cdna_end)
        forward = transcript.tx_position.is_forward_strand
        chrom = transcript.tx_position.chrom
    elif h.kind == "g":
        first, last, chrom, forward = h.start, h.end, h.chrom, True
    else:
        raise ValueError("unsupported HGVS coordinate kind")
    from vepbench_sge.prepare import _NCGenome

    if _NCGenome._key(chrom) != spec.expected_chrom:
        raise ValueError("unexpected chromosome")
    lower, upper = sorted((first, last))
    reference = str(genome[chrom][lower - 1 : upper]).upper()
    if len(reference) != upper - lower + 1:
        raise ValueError("invalid reference interval")
    source_ref = h.ref_allele.upper()
    if source_ref and set(source_ref) <= set("ACGT"):
        expected = source_ref if forward else reverse_complement(source_ref)
        if expected != reference:
            raise ValueError("source REF mismatch")
    alt = h.alt_allele.upper()
    if not forward:
        alt = reverse_complement(alt)
    kind = h.mutation_type
    if kind == ">" or kind == "delins":
        pos, ref = lower, reference
    elif kind == "del":
        pos, ref, alt = lower, reference, ""
    elif kind == "ins":
        if upper != lower + 1:
            raise ValueError("insertion endpoints are not adjacent")
        pos, ref = upper, ""
    elif kind == "dup":
        pos, ref, alt = (upper + 1 if forward else lower), "", reference
    else:
        raise ValueError("unsupported_or_unchanged_allele")
    if set(ref + alt) - set("ACGT") or ref == alt:
        raise ValueError("invalid_or_unchanged_allele")
    # The normalizer anchors empty alleles and left-aligns equivalent indels.
    _, pos, ref, alternatives = normalize_variant(chrom, pos, ref, [alt], genome).variant
    if str(genome[chrom][pos - 1 : pos - 1 + len(ref)]).upper() != ref:
        raise ValueError("normalized REF mismatch")
    return pos, ref, alternatives[0]


def analyze_sge(inputs: Path, output: Path, download: bool) -> dict:
    from pyfaidx import Fasta
    from pyhgvs.utils import make_transcript
    from vepbench_sge.configuration import CONFIG
    from vepbench_sge.prepare import Genome, _NCGenome
    from vepbench_sge.task import (
        GENE_SPECS,
        _gene_spec,
        transcript_coding_sequence,
        transcript_from_cdot,
        validate_mavedb_metadata,
    )

    ref_config = CONFIG.values["upstream"]["reference"]
    ref_url = (
        f"https://huggingface.co/datasets/{ref_config['dataset']}/resolve/"
        f"{ref_config['revision']}/{ref_config['filename']}"
    )
    ref_path = pinned_file(inputs / "reference.fa", CONFIG.pins["reference"], ref_url, download)
    fasta = Fasta(str(ref_path))
    genome = _NCGenome(fasta)
    report = {
        "task": "sge",
        "inputs": {"reference": CONFIG.pins["reference"]},
        "populations": [],
        "genes": {},
    }
    current = json.loads((ROOT / "data/sources/sge-mavedb-2026-09-03.manifest.json").read_text())
    supplemental = json.loads((ROOT / "scripts/task_sampling_audit_inputs.json").read_text())
    for spec in (*GENE_SPECS, _gene_spec(supplemental["gene"])):
        urn = urllib.parse.quote(spec.mavedb_urn, safe="")
        base = f"https://api.mavedb.org/api/v1/score-sets/{urn}"
        is_supplemental = spec.gene == supplemental["gene"]["gene"]
        pins = supplemental["pins"] if is_supplemental else CONFIG.pins["mavedb"][spec.mavedb_urn]
        transcript_pin = (
            pins["transcript"] if is_supplemental else CONFIG.pins["cdot"][spec.transcript]
        )
        paths = {
            name: pinned_file(
                inputs / "sge" / f"{spec.gene}-{name}.{suffix}",
                pins[name],
                base + ("/scores" if name == "scores" else ""),
                download,
            )
            for name, suffix in (("metadata", "json"), ("scores", "csv"))
        }
        transcript_path = pinned_file(
            inputs / "sge" / f"{spec.gene}-transcript.json",
            transcript_pin,
            f"https://cdotlib.org/transcript/{spec.transcript}",
            download,
        )
        report["inputs"][spec.gene] = {**pins, "transcript": transcript_pin}
        metadata = validate_mavedb_metadata(paths["metadata"].read_bytes(), spec)
        transcript = transcript_from_cdot(transcript_path.read_bytes(), spec)
        build = json.loads(transcript_path.read_text())["genome_builds"]["GRCh38"]
        mapping = make_transcript(
            {
                "id": spec.transcript,
                "chrom": build["contig"],
                "start": build["exons"][0][0],
                "end": build["exons"][-1][1],
                "strand": build["strand"],
                "cds_start": build["cds_start"],
                "cds_end": build["cds_end"],
                "gene_name": spec.gene,
                "exons": [exon[:2] for exon in build["exons"]],
            }
        )
        if spec.coordinate_mode == "target_coding_hgvs" and (
            metadata["target"]["sequence"] != transcript_coding_sequence(transcript, Genome(fasta))
        ):
            raise ValueError("TINF2 target/CDS mismatch")
        excluded, errors, rows = Counter(), {}, []
        source_count = 0
        with paths["scores"].open(newline="") as source:
            for row in csv.DictReader(source):
                source_count += 1
                try:
                    score = float(row["score"])
                    if not math.isfinite(score):
                        raise ValueError
                except ValueError:
                    excluded["nonfinite_score"] += 1
                    continue
                if spec.qc_field:
                    value = row[spec.qc_field]
                    if value in spec.qc_fail_values:
                        excluded["source_qc_failure"] += 1
                        continue
                    if value not in spec.qc_pass_values:
                        raise ValueError(f"{spec.gene}: unknown QC value")
                try:
                    pos, ref, alt = map_sge_allele(row["hgvs_nt"], spec, mapping, genome)
                except (ValueError, NotImplementedError, AssertionError) as exc:
                    reason = str(exc)
                    excluded["mapping:" + type(exc).__name__] += 1
                    errors.setdefault(reason, row["hgvs_nt"])
                    continue
                rows.append(
                    {
                        "key": f"{spec.expected_chrom}:{pos}:{ref}:{alt}",
                        "score": spec.score_direction * score,
                        "pos": pos,
                        "ref": ref,
                        "alt": alt,
                        "source_hgvs": row["hgvs_nt"],
                        "variant_type": variant_kind(ref, alt),
                    }
                )
        counts = Counter(row["key"] for row in rows)
        unique = [row for row in rows if counts[row["key"]] == 1]
        excluded["duplicate_normalized_allele_rows"] = len(rows) - len(unique)
        report["genes"][spec.gene] = {
            "source_rows": source_count,
            "eligible_unique": len(unique),
            "exclusions": dict(excluded),
            "mapping_examples": errors,
        }
        old = current["population"].get(spec.gene, {}).get("selected_exon")
        for exon in transcript.exons:
            window = [
                row
                for row in unique
                if exon.start - 100 <= row["pos"]
                and row["pos"] + len(row["ref"]) - 1 <= exon.end + 100
            ]
            if not window:
                continue
            for pool_name, population in (
                ("SNV", [r for r in window if r["variant_type"] == "SNV"]),
                ("all_alleles", window),
            ):
                report["populations"].append(
                    compare_population(
                        f"sge:{spec.gene}:{exon.start}-{exon.end}:{pool_name}",
                        population,
                        output,
                        unit=spec.gene,
                        eligibility=pool_name,
                        proposed_addition=is_supplemental,
                        exon=[exon.start, exon.end],
                        current_selected=old == {"start": exon.start, "end": exon.end},
                        validation="pinned source/QC; HGVS mapping; full normalized REF validation",
                    )
                )
        print(f"SGE {spec.gene}: {len(unique)} mapped unique alleles", flush=True)
    fasta.close()
    return report


def main() -> None:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--inputs", type=Path, required=True)
    parser.add_argument("--output", type=Path, required=True)
    parser.add_argument(
        "--tasks",
        nargs="+",
        choices=("satmut-mpra", "opensplice", "sge"),
        default=["satmut-mpra", "opensplice", "sge"],
    )
    parser.add_argument("--download", action="store_true")
    args = parser.parse_args()
    args.output.mkdir(parents=True, exist_ok=True)
    (args.output / "populations.jsonl").write_text("")
    for task in args.tasks:
        adapter = {
            "satmut-mpra": analyze_mpra,
            "opensplice": analyze_opensplice,
            "sge": analyze_sge,
        }[task]
        report = adapter(args.inputs, args.output, args.download)
        report["analysis_schema_version"] = "1.0"
        report["python_version"] = platform.python_version()
        report["implementation_sha256"] = {
            name: hashlib.sha256((ROOT / name).read_bytes()).hexdigest()
            for name in (
                "scripts/analyze_task_sampling.py",
                "scripts/task_sampling_audit_inputs.json",
                "src/vepbench/sampling.py",
            )
        }
        report["sampling"] = {
            "algorithm": "score_space_audit_v1",
            "seed": SEED,
            "panel_size": 50,
            "bins": 5,
            "boundary": "equal goes right",
        }
        (args.output / f"{task}.json").write_text(canonical_json(report) + "\n")
        print(f"Completed {task}: {len(report['populations'])} populations", flush=True)


if __name__ == "__main__":
    main()
