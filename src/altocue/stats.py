"""Small statistics in numpy. Seeded, because the same inputs must give the same output."""

from __future__ import annotations

from collections.abc import Sequence
from math import comb

import numpy as np

Numbers = Sequence[float] | np.ndarray
SEED = 20261102
PERMUTATIONS = 9999
RESAMPLES = 2000


def median(values: Numbers) -> float:
    return float(np.median(np.asarray(values, dtype=float)))


def ranks(values: np.ndarray) -> np.ndarray:
    """Ranks from 1, with ties sharing their average."""
    _, inverse, counts = np.unique(values, return_inverse=True, return_counts=True)
    return (np.cumsum(counts) - (counts - 1) / 2)[inverse]


def spearman(x: Numbers, y: Numbers) -> float | None:
    """Rank correlation. None when either side never changes, because nothing can be said then."""
    left, right = ranks(np.asarray(x, dtype=float)), ranks(np.asarray(y, dtype=float))
    if left.std() == 0 or right.std() == 0:
        return None
    return float(np.corrcoef(left, right)[0, 1])


def permutation_p(x: Numbers, y: Numbers, *, rounds: int = PERMUTATIONS) -> float:
    """Two-sided: how often shuffled pairs correlate at least this strongly. Needs no distribution."""
    left, right = ranks(np.asarray(x, dtype=float)), ranks(np.asarray(y, dtype=float))
    left, right = left - left.mean(), right - right.mean()
    scale = float(np.linalg.norm(left) * np.linalg.norm(right))
    if scale == 0:
        return 1.0
    observed = abs(float(left @ right)) / scale
    shuffled = np.random.default_rng(SEED).permuted(np.tile(right, (rounds, 1)), axis=1)
    hits = int((np.abs(shuffled @ left) / scale >= observed - 1e-12).sum())
    return (hits + 1) / (rounds + 1)


def bootstrap_interval(x: Numbers, y: Numbers, *, rounds: int = RESAMPLES) -> tuple[float, float]:
    """The middle 95 percent of the correlation when the posts are resampled."""
    left, right = np.asarray(x, dtype=float), np.asarray(y, dtype=float)
    picks = np.random.default_rng(SEED).integers(0, len(left), size=(rounds, len(left)))
    found = [rho for pick in picks if (rho := spearman(left[pick], right[pick])) is not None]
    return float(np.percentile(found, 2.5)), float(np.percentile(found, 97.5))


def adjust(pvalues: Numbers) -> list[float]:
    """Benjamini-Hochberg. Each adjusted value is the false discovery rate at which that test holds."""
    count = len(pvalues)
    if not count:
        return []
    order = np.argsort(pvalues)
    scaled = np.asarray(pvalues, dtype=float)[order] * count / np.arange(1, count + 1)
    adjusted = np.minimum(np.minimum.accumulate(scaled[::-1])[::-1], 1.0)
    result = np.empty(count)
    result[order] = adjusted
    return result.tolist()


def fisher(a: int, b: int, c: int, d: int) -> float:
    """Two-sided exact test on the table [[a, b], [c, d]]."""
    left, right, column = a + b, c + d, a + c
    total = comb(left + right, column)
    if not total:
        return 1.0
    chance = [comb(left, k) * comb(right, column - k) / total for k in range(max(0, column - right), min(left, column) + 1)]
    seen = comb(left, a) * comb(right, c) / total
    return min(1.0, sum(p for p in chance if p <= seen * (1 + 1e-9)))


def sign_test(wins: int, losses: int) -> float:
    """Two-sided exact binomial at one half. Ties are dropped before this."""
    pairs = wins + losses
    if not pairs:
        return 1.0
    return min(1.0, 2 * sum(comb(pairs, k) for k in range(max(wins, losses), pairs + 1)) / 2**pairs)
