"""Offline transport and coordinate checks using the installed SDK's data types."""

import copy
from types import SimpleNamespace

import anndata
import numpy as np
import pandas as pd
import pytest
from alphagenome.data import genome
from alphagenome.models import dna_output
from vepbench_blog_analysis.alphagenome_client import (
    AlphaGenomeClient,
    alternate_position,
    avi_score,
    dnase_score,
    splice_score,
)
from vepbench_blog_analysis.specialists import POST, read_json


@pytest.fixture(autouse=True)
def prohibit_live_clients(monkeypatch):
    def fail(*args, **kwargs):
        raise AssertionError("tests must not create a live client")

    monkeypatch.setattr("alphagenome.models.dna_client.create", fail)
    monkeypatch.setattr("alphagenome.atlas.atlas.create", fail)


def variant(ref="A", alt="T", pos=103):
    return {"assembly": "GRCh38", "chrom": "1", "pos": pos, "ref": ref, "alt": alt}


def sdk_variant(v):
    return genome.Variant("chr" + v["chrom"], v["pos"], v["ref"], v["alt"])


def tracks(values):
    return SimpleNamespace(
        resolution=1,
        interval=genome.Interval("chr1", 100, 110),
        metadata=pd.DataFrame(
            {"name": ["acceptor", "donor", "acceptor", "donor"], "strand": ["+", "+", "-", "-"]}
        ),
        values=np.array(values, dtype=float),
    )


@pytest.mark.parametrize(
    "ref,alt,expected",
    [
        ("ACG", "A", [100, 101, 102, None, None, 103, 104]),
        ("ACG", "G", [100, 101, None, None, 102, 103, 104]),
        ("A", "ATC", [100, 101, 102, 105, 106, 107, 108]),
        ("A", "TCA", [100, 101, 104, 105, 106, 107, 108]),
        ("AC", "TG", list(range(100, 107))),
    ],
)
def test_complete_allele_coordinate_correspondence(ref, alt, expected):
    assert [alternate_position(p, variant(ref, alt)) for p in range(100, 107)] == expected


def test_ambiguous_replacement_is_not_treated_as_a_deletion():
    with pytest.raises(ValueError, match="ambiguous"):
        alternate_position(104, variant("AC", "GTT"))


@pytest.mark.parametrize(
    "strand,sites,columns",
    [
        ("+", {"acceptor": 103, "donor": 108}, (0, 1)),
        ("-", {"acceptor": 108, "donor": 103}, (2, 3)),
    ],
)
def test_splice_score_maps_deleted_site_to_zero_and_shifts_surviving_site(strand, sites, columns):
    ref = np.zeros((10, 4))
    alt = np.ones((10, 4)) * 0.99  # Wrong coordinate or strand produces a very different answer.
    for role, col in zip(("acceptor", "donor"), columns, strict=True):
        ref[sites[role] - 100, col] = 0.8
        mapped = alternate_position(sites[role], variant("ACG", "A"))
        if mapped is not None:
            alt[mapped - 100, col] = 0.6
    request = {
        "variant": variant("ACG", "A"),
        "strand": strand,
        "interval": {"chromosome": "chr1", "start": 100, "end": 110},
        "sites": sites,
    }
    result = splice_score(tracks(ref), tracks(alt), request)
    assert result["score"] == pytest.approx(-0.5)


def test_splice_score_rejects_nonprobabilities_and_wrong_coordinates():
    request = {
        "variant": variant(),
        "strand": "+",
        "interval": {"chromosome": "chr1", "start": 100, "end": 110},
        "sites": {"acceptor": 103, "donor": 108},
    }
    ref, alt = tracks(np.zeros((10, 4))), tracks(np.ones((10, 4)) * 2)
    with pytest.raises(ValueError, match="probability"):
        splice_score(ref, alt, request)
    alt.interval = genome.Interval("chr1", 101, 111)
    with pytest.raises(ValueError, match="coordinate"):
        splice_score(ref, alt, request)


def test_dnase_uses_signed_raw_scores_and_every_matched_track():
    metadata = [
        {"name": "a", "ontology_curie": "match"},
        {"name": "b", "ontology_curie": "other"},
        {"name": "c", "ontology_curie": "match"},
    ]
    request = {"variant": variant(), "ontology_terms": ["match"]}
    data = anndata.AnnData(
        np.array([[-5.0, 100.0, 1.0]]),
        var=pd.DataFrame(metadata),
        uns={"variant": sdk_variant(variant())},
    )
    data.layers["quantiles"] = np.array([[0.9, 0.99, 0.8]])
    result = dnase_score(data, request, metadata)
    assert result["score"] == -2
    assert result["track_scores"] == [-5, 1]
    changed = copy.deepcopy(metadata)
    changed[2]["name"] = "different"
    with pytest.raises(ValueError, match="tracks changed"):
        dnase_score(data, request, changed)


def test_avi_requires_exact_ref_alt_and_preserves_missingness():
    request = {"variant": variant()}
    data = anndata.AnnData(
        np.array([[0.7]]), obs=pd.DataFrame({"variant": [sdk_variant(variant())]})
    )
    assert avi_score(data, request) == {"status": "scored", "score": 0.7}
    assert avi_score(None, request)["reason"] == "atlas_score_not_found"
    data.obs["variant"] = [sdk_variant(variant("T", "A"))]
    with pytest.raises(ValueError, match="exact"):
        avi_score(data, request)


def test_adapter_pins_sdk_model_and_dnase_recipe(monkeypatch):
    seen = {}
    metadata = pd.DataFrame([{"name": "HepG2", "ontology_curie": "EFO:0001187"}])

    def score(interval, v, *, variant_scorers):
        seen["interval"], seen["scorer"] = interval, variant_scorers[0]
        return [anndata.AnnData(np.array([[-2.0]]), var=metadata, uns={"variant": v})]

    def create(key, **kwargs):
        assert key == "offline-test-value"
        seen.update(kwargs)
        return SimpleNamespace(score_variant=score)

    monkeypatch.setattr("alphagenome.models.dna_client.create", create)
    monkeypatch.setattr("alphagenome.atlas.atlas.create", lambda *a, **k: object())
    client = AlphaGenomeClient("offline-test-value", read_json(POST / "specialist-policy.json"))
    req = {
        "task_family": "satmut_mpra",
        "variant": variant("AC", "A"),
        "ontology_terms": ["EFO:0001187"],
        "interval": {"chromosome": "chr1", "start": 0, "end": 1048576},
    }
    assert client.predict(req, {"dnase": metadata.to_dict("records")})["score"] == -2
    assert seen["scorer"].width == 501
    assert seen["scorer"].aggregation_type.name == "DIFF_LOG2_SUM"
    assert seen["scorer"].requested_output == dna_output.OutputType.DNASE
    assert seen["model_version"].name == "ALL_FOLDS"
