"""Small, explicit AlphaGenome/Atlas adapter; importing this module is offline."""

from __future__ import annotations

import importlib.metadata
import json
import math
from typing import Any


def finite(value: Any) -> float:
    if isinstance(value, bool):
        raise ValueError("boolean prediction")
    value = float(value)
    if not math.isfinite(value):
        raise ValueError("non-finite prediction")
    return value


def metadata_records(frame: Any) -> list:
    """Normalize pandas missing values and scalar types to strict JSON."""
    return json.loads(frame.to_json(orient="records"))


def alternate_position(pos0: int, variant: dict) -> int | None:
    """Map a reference base to the unaligned ALT sequence; deleted bases are absent.

    Keep shared VCF padding on either side. A substitution retains positional
    correspondence; a simultaneous unequal-length replacement is ambiguous.
    """
    start = variant["pos"] - 1
    ref, alt = variant["ref"], variant["alt"]
    if len(ref) == len(alt):
        return pos0
    while ref and alt and ref[0] == alt[0]:
        start, ref, alt = start + 1, ref[1:], alt[1:]
    while ref and alt and ref[-1] == alt[-1]:
        ref, alt = ref[:-1], alt[:-1]
    if ref and alt:
        raise ValueError("ambiguous correspondence for an unequal-length replacement")
    if pos0 < start:
        return pos0
    if pos0 < start + len(ref):
        return None
    return pos0 + len(alt) - len(ref)


def splice_score(reference: Any, alternate: Any, request: dict) -> dict:
    """Read only the strand-specific canonical sites from raw API tracks."""
    interval = request["interval"]
    strand = request["strand"]
    for tracks in (reference, alternate):
        if (
            tracks.resolution != 1
            or tracks.interval.start != interval["start"]
            or tracks.interval.end != interval["end"]
            or tracks.interval.chromosome != interval["chromosome"]
            or str(tracks.interval.strand) != "."
        ):
            raise ValueError("unexpected splice-track coordinate system")
    sites = {}
    metadata = []
    for role, position in request["sites"].items():
        values = []
        for is_alt, tracks in ((False, reference), (True, alternate)):
            indices = [
                i
                for i, row in enumerate(metadata_records(tracks.metadata))
                if row["name"] == role and row["strand"] == strand
            ]
            if len(indices) != 1:
                raise ValueError("expected one track per splice role and strand")
            index = indices[0]
            metadata.append(metadata_records(tracks.metadata)[index])
            pos = alternate_position(position, request["variant"]) if is_alt else position
            if pos is None:
                value = 0.0
            else:
                offset = pos - interval["start"]
                if not 0 <= offset < len(tracks.values):
                    raise ValueError("splice site lies outside the returned track")
                value = finite(tracks.values[offset, index])
            if not 0 <= value <= 1:
                raise ValueError("splice-site output is not a probability")
            values.append(value)
        sites[role] = {"reference": values[0], "alternate": values[1]}
    if set(sites) != {"acceptor", "donor"}:
        raise ValueError("both canonical splice sites are required")
    return {
        "score": math.fsum(v["alternate"] - v["reference"] for v in sites.values()) / 2,
        "sites": sites,
        "track_metadata": metadata,
    }


def variant_identity(variant: Any) -> dict:
    return {
        "assembly": "GRCh38",
        "chrom": variant.chromosome.removeprefix("chr"),
        "pos": variant.position,
        "ref": variant.reference_bases,
        "alt": variant.alternate_bases,
    }


def avi_score(result: Any, request: dict) -> dict:
    if result is None or result.shape[0] == 0:
        return {"status": "excluded", "reason": "atlas_score_not_found"}
    if (
        result.shape != (1, 1)
        or variant_identity(result.obs.iloc[0]["variant"]) != request["variant"]
    ):
        raise ValueError("AVI result does not match the exact requested allele")
    return {"status": "scored", "score": finite(result.X[0, 0])}


def dnase_score(result: Any, request: dict, expected_metadata: list) -> dict:
    if variant_identity(result.uns["variant"]) != request["variant"]:
        raise ValueError("DNase result allele mismatch")
    observed = metadata_records(result.var)
    terms = set(request["ontology_terms"])
    indices = [i for i, row in enumerate(observed) if row["ontology_curie"] in terms]
    selected = [observed[i] for i in indices]
    # Preserve every matching replicate track, with equal track weight. No
    # quantile layer, tissue maximum, or selection using measured MPRA effects.
    expected = [row for row in expected_metadata if row["ontology_curie"] in terms]
    if not expected or selected != expected or result.shape[0] != 1:
        raise ValueError("DNase tracks changed or the published match is unavailable")
    values = [finite(result.X[0, i]) for i in indices]
    return {
        "score": math.fsum(values) / len(values),
        "track_scores": values,
        "track_metadata": selected,
    }


class AlphaGenomeClient:
    def __init__(self, api_key: str, policy: dict):
        from alphagenome.atlas import atlas
        from alphagenome.data import genome
        from alphagenome.models import dna_client, dna_model, dna_output, variant_scorers

        self.version = importlib.metadata.version("alphagenome")
        if self.version != policy["client_version"]:
            raise ValueError("AlphaGenome client version differs from the frozen policy")
        self.genome, self.scorers, self.output_type = genome, variant_scorers, dna_output.OutputType
        self.model = dna_client.create(
            api_key, model_version=dna_model.ModelVersion[policy["model_version"]], timeout=30
        )
        self.atlas = atlas.create(api_key, timeout=30)
        self.policy = policy

    def metadata(self) -> dict:
        molecular = self.model.output_metadata()
        avi = self.atlas.scorer_metadata()[self.policy["avi"]["scorer"]]
        return {
            "client_version": self.version,
            "model_version": self.policy["model_version"],
            "model_revision": "server weights not exposed by the API",
            "dnase": json.loads(molecular.dnase.to_json(orient="records")),
            "splice_sites": json.loads(molecular.splice_sites.to_json(orient="records")),
            "avi": {
                "name": avi.name,
                "is_signed": avi.is_signed,
                "tracks": json.loads(avi.track_metadata.to_json(orient="records")),
            },
        }

    def predict(self, request: dict, metadata: dict) -> dict:
        v = request["variant"]
        variant = self.genome.Variant(
            chromosome="chr" + v["chrom"],
            position=v["pos"],
            reference_bases=v["ref"],
            alternate_bases=v["alt"],
        )
        if request["task_family"] == "sge":
            try:
                result = self.atlas.query_variant(
                    variant, requested_scorers=[self.policy["avi"]["scorer"]]
                ).get(self.policy["avi"]["scorer"])
            except IndexError:
                return {"status": "excluded", "reason": "atlas_score_not_found"}
            return avi_score(result, request)
        interval = self.genome.Interval(**request["interval"])
        if request["task_family"] == "opensplice_snv":
            result = self.model.predict_variant(
                interval,
                variant,
                requested_outputs=[self.output_type.SPLICE_SITES],
                ontology_terms=None,
            )
            return {
                "status": "scored",
                **splice_score(
                    result.reference.splice_sites, result.alternate.splice_sites, request
                ),
            }
        config = self.policy["mpra"]
        scorer = self.scorers.CenterMaskScorer(
            requested_output=self.output_type.DNASE,
            width=config["width"],
            aggregation_type=self.scorers.AggregationType[config["aggregation"]],
        )
        results = self.model.score_variant(interval, variant, variant_scorers=[scorer])
        if len(results) != 1:
            raise ValueError("expected one DNase scorer result")
        return {"status": "scored", **dnase_score(results[0], request, metadata["dnase"])}
