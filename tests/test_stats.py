"""The small statistics `benchmark` and `learn` lean on."""

from __future__ import annotations

import numpy as np
import pytest

from altocue import stats


def test_median_of_even_and_odd():
    assert stats.median([1, 3, 2]) == 2
    assert stats.median([1, 2, 3, 100]) == 2.5


def test_ranks_share_ties():
    assert stats.ranks(np.array([10.0, 20.0, 20.0, 30.0])).tolist() == [1.0, 2.5, 2.5, 4.0]


def test_spearman_reads_order_not_size():
    assert stats.spearman([1, 2, 3, 4, 5], [1, 4, 9, 16, 1000]) == pytest.approx(1.0)
    assert stats.spearman([1, 2, 3, 4, 5], [5, 4, 3, 2, 1]) == pytest.approx(-1.0)


def test_spearman_of_a_constant_is_undefined():
    assert stats.spearman([1, 1, 1, 1], [1, 2, 3, 4]) is None


def test_permutation_p_is_small_for_order_and_large_for_noise():
    x = np.arange(30.0)
    assert stats.permutation_p(x, x * 2) < 0.001
    noise = np.random.default_rng(1).normal(size=30)
    assert stats.permutation_p(x, noise) > 0.05


def test_permutation_p_repeats_exactly():
    x, y = np.arange(20.0), np.random.default_rng(2).normal(size=20)
    assert stats.permutation_p(x, y) == stats.permutation_p(x, y)


def test_interval_holds_the_estimate():
    x = np.arange(40.0)
    y = x + np.random.default_rng(3).normal(scale=8, size=40)
    low, high = stats.bootstrap_interval(x, y)
    assert low < stats.spearman(x, y) < high


def test_benjamini_hochberg_matches_a_worked_example():
    assert stats.adjust([0.01, 0.04, 0.03, 0.2]) == pytest.approx([0.04, 0.0533333, 0.0533333, 0.2], abs=1e-6)


def test_adjusted_values_stay_at_one_or_under():
    assert max(stats.adjust([0.9, 0.95, 1.0])) <= 1.0
    assert stats.adjust([]) == []


def test_fisher_matches_the_tea_example():
    assert stats.fisher(3, 1, 1, 3) == pytest.approx(0.4857, abs=1e-3)


def test_fisher_with_empty_margins_says_nothing():
    assert stats.fisher(0, 0, 0, 5) == 1.0


def test_sign_test_is_exact():
    assert stats.sign_test(6, 0) == pytest.approx(0.03125)
    assert stats.sign_test(5, 0) == pytest.approx(0.0625)
    assert stats.sign_test(0, 0) == 1.0
