"""Pieces made for films with no footage: a board that does the work, a gauge that carries the plot, and small things that keep a frame alive."""

from __future__ import annotations

import functools
import json
import math
import random
from typing import Any

from ...brand import data_path
from . import Format, Slot, kit
from .product import _scoped as scoped

TICK = '<svg viewBox="0 0 24 24" aria-hidden="true"><path pathLength="1" d="M5 12.8l4.6 4.7L19 7.4"/></svg>'
COUNT_STEPS = 18
TRACK_SAMPLES = 24
SILHOUETTE_POSES = ("walk", "wave", "point")
SAY_GAP = 0.16  # seconds between words as a line comes up


def _count(start: int, end: int, begin: float, finish: float) -> str:
    """A counter as a stack of values, each shown for its slice of time. It eases down and lands exactly as the last card ticks."""
    span = max(finish - begin, 0.1)
    direction = 1 if end >= start else -1
    values: list[tuple[int, float]] = []
    for step in range(COUNT_STEPS + 1):
        share = step / COUNT_STEPS
        value = round(start + (end - start) * (1 - (1 - share) ** 1.6))
        if step < COUNT_STEPS and (end - value) * direction <= 0:
            value = end - direction  # the last value belongs to the last moment
        if not values or value != values[-1][0]:
            values.append((value, begin + span * share if step else 0.0))
    cells = []
    for index, (value, at) in enumerate(values):
        leave = values[index + 1][1] if index + 1 < len(values) else 99999.0
        cells.append(f'<b class="n" style="--t:{at:.3f}s;--o:{leave:.3f}s">{value:,}</b>')
    return f'<span class="ct" data-layout-allow-overlap>{"".join(cells)}</span>'


def _work_cascade(item: dict[str, Any]) -> str:
    rows = kit.rows(item, "rows")
    seconds = kit.seconds(item)
    count = max(len(rows), 1)
    begin, finish = 0.5, seconds * 0.72
    gap = (finish - begin) / (count + 0.6)
    each = gap * 1.6
    start = int(float(item.get("from", 412)))
    end = int(float(item.get("to", 0)))
    cells = []
    for index, row in enumerate(rows):
        at = begin + index * gap
        chip = f'<em class="tag" data-layout-ignore>{kit.esc(row.get("label", ""))}</em>' if row.get("label") else ""
        cells.append(
            f'<div class="row" style="--i:{index};--t:{at:.3f}s;--n:{at + each:.3f}s"><i class="st"><u></u><s>{TICK}</s></i><span class="txt">{kit.emphasis(str(row.get("text", "")))}</span>{chip}<b class="bar"></b></div>'
        )
    title = kit.emphasis(str(item.get("title", "")))
    done = f'<p class="clear" style="--t:{finish + 0.25:.3f}s">{kit.emphasis(str(item["done"]))}</p>' if item.get("done") else ""
    return (
        f'<div class="board"><header><strong>{title}</strong><span class="pill" style="--t:{finish:.3f}s"><i></i>{_count(start, end, 0.4, finish)}<small>{kit.esc(item.get("label", ""))}</small></span></header>'
        f'<div class="list">{"".join(cells)}</div>{done}</div>'
    )


def _state_gauge(item: dict[str, Any]) -> str:
    """Rows of values that change on cue. Write the table of states first, and the film follows it."""
    seconds = kit.seconds(item)
    made = []
    for index, row in enumerate(item.get("rows") or []):
        states = row.get("states") or [{"value": row.get("value", ""), "level": row.get("level", 0)}]
        total = max(len(states), 1)
        cells, fills = [], []
        previous = float(states[0].get("level", 0))
        for number, state in enumerate(states):
            at = state.get("at")
            start = float(at) - float(item["start"]) if at is not None else seconds * number / total
            after = states[number + 1] if number + 1 < total else None
            stop = (float(after["at"]) - float(item["start"]) if after.get("at") is not None else seconds * (number + 1) / total) if after else 9999.0
            level = float(state.get("level", previous))
            cells.append(f'<b class="v" style="--t:{start:.3f}s;--o:{stop:.3f}s">{kit.esc(state.get("value", ""))}</b>')
            fills.append(f'<i class="fill{" hi" if state.get("hot") else ""}" style="--t:{start:.3f}s;--o:{stop:.3f}s;--a:{previous:.3f};--b:{level:.3f}"></i>')
            previous = level
        made.append(
            f'<div class="rw" style="--i:{index}"><span class="lb">{kit.esc(row.get("text", ""))}</span><span class="vs">{"".join(cells)}</span><div class="mt">{"".join(fills)}</div></div>'
        )
    title = f"<h3>{kit.esc(item['title'])}</h3>" if item.get("title") else ""
    note = f'<p class="nt">{kit.esc(item["note"])}</p>' if item.get("note") else ""
    return f'<div class="gauge">{title}{"".join(made)}{note}</div>'


def _say_line(item: dict[str, Any]) -> str:
    """The words come up at a speaking pace, then hold, so a viewer with the sound off is carried along."""
    count = max(len(kit.tokens(item["text"])), 1)
    gap = min(SAY_GAP, kit.seconds(item) * 0.3 / count)
    return f'<p class="say" style="--gap:{gap:.3f}s">{kit.words(item["text"])}</p>'


def _corner_stamp(item: dict[str, Any]) -> str:
    corner = item.get("corner", "tl")
    label = f'<span class="lb" data-layout-ignore>{kit.esc(item["label"])}</span>' if item.get("label") else ""
    return f'<div class="stamp {kit.esc(corner)}"><i></i><span class="tx">{kit.letters(str(item["text"]))}</span>{label}</div>'


def _tracking_marks(item: dict[str, Any]) -> str:
    """Small squares fixed to moving points, joined by thin lines. Every position and angle comes from the same paths, so a line never leaves its points."""
    rng = random.Random(int(item.get("seed", 5)))
    labels = list(item.get("labels") or [])
    count = max(len(labels), 3)
    points = []
    for _ in range(count):
        points.append((rng.uniform(16, 84), rng.uniform(18, 82), rng.uniform(3, 7), rng.uniform(2, 6), rng.uniform(0, 6.28), rng.uniform(0.6, 1.2)))
    name = f"tm{int(item.get('seed', 5))}"
    span = max(kit.seconds(item), 0.5)

    def spot(point: tuple[float, ...], share: float) -> tuple[float, float]:
        x, y, reach_x, reach_y, phase, speed = point
        turn = phase + share * span * speed
        return x + reach_x * math.sin(turn), y + reach_y * math.cos(turn * 0.8)

    rules, marks = [], []
    for index, point in enumerate(points):
        steps = "".join(
            f"{100 * s / TRACK_SAMPLES:.1f}%{{left:{spot(point, s / TRACK_SAMPLES)[0]:.3f}%;top:{spot(point, s / TRACK_SAMPLES)[1]:.3f}%}}"
            for s in range(TRACK_SAMPLES + 1)
        )
        rules.append(f"@keyframes {name}p{index}{{{steps}}}")
        text = f"<em>{kit.esc(labels[index])}</em>" if index < len(labels) else ""
        marks.append(f'<div class="mk" style="animation:{name}p{index} {span:.3f}s linear both"><i></i>{text}</div>')
    lines = []
    for index in range(count - 1):
        a, b = points[index], points[index + 1]
        steps = []
        for s in range(TRACK_SAMPLES + 1):
            ax, ay = spot(a, s / TRACK_SAMPLES)
            bx, by = spot(b, s / TRACK_SAMPLES)
            steps.append(
                f"{100 * s / TRACK_SAMPLES:.1f}%{{clip-path:polygon({ax:.3f}% {ay:.3f}%,{bx:.3f}% {by:.3f}%,{bx:.3f}% {by + 0.14:.3f}%,{ax:.3f}% {ay + 0.14:.3f}%)}}"
            )
        rules.append(f"@keyframes {name}l{index}{{{''.join(steps)}}}")
        lines.append(f'<div class="ln" style="animation:{name}l{index} {span:.3f}s linear both"></div>')
    return f"<style>{''.join(rules)}</style>" + "".join(lines) + "".join(marks)


def _silhouette_act(item: dict[str, Any]) -> str:
    pose = item.get("pose", "walk") if item.get("pose") in SILHOUETTE_POSES else "walk"
    caption = f'<p class="line">{kit.emphasis(str(item["text"]))}</p>' if item.get("text") else ""
    body = (
        '<div class="fig"><div class="hips"><div class="trunk"><div class="head"></div>'
        '<div class="arm a1"><div class="fore"></div></div><div class="arm a2"><div class="fore"></div></div></div>'
        '<div class="leg l1"><div class="shin"></div></div><div class="leg l2"><div class="shin"></div></div></div></div>'
    )
    return f'<div class="stagefield {pose}"><div class="floor"></div>{body}{caption}</div>'


def _stage_strip(item: dict[str, Any]) -> str:
    """Real pictures in a row, each landing in turn with its name. Stage icons, or frames from a real run."""
    rows = kit.rows(item, "stages")
    seconds = kit.seconds(item)
    gap = min(seconds * 0.5 / max(len(rows), 1), 0.45)
    cells = []
    for index, row in enumerate(rows):
        picture = f'<img src="{kit.esc(row["image"])}" alt="">' if row.get("image") else ""
        cells.append(f'<figure style="--t:{0.2 + index * gap:.3f}s">{picture}<figcaption>{kit.esc(row.get("text", ""))}</figcaption></figure>')
    title = f"<h2>{kit.emphasis(str(item['title']))}</h2>" if item.get("title") else ""
    return f'{title}<div class="strip {kit.esc(item.get("size", "icons"))}">{"".join(cells)}</div>'


def _split_wipe(item: dict[str, Any]) -> str:
    """One real frame, before and after, with a divider that sweeps across and back so the change is seen, not claimed."""
    rows = kit.rows(item, "frames")
    before, after = rows[0], rows[-1]
    return (
        f'<div class="pair"><img class="under" src="{kit.esc(before.get("image", ""))}" alt=""><div class="over"><img src="{kit.esc(after.get("image", ""))}" alt=""></div>'
        f'<i class="bar"></i><b class="tag lf">{kit.esc(before.get("text", ""))}</b><b class="tag rt">{kit.esc(after.get("text", ""))}</b></div>'
    )


CUT_TAGS = {"silence": "pause", "quiet": "pause", "manual": "cut"}  # what each removal is called on screen, where the cut's own name is not plain
CUT_TEXT_AREA = 812 * 360  # square units the words may fill at full scale, inside the card's padding
CUT_TYPE = (20.0, 40.0)  # smallest and largest word size
CUT_FIT = 0.55 * 1.52 * 1.15  # a glyph's width and a line's height in ems, and room for wrapping


def _span(row: Any) -> tuple[float, float]:
    if isinstance(row, dict):
        return float(row["start"]), float(row["end"])
    return float(row[0]), float(row[1])


def _cut_plan(item: dict[str, Any]) -> dict[str, float]:
    """When the playhead sweeps, when the struck words fold, and when the length flips, as shares of the graphic's time."""
    seconds = kit.seconds(item)
    sweep_from = min(0.35, seconds * 0.12)
    sweep = max(seconds * 0.42, 0.6)
    fold = sweep_from + sweep + 0.12
    return {"from": sweep_from, "sweep": sweep, "fold": fold, "flip": fold + 0.3}


def _rebased(item: dict[str, Any]) -> dict[str, Any]:
    """A cut of a cut. With `base`, the spans an earlier cut kept, the track starts from that cut: only its words, on its own clock."""
    base = sorted(_span(row) for row in item.get("base") or [])
    if not base:
        return item
    starts, at = [], 0.0
    for start, end in base:
        starts.append(at)
        at += end - start

    def pieces(start: float, end: float) -> list[tuple[float, float]]:
        return [(offset + max(start, a) - a, offset + min(end, b) - a) for (a, b), offset in zip(base, starts, strict=True) if min(end, b) > max(start, a)]

    words = []
    for word in item.get("words") or []:
        middle = (float(word["start"]) + float(word["end"])) / 2
        for (a, b), offset in zip(base, starts, strict=True):
            if a <= middle <= b:
                words.append({**word, "start": offset + max(float(word["start"]), a) - a, "end": offset + min(float(word["end"]), b) - a})
    kept = sorted((s, e) for row in item.get("kept") or [] for s, e in pieces(*_span(row)))
    gaps, cursor = [], 0.0  # what the new cut takes out of the old one. A removal the old cut already made only touches it at a snapped edge
    for start, end in [*kept, (at, at)]:
        if start - cursor > 0.02:
            gaps.append((cursor, start))
        cursor = max(cursor, end)
    removed = []
    for row in item.get("removed") or []:
        for s, e in pieces(float(row["start"]), float(row["end"])):
            removed += [{**row, "start": max(s, a), "end": min(e, b)} for a, b in gaps if min(e, b) - max(s, a) > 0.02]
    return {**item, "words": words, "kept": [{"start": s, "end": e} for s, e in kept], "removed": removed, "raw": at, "base": []}


def _timeline_cut(item: dict[str, Any]) -> str:
    """A take's real words on a track. The playhead strikes each removal as it passes, then the struck words and their time fold away."""
    item = _rebased(item)
    words = [dict(row) for row in item.get("words") or []]
    kept = [_span(row) for row in item.get("kept") or []]
    removed = [{**row, "start": float(row["start"]), "end": float(row["end"])} for row in item.get("removed") or []]
    raw = float(item.get("raw") or max([end for _, end in kept] + [r["end"] for r in removed] + [float(w["end"]) for w in words] + [0.1]))
    plan = _cut_plan(item)

    def at(source: float) -> float:
        return plan["from"] + plan["sweep"] * min(max(source / raw, 0.0), 1.0)

    def kept_word(word: dict[str, Any]) -> bool:
        middle = (float(word["start"]) + float(word["end"])) / 2
        return any(start <= middle <= end for start, end in kept)

    def owner(word: dict[str, Any]) -> dict[str, Any] | None:
        middle = (float(word["start"]) + float(word["end"])) / 2
        holding = [r for r in removed if r["start"] <= middle <= r["end"]]
        return min(holding, key=lambda r: r["end"] - r["start"]) if holding else None

    def tag(kind: str, when: float) -> str:
        name = CUT_TAGS.get(kind, kind)
        return f'<em class="tg {kit.esc(kind)}" style="--s:{when:.3f}s;--c:{plan["fold"]:.3f}s">{kit.esc(name)}</em>'

    pauses = [r for r in removed if not any(r["start"] <= (float(w["start"]) + float(w["end"])) / 2 <= r["end"] for w in words)]
    cells: list[str] = []
    last: dict[str, Any] | None = None
    shown_pauses: set[int] = set()
    for word in words:
        for index, pause in enumerate(pauses):
            if index not in shown_pauses and pause["start"] < float(word["start"]):
                cells.append(tag(str(pause.get("kind", "silence")), at(pause["start"])))
                shown_pauses.add(index)
        text = kit.esc(str(word["text"]))
        if kept_word(word):
            cells.append(f'<span class="w">{text} </span>')
            last = None
            continue
        held = owner(word)
        if held is not None and held is not last:
            cells.append(tag(str(held.get("kind", "manual")), at(float(word["start"]))))
        last = held
        cells.append(f'<span class="w x" style="--s:{at(float(word["start"])):.3f}s;--c:{plan["fold"]:.3f}s"><u></u>{text} </span>')
    cells += [tag(str(pause.get("kind", "silence")), at(pause["start"])) for index, pause in enumerate(pauses) if index not in shown_pauses]

    pieces, cursor = [], 0.0
    for start, end in sorted(kept):
        if start > cursor:
            pieces.append(("x", cursor, start))
        pieces.append(("k", start, end))
        cursor = max(cursor, end)
    if cursor < raw:
        pieces.append(("x", cursor, raw))
    bar = "".join(
        f'<i class="sg {kind}" style="--w:{100 * (end - start) / raw:.3f}%;--s:{at(start):.3f}s;--c:{plan["fold"]:.3f}s"></i>' for kind, start, end in pieces if end > start
    )
    letters = sum(len(str(w["text"])) + 1 for w in words) + 8 * (len(removed))
    size = min(max((CUT_TEXT_AREA / (CUT_FIT * max(letters, 1))) ** 0.5, CUT_TYPE[0]), CUT_TYPE[1])
    title = f'<b class="nm">{kit.emphasis(str(item["title"]))}</b>' if item.get("title") else "<b></b>"
    length = f'<span class="ln"><s style="--t:{plan["flip"]:.3f}s">{kit.esc(item["before"])}</s><b style="--t:{plan["flip"]:.3f}s">{kit.esc(item["after"])}</b></span>'
    sweep = f"--a:{plan['from']:.3f}s;--d:{plan['sweep']:.3f}s;--c:{plan['fold']:.3f}s"
    return (
        f'<div class="tc" style="--fs:{size:.1f}"><header>{title}{length}</header><p class="tx">{"".join(cells)}</p>'
        f'<div class="bar"><div class="trk">{bar}</div><i class="ph" style="{sweep}"></i></div></div>'
    )


DRAWN_ICONS = {  # original, on Tabler's 24 unit grid and 2 unit stroke so they sit with its set
    "waveform": ["M3 10.5v3", "M6.6 7.5v9", "M10.2 4v16", "M13.8 8v8", "M17.4 5.5v13", "M21 10v4"],
    "clip-cut": [
        "M3 11a2 2 0 0 1 2 -2h14a2 2 0 0 1 2 2v7a2 2 0 0 1 -2 2h-14a2 2 0 0 1 -2 -2z",
        "M7.5 4m-1.5 0a1.5 1.5 0 1 0 3 0a1.5 1.5 0 1 0 -3 0",
        "M16.5 4m-1.5 0a1.5 1.5 0 1 0 3 0a1.5 1.5 0 1 0 -3 0",
        "M8.6 5.2l5.4 9.3",
        "M15.4 5.2l-5.4 9.3",
    ],
}
BADGE_CORNERS = ("tl", "tr", "bl", "br")


@functools.cache
def badge_icons() -> dict[str, list[str]]:
    """Every icon a badge can draw: Tabler's MIT set as shipped in data/icons, and the few drawn here."""
    shipped = json.loads(data_path("icons", "tabler.json").read_text(encoding="utf-8"))["icons"]
    return {**shipped, **DRAWN_ICONS}


def icon_svg(name: str) -> str:
    paths = "".join(f'<path d="{kit.esc(d)}"/>' for d in badge_icons()[name])
    return f'<svg viewBox="0 0 24 24" aria-hidden="true">{paths}</svg>'


def _feature_badge(item: dict[str, Any]) -> str:
    """A feature's icon and name pop in on the cut, in a corner clear of the action, and hold."""
    corner = item.get("corner") if item.get("corner") in BADGE_CORNERS else "tl"
    return f'<div class="badge {corner}"><span class="ic">{icon_svg(str(item["icon"]))}</span><span class="nm">{kit.emphasis(str(item["text"]))}</span></div>'


def _waveform(item: dict[str, Any]) -> str:
    """Sound measured from a real file, drawn as bars, with its numbers beside it."""
    made = []
    for index, row in enumerate(item.get("rows") or []):
        values = [max(min(float(value), 1.0), 0.02) for value in row.get("values") or []]
        bars = "".join(f'<i style="--v:{value:.3f};--i:{position}"></i>' for position, value in enumerate(values))
        made.append(
            f'<div class="track{" hot" if row.get("hot") else ""}" style="--r:{index}"><p><span>{kit.esc(row.get("text", ""))}</span><b>{kit.esc(row.get("note", ""))}</b></p><div class="bars">{bars}</div></div>'
        )
    title = f"<h2>{kit.emphasis(str(item['title']))}</h2>" if item.get("title") else ""
    return f'<div class="waves">{title}{"".join(made)}</div>'


CASCADE_CSS = """
$ .board{position:relative;text-align:left;width:880u;max-width:100%;padding:34u 34u 30u;border-radius:40u;background:color-mix(in srgb,var(--surface) 88%,var(--panel));box-shadow:0 0 0 1.5px color-mix(in srgb,var(--ink) 12%,transparent) inset,0 2u 0 color-mix(in srgb,var(--ink) 9%,transparent) inset,0 60u 140u rgba(0,0,0,.55);animation:wcin .62s cubic-bezier(.16,1,.3,1) both}
$ header{display:flex;align-items:center;justify-content:space-between;gap:20u;padding:4u 8u 26u}
$ strong{font-size:40u;font-weight:600;letter-spacing:-.025em}
$ .pill{position:relative;display:inline-flex;align-items:center;gap:12u;padding:12u 24u 12u 20u;border-radius:99u;font-size:34u;font-weight:600;letter-spacing:-.02em;background:color-mix(in srgb,var(--ink) 8%,transparent);animation:wcpill .5s cubic-bezier(.2,1.5,.4,1) var(--t) both}
$ .pill>i{width:14u;height:14u;border-radius:50%;background:var(--accent);box-shadow:0 0 14u var(--accent);animation:wcbeat 1s ease-in-out infinite alternate}
$ .pill small{font-size:24u;font-weight:500;color:var(--soft);animation:wcsoft .4s linear var(--t) both}
$ .ct{position:relative;display:inline-block;min-width:2.4ch;height:1.15em;line-height:1.15em;text-align:right;font-variant-numeric:tabular-nums}
$ .n{position:absolute;right:0;top:0;font-weight:inherit;opacity:0;animation:wcon .01s linear var(--t) forwards,wcoff .01s linear var(--o) forwards}
$ .list{display:flex;flex-direction:column;gap:12u}
$ .row{position:relative;display:flex;align-items:center;gap:20u;min-height:84u;padding:0 24u;border-radius:22u;overflow:hidden;background:color-mix(in srgb,var(--ink) 5%,transparent);animation:wcrow .5s cubic-bezier(.16,1,.3,1) calc(.08s + var(--i) * .06s) both,wcglow .9s ease-out var(--t) both,wcdone .5s ease-out var(--n) both}
$ .txt{flex:1;min-width:0;overflow:hidden;white-space:nowrap;text-overflow:ellipsis;font-size:35u;font-weight:500;letter-spacing:-.015em}
$ .tag{flex:none;padding:6u 16u;border-radius:99u;font-size:23u;font-style:normal;font-weight:500;color:var(--soft);background:color-mix(in srgb,var(--ink) 7%,transparent)}
$ .st{position:relative;flex:none;width:40u;height:40u;border-radius:50%;box-shadow:0 0 0 2.5u color-mix(in srgb,var(--ink) 22%,transparent) inset}
$ .st u{position:absolute;inset:0;border-radius:50%;background:conic-gradient(var(--accent) 0 28%,transparent 0);-webkit-mask-image:radial-gradient(closest-side,transparent 66%,#000 68%);opacity:0;animation:wcspin .7s linear var(--t) infinite,wcshow .15s linear var(--t) both,wchide .1s linear var(--n) forwards}
$ .st s{position:absolute;inset:-2u;display:grid;place-items:center;border-radius:50%;text-decoration:none;color:var(--panel);background:var(--accent);transform:scale(0);animation:wctick .46s cubic-bezier(.2,1.6,.4,1) var(--n) forwards}
$ .st svg{width:68%;height:68%;display:block}
$ .st path{fill:none;stroke:currentColor;stroke-width:3.6;stroke-linecap:round;stroke-linejoin:round}
$ .bar{position:absolute;left:0;right:0;bottom:0;height:4u;background:var(--accent);transform:scaleX(0);transform-origin:0 50%;animation:wcfill calc(var(--n) - var(--t)) cubic-bezier(.5,0,.2,1) var(--t) both,wchide .2s linear var(--n) forwards}
$ .clear{position:absolute;left:0;right:0;bottom:-84u;margin:0;text-align:center;font-size:34u;font-weight:600;letter-spacing:-.02em;animation:wcup .5s cubic-bezier(.16,1,.3,1) var(--t) both}
@keyframes wcin{from{opacity:0;transform:translateY(60u) scale(.96)}to{opacity:1;transform:none}}
@keyframes wcrow{from{opacity:0;transform:translateY(26u)}to{opacity:1;transform:none}}
@keyframes wcglow{0%{box-shadow:0 0 0 0 color-mix(in srgb,var(--accent) 0%,transparent) inset}24%{box-shadow:0 0 0 2u var(--accent) inset}100%{box-shadow:0 0 0 2u color-mix(in srgb,var(--accent) 40%,transparent) inset}}
@keyframes wcdone{to{background:color-mix(in srgb,var(--accent) 12%,transparent);box-shadow:0 0 0 0 transparent inset}}
@keyframes wcspin{to{transform:rotate(360deg)}}
@keyframes wcshow{to{opacity:1}}
@keyframes wchide{to{opacity:0}}
@keyframes wctick{0%{transform:scale(0)}100%{transform:none}}
@keyframes wcfill{to{transform:none}}
@keyframes wcon{to{opacity:1}}
@keyframes wcoff{to{opacity:0}}
@keyframes wcbeat{from{opacity:.5}to{opacity:1}}
@keyframes wcpill{0%{background:color-mix(in srgb,var(--ink) 8%,transparent)}40%{transform:scale(1.14)}100%{color:var(--panel);background:var(--accent);transform:none}}
@keyframes wcsoft{to{color:var(--panel)}}
@keyframes wcup{from{opacity:0;transform:translateY(24u)}to{opacity:1;transform:none}}
"""

GAUGE_CSS = """
$ .gauge{width:560u;max-width:100%;padding:26u 30u;border-radius:30u;background:color-mix(in srgb,var(--surface) 82%,transparent);box-shadow:0 0 0 1.5px color-mix(in srgb,var(--ink) 13%,transparent) inset,0 30u 80u rgba(0,0,0,.42);animation:sgin .5s cubic-bezier(.16,1,.3,1) both}
$ h3{margin:0 0 18u;font-size:25u;font-weight:500;letter-spacing:.01em;color:var(--soft)}
$ .rw{display:grid;grid-template-columns:1fr auto;align-items:baseline;gap:6u 18u;padding:10u 0;animation:sgin .5s cubic-bezier(.16,1,.3,1) calc(.1s + var(--i) * .08s) both}
$ .lb{font-size:30u;font-weight:500;letter-spacing:-.01em}
$ .vs{position:relative;display:block;min-width:4ch;height:1.2em;font-size:40u;font-weight:600;letter-spacing:-.03em;text-align:right;font-variant-numeric:tabular-nums}
$ .v{position:absolute;right:0;top:0;white-space:nowrap;font-weight:inherit;opacity:0;animation:sgup .32s cubic-bezier(.2,1.2,.4,1) var(--t) both,sgout .14s linear var(--o) forwards}
$ .mt{position:relative;grid-column:1 / -1;height:9u;border-radius:9u;overflow:hidden;background:color-mix(in srgb,var(--ink) 10%,transparent)}
$ .fill{position:absolute;inset:0;border-radius:9u;background:var(--ink);transform:scaleX(var(--a));transform-origin:0 50%;opacity:0;animation:sgfill .7s cubic-bezier(.3,0,.1,1) var(--t) both,sgout .01s linear var(--o) forwards}
$ .fill.hi{background:var(--accent)}
$ .nt{margin:16u 0 0;font-size:22u;line-height:1.25;color:var(--soft)}
@keyframes sgin{from{opacity:0;transform:translateY(22u)}to{opacity:1;transform:none}}
@keyframes sgup{from{opacity:0;transform:translateY(.5em)}to{opacity:1;transform:none}}
@keyframes sgout{to{opacity:0}}
@keyframes sgfill{from{opacity:1;transform:scaleX(var(--a))}to{opacity:1;transform:scaleX(var(--b))}}
"""

SAY_CSS = """
$.zone{justify-content:flex-end;align-items:center;text-align:center}
$ .say{margin:0;display:inline-block;max-width:100%;padding:.2em .6em .26em;border-radius:.4em;font-size:64u;font-weight:650;line-height:1.14;letter-spacing:-.015em;
color:var(--ink);text-wrap:balance;background:color-mix(in srgb,var(--panel) 70%,transparent)}
$ .say .w{display:inline-block;animation:sayon .24s ease-out both;animation-delay:calc(var(--i) * var(--gap))}
$ .say .w.hot{color:var(--accent)}
@keyframes sayon{from{opacity:0;transform:translateY(.16em)}to{opacity:1;transform:none}}
"""

STAMP_CSS = """
$ .stamp{position:absolute;display:flex;flex-direction:column;gap:6u;font-family:var(--mono);color:var(--ink);text-shadow:0 1u 10u rgba(0,0,0,.6)}
$ .tl{left:var(--gutter);top:var(--top)}
$ .tr{right:var(--gutter);top:var(--top);align-items:flex-end}
$ .bl{left:var(--gutter);bottom:calc(var(--top) * .6)}
$ .br{right:var(--gutter);bottom:calc(var(--top) * .6);align-items:flex-end}
$ .stamp>i{width:14u;height:14u;background:var(--accent);animation:csblink 1s steps(2) infinite}
$ .tx{font-size:38u;font-weight:500;letter-spacing:.04em}
$ .word{display:inline-block;white-space:nowrap}
$ .c{display:inline-block;opacity:0;animation:cson .01s linear calc(var(--i) * .035s) forwards}
$ .lb{font-size:28u;letter-spacing:.05em;color:var(--soft)}
@keyframes csblink{0%{opacity:1}100%{opacity:.15}}
@keyframes cson{to{opacity:1}}
"""

TRACK_CSS = """
$ {overflow:hidden}
$ .ln{position:absolute;inset:0;background:color-mix(in srgb,var(--accent) 70%,transparent)}
$ .mk{position:absolute;width:0;height:0}
$ .mk i{position:absolute;left:-9u;top:-9u;width:18u;height:18u;box-shadow:0 0 0 2u var(--accent);animation:tmpop .3s cubic-bezier(.2,1.6,.4,1) both}
$ .mk em{position:absolute;left:18u;top:-13u;white-space:nowrap;font-family:var(--mono);font-style:normal;font-size:22u;letter-spacing:.03em;color:var(--ink);text-shadow:0 1u 8u rgba(0,0,0,.7)}
@keyframes tmpop{from{transform:scale(2.2);opacity:0}to{transform:none;opacity:1}}
"""

SILHOUETTE_CSS = """
$ .stagefield{position:absolute;inset:0;overflow:hidden}
$ .floor{position:absolute;left:0;right:0;top:72%;bottom:0;background:color-mix(in srgb,var(--accent) 16%,transparent)}
$ .line{position:absolute;left:var(--gutter);right:var(--gutter);top:12%;margin:0;text-align:center;font-size:60u;font-weight:600;line-height:1.1;letter-spacing:-.025em;text-wrap:balance}
$ .fig{position:absolute;left:50%;top:72%;width:0;height:0;color:var(--ink);--u:calc(19u);animation:safig var(--dur) linear both}
$ .hips{position:absolute;left:0;top:calc(var(--u) * -11);width:0;height:0;animation:sabob .6s ease-in-out infinite alternate}
$ .trunk{position:absolute;left:calc(var(--u) * -1.6);bottom:0;width:calc(var(--u) * 3.2);height:calc(var(--u) * 7.4);border-radius:calc(var(--u) * 1.6);background:currentColor;transform-origin:50% 100%;animation:salean 1.2s ease-in-out infinite alternate}
$ .head{position:absolute;left:50%;top:calc(var(--u) * -3.2);width:calc(var(--u) * 2.9);height:calc(var(--u) * 2.9);margin-left:calc(var(--u) * -1.45);border-radius:50%;background:currentColor;transform-origin:50% 100%;animation:sahead 1.5s ease-in-out infinite alternate}
$ .arm,$ .leg{position:absolute;width:calc(var(--u) * 1.15);border-radius:calc(var(--u) * .6);background:currentColor;transform-origin:50% calc(var(--u) * .6)}
$ .arm{left:calc(50% - var(--u) * .58);top:calc(var(--u) * .5);height:calc(var(--u) * 3.6)}
$ .fore,$ .shin{position:absolute;left:0;top:calc(100% - var(--u) * .6);width:100%;border-radius:calc(var(--u) * .6);background:currentColor;transform-origin:50% calc(var(--u) * .6)}
$ .fore{height:calc(var(--u) * 3.4)}
$ .leg{left:calc(var(--u) * -.58);top:calc(var(--u) * -.5);height:calc(var(--u) * 5.8)}
$ .shin{height:calc(var(--u) * 5.8)}
$ .a1{animation:saarm 1.2s ease-in-out infinite alternate}
$ .a2{animation:saarm 1.2s ease-in-out -1.2s infinite alternate}
$ .a1 .fore{animation:safore 1.2s ease-in-out -.1s infinite alternate}
$ .a2 .fore{animation:safore 1.2s ease-in-out -1.3s infinite alternate}
$ .l1{animation:saleg 1.2s ease-in-out infinite alternate}
$ .l2{animation:saleg 1.2s ease-in-out -1.2s infinite alternate}
$ .l1 .shin{animation:sashin 1.2s ease-in-out -.14s infinite alternate}
$ .l2 .shin{animation:sashin 1.2s ease-in-out -1.34s infinite alternate}
$ .wave .a1{animation:sawave .7s ease-in-out infinite alternate}
$ .wave .a1 .fore{animation:sawavef .7s ease-in-out -.1s infinite alternate}
$ .wave .l1,$ .wave .l2,$ .point .l1,$ .point .l2,$ .wave .shin,$ .point .shin{animation:none}
$ .wave .l1,$ .point .l1{transform:rotate(7deg)}
$ .wave .l2,$ .point .l2{transform:rotate(-7deg)}
$ .wave .fig,$ .point .fig{animation:none}
$ .point .a1{animation:sapoint .9s cubic-bezier(.2,1.2,.4,1) both}
$ .point .a1 .fore{animation:none;transform:rotate(-8deg)}
@keyframes safig{from{transform:translateX(-26vmin)}to{transform:translateX(26vmin)}}
@keyframes sabob{from{transform:translateY(calc(var(--u) * .3))}to{transform:translateY(calc(var(--u) * -.2))}}
@keyframes salean{from{transform:rotate(-2deg)}to{transform:rotate(3deg)}}
@keyframes sahead{from{transform:rotate(-4deg)}to{transform:rotate(4deg)}}
@keyframes saarm{from{transform:rotate(30deg)}to{transform:rotate(-30deg)}}
@keyframes safore{from{transform:rotate(-34deg)}to{transform:rotate(-6deg)}}
@keyframes saleg{from{transform:rotate(26deg)}to{transform:rotate(-26deg)}}
@keyframes sashin{from{transform:rotate(4deg)}to{transform:rotate(48deg)}}
@keyframes sawave{from{transform:rotate(-150deg)}to{transform:rotate(-112deg)}}
@keyframes sawavef{from{transform:rotate(-24deg)}to{transform:rotate(24deg)}}
@keyframes sapoint{from{transform:rotate(20deg)}to{transform:rotate(-98deg)}}
"""

STRIP_CSS = """
$ h2{margin:0 0 34u;font-size:64u;font-weight:600;letter-spacing:-.03em;text-align:center;animation:rise .5s var(--ease) both}
$ .strip{display:flex;flex-wrap:wrap;justify-content:center;gap:26u}
$ figure{margin:0;display:flex;flex-direction:column;align-items:center;gap:12u;animation:stland .5s cubic-bezier(.2,1.3,.4,1) var(--t) both}
$ .icons img{display:block;width:118u;height:118u}
$ .looks img{display:block;width:190u;height:338u;object-fit:cover;border-radius:18u;box-shadow:0 18u 44u rgba(0,0,0,.45)}
$ .frames img{display:block;width:420u;height:206u;object-fit:cover;border-radius:16u;box-shadow:0 18u 44u rgba(0,0,0,.45)}
$ .frames figcaption{font-size:44u}
$ figcaption{font-size:30u;font-weight:500;letter-spacing:-.01em;color:var(--ink)}
@keyframes stland{from{opacity:0;transform:translateY(34u) scale(.9)}to{opacity:1;transform:none}}
"""

WIPE_CSS = """
$ .pair{position:relative;width:100%;aspect-ratio:9/12;max-height:100%;margin:0 auto;border-radius:28u;overflow:hidden;box-shadow:0 40u 90u rgba(0,0,0,.5);animation:rise .5s var(--ease) both}
$ img{position:absolute;inset:0;width:100%;height:100%;object-fit:cover;object-position:50% 22%;display:block}
$ .over{position:absolute;inset:0;animation:swsweep var(--dur) cubic-bezier(.65,0,.3,1) both}
$ .bar{position:absolute;top:0;bottom:0;left:0;width:5u;background:var(--accent);box-shadow:0 0 22u var(--accent);animation:swline var(--dur) cubic-bezier(.65,0,.3,1) both}
$ .tag{position:absolute;bottom:22u;padding:8u 24u;border-radius:99u;font-size:72u;font-weight:600;color:var(--panel);background:var(--ink)}
$ .lf{left:22u}
$ .rt{right:22u;background:var(--accent)}
@keyframes swsweep{0%{clip-path:inset(0 0 0 96%)}34%,44%{clip-path:inset(0 0 0 22%)}72%,80%{clip-path:inset(0 0 0 72%)}100%{clip-path:inset(0 0 0 46%)}}
@keyframes swline{0%{left:96%}34%,44%{left:22%}72%,80%{left:72%}100%{left:46%}}
"""

WAVE_CSS = """
$ .waves{width:100%;padding:34u 38u;border-radius:30u;background:color-mix(in srgb,var(--surface) 84%,transparent);box-shadow:0 0 0 1.5px color-mix(in srgb,var(--ink) 12%,transparent) inset,0 40u 100u rgba(0,0,0,.45);animation:rise .5s var(--ease) both}
$ h2{margin:0 0 22u;font-size:50u;font-weight:600;letter-spacing:-.03em}
$ .track{margin-top:26u}
$ p{display:flex;justify-content:space-between;align-items:baseline;margin:0 0 12u;font-size:36u;font-weight:500}
$ p b{font-weight:600;font-variant-numeric:tabular-nums}
$ .hot p b{color:var(--accent)}
$ .bars{display:flex;align-items:center;gap:3u;height:150u}
$ .bars i{flex:1;height:calc(var(--v) * 100%);border-radius:9u;background:color-mix(in srgb,var(--ink) 40%,transparent);transform:scaleY(0);animation:wvup .4s cubic-bezier(.2,1.2,.4,1) calc(.2s + var(--r) * .5s + var(--i) * .018s) both}
$ .hot .bars i{background:var(--accent)}
@keyframes wvup{to{transform:none}}
"""

CUT_CSS = """
$ .tc{--hot:#ff5d6c;position:relative;text-align:left;width:880u;max-width:100%;padding:30u 34u 34u;border-radius:38u;background:color-mix(in srgb,var(--surface) 90%,var(--panel));
box-shadow:0 0 0 1.5px color-mix(in srgb,var(--ink) 12%,transparent) inset,0 60u 140u rgba(0,0,0,.5);animation:tcin .55s cubic-bezier(.16,1,.3,1) both}
$ header{display:flex;flex-wrap:wrap;align-items:baseline;justify-content:space-between;gap:6u 24u;margin:0 0 16u}
$ .nm{font-size:34u;font-weight:600;letter-spacing:-.02em;white-space:nowrap;color:var(--soft)}
$ .ln{display:inline-flex;white-space:nowrap;margin-left:auto;align-items:baseline;gap:18u;font-size:52u;font-weight:650;letter-spacing:-.03em;font-variant-numeric:tabular-nums}
$ .ln s{text-decoration:line-through;text-decoration-thickness:.09em;text-decoration-color:transparent;color:var(--ink);animation:tcwas .35s ease-out var(--t) forwards}
$ .ln b{font-weight:inherit;color:var(--accent);opacity:0;transform:translateY(.35em);animation:tcnow .5s cubic-bezier(.2,1.4,.4,1) calc(var(--t) + .15s) forwards}
$ .tx{margin:0;font-size:calc(var(--fs) * 1u);line-height:1.52;font-weight:500;letter-spacing:-.01em;color:var(--ink)}
$ .w{position:relative;display:inline-block;white-space:pre;vertical-align:baseline}
$ .x{animation:tcred .18s linear var(--s) forwards,tcfold .5s cubic-bezier(.6,0,.3,1) var(--c) forwards}
$ .x u{position:absolute;left:-.04em;right:.22em;top:54%;height:.1em;border-radius:.05em;background:var(--hot);transform:scaleX(0);transform-origin:0 50%;animation:tcline .22s ease-out var(--s) forwards}
$ .tg{display:inline-block;margin:0 .34em 0 0;padding:.1em .42em .14em;border-radius:.42em;font-size:.6em;font-style:normal;font-weight:700;letter-spacing:.04em;line-height:1.2;
text-transform:uppercase;vertical-align:.18em;white-space:nowrap;color:#fff;background:var(--hot);opacity:0;transform:scale(.5);animation:tcpop .32s cubic-bezier(.2,1.6,.4,1) var(--s) forwards,tcgone .5s cubic-bezier(.6,0,.3,1) var(--c) forwards}
$ .bar{position:relative;margin-top:26u;height:30u}
$ .trk{display:flex;height:100%;border-radius:10u;overflow:hidden}
$ .sg{flex:none;width:var(--w);height:100%;box-shadow:-2u 0 0 color-mix(in srgb,var(--surface) 90%,var(--panel)) inset}
$ .sg.k{background:var(--accent)}
$ .sg.x{background:color-mix(in srgb,var(--ink) 26%,transparent);animation:tcbar .2s linear var(--s) forwards,tcshut .5s cubic-bezier(.6,0,.3,1) var(--c) forwards}
$ .ph{position:absolute;top:-12u;bottom:-12u;left:0;width:5u;margin-left:-2u;border-radius:3u;background:var(--ink);box-shadow:0 0 16u var(--ink);animation:tcsweep var(--d) linear var(--a) both,tchide .2s linear var(--c) forwards}
@keyframes tcin{from{opacity:0;transform:translateY(50u) scale(.97)}to{opacity:1;transform:none}}
@keyframes tcwas{to{color:var(--soft);text-decoration-color:var(--hot)}}
@keyframes tcnow{to{opacity:1;transform:none}}
@keyframes tcred{to{color:var(--hot)}}
@keyframes tcline{to{transform:none}}
@keyframes tcpop{to{opacity:1;transform:none}}
@keyframes tcfold{from{font-size:1em;opacity:1}to{font-size:0;opacity:0}}
@keyframes tcgone{from{font-size:.6em;opacity:1;margin-right:.34em;padding:.1em .42em .14em}to{font-size:0;opacity:0;margin-right:0;padding:0}}
@keyframes tcbar{to{background:var(--hot)}}
@keyframes tcshut{from{width:var(--w)}to{width:0}}
@keyframes tcsweep{from{left:0}to{left:100%}}
@keyframes tchide{to{opacity:0}}
"""

BADGE_CSS = """
$ .badge{position:absolute;display:flex;align-items:center;gap:22u;padding:12u 36u 12u 12u;border-radius:99u;background:color-mix(in srgb,var(--panel) 84%,transparent);
box-shadow:0 0 0 1.5px color-mix(in srgb,var(--ink) 14%,transparent) inset,0 24u 60u rgba(0,0,0,.42);transform-origin:0 50%;animation:fbin .5s cubic-bezier(.2,1.5,.4,1) both}
$ .tl{left:var(--gutter);top:calc(var(--top) * .55)}
$ .tr{right:var(--gutter);top:calc(var(--top) * .55);transform-origin:100% 50%}
$ .bl{left:var(--gutter);bottom:calc(var(--top) * .55)}
$ .br{right:var(--gutter);bottom:calc(var(--top) * .55);transform-origin:100% 50%}
$ .ic{flex:none;display:grid;place-items:center;width:96u;height:96u;border-radius:50%;color:var(--panel);background:var(--accent);
box-shadow:0 0 32u color-mix(in srgb,var(--accent) 45%,transparent);animation:fbic .55s cubic-bezier(.2,1.6,.4,1) .06s both}
$ .ic svg{display:block;width:58%;height:58%;fill:none;stroke:currentColor;stroke-width:2.1;stroke-linecap:round;stroke-linejoin:round}
$ .nm{font-size:52u;font-weight:650;letter-spacing:-.025em;line-height:1;white-space:nowrap;animation:fbnm .45s cubic-bezier(.16,1,.3,1) .12s both}
@keyframes fbin{from{opacity:0;transform:scale(.6)}to{opacity:1;transform:none}}
@keyframes fbic{from{transform:rotate(-40deg) scale(.4)}to{transform:none}}
@keyframes fbnm{from{opacity:0;transform:translateX(-18u)}to{opacity:1;transform:none}}
"""

FORMATS = (
    Format(
        id="feature_badge",
        title="Feature badge",
        summary="A feature's icon and name pop in on the cut, held in a corner clear of the action and of faces.",
        group="film",
        jobs=("explain", "prove"),
        slots=(
            Slot("icon", kind="choice", choices=tuple(sorted(badge_icons()))),
            Slot("text", limit=22),
            Slot("corner", kind="choice", required=False, choices=BADGE_CORNERS),
        ),
        example={"icon": "scissors", "text": "Cutting", "corner": "tl"},
        draw=_feature_badge,
        seconds=3.0,
        shortest=1.0,
        longest=12.0,
        area="full",
        tall=140,
        sound=None,
        css=scoped("feature_badge", BADGE_CSS),
    ),
    Format(
        id="timeline_cut",
        title="Timeline cut",
        summary="A take's real words on a track. Each removal strikes as the playhead passes, then folds away.",
        group="film",
        jobs=("prove", "explain"),
        slots=(
            Slot("words", kind="list", limit=44, each=12),
            Slot("kept", kind="list", limit=16),
            Slot("removed", kind="list", limit=16, required=False),
            Slot("before", limit=8),
            Slot("after", limit=8),
            Slot("title", limit=20, required=False),
            Slot("raw", kind="number", required=False),
            Slot("base", kind="list", limit=16, required=False),
        ),
        example={
            "title": "Sample take",
            "before": "5.4 s",
            "after": "1.4 s",
            "raw": 5.4,
            "words": [
                {"text": "So,", "start": 0.1, "end": 0.3},
                {"text": "um,", "start": 0.45, "end": 0.65},
                {"text": "the", "start": 0.8, "end": 0.95},
                {"text": "the", "start": 1.0, "end": 1.15},
                {"text": "plan", "start": 1.2, "end": 1.5},
                {"text": "is", "start": 1.55, "end": 1.75},
                {"text": "wait.", "start": 2.6, "end": 3.0},
                {"text": "The", "start": 3.8, "end": 4.0},
                {"text": "plan", "start": 4.0, "end": 4.3},
                {"text": "is", "start": 4.3, "end": 4.5},
                {"text": "simple.", "start": 4.5, "end": 5.1},
            ],
            "kept": [{"start": 3.75, "end": 5.15}],
            "removed": [
                {"start": 0.0, "end": 2.6, "kind": "retake"},
                {"start": 0.45, "end": 0.65, "kind": "filler"},
                {"start": 0.8, "end": 1.0, "kind": "stutter"},
                {"start": 2.6, "end": 3.0, "kind": "cue"},
                {"start": 3.0, "end": 3.75, "kind": "silence"},
                {"start": 5.15, "end": 5.4, "kind": "silence"},
            ],
        },
        draw=_timeline_cut,
        seconds=4.0,
        shortest=2.5,
        longest=10.0,
        tall=640,
        sound=None,
        css=scoped("timeline_cut", CUT_CSS),
    ),
    Format(
        id="stage_strip",
        title="Stage strip",
        summary="Real pictures land in a row, each with its name, one after another.",
        group="film",
        jobs=("explain", "prove"),
        slots=(
            Slot("stages", kind="list", limit=10, each=16),
            Slot("title", limit=44, required=False),
            Slot("size", kind="choice", required=False, choices=("icons", "looks", "frames")),
        ),
        example={"title": "Every *stage* in one place", "stages": [{"text": "Cut"}, {"text": "Clean"}, {"text": "Grade"}, {"text": "Caption"}]},
        draw=_stage_strip,
        seconds=4.0,
        shortest=2.0,
        longest=12.0,
        tall=520,
        sound="pop",
        css=scoped("stage_strip", STRIP_CSS),
    ),
    Format(
        id="split_wipe",
        title="Split wipe",
        summary="A divider sweeps across one real frame, so before and after are seen side by side.",
        group="film",
        jobs=("prove",),
        slots=(Slot("frames", kind="list", limit=2, each=14),),
        example={"frames": [{"text": "Raw"}, {"text": "Graded"}]},
        draw=_split_wipe,
        seconds=5.0,
        shortest=3.0,
        longest=10.0,
        tall=900,
        sound="whoosh",
        css=scoped("split_wipe", WIPE_CSS),
    ),
    Format(
        id="waveform",
        title="Waveform",
        summary="Sound measured from a real file rises as bars, with its numbers beside it.",
        group="film",
        jobs=("prove", "explain"),
        slots=(Slot("rows", kind="list", limit=3, each=20), Slot("title", limit=40, required=False)),
        example={
            "title": "Sample sound",
            "rows": [
                {"text": "Raw", "note": "-21 LUFS", "values": [0.2, 0.5, 0.3, 0.6, 0.2, 0.4]},
                {"text": "Mastered", "note": "-14 LUFS", "hot": True, "values": [0.4, 0.8, 0.6, 0.9, 0.5, 0.7]},
            ],
        },
        draw=_waveform,
        seconds=5.0,
        shortest=2.5,
        longest=12.0,
        tall=620,
        sound=None,
        css=scoped("waveform", WAVE_CSS),
    ),
    Format(
        id="work_cascade",
        title="Work cascade",
        summary="Cards pulse as they start work, tick in a cascade, and a counter lands on zero.",
        group="film",
        jobs=("prove", "explain"),
        slots=(
            Slot("title", limit=30),
            Slot("rows", kind="list", limit=6, each=40),
            Slot("from", kind="number"),
            Slot("to", kind="number", required=False),
            Slot("label", limit=12, required=False),
            Slot("done", limit=34, required=False),
        ),
        example={
            "title": "Support *queue*",
            "from": 412,
            "to": 0,
            "label": "open",
            "done": "Queue *clear*",
            "rows": [
                {"text": "Refund for a sample order", "label": "Billing"},
                {"text": "Reset a password", "label": "Account"},
                {"text": "Where is my parcel", "label": "Orders"},
                {"text": "Change the plan", "label": "Billing"},
            ],
        },
        draw=_work_cascade,
        seconds=7.0,
        shortest=3.5,
        longest=14.0,
        tall=660,
        sound="tick",
        css=scoped("work_cascade", CASCADE_CSS),
    ),
    Format(
        id="state_gauge",
        title="State gauge",
        summary="A small panel of rows that change value on cue, so one number can carry a story.",
        group="film",
        jobs=("prove", "explain"),
        slots=(Slot("title", limit=26, required=False), Slot("rows", kind="list", limit=4, each=20), Slot("note", limit=48, required=False)),
        example={
            "title": "Sample battery",
            "rows": [
                {"text": "Charge", "states": [{"value": "12%", "level": 0.12}, {"value": "64%", "level": 0.64, "hot": True}]},
                {"text": "Range", "states": [{"value": "40 km", "level": 0.2}, {"value": "210 km", "level": 0.7}]},
            ],
            "note": "Invented figures for illustration",
        },
        draw=_state_gauge,
        seconds=5.0,
        shortest=2.0,
        longest=30.0,
        tall=360,
        sound="tick",
        css=scoped("state_gauge", GAUGE_CSS),
    ),
    Format(
        id="say_line",
        title="Say line",
        summary="The line a narrator would say, set at the foot of the frame for a viewer with the sound off.",
        group="film",
        jobs=("explain",),
        slots=(Slot("text", limit=56),),
        example={"text": "Each number here is read from the *tool*"},
        draw=_say_line,
        seconds=3.0,
        shortest=1.5,
        longest=12.0,
        tall=90,
        sound=None,
        css=scoped("say_line", SAY_CSS),
    ),
    Format(
        id="corner_stamp",
        title="Corner stamp",
        summary="A small typed date and time sits in a corner, like a line in an evidence log.",
        group="film",
        jobs=("explain",),
        slots=(Slot("text", limit=30), Slot("label", limit=30, required=False), Slot("corner", kind="choice", required=False, choices=("tl", "tr", "bl", "br"))),
        example={"text": "MON 09:02:41", "label": "Sample log", "corner": "tl"},
        draw=_corner_stamp,
        seconds=4.0,
        shortest=1.0,
        longest=60.0,
        area="full",
        tall=120,
        sound=None,
        fonts=("mono",),
        css=scoped("corner_stamp", STAMP_CSS),
    ),
    Format(
        id="tracking_marks",
        title="Tracking marks",
        summary="Small squares ride moving points with mono labels, like a machine reading the frame.",
        group="film",
        jobs=("explain", "hook"),
        slots=(Slot("labels", kind="list", limit=6, each=18), Slot("seed", kind="number", required=False)),
        example={"labels": ["Sample A", "Sample B", "Sample C", "Sample D"], "seed": 5},
        draw=_tracking_marks,
        seconds=4.0,
        shortest=1.5,
        longest=20.0,
        area="full",
        tall=1920,
        sound=None,
        fonts=("mono",),
        css=scoped("tracking_marks", TRACK_CSS),
    ),
    Format(
        id="silhouette_act",
        title="Silhouette act",
        summary="A flat figure on a colour field acts out the line on real joints.",
        group="film",
        jobs=("explain", "hook"),
        slots=(Slot("text", limit=60), Slot("pose", kind="choice", required=False, choices=SILHOUETTE_POSES)),
        example={"text": "He walks the *long way* round", "pose": "walk"},
        draw=_silhouette_act,
        seconds=4.0,
        shortest=2.0,
        longest=12.0,
        area="full",
        tall=1920,
        sound=None,
        css=scoped("silhouette_act", SILHOUETTE_CSS),
    ),
)
