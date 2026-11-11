"""Where a caption page breaks into lines. Chosen here once, so both renderers draw the same lines.

A browser wraps greedily and so did the fallback renderer, which leaves "of" or "it" alone on the second line.
This splits a page into the fewest lines that fit and then makes them as even as it can.
"""

from __future__ import annotations

from collections.abc import Sequence
from functools import lru_cache
from pathlib import Path
from typing import Any

from PIL import ImageFont

EVEN = 40.0  # what an uneven pair of lines costs: this, times the square of each line's distance from the mean, as a share of the room
LONE_LAST = 6.0  # a last line of one word, when the page has three or more
TOO_WIDE = 1000.0  # a line wider than its room, per share of the room it runs over


def split(widths: Sequence[float], space: float, room: float, glue: Sequence[float] | None = None) -> list[int]:
    """How many words go on each line.

    `widths` are the words' widths and `space` the gap between two, in the same unit as `room`, the width a line may fill.
    `glue` says what a break after each word but the last costs: 8 is a word that leans on the next, and under 0 is welcome.
    """
    count = len(widths)
    if count == 0:
        return []
    room = max(float(room), 1.0)
    glue = [float(cost) for cost in (glue or [])][: count - 1]
    glue += [0.0] * (count - 1 - len(glue))
    run = [0.0]
    for width in widths:
        run.append(run[-1] + float(width))

    def wide(first: int, last: int) -> float:
        return run[last] - run[first] + space * (last - first - 1)

    rows, used = 1, 0.0
    for index in range(count):  # the fewest lines: fill each one, as a browser would
        length = float(widths[index])
        if used and used + space + length > room:
            rows, used = rows + 1, 0.0
        used += (space if used else 0.0) + length
    if rows == 1:
        return [count]
    mean = wide(0, count) / rows
    # best[r][j] is the cheapest way to set the first j words on r lines, and the word its last line starts on
    best: list[dict[int, tuple[float, int]]] = [{0: (0.0, 0)}]
    for row in range(1, rows + 1):
        best.append({})
        for last in range(row, count - (rows - row) + 1):
            for first, (before, _) in best[row - 1].items():
                if first >= last:
                    continue
                length = wide(first, last)
                cost = before + EVEN * ((length - mean) / room) ** 2 + TOO_WIDE * max(length - room, 0.0) / room
                if first:
                    cost += glue[first - 1]
                if row == rows and last - first == 1 and count >= 3:
                    cost += LONE_LAST
                if last not in best[row] or cost < best[row][last][0]:
                    best[row][last] = (cost, first)
    counts, last = [], count
    for row in range(rows, 0, -1):
        first = best[row][last][1]
        counts.append(last - first)
        last = first
    return counts[::-1]


def given(page: dict[str, Any]) -> list[int] | None:
    """The lines a page already carries, when they account for every word on it."""
    lines = page.get("lines")
    if not isinstance(lines, list) or not lines or not all(isinstance(n, int) and not isinstance(n, bool) and n > 0 for n in lines):
        return None
    return lines if sum(lines) == len(page["words"]) else None


def rows(counts: Sequence[int]) -> list[list[int]]:
    """Word counts per line as the indexes of the words on each line."""
    out, first = [], 0
    for count in counts:
        out.append(list(range(first, first + count)))
        first += count
    return out


@lru_cache(maxsize=32)
def face(path: Path, size: int, weight: int) -> ImageFont.FreeTypeFont:
    font = ImageFont.truetype(str(path), size)
    try:
        font.set_variation_by_axes([weight])
    except OSError:
        pass  # a face with one weight has no axis to set
    return font
