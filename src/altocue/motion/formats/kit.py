"""Small pieces the format modules share."""

from __future__ import annotations

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
