"""Compare pinned public score releases with the selected SGE variants."""

import argparse
import csv
import gzip
import hashlib
import json
import math
import shutil
import urllib.request
import xml.etree.ElementTree as ET
import zipfile
from collections.abc import Iterable, Iterator, Mapping
from pathlib import Path
from typing import Any


def file_sha256(path: Path) -> str:
    with path.open("rb") as stream:
        return hashlib.file_digest(stream, "sha256").hexdigest()


def allele_key(chrom: str, pos: str, ref: str, alt: str) -> str:
    """Remove VCF padding; treat the workbook's '-' as an empty allele."""
    position = int(pos)
    ref, alt = ref.upper().replace("-", ""), alt.upper().replace("-", "")
    while ref and alt and ref[-1] == alt[-1]:
        ref, alt = ref[:-1], alt[:-1]
    while ref and alt and ref[0] == alt[0]:
        position += 1
        ref, alt = ref[1:], alt[1:]
    return f"{chrom.removeprefix('chr')}:{position}:{ref}:{alt}"


def compare_scores(
    selected: list[dict], rows: Iterable[Mapping[str, str]], match_by: str = "genomic"
) -> dict[str, Any]:
    wanted = {
        (
            v["source_hgvs"]
            if match_by == "source_hgvs"
            else allele_key(*v["genomic_key"].split(":"))
        ): v
        for v in selected
    }
    if len(wanted) != len(selected):
        raise ValueError("Selected alleles must be unique")
    found: dict[str, list[float]] = {key: [] for key in wanted}
    for row in rows:
        key = (
            row["source_hgvs"]
            if match_by == "source_hgvs"
            else allele_key(row["chrom"], row["pos"], row["ref"], row["alt"])
        )
        if key in wanted:
            score = float(row["score"])
            if not math.isfinite(score):
                raise ValueError(f"Non-finite public score for {key}")
            found[key].append(score)
    if any(len(values) > 1 for values in found.values()):
        raise ValueError("Ambiguous duplicate public scores for a selected allele")
    matches = [
        {
            "candidate_id": wanted[key]["candidate_id"],
            "genomic_key": wanted[key]["genomic_key"],
            "matched_identity": key,
            "public_score": values[0],
            "pinned_score": wanted[key]["source_score"],
        }
        for key, values in found.items()
        if values
    ]
    differences = [abs(row["public_score"] - row["pinned_score"]) for row in matches]
    return {
        "match_by": match_by,
        "selected_count": len(selected),
        "matched_count": len(matches),
        "exact_score_count": sum(difference == 0 for difference in differences),
        "within_1e_6_count": sum(difference <= 1e-6 for difference in differences),
        "max_absolute_score_difference": max(differences, default=None),
        "matches": sorted(matches, key=lambda row: row["candidate_id"]),
        "missing_candidate_ids": sorted(
            wanted[key]["candidate_id"] for key, values in found.items() if not values
        ),
    }


def xlsx_rows(path: Path, sheet: str, header_row: int) -> Iterator[dict[str, str]]:
    """Read the archived BAP1 workbook one row at a time, without a dataframe."""
    ns = "{http://schemas.openxmlformats.org/spreadsheetml/2006/main}"
    with zipfile.ZipFile(path) as archive:
        strings = []
        with archive.open("xl/sharedStrings.xml") as stream:
            for _, element in ET.iterparse(stream, events=("end",)):
                if element.tag == ns + "si":
                    strings.append("".join(element.itertext()))
                    element.clear()
        headers = {}
        with archive.open(f"xl/worksheets/{sheet}.xml") as stream:
            for _, element in ET.iterparse(stream, events=("end",)):
                if element.tag != ns + "row":
                    continue
                index = int(element.attrib["r"])
                cells = {}
                for cell in element:
                    column = cell.attrib["r"].rstrip("0123456789")
                    value = cell.findtext(ns + "v", "")
                    cells[column] = strings[int(value)] if cell.attrib.get("t") == "s" else value
                if index == header_row:
                    headers = cells
                elif index > header_row and any(cells.values()):
                    yield {name: cells.get(column, "") for column, name in headers.items()}
                element.clear()


def score_rows(path: Path, source: dict) -> Iterator[dict[str, str]]:
    if source["format"] == "xlsx":
        columns = source["columns"]
        for row in xlsx_rows(path, source["sheet"], source["header_row"]):
            yield {name: row[column] for name, column in columns.items()}
    else:
        opener = gzip.open if source["format"] == "tsv.gz" else open
        delimiter = "\t" if source["format"] == "tsv.gz" else ","
        with opener(path, "rt", encoding="utf-8", newline="") as stream:
            yield from csv.DictReader(stream, delimiter=delimiter)


def public_date(metadata: dict, registry: str) -> str:
    if registry == "MaveDB":
        return metadata["publishedDate"][:10]
    if registry == "IGVF":
        # Creation/check timestamps may precede a private file's public release.
        return metadata["release_timestamp"][:10]
    if registry == "Zenodo":
        return next(
            row["date"]
            for row in metadata["data"]["attributes"]["dates"]
            if row["dateType"] == "Issued"
        )
    raise ValueError(f"Unsupported release registry: {registry}")


def cached_file(spec: dict, cache: Path, download: bool) -> Path:
    path = cache / spec["filename"]
    if not path.exists() and download:
        cache.mkdir(parents=True, exist_ok=True)
        partial = path.with_suffix(path.suffix + ".partial")
        try:
            with (
                urllib.request.urlopen(spec["url"], timeout=45) as response,
                partial.open("wb") as output,
            ):
                shutil.copyfileobj(response, output, length=64 * 1024)
            if file_sha256(partial) != spec["sha256"]:
                raise ValueError(f"Public file changed: {spec['url']}")
            partial.replace(path)
        finally:
            partial.unlink(missing_ok=True)
    if file_sha256(path) != spec["sha256"]:
        raise ValueError(f"Digest mismatch: {path}")
    return path


def audit(manifest_path: Path, panels_path: Path, cache: Path, download: bool = False) -> dict:
    manifest = json.loads(manifest_path.read_text())
    if file_sha256(panels_path) != manifest["source_panels_sha256"]:
        raise ValueError("Pinned SGE source panels changed; review the audit manifest")
    with panels_path.open() as stream:
        panels = {row["source_record_id"]: row for row in map(json.loads, stream)}
    sources = []
    for source in manifest["sources"]:
        metadata = json.loads(cached_file(source["metadata"], cache, download).read_text())
        scores = cached_file(source["scores"], cache, download)
        sources.append(
            {
                "gene": source["gene"],
                "registry": source["registry"],
                "url": source["url"],
                "public_date": public_date(metadata, source["registry"]),
                "metadata_sha256": source["metadata"]["sha256"],
                "scores_sha256": source["scores"]["sha256"],
                **compare_scores(
                    panels[source["gene"]]["source_metadata"]["selected_candidates"],
                    score_rows(scores, source["scores"]),
                    source.get("match_by", "genomic"),
                ),
            }
        )
    return {
        "schema_version": "1.0",
        "assembly": "GRCh38",
        "manifest_sha256": file_sha256(manifest_path),
        "source_panels_sha256": file_sha256(panels_path),
        "sources": sorted(sources, key=lambda row: (row["gene"], row["public_date"], row["url"])),
    }


def main() -> None:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--manifest", type=Path, required=True)
    parser.add_argument("--panels", type=Path, required=True)
    parser.add_argument("--cache", type=Path, required=True)
    parser.add_argument("--download", action="store_true")
    parser.add_argument("--output", type=Path, required=True)
    args = parser.parse_args()
    result = audit(args.manifest, args.panels, args.cache, args.download)
    args.output.write_text(json.dumps(result, indent=2, sort_keys=True, allow_nan=False) + "\n")


if __name__ == "__main__":
    main()
