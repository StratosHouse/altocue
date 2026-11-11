"""The change list between two versions, worked out from the files themselves.

Everything here is pure: plain dictionaries in, plain dictionaries out, no disk and no model. A summary an agent
writes can be wrong or kind to itself. This list cannot, because it is only what differs between two sets of
decision files: which words left the cut and where, which graphic moved, which setting changed.

Every `at` is seconds in the newer film, so a jump to a change lands on it. A removal has no place of its own in
the newer film, so its `at` is the join it left behind, and `was_at` is where it sat in the older film.
"""

from __future__ import annotations

import json
import re
from collections import Counter
from collections.abc import Callable
from dataclasses import dataclass, field
from typing import Any

SLIVER = 0.02  # seconds. Under a frame at 50 fps: rounding between two saves, never an edit
TRIM_UNDER = 0.5  # with no transcript to ask, a change this short is an edge that moved, not a line that went
MOST_PER_FILE = 30  # changes listed for one file before the rest are only counted
LONGEST_VALUE = 160  # characters of one old or new value kept in a record
NUMBER_SLACK = 1e-3  # two crops or gains closer than this are the same number written twice
COMMENT = re.compile(r"<!--.*?-->", re.DOTALL)
LINE_TAG = re.compile(r"^\[L\d+[^\]]*\]\s*")
TEXT_KEYS = ("text", "headline", "title", "value", "label", "name")
SHOT_FIELDS = {"zoom_from": 1.0, "zoom_to": 1.0, "gain": 1.0, "crop": None, "path": None}
SECTIONS = ("cuts", "shots", "graphics", "inserts", "transitions", "overlays", "music", "sfx", "settings", "script", "files")
PLACED = {"graphics": "graphic", "inserts": "insert", "transitions": "transition", "overlays": "overlay", "music": "music", "sfx": "sound effect"}
READ_BY = {
    "edl.json": ("cuts", "shots"),
    "motion.json": ("graphics", "inserts", "transitions", "overlays", "music", "sfx"),
    "broll.json": ("inserts",),
    "altocue.toml": ("settings",),
    "script.md": ("script",),
}

Words = dict[str, list[dict[str, Any]]]  # each clip's words in the clip's own seconds: text, start, end
Spans = dict[str, list[tuple[float, float]]]


@dataclass
class Snapshot:
    """One version's decision files, already read. A file that is missing or cannot be read is None."""

    edl: dict[str, Any] | None = None
    motion: dict[str, Any] | None = None
    broll: dict[str, Any] | None = None
    config: dict[str, Any] | None = None
    script: str | None = None
    others: dict[str, Any] = field(default_factory=dict)  # every other decision file by path: parsed JSON, or its text
    hashes: dict[str, str] = field(default_factory=dict)  # so a file that changed in a way no reader names is still listed
    duration: float | None = None  # the render's length, when there was one


@dataclass(frozen=True)
class Piece:
    """A stretch of one clip and where it plays in a film."""

    clip: str
    start: float
    end: float
    at: float

    @property
    def seconds(self) -> float:
        return self.end - self.start


@dataclass(frozen=True)
class Common:
    """A stretch both films play, with where it sits in each."""

    clip: str
    start: float
    end: float
    was_at: float
    at: float

    @property
    def seconds(self) -> float:
        return self.end - self.start


# Small helpers


def canon(value: Any) -> str:
    return json.dumps(value, sort_keys=True, ensure_ascii=False, default=str)


def brief(value: Any) -> Any:
    """A value small enough to keep in a record. A long one is kept as the start of its text."""
    text = canon(value)
    return value if len(text) <= LONGEST_VALUE else text[: LONGEST_VALUE - 3] + "..."


def same(a: Any, b: Any) -> bool:
    """Equal, with numbers allowed the difference a round trip through a file makes."""
    if isinstance(a, bool) or isinstance(b, bool):
        return a is b
    if isinstance(a, int | float) and isinstance(b, int | float):
        return abs(a - b) <= NUMBER_SLACK * max(1.0, abs(a), abs(b))
    if isinstance(a, list) and isinstance(b, list):
        return len(a) == len(b) and all(same(x, y) for x, y in zip(a, b, strict=True))
    if isinstance(a, dict) and isinstance(b, dict):
        return a.keys() == b.keys() and all(same(a[key], b[key]) for key in a)
    return a == b


def stamp(seconds: float) -> str:
    tenths = int(max(seconds, 0.0) * 10 + 0.5)  # halves go up, as they do in the page's own script, so the two never disagree
    return f"{tenths // 600}:{tenths % 600 / 10:04.1f}"


def clipped(text: str, most: int = 90) -> str:
    text = " ".join(str(text).split())
    return text if len(text) <= most else text[: most - 3].rstrip() + "..."


def shown(value: Any) -> str:
    """One old or new value as a person would write it."""
    if value is None:
        return "not set"
    return f'"{clipped(value, 60)}"' if isinstance(value, str) else clipped(canon(value), 60)


def describe(item: dict[str, Any]) -> str:
    """The words a person would know a graphic, a sound or a file by."""
    for key in TEXT_KEYS:
        value = item.get(key)
        if isinstance(value, str) and value.strip():
            return " ".join(value.replace("*", "").split())
    return ""


def bare(word: str) -> str:
    return word.replace("’", "'").strip(" .,!?;:\"'").lower()


# The cut


def readable(edl: dict[str, Any] | None) -> list[tuple[dict[str, Any], Piece]]:
    """Each kept range with where it starts in the film. A segment that cannot be read is skipped, never guessed."""
    found: list[tuple[dict[str, Any], Piece]] = []
    at = 0.0
    for segment in (edl or {}).get("segments") or []:
        try:
            clip, start, end = str(segment["clip"]), float(segment["start"]), float(segment["end"])
        except (KeyError, TypeError, ValueError):
            continue
        if end > start:
            found.append((segment, Piece(clip, start, end, at)))
            at += end - start
    return found


def layout(edl: dict[str, Any] | None) -> list[Piece]:
    return [piece for _, piece in readable(edl)]


def length(pieces: list[Piece]) -> float:
    return sum(piece.seconds for piece in pieces)


def kept(pieces: list[Piece]) -> Spans:
    """What each clip plays, as merged ranges in the clip's own seconds."""
    spans: Spans = {}
    for piece in sorted(pieces, key=lambda p: (p.clip, p.start)):
        mine = spans.setdefault(piece.clip, [])
        if mine and piece.start <= mine[-1][1] + SLIVER:
            mine[-1] = (mine[-1][0], max(mine[-1][1], piece.end))
        else:
            mine.append((piece.start, piece.end))
    return spans


def minus(start: float, end: float, taken: list[tuple[float, float]]) -> list[tuple[float, float]]:
    """What is left of one range once the taken ranges are out. Slivers are dropped."""
    left = []
    at = start
    for a, b in taken:
        if b <= at or a >= end:
            continue
        if a - at > SLIVER:
            left.append((at, a))
        at = max(at, b)
    if end - at > SLIVER:
        left.append((at, end))
    return left


def only_in(pieces: list[Piece], other: Spans) -> list[list[Piece]]:
    """Runs of one film that the other cut does not play. Neighbours in the film join into one run."""
    runs: list[list[Piece]] = []
    for piece in pieces:
        for a, b in minus(piece.start, piece.end, other.get(piece.clip, [])):
            part = Piece(piece.clip, a, b, piece.at + a - piece.start)
            if runs and abs(runs[-1][-1].at + runs[-1][-1].seconds - part.at) < SLIVER:
                runs[-1].append(part)
            else:
                runs.append([part])
    return runs


def in_both(was: list[Piece], now: list[Piece]) -> list[Common]:
    """Every stretch both films play, in the newer film's order."""
    found = []
    for new in now:
        for old in was:
            a, b = max(old.start, new.start), min(old.end, new.end)
            if old.clip == new.clip and b - a > SLIVER:
                found.append(Common(new.clip, a, b, old.at + a - old.start, new.at + a - new.start))
    return sorted(found, key=lambda part: part.at)


def stayed(parts: list[Common]) -> set[int]:
    """Which shared stretches kept their order: the most film, by seconds, that plays in the same order in both.

    Everything outside that run is what moved. Weighing by seconds means a line carried across the film is the
    thing that moved, not the rest of the film around it.
    """
    best = [part.seconds for part in parts]
    before = [-1] * len(parts)
    for index, part in enumerate(parts):
        for earlier in range(index):
            if parts[earlier].was_at < part.was_at and best[earlier] + part.seconds > best[index]:
                best[index], before[index] = best[earlier] + part.seconds, earlier
    chosen: set[int] = set()
    index = max(range(len(parts)), key=lambda i: best[i], default=-1)
    while index >= 0:
        chosen.add(index)
        index = before[index]
    return chosen


def said(words: Words, clip: str, start: float, end: float) -> str | None:
    """The words whose middle falls in a range. None when this clip has no word file, which is not the same as silence."""
    if clip not in words:
        return None
    return " ".join(str(word["text"]) for word in words[clip] if start <= (float(word["start"]) + float(word["end"])) / 2 < end)


def cut_entry(kind: str, run: list[Piece] | list[Common], at: float, words: Words) -> dict[str, Any]:
    """One change to the cut. A stretch with no words in it is a trim, whichever way the edge moved."""
    heard = [said(words, part.clip, part.start, part.end) for part in run]
    seconds = sum(part.seconds for part in run)
    entry: dict[str, Any] = {
        "kind": kind,
        "at": round(max(at, 0.0), 3),
        "seconds": round(seconds, 3),
        "words": " ".join(text for text in heard if text),
        "source": [[part.clip, round(part.start, 3), round(part.end, 3)] for part in run],
    }
    known = all(text is not None for text in heard)
    if kind in ("removed", "added") and ((known and not entry["words"]) or (not known and seconds < TRIM_UNDER)):
        entry["kind"] = "trimmed"
        entry["change"] = "shorter" if kind == "removed" else "longer"
    return entry


def cut_changes(was: list[Piece], now: list[Piece], words: Words) -> list[dict[str, Any]]:
    """What left the cut, what came in, what moved and which edges were trimmed."""
    shared = in_both(was, now)
    still = stayed(shared)
    in_order = [part for index, part in enumerate(shared) if index in still]
    changes = []
    for run in only_in(was, kept(now)):
        before = max((part for part in in_order if part.was_at + part.seconds <= run[0].at + SLIVER), key=lambda part: part.was_at, default=None)
        join = before.at + before.seconds if before else 0.0  # the newer film's side of the hole: the end of the last thing still in place before it
        changes.append({**cut_entry("removed", run, join, words), "was_at": round(run[0].at, 3)})
    for run in only_in(now, kept(was)):
        changes.append(cut_entry("added", run, run[0].at, words))
    moved: list[list[Common]] = []
    for index, part in enumerate(shared):
        if index in still:
            continue
        last = moved[-1][-1] if moved else None
        if last and abs(last.at + last.seconds - part.at) < SLIVER and abs(last.was_at + last.seconds - part.was_at) < SLIVER:
            moved[-1].append(part)
        else:
            moved.append([part])
    for group in moved:
        changes.append({**cut_entry("moved", group, group[0].at, words), "was_at": round(group[0].was_at, 3)})
    return sorted(changes, key=lambda entry: (entry["at"], entry["kind"]))


def shot_changes(old: dict[str, Any] | None, new: dict[str, Any] | None) -> list[dict[str, Any]]:
    """Kept material whose crop, zoom or gain changed. A shot is the same shot when most of it is the same source."""
    before = readable(old)
    changes = []
    for segment, piece in readable(new):
        overlaps = [(min(was.end, piece.end) - max(was.start, piece.start), index) for index, (_, was) in enumerate(before) if was.clip == piece.clip]
        most, index = max(overlaps, default=(0.0, -1))
        if index < 0 or most < piece.seconds / 2:
            continue
        earlier = before[index][0]
        fields = [
            {"field": name, "old": brief(earlier.get(name, default)), "new": brief(segment.get(name, default))}
            for name, default in SHOT_FIELDS.items()
            if not same(earlier.get(name, default), segment.get(name, default))
        ]
        if fields:
            changes.append({"kind": "changed", "at": round(piece.at, 3), "seconds": round(piece.seconds, 3), "fields": fields})
    return changes


# Times that follow speech


def film_words(pieces: list[Piece], words: Words) -> list[dict[str, Any]]:
    """The kept words with their place in the film, so an item anchored to a word can be given a time."""
    heard = []
    for piece in pieces:
        for word in words.get(piece.clip, []):
            start, end = float(word["start"]), float(word["end"])
            if piece.start <= (start + end) / 2 < piece.end:
                heard.append({"text": str(word["text"]), "start": piece.at + max(start - piece.start, 0.0)})
    return heard


def time_of(value: Any, heard: list[dict[str, Any]]) -> float | None:
    """Seconds, or a `{"word": ...}` anchor found in the film's words. None when it cannot be placed, never a guess."""
    if isinstance(value, bool):
        return None
    if isinstance(value, int | float):
        return round(float(value), 3)
    if not isinstance(value, dict) or "word" not in value:
        return None
    hits = [word for word in heard if bare(word["text"]) == bare(str(value["word"]))]
    try:
        occurrence, offset = int(value.get("occurrence", 1)), float(value.get("offset", 0))
    except (TypeError, ValueError):
        return None
    return round(hits[occurrence - 1]["start"] + offset, 3) if 1 <= occurrence <= len(hits) else None


# Lists of things placed on the film


def dicts(value: Any) -> list[dict[str, Any]]:
    """A list of entries, however the file wrote them: one entry, a list, a bare name or nothing."""
    if value is None or value is False:
        return []
    if value is True:
        return [{"type": "on"}]
    rows = value if isinstance(value, list) else [value]
    return [row if isinstance(row, dict) else {"type": str(row)} for row in rows]


def pair(old: list[dict[str, Any]], new: list[dict[str, Any]], keys: list[Callable[[dict[str, Any]], Any]]) -> tuple[list[tuple[dict, dict]], list[dict], list[dict]]:
    """What changed, what went and what is new. Exact copies pair off first and say nothing, then each key in turn pairs what is left."""
    gone, fresh = list(old), list(new)
    for item in list(fresh):
        match = next((index for index, other in enumerate(gone) if canon(other) == canon(item)), None)
        if match is not None:
            gone.pop(match)
            fresh.pop(next(index for index, other in enumerate(fresh) if other is item))
    changed = []
    for key in keys:
        for item in list(fresh):
            wanted = key(item)
            match = next((index for index, other in enumerate(gone) if wanted is not None and key(other) == wanted), None)
            if match is not None:
                changed.append((gone.pop(match), item))
                fresh.pop(next(index for index, other in enumerate(fresh) if other is item))
    return changed, gone, fresh


def fields_of(old: dict[str, Any], new: dict[str, Any]) -> list[dict[str, Any]]:
    return [{"field": key, "old": brief(old.get(key)), "new": brief(new.get(key))} for key in sorted(set(old) | set(new)) if canon(old.get(key)) != canon(new.get(key))]


def placed_changes(
    old: list[dict[str, Any]],
    new: list[dict[str, Any]],
    *,
    name: Callable[[dict[str, Any]], tuple[str, str]],
    keys: list[Callable[[dict[str, Any]], Any]],
    was_when: Callable[[dict[str, Any]], float | None],
    when: Callable[[dict[str, Any]], float | None],
) -> list[dict[str, Any]]:
    """Added, removed and changed entries of one list, each with its type, its words and its time in each film."""
    changed, gone, fresh = pair(old, new, keys)
    rows = []
    for item in fresh:
        kind, text = name(item)
        rows.append({"kind": "added", "type": kind, "text": text, "at": when(item)})
    for item in gone:
        kind, text = name(item)
        rows.append({"kind": "removed", "type": kind, "text": text, "at": None, "was_at": was_when(item)})
    for before, after in changed:
        kind, text = name(after)
        rows.append({"kind": "changed", "type": kind, "text": text, "at": when(after), "was_at": was_when(before), "fields": fields_of(before, after)})
    return sorted(rows, key=lambda row: (row["at"] if row["at"] is not None else row.get("was_at") or 0.0, row["kind"]))


def sfx_of(raw: Any) -> tuple[bool, list[dict[str, Any]]]:
    """Whether the house sounds play, and the project's own cues. The same reading `timeline.resolve_sfx` makes."""
    if raw is None or isinstance(raw, bool):
        return raw is not False, []
    if isinstance(raw, dict):
        return bool(raw.get("house", True)), dicts(raw.get("cues"))
    return True, dicts(raw)


# Settings, scripts and every other file


def flat(config: dict[str, Any] | None, prefix: str = "") -> dict[str, Any]:
    """`section.key` for every setting, however deep its table sits."""
    found: dict[str, Any] = {}
    for key, value in (config or {}).items():
        found.update(flat(value, f"{prefix}{key}.") if isinstance(value, dict) else {f"{prefix}{key}": value})
    return found


def setting_changes(old: dict[str, Any] | None, new: dict[str, Any] | None) -> list[dict[str, Any]]:
    before, after = flat(old), flat(new)
    changed = [key for key in sorted(set(before) | set(after)) if canon(before.get(key)) != canon(after.get(key))]
    return [{"key": key, "old": brief(before.get(key)), "new": brief(after.get(key))} for key in changed]


def text_lines(text: str | None, *, tags: bool = False) -> list[str]:
    """The lines a person wrote. Comments go, and so does a script line's `[L12]` tag, which is a place and not a word."""
    lines = [" ".join(line.split()) for line in COMMENT.sub("", text or "").splitlines()]
    return [LINE_TAG.sub("", line) if tags else line for line in lines if line]


def line_changes(old: str | None, new: str | None, *, tags: bool = False) -> list[dict[str, Any]]:
    """Lines only one side has. A line that only moved is in both, so it is not listed."""
    before, after = text_lines(old, tags=tags), text_lines(new, tags=tags)
    extra, missing = Counter(after) - Counter(before), Counter(before) - Counter(after)
    rows = []
    for kind, lines, left in (("removed", before, missing), ("added", after, extra)):
        for line in lines:
            if left[line] > 0:
                left[line] -= 1
                rows.append({"kind": kind, "text": line})
    return rows


def json_changes(old: Any, new: Any, key: str = "") -> list[dict[str, Any]]:
    """Every leaf that differs between two JSON values, named by its path."""
    if isinstance(old, dict) and isinstance(new, dict):
        return [row for name in sorted(set(old) | set(new)) for row in json_changes(old.get(name), new.get(name), f"{key}.{name}" if key else str(name))]
    if isinstance(old, list) and isinstance(new, list) and any(isinstance(row, dict | list) for row in old + new):
        pairs = [(old[index] if index < len(old) else None, new[index] if index < len(new) else None) for index in range(max(len(old), len(new)))]
        return [row for index, (before, after) in enumerate(pairs) for row in json_changes(before, after, f"{key}[{index}]")]
    return [] if canon(old) == canon(new) else [{"key": key or "(whole file)", "old": brief(old), "new": brief(new)}]


def file_changes(old: Snapshot, new: Snapshot, found: dict[str, Any]) -> list[dict[str, Any]]:
    """Every other decision file, change by change, and any file that changed in a way no reader above names."""
    rows: list[dict[str, Any]] = []
    for path in sorted(set(old.others) | set(new.others)):
        before, after = old.others.get(path), new.others.get(path)
        if before is None or after is None:
            rows += [{"path": path, "kind": "added" if before is None else "removed"}] if before is not after else []
            continue
        if isinstance(before, str) or isinstance(after, str):
            mine = [{"path": path, "kind": "changed", "line": row["kind"], "text": row["text"]} for row in line_changes(str(before), str(after))]
        else:
            mine = [{"path": path, "kind": "changed", **row} for row in json_changes(before, after)]
        rows += mine[:MOST_PER_FILE] + ([{"path": path, "kind": "changed", "more": len(mine) - MOST_PER_FILE}] if len(mine) > MOST_PER_FILE else [])
    named = {row["path"] for row in rows}
    for path in sorted(set(old.hashes) | set(new.hashes)):
        if old.hashes.get(path) == new.hashes.get(path) or path in named:
            continue
        if path not in old.hashes or path not in new.hashes:
            if path in READ_BY and any(found[section] for section in READ_BY[path]):
                continue
            rows.append({"path": path, "kind": "added" if path not in old.hashes else "removed"})
        elif not any(found[section] for section in READ_BY.get(path, ())):
            rows.append({"path": path, "kind": "changed", "quiet": True})
    return rows


# The whole comparison


def duration_change(old: Snapshot, new: Snapshot, was: list[Piece], now: list[Piece]) -> dict[str, Any] | None:
    """The films' own lengths when both were rendered, else the cuts'. None when neither can be read."""
    if old.duration is not None and new.duration is not None:
        before, after, source = old.duration, new.duration, "render"
    elif was and now:
        before, after, source = length(was), length(now), "cut"
    else:
        return None
    return {"old": round(before, 3), "new": round(after, 3), "change": round(after - before, 3), "from": source}


def compare(old: Snapshot, new: Snapshot, words: Words | None = None) -> dict[str, Any]:
    """The whole change list from one version to the next. `words` names what was said; without it a cut is named by its source seconds."""
    words = words or {}
    was, now = layout(old.edl), layout(new.edl)
    heard_was, heard_now = film_words(was, words), film_words(now, words)
    before, after = old.motion or {}, new.motion or {}

    def at_key(key: str, default: Any = None) -> tuple[Callable[[dict[str, Any]], float | None], Callable[[dict[str, Any]], float | None]]:
        return (lambda item: time_of(item.get(key, default), heard_was)), (lambda item: time_of(item.get(key, default), heard_now))

    def graphic_time(heard: list[dict[str, Any]], total: float) -> Callable[[dict[str, Any]], float | None]:
        return lambda item: round(total, 3) if item.get("type") == "endcard" else time_of(item.get("start", 0), heard)

    def kind_of(default: str) -> Callable[[dict[str, Any]], tuple[str, str]]:
        return lambda item: (str(item.get("type") or default), describe(item))

    def source_of(item: dict[str, Any]) -> str:
        return str(item.get("file") or item.get("shot") or item.get("path") or item.get("name") or "")

    was_start, start = at_key("start")
    was_at, at = at_key("at")
    house_was, cues_was = sfx_of(before.get("sfx"))
    house_now, cues_now = sfx_of(after.get("sfx"))
    found: dict[str, Any] = {
        "duration": duration_change(old, new, was, now),
        "cuts": cut_changes(was, now, words),
        "shots": shot_changes(old.edl, new.edl),
        "graphics": placed_changes(
            dicts(before.get("items")),
            dicts(after.get("items")),
            name=kind_of("graphic"),
            keys=[lambda item: (item.get("type"), describe(item)), lambda item: (item.get("type"), canon(item.get("start")))],
            was_when=graphic_time(heard_was, length(was)),
            when=graphic_time(heard_now, length(now)),
        ),
        "inserts": placed_changes(
            dicts(before.get("inserts")) + dicts((old.broll or {}).get("inserts")),
            dicts(after.get("inserts")) + dicts((new.broll or {}).get("inserts")),
            name=lambda item: ("insert", source_of(item)),
            keys=[source_of, lambda item: canon(item.get("start"))],
            was_when=was_start,
            when=start,
        ),
        "transitions": placed_changes(
            dicts(before.get("transitions")) + [{"type": "freeze", **row} for row in dicts(before.get("freezes"))],
            dicts(after.get("transitions")) + [{"type": "freeze", **row} for row in dicts(after.get("freezes"))],
            name=lambda item: (str(item.get("type") or "transition"), ""),
            keys=[lambda item: (item.get("type"), canon(item.get("at"))), lambda item: canon(item.get("at")), lambda item: item.get("type")],
            was_when=was_at,
            when=at,
        ),
        "overlays": placed_changes(
            dicts(before.get("overlay")) + dicts(before.get("tracked")),
            dicts(after.get("overlay")) + dicts(after.get("tracked")),
            name=kind_of("overlay"),
            keys=[lambda item: (item.get("type"), canon(item.get("target"))), lambda item: item.get("type")],
            was_when=lambda item: time_of(item.get("start"), heard_was),
            when=lambda item: time_of(item.get("start"), heard_now),
        ),
        "music": placed_changes(
            [{"path": row["type"]} if set(row) == {"type"} else row for row in dicts(before.get("music"))],
            [{"path": row["type"]} if set(row) == {"type"} else row for row in dicts(after.get("music"))],
            name=lambda item: ("music", source_of(item)),
            keys=[lambda item: "music"],
            was_when=lambda item: None,
            when=lambda item: None,
        ),
        "sfx": placed_changes(
            cues_was,
            cues_now,
            name=lambda item: ("sound effect", source_of(item)),
            keys=[lambda item: (source_of(item), canon(item.get("at"))), source_of],
            was_when=was_at,
            when=at,
        ),
        "settings": setting_changes(old.config, new.config),
        "script": line_changes(old.script, new.script, tags=True),
    }
    if house_was != house_now:
        turned = "added" if house_now else "removed"  # one switch for every house sound, so it reads as they came or went
        found["sfx"].append({"kind": turned, "type": "house sounds", "text": "", "at": None, "was_at": None, "fields": []})
    found["files"] = file_changes(old, new, found)
    found["count"] = sum(len(found[section]) for section in SECTIONS)
    return found


# Saying it in plain lines


def _fields(entry: dict[str, Any]) -> str:
    return ", ".join(f"{row['field']} {shown(row['old'])} to {shown(row['new'])}" for row in entry.get("fields", []))


def _cut_text(entry: dict[str, Any]) -> str:
    seconds = f"{entry['seconds']:.1f} s"
    where = ", ".join(f"{clip} {start:.2f} to {end:.2f} s" for clip, start, end in entry.get("source", [])[:2])
    quote = f': "{clipped(entry["words"])}"' if entry.get("words") else (f", source {where}" if where else "")
    if entry["kind"] == "trimmed":
        return f"trimmed {seconds} {entry.get('change', 'shorter')}, no words in it" + (f", source {where}" if where else "")
    if entry["kind"] == "moved":
        return f"moved {seconds} from {stamp(entry.get('was_at', 0.0))}{quote}"
    return f"{entry['kind']} {seconds}{quote}"


def _placed_text(what: str, entry: dict[str, Any]) -> str:
    kind = entry["type"] if entry["type"] != what else ""
    named = " ".join(part for part in (kind, f'"{clipped(entry["text"], 60)}"' if entry["text"] else "") if part)
    text = ": ".join(part for part in (f"{what} {entry['kind']}", named, _fields(entry)) if part)
    return text + (f", was at {stamp(entry['was_at'])}" if entry["kind"] == "removed" and entry.get("was_at") is not None else "")


def _file_text(entry: dict[str, Any]) -> str:
    path = entry["path"]
    if entry["kind"] != "changed":
        return f"{path} {entry['kind']}"
    if entry.get("quiet"):
        return f"{path} changed, in a way that names no change to the film"
    if "more" in entry:
        return f"{path}: {entry['more']} more changes"
    if "line" in entry:
        return f'{path} line {entry["line"]}: "{clipped(entry["text"])}"'
    return f"{path} {entry['key']}: {shown(entry['old'])} to {shown(entry['new'])}"


def rows(diff: dict[str, Any]) -> list[dict[str, Any]]:
    """Every change as one row to print or to click: its section, its text, and its time in each film when it has one."""
    found: list[dict[str, Any]] = []

    def add(section: str, text: str, entry: dict[str, Any] | None = None) -> None:
        entry = entry or {}
        found.append({"section": section, "at": entry.get("at"), "was_at": entry.get("was_at"), "text": text})

    length = diff.get("duration")
    if length and abs(length["change"]) >= 0.05:
        way = "shorter" if length["change"] < 0 else "longer"
        add("duration", f"film {stamp(length['old'])} to {stamp(length['new'])}, {abs(length['change']):.1f} s {way}")
    for entry in diff.get("cuts", []):
        add("cuts", _cut_text(entry), entry)
    for entry in diff.get("shots", []):
        add("shots", f"shot changed: {_fields(entry)}", entry)
    for section, what in PLACED.items():
        for entry in diff.get(section, []):
            add(section, _placed_text(what, entry), entry)
    for entry in diff.get("settings", []):
        add("settings", f"setting {entry['key']}: {shown(entry['old'])} to {shown(entry['new'])}")
    for entry in diff.get("script", []):
        add("script", f'script line {entry["kind"]}: "{clipped(entry["text"])}"')
    for entry in diff.get("files", []):
        add("files", _file_text(entry))
    return found


def lines(diff: dict[str, Any]) -> list[str]:
    """The change list as plain lines, such as `0:12.4 removed 1.8 s: "for an experimentation process"`."""
    return [f"{stamp(row['at'])} {row['text']}" if row["at"] is not None else row["text"] for row in rows(diff)]
