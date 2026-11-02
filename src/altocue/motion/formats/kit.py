"""Small pieces the format modules share."""

from __future__ import annotations

from collections.abc import Callable, Sequence
from html import escape
from typing import Any


def esc(text: Any) -> str:
    return escape(str(text))


def emphasis(text: str) -> str:
    """`*word*` in copy becomes the accent colour."""
    parts = esc(text).split("*")
    return "".join(f"<em>{part}</em>" if i % 2 else part for i, part in enumerate(parts))


def tokens(text: str) -> list[tuple[str, bool]]:
    """Words with their accent flag. A `*marked phrase*` may span words."""
    found: list[tuple[str, bool]] = []
    for index, part in enumerate(str(text).split("*")):
        found += [(word, bool(index % 2)) for word in part.split()]
    return found


def words(text: str, cls: str = "w") -> str:
    """One span per word. `--i` is the word's place, for stagger."""
    return " ".join(f'<span class="{cls}{" hot" if hot else ""}" style="--i:{i}">{esc(word)}</span>' for i, (word, hot) in enumerate(tokens(text)))


def letters(text: str, cls: str = "c") -> str:
    """One span per letter, grouped by word so lines still break between words."""
    out, index = [], 0
    for word, hot in tokens(text):
        cells = []
        for char in word:
            cells.append(f'<span class="{cls}" style="--i:{index}">{esc(char)}</span>')
            index += 1
        out.append(f'<span class="word{" hot" if hot else ""}">{"".join(cells)}</span>')
        index += 1
    return " ".join(out)


def rows(item: dict[str, Any], name: str) -> list[dict[str, Any]]:
    """A list slot as dicts, with `delay` in seconds from the graphic's start. Rows without a time spread evenly across the first half."""
    raw = item.get(name) or []
    span = max(float(item["end"]) - float(item["start"]), 0.1) * 0.5
    out = []
    for index, row in enumerate(raw):
        entry = dict(row) if isinstance(row, dict) else {"text": row}
        at = entry.get("at")
        entry["delay"] = max(float(at) - float(item["start"]), 0.0) if at is not None else span * index / max(len(raw), 1)
        out.append(entry)
    return out


def seconds(item: dict[str, Any]) -> float:
    return max(float(item["end"]) - float(item["start"]), 0.04)


def flow(times: Sequence[float], values: Sequence[float]) -> Callable[[float], float]:
    """A curve through key poses. Eased keys stop dead at every pose, and this one keeps its speed through them.

    It is a monotone cubic, after Fritsch and Carlson, so it never overshoots a pose and a held value stays held.
    Past either end it holds the last pose.
    """
    if len(times) != len(values) or len(times) < 2:
        raise ValueError("flow needs at least two poses, one time for each value")
    widths = [b - a for a, b in zip(times, times[1:], strict=False)]
    if min(widths) <= 0:
        raise ValueError("flow needs times that only rise")
    slopes = [(b - a) / width for a, b, width in zip(values, values[1:], widths, strict=False)]
    tangents = [slopes[0]]
    for before, after, width_a, width_b in zip(slopes, slopes[1:], widths, widths[1:], strict=False):
        if before * after <= 0:
            tangents.append(0.0)
            continue
        weight_a, weight_b = 2 * width_b + width_a, width_b + 2 * width_a
        tangents.append((weight_a + weight_b) / (weight_a / before + weight_b / after))  # a harmonic mean, which is what keeps it from overshooting
    tangents.append(slopes[-1])

    def at(time: float) -> float:
        if time <= times[0]:
            return float(values[0])
        if time >= times[-1]:
            return float(values[-1])
        index = next(i for i, width in enumerate(widths) if time <= times[i] + width)
        width = widths[index]
        share = (time - times[index]) / width
        share2, share3 = share * share, share * share * share
        return float(
            (2 * share3 - 3 * share2 + 1) * values[index]
            + (share3 - 2 * share2 + share) * width * tangents[index]
            + (3 * share2 - 2 * share3) * values[index + 1]
            + (share3 - share2) * width * tangents[index + 1]
        )

    return at


def flow_easing(times: Sequence[float], values: Sequence[float], steps: int = 32) -> str:
    """The same curve as a CSS `linear()` easing, for one element that passes through several poses in one animation."""
    start, end = times[0], times[-1]
    low, high = values[0], values[-1]
    if high == low:
        raise ValueError("A flow easing needs its last pose to differ from its first")
    curve = flow(times, values)
    points = (start + (end - start) * index / steps for index in range(steps + 1))
    return "linear(" + ",".join(f"{(curve(point) - low) / (high - low):.4f} {100 * index / steps:.2f}%" for index, point in enumerate(points)) + ")"
