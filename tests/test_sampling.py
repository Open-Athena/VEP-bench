from collections import Counter

import pytest

from vepbench.sampling import ScoredAllele, allocate_bins, quantile, sample_score_bins


def test_sparse_bins_keep_all_rare_alleles_and_redistribute_slots() -> None:
    capacities = [400, 80, 12, 6, 2]
    alleles = [
        ScoredAllele(f"{i}:{j}", float(i)) for i, n in enumerate(capacities) for j in range(n)
    ]
    panel = sample_score_bins(alleles, question_key="window", seed="1", anchor_policy="minmax")
    assert panel.population_counts == tuple(capacities)
    assert panel.allocations == (15, 15, 12, 6, 2)
    assert len({a.key for a, _ in panel.selected}) == 50
    assert {a.key for a, b in panel.selected if b >= 3} == {a.key for a in alleles if a.score >= 3}
    assert panel == sample_score_bins(
        alleles[::-1], question_key="window", seed="1", anchor_policy="minmax"
    )


def test_boundary_ties_stay_together_and_empty_bins_are_allowed() -> None:
    alleles = [ScoredAllele(str(i), 0.0 if i < 30 else 1.0 if i < 60 else 5.0) for i in range(90)]
    panel = sample_score_bins(alleles, question_key="w", seed="1", anchor_policy="minmax")
    assert panel.cutpoints == (1, 2, 3, 4)
    assert panel.population_counts == (30, 30, 0, 0, 30)
    assert panel.allocations == (17, 17, 0, 0, 16)
    assert Counter(b for _, b in panel.selected) == {0: 17, 1: 17, 4: 16}


def test_robust_anchors_keep_tail_scores_unchanged() -> None:
    alleles = [ScoredAllele(str(i), float(i)) for i in range(100)]
    panel = sample_score_bins(alleles, question_key="w", seed="s", panel_size=100)
    assert panel.anchors == pytest.approx((0.99, 98.01))
    assert sorted(a.score for a, _ in panel.selected) == list(range(100))
    assert quantile([0, 10, 20, 30], 0.05) == pytest.approx(1.5)


def test_collapsed_robust_anchors_do_not_silently_fall_back() -> None:
    alleles = [ScoredAllele(str(i), 0.0) for i in range(999)] + [ScoredAllele("tail", 1.0)]
    with pytest.raises(ValueError, match="collapsed"):
        sample_score_bins(alleles, question_key="w", seed="1")
    panel = sample_score_bins(alleles, question_key="w", seed="1", anchor_policy="minmax")
    assert panel.allocations == (49, 0, 0, 0, 1)


@pytest.mark.parametrize(
    "capacities,size", [([1, 2], 4), ([-1, 60], 50), ([True, 60], 50), ([], 1)]
)
def test_invalid_capacity_fails(capacities, size) -> None:
    with pytest.raises(ValueError):
        allocate_bins(capacities, size)


def test_invalid_population_fails() -> None:
    for alleles in (
        [ScoredAllele("same", float(i)) for i in range(50)],
        [ScoredAllele(str(i), float("nan")) for i in range(50)],
        [ScoredAllele(str(i), 1.0) for i in range(50)],
        [ScoredAllele(str(i), float(i)) for i in range(49)],
    ):
        with pytest.raises(ValueError):
            sample_score_bins(alleles, question_key="w", seed="1")
