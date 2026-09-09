"""Explicit, cached Ensembl annotation for explorer-only variant metadata."""

from __future__ import annotations

import json
import re
import time
from collections.abc import Callable, Mapping, Sequence
from datetime import UTC, datetime
from pathlib import Path
from typing import Any
from urllib.error import HTTPError, URLError
from urllib.parse import urlencode
from urllib.request import Request, urlopen

from vepbench.alleles import normalize_allele
from vepbench.artifacts import canonical_json, read_jsonl, sha256_json
from vepbench.errors import BuildError

ENSEMBL_URL = "https://rest.ensembl.org"
VEP_PARAMETERS = {"distance": 1000}
Transport = Callable[[dict[str, Any]], Any]


def valid_ensembl_url(value: Any) -> bool:
    return isinstance(value, str) and bool(
        re.fullmatch(
            r"https://(?:(?:jan|feb|mar|apr|may|jun|jul|aug|sep|oct|nov|dec)20\d{2}\.)?"
            r"rest\.ensembl\.org",
            value,
        )
    )


def request_ensembl(request: dict[str, Any]) -> Any:
    """Request public annotation, retrying transient failures and rate limits."""
    url = f"{request['base_url']}/{request['path']}"
    if request["params"]:
        url += f"?{urlencode(request['params'])}"
    body = request["body"]
    data = canonical_json(body).encode() if body is not None else None
    for attempt in range(3):
        try:
            with urlopen(
                Request(
                    url,
                    data=data,
                    headers={"Accept": "application/json", "Content-Type": "application/json"},
                    method=request["method"],
                ),
                timeout=60,
            ) as response:
                return json.load(response)
        except HTTPError as exc:
            if exc.code not in {429, 500, 502, 503, 504} or attempt == 2:
                raise BuildError(f"Ensembl {request['path']}: HTTP {exc.code}") from exc
            delay = exc.headers.get("Retry-After", "")
            time.sleep(float(delay) if delay.replace(".", "", 1).isdigit() else 2**attempt)
        except (URLError, TimeoutError) as exc:
            if attempt == 2:
                raise BuildError(f"Ensembl {request['path']}: request failed") from exc
            time.sleep(2**attempt)
    raise AssertionError("unreachable")


class EnsemblCache:
    """Preserve exact requests, full responses and retrieval dates for replay."""

    def __init__(
        self,
        directory: Path,
        transport: Transport = request_ensembl,
        *,
        base_url: str = ENSEMBL_URL,
    ) -> None:
        if not valid_ensembl_url(base_url):
            raise BuildError("use the GRCh38 Ensembl REST server or a dated REST archive")
        self.directory = directory
        self.base_url = base_url
        self.transport = transport
        self.used: dict[str, dict[str, str]] = {}
        self.software: dict[str, Any] | None = None
        self.checked_release = False
        self.fetched = False

    def verify_release(self) -> None:
        if self.software is None:
            return
        current = self.transport(
            {
                "base_url": self.base_url,
                "path": "info/software",
                "method": "GET",
                "params": {"content-type": "application/json"},
                "body": None,
            }
        )
        if current != self.software:
            raise BuildError("Ensembl release changed; use a new annotation cache directory")

    def get(self, path: str, *, body: Any = None, params: Mapping[str, Any] | None = None) -> Any:
        request = {
            "base_url": self.base_url,
            "path": path,
            "method": "GET" if body is None else "POST",
            "params": {"content-type": "application/json", **(params or {})},
            "body": body,
        }
        key = sha256_json(request)
        destination = self.directory / f"{key}.json"
        if destination.is_file():
            cached = json.loads(destination.read_text(encoding="utf-8"))
            if cached.get("request") != request or cached.get("response_sha256") != sha256_json(
                cached.get("response")
            ):
                raise BuildError(f"invalid Ensembl cache entry {destination}")
        else:
            if path != "info/software" and not self.checked_release:
                self.verify_release()
                self.checked_release = True
            response = self.transport(request)
            self.fetched = True
            if isinstance(response, dict) and response.get("error"):
                raise BuildError(f"Ensembl {path}: {response['error']}")
            cached = {
                "request": request,
                "retrieved_at": datetime.now(UTC).isoformat(),
                "response": response,
                "response_sha256": sha256_json(response),
            }
            write_annotation_json(destination, cached)
        self.used[key] = {
            "request_sha256": key,
            "response_sha256": cached["response_sha256"],
            "retrieved_at": cached["retrieved_at"],
        }
        if path == "info/software":
            self.software = cached["response"]
            if self.fetched:
                self.checked_release = True
        return cached["response"]


def write_annotation_json(path: Path, value: Any) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    temporary = path.with_suffix(".tmp")
    temporary.write_text(f"{canonical_json(value)}\n", encoding="utf-8", newline="\n")
    temporary.replace(path)


def _reverse_complement(value: str) -> str:
    return value.translate(str.maketrans("ACGT", "TGCA"))[::-1]


def _genomic_allele(key: str) -> dict[str, Any]:
    try:
        chrom, pos, ref, alt = key.split(":")
        allele: dict[str, Any] = {
            "assembly": "GRCh38",
            "chrom": chrom,
            "pos": int(pos),
            "ref": ref,
            "alt": alt,
        }
    except (ValueError, AttributeError) as exc:
        raise BuildError(f"invalid genomic allele {key!r}") from exc
    if (
        not re.fullmatch(r"(?:[1-9]|1[0-9]|2[0-2]|X|Y|MT)", chrom)
        or allele["pos"] < 1
        or not ref
        or not alt
        or ref == alt
        or set(ref + alt) - set("ACGT")
    ):
        raise BuildError(f"invalid genomic allele {key!r}")
    return allele


def opensplice_alleles(record: Mapping[str, Any], client: EnsemblCache) -> dict[str, Any]:
    """Project complete native-insert edits after checking GRCh38 sequence identity."""
    metadata = record["source_metadata"]
    exon = metadata["native_exon"]
    construct = metadata["construct"]
    # Source exon IDs can be retired; the gene supplies the chromosome, while
    # the entire deposited insert must independently match its genomic interval.
    gene = client.get(f"lookup/symbol/homo_sapiens/{metadata['gene']}")
    if (
        gene.get("assembly_name") != "GRCh38"
        or gene.get("strand") != exon["strand"]
        or not gene["start"] <= exon["start_exon"] <= exon["end_exon"] <= gene["end"]
    ):
        raise BuildError(f"{metadata['gene']}: OpenSplice genomic mapping does not match GRCh38")
    forward = exon["strand"] == 1
    upstream = construct["native_upstream_length"]
    downstream = construct["native_downstream_length"]
    start = exon["start_exon"] - (upstream if forward else downstream)
    end = exon["end_exon"] + (downstream if forward else upstream)
    chrom = gene["seq_region_name"]
    # One native base at each end provides VCF padding outside the reporter.
    sequence = client.get(f"sequence/region/homo_sapiens/{chrom}:{start - 1}..{end + 1}:1")[
        "seq"
    ].upper()
    native_sequence = sequence[1:-1]
    expected = construct["components"]["wt_seq"]
    if (
        len(sequence) != end - start + 3
        or (native_sequence if forward else _reverse_complement(native_sequence)) != expected
    ):
        raise BuildError(f"{metadata['gene']}: OpenSplice insert does not match GRCh38")
    alleles = {}
    for candidate in metadata["selected_candidates"]:
        source = candidate["source"]
        edit = source["construct_variant"]
        local_pos, ref, alt = edit["insert_position"], edit["ref"], edit["alt"]
        if expected[local_pos - 1 : local_pos - 1 + len(ref)] != ref:
            raise BuildError("OpenSplice source edit REF mismatch")
        mutant = expected[: local_pos - 1] + alt + expected[local_pos - 1 + len(ref) :]
        if mutant != source["mutant_insert_sequence"]:
            raise BuildError("OpenSplice source edit does not reconstruct the mutant insert")
        pos = start + local_pos - 1 if forward else end - local_pos - len(ref) + 2
        if not forward:
            ref, alt = _reverse_complement(ref), _reverse_complement(alt)
        projected = sequence[: pos - start + 1] + alt + sequence[pos - start + 1 + len(ref) :]
        expected_mutant = mutant if forward else _reverse_complement(mutant)
        if projected != sequence[0] + expected_mutant + sequence[-1]:
            raise BuildError("OpenSplice genomic edit does not reconstruct the mutant insert")
        try:
            local, ref, alt = normalize_allele(sequence, pos - start + 2, ref, alt)
        except ValueError as exc:
            raise BuildError("OpenSplice genomic allele normalization failed") from exc
        alleles[candidate["candidate_id"]] = _genomic_allele(
            f"{chrom}:{start - 2 + local}:{ref}:{alt}"
        )
    return alleles


def source_alleles(record: Mapping[str, Any], client: EnsemblCache | None = None) -> dict[str, Any]:
    family = record["task_family"]
    if family == "opensplice_snv":
        return opensplice_alleles(record, client) if client is not None else {}
    key = {"sge": "genomic_key", "satmut_mpra": "vcf_key"}.get(family)
    if key is None:
        raise BuildError(f"no genomic mapping for task {family}")
    return {
        item["candidate_id"]: _genomic_allele(item[key])
        for item in record["source_metadata"]["selected_candidates"]
    }


def generate_annotations(source_paths: Sequence[Path], client: EnsemblCache) -> dict[str, Any]:
    """Annotate unique complete alleles; never modify questions or source records."""
    software = client.get("info/software")
    if not isinstance(software.get("release"), int):
        raise BuildError("Ensembl did not identify its software release")
    records = sorted(
        (record for path in source_paths for record in read_jsonl(path)),
        key=lambda record: (record["task_family"], record["source_record_id"]),
    )
    families: dict[str, Any] = {}
    unique: dict[str, Any] = {}
    for record in records:
        alleles = source_alleles(record, client)
        expected_ids = {candidate["candidate_id"] for candidate in record["candidates"]}
        if set(alleles) != expected_ids:
            raise BuildError("genomic allele IDs do not match the source candidates")
        entries = {}
        for candidate_id, allele in sorted(alleles.items()):
            identity = sha256_json(allele)
            unique[identity] = allele
            entries[candidate_id] = {"genomic": allele, "allele_sha256": identity}
        task = families.setdefault(record["task_family"], {})
        if record["source_record_id"] in task:
            raise BuildError("duplicate source identity in annotation inputs")
        task[record["source_record_id"]] = {
            "source_record_sha256": sha256_json(record),
            "variants": entries,
        }
    consequences = {}
    identities = sorted(unique)
    for offset in range(0, len(identities), 200):
        batch = identities[offset : offset + 200]
        inputs = {}
        for identity in batch:
            allele = unique[identity]
            inputs[identity] = (
                f"{allele['chrom']} {allele['pos']} {identity} "
                f"{allele['ref']} {allele['alt']} . . ."
            )
        response = client.get(
            "vep/homo_sapiens/region",
            body={"variants": list(inputs.values())},
            params=VEP_PARAMETERS,
        )
        if not isinstance(response, list) or len(response) != len(batch):
            raise BuildError("VEP did not return every requested variant")
        for item in response:
            identity = item.get("id")
            term = item.get("most_severe_consequence")
            if (
                identity not in inputs
                or identity in consequences
                or item.get("input") != inputs[identity]
                or item.get("assembly_name") != "GRCh38"
                or not isinstance(term, str)
                or not re.fullmatch(r"[A-Za-z0-9_]+", term)
            ):
                raise BuildError("VEP returned an invalid or mismatched consequence")
            consequences[identity] = term
    for task in families.values():
        for record in task.values():
            for variant in record["variants"].values():
                variant["most_severe_consequence"] = consequences[variant.pop("allele_sha256")]
    if client.fetched:
        client.verify_release()
    return {
        "schema_version": "1.0",
        "annotation": {
            "provider": "Ensembl VEP",
            "base_url": client.base_url,
            "software": software,
            "assembly": "GRCh38",
            "parameters": VEP_PARAMETERS,
            "consequence_policy": "most_severe_consequence",
            "transcripts": "Ensembl (all)",
            "requests": [client.used[key] for key in sorted(client.used)],
        },
        "by_task_family": families,
    }


def load_annotations(path: Path) -> dict[str, Any]:
    document = json.loads(path.read_text(encoding="utf-8"))
    annotation = document.get("annotation", {})
    if (
        document.get("schema_version") != "1.0"
        or not isinstance(document.get("by_task_family"), dict)
        or not valid_ensembl_url(annotation.get("base_url"))
        or annotation.get("assembly") != "GRCh38"
        or annotation.get("parameters") != VEP_PARAMETERS
        or annotation.get("consequence_policy") != "most_severe_consequence"
        or annotation.get("transcripts") != "Ensembl (all)"
        or not isinstance(annotation.get("software", {}).get("release"), int)
    ):
        raise BuildError(f"{path}: invalid variant annotation contract")
    return document


def variants_for_record(
    record: Mapping[str, Any], annotations: Mapping[str, Any]
) -> dict[str, Any]:
    """Fail closed if a saved annotation is stale or incomplete for this source."""
    saved = (
        annotations.get("by_task_family", {})
        .get(record["task_family"], {})
        .get(record["source_record_id"], {})
    )
    variants = saved.get("variants", {})
    if saved.get("source_record_sha256") != sha256_json(record) or set(variants) != {
        candidate["candidate_id"] for candidate in record["candidates"]
    }:
        raise BuildError("variant annotations are missing or stale; run vepbench-site annotate")
    for variant in variants.values():
        allele = variant.get("genomic", {})
        normalized = _genomic_allele(
            f"{allele.get('chrom')}:{allele.get('pos')}:{allele.get('ref')}:{allele.get('alt')}"
        )
        term = variant.get("most_severe_consequence")
        if (
            allele != normalized
            or not isinstance(term, str)
            or not re.fullmatch(r"[A-Za-z0-9_]+", term)
        ):
            raise BuildError("invalid saved variant annotation")
    return variants
