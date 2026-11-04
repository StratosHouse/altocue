"""Charts and numbers. Every mark is computed from the values passed in, so a graphic cannot drift from its data.

Only HTML elements animate, because the renderer seeks SVG and pseudo-element animations to film time, not clip time. A line draws
because stroke-dashoffset inherits from the div around its svg. Anything that travels is a div.
"""

from __future__ import annotations

import hashlib
import json
import math
from collections.abc import Callable, Iterable
from typing import Any

from ...errors import AltocueError
from . import Format, Slot, kit

FPS = 30
W = 808  # chart width in svg units: the card's inner width on a tall canvas

SKIN = """
.F{max-width:calc(940px * var(--scale));--mute:color-mix(in srgb,var(--ink) 30%,transparent);--hair:color-mix(in srgb,var(--ink) 15%,transparent);--back:color-mix(in srgb,var(--panel) 93%,transparent)}
.F .card{position:relative;padding:calc(42px * var(--scale)) calc(36px * var(--scale)) calc(28px * var(--scale));border-radius:calc(26px * var(--scale));background:var(--back);box-shadow:0 0 0 1px var(--hair) inset,0 28px 80px rgba(0,0,0,.4);animation:dkcard .5s var(--ease) both}
.F .tab{position:absolute;left:calc(36px * var(--scale));top:0;width:calc(84px * var(--scale));height:calc(7px * var(--scale));background:var(--accent);transform-origin:0 50%;animation:dktab .5s .12s var(--ease) both}
.F h2{margin:0;font-size:calc(46px * var(--scale));font-weight:600;line-height:1.12;letter-spacing:-.024em;text-wrap:balance}
.F .sub{margin:calc(8px * var(--scale)) 0 0;font-size:calc(30px * var(--scale));line-height:1.2;color:var(--soft);letter-spacing:-.005em}
.F .foot{margin:calc(16px * var(--scale)) 0 0;font-size:calc(25px * var(--scale));line-height:1.2;color:var(--soft)}
.F .plot{position:relative;width:100%;margin-top:calc(22px * var(--scale))}
.F .ly{position:absolute;inset:0}
.F .plot svg{position:absolute;inset:0;display:block;width:100%;height:100%;overflow:visible}
.F .plot text{fill:var(--soft);font-family:inherit;font-size:27px}
.F .ct{display:inline-block;height:1.16em;line-height:1.16;overflow:hidden;vertical-align:bottom;font-variant-numeric:tabular-nums}
.F .st{display:block;animation:dkstrip var(--r) steps(var(--n)) var(--w) both}
.F .st i{display:block;height:1.16em;font-style:normal;white-space:nowrap}
@keyframes dkcard{from{opacity:0;transform:translateY(calc(24px * var(--scale)))}to{opacity:1;transform:none}}
@keyframes dktab{from{transform:scaleX(0)}to{transform:none}}
@keyframes dkfade{from{opacity:0}to{opacity:1}}
@keyframes dkrise{from{opacity:0;transform:translateY(calc(12px * var(--scale)))}to{opacity:1;transform:none}}
@keyframes dkstrip{to{transform:translateY(calc(var(--n) * -1.16em))}}
@keyframes dkdraw{from{stroke-dashoffset:1}to{stroke-dashoffset:0}}
"""


def _skin(fid: str, css: str) -> str:
    return SKIN.replace(".F", f".f-{fid}") + css


def _fail(owner: str, what: str) -> None:
    raise AltocueError(f"{owner}: {what}", fix=f"See `altocue library show {owner}`.")


def _num(value: Any, owner: str, name: str) -> float:
    try:
        out = float(value)
    except (TypeError, ValueError):
        out = math.nan
    if not math.isfinite(out):
        _fail(owner, f'"{name}" must be a number, and {value!r} is not one.')
    return out


def _amounts(rows: Iterable[dict[str, Any]], owner: str, name: str = "value") -> list[float]:
    """Amounts for a chart drawn from zero: at least one above zero, and none below it."""
    values = [_num(row.get(name), owner, name) for row in rows]
    if not values or max(values) <= 0 or min(values) < 0:
        _fail(owner, "needs amounts of zero or more, with at least one above zero.")
    return values


def _places(values: Iterable[float]) -> int:
    """How many decimals the data itself carries, up to two."""
    found = 0
    for value in values:
        tail = f"{float(value):.6f}".rstrip("0").split(".")[1]
        found = max(found, len(tail))
    return min(found, 2)


def _show(value: float, places: int = 0, prefix: str = "", suffix: str = "") -> str:
    sign = "−" if round(value, places) < 0 else ""
    return f"{sign}{prefix}{abs(value):,.{places}f}{suffix}"


def _short(value: float, prefix: str = "", suffix: str = "") -> str:
    """An axis label. Large round numbers lose their zeros unless the data names its own unit."""
    if not suffix:
        for size, mark in ((1e9, "bn"), (1e6, "m"), (1e3, "k")):
            if abs(value) >= size:
                return f"{'−' if value < 0 else ''}{prefix}{abs(value) / size:g}{mark}"
    return f"{'−' if value < 0 else ''}{prefix}{abs(value):,g}{suffix}"


def _out(t: float) -> float:
    """The house easing as a function, so a number and the mark beside it land together."""
    return 1.0 if t >= 1 else 1 - 2 ** (-9 * t)


def _smooth(t: float) -> float:
    t = min(max(t, 0.0), 1.0)
    return t * t * (3 - 2 * t)


def _strip(texts: list[str], run: float, wait: float) -> str:
    """Stacked labels stepped through one per frame. The last one is what holds."""
    cells = "".join(f"<i>{kit.esc(text)}</i>" for text in texts)
    return f'<span class="ct"><span class="st" data-layout-allow-overflow data-layout-allow-overlap style="--n:{len(texts) - 1};--r:{run:.3f}s;--w:{wait:.3f}s">{cells}</span></span>'


def _count(final: float, places: int = 0, prefix: str = "", suffix: str = "", run: float = 1.0, wait: float = 0.0, linear: bool = False, rate: float = 0.0) -> str:
    """A number that climbs from zero. Each row is the value at one frame, so thousands separators and decimals stay true.

    A full-screen number changing every frame reads as a flicker. `rate` slows the changes, in values a second.
    """
    frames = max(round(run * (rate or FPS)), 6)
    texts = [_show(final * (k / frames if linear else _out(k / frames)), places, prefix, suffix) for k in range(frames)]
    return _strip([*texts, _show(final, places, prefix, suffix)], run, wait)


def _ticks(low: float, high: float, count: int = 4) -> list[float]:
    """Round axis steps that cover the data."""
    span = (high - low) or abs(high) or 1.0
    rough = span / count
    size = 10.0 ** math.floor(math.log10(rough))
    step = next(n * size for n in (1, 2, 2.5, 5, 10) if n * size >= rough * 0.999)
    first = math.floor(low / step + 1e-9) * step
    total = math.ceil((high - first) / step - 1e-9)
    return [round(first + n * step, 9) for n in range(total + 1)]


def _mark(item: dict[str, Any]) -> str:
    """A keyframe name of this graphic's own, the same on every render."""
    return "k" + hashlib.sha256(json.dumps(item, sort_keys=True, default=str).encode()).hexdigest()[:9]


def _keys(name: str, frames: list[tuple[float, str]]) -> str:
    """One graphic's own keyframes. The last stop always reaches 100%, or the value would fall back to where it started."""
    closed = [*frames, (100.0, frames[-1][1])] if frames[-1][0] < 100 else frames
    kept = [f for i, f in enumerate(closed) if i in (0, len(closed) - 1) or not closed[i - 1][1] == f[1] == closed[i + 1][1]]
    return f"@keyframes {name}{{" + "".join(f"{min(at, 100.0):.3f}%{{{css}}}" for at, css in kept) + "}"


def _head(item: dict[str, Any]) -> str:
    title = f"<h2>{kit.emphasis(item['title'])}</h2>" if item.get("title") else ""
    sub = f'<p class="sub">{kit.esc(item["sub"])}</p>' if item.get("sub") else ""
    return title + sub


def _foot(item: dict[str, Any]) -> str:
    return f'<p class="foot">{kit.esc(item["note"])}</p>' if item.get("note") else ""


def _card(item: dict[str, Any], body: str) -> str:
    return f'<div class="card"><i class="tab"></i>{_head(item)}{body}{_foot(item)}</div>'


def _affix(item: dict[str, Any]) -> tuple[str, str]:
    return str(item.get("prefix") or ""), str(item.get("suffix") or "")


def _hot(rows: list[dict[str, Any]], values: list[float]) -> int:
    """The row in the accent colour: the one marked `hot`, or else the largest."""
    return next((i for i, row in enumerate(rows) if row.get("hot")), values.index(max(values)))


def _svg(height: int, inner: str, width: int = W) -> str:
    return f'<svg viewBox="0 0 {width} {height}" aria-hidden="true">{inner}</svg>'


def _path(d: str, measured: bool = True) -> str:
    """A path element. `pathLength` of 1 is what lets a wrapper's stroke-dashoffset draw it from 1 to 0."""
    return f'<path d="{d}" pathLength="1"/>' if measured else f'<path d="{d}"/>'


def _pct(a: float, b: float) -> str:
    return f"{a / b * 100:.3f}%"


# Line charts


def _every(count: int, most: int = 6) -> list[int]:
    """Evenly spaced places to label, always ending on the last one."""
    step = max(math.ceil((count - 1) / (most - 1)), 1)
    shown = list(range(0, count, step))
    if shown[-1] != count - 1:
        shown = [*[i for i in shown if count - 1 - i > step / 2], count - 1]
    return shown


def _line_geometry(
    values: list[float], labels: list[str], height: int, prefix: str, suffix: str, top: int = 58, bottom: int = 46, left: int = 78, right: int = 40
) -> dict[str, Any]:
    """Points in svg units plus the static axes. A line may sit on a labelled floor above zero, but only when the data never comes near zero."""
    low, high = min(values), max(values)
    floor = 0.0 if 0 <= low <= high * 0.62 else low
    ticks = _ticks(min(floor, low), high)
    bottom_value, top_value = ticks[0], ticks[-1]
    plot = height - top - bottom
    left = max(left, 22 + max(len(_short(tick, prefix, suffix)) for tick in ticks) * 15)
    span = max(len(values) - 1, 1)

    def where(index: int, value: float) -> tuple[float, float]:
        return left + (W - left - right) * index / span, top + plot * (top_value - value) / (top_value - bottom_value)

    points = [where(i, v) for i, v in enumerate(values)]
    grid = []
    for tick in ticks:
        y = where(0, tick)[1]
        grid.append(f'<line x1="{left}" x2="{W - right}" y1="{y:.1f}" y2="{y:.1f}" class="{"zero" if tick == 0 else "gl"}"/>')
        grid.append(f'<text x="{left - 14}" y="{y + 9:.1f}" text-anchor="end">{kit.esc(_short(tick, prefix, suffix))}</text>')
    for index in _every(len(labels)):
        anchor = "end" if index == len(labels) - 1 else "middle"
        grid.append(f'<text x="{points[index][0]:.1f}" y="{height - 8}" text-anchor="{anchor}">{kit.esc(labels[index])}</text>')
    return {"points": points, "grid": "".join(grid), "left": left, "base": where(0, max(bottom_value, 0) if bottom_value <= 0 <= top_value else bottom_value)[1]}


def _trace(points: list[tuple[float, float]], values: list[float], share: float) -> tuple[float, float, float, float]:
    """Where the line is at a share of its width: x, y, share of its length, and the value there."""
    lengths = [math.dist(points[i], points[i + 1]) for i in range(len(points) - 1)]
    total = sum(lengths) or 1.0
    x = points[0][0] + share * (points[-1][0] - points[0][0])
    done = 0.0
    for index, length in enumerate(lengths):
        (ax, ay), (bx, by) = points[index], points[index + 1]
        if x <= bx or index == len(lengths) - 1:
            part = min(max((x - ax) / ((bx - ax) or 1.0), 0.0), 1.0)
            return x, ay + (by - ay) * part, (done + length * part) / total, values[index] + (values[index + 1] - values[index]) * part
        done += length
    return points[0][0], points[0][1], 0.0, values[0]


def _line_layers(
    mark: str, geo: dict[str, Any], values: list[float], height: int, begin: float, end: float, share_at: Callable[[float], float], places: int, prefix: str, suffix: str
) -> tuple[str, str]:
    """The drawn line, its fill, the travelling dot and its value flag. Each is a div, driven by the same samples, so none can slip."""
    points = geo["points"]
    path = "M" + " L".join(f"{x:.1f} {y:.1f}" for x, y in points)
    area = f"{path} L{points[-1][0]:.1f} {geo['base']:.1f} L{points[0][0]:.1f} {geo['base']:.1f} Z"
    frames = max(round((end - begin) * FPS), 8)
    span = end + 0.02
    head, line, fill, flags = (
        [(0.0, _at(points[0], height) + ";opacity:0")],
        [(0.0, "stroke-dashoffset:1")],
        [(0.0, f"clip-path:inset(0 {100 - points[0][0] / W * 100:.2f}% 0 0)")],
        [],
    )
    for k in range(frames + 1):
        moment = begin + (end - begin) * k / frames
        x, y, along, value = _trace(points, values, share_at(moment))
        flags.append(_show(value, places, prefix, suffix))
        if k % 3 and k != frames:
            continue
        at = moment / span * 100
        if k == 0:
            head.append((max(at - 1.2, 0.01), _at((x, y), height) + ";opacity:0"))
            line.append((at, "stroke-dashoffset:1"))
            fill.append((at, fill[0][1]))
        head.append((at, _at((x, y), height) + ";opacity:1"))
        line.append((at, f"stroke-dashoffset:{1 - along:.5f}"))
        fill.append((at, f"clip-path:inset(0 {100 - x / W * 100:.3f}% 0 0)"))
    style = _keys(f"{mark}h", head) + _keys(f"{mark}l", line) + _keys(f"{mark}f", fill)
    motion = f"{span:.3f}s linear both"
    layers = (
        f'<div class="ly fill" style="animation:{mark}f {motion}">{_svg(height, _path(area, measured=False))}</div>'
        f'<div class="ly line" style="animation:{mark}l {motion}">{_svg(height, _path(path))}</div>'
        f'<div class="ly head" style="animation:{mark}h {motion}"><i class="dot"></i><span class="flag">{_strip(flags, end - begin, begin)}</span></div>'
    )
    return style, layers


def _at(point: tuple[float, float], height: int) -> str:
    return f"transform:translate({point[0] / W * 100:.3f}%,{point[1] / height * 100:.3f}%)"


LINE_CSS = """
.F .gl{stroke:var(--hair);stroke-width:1.5}
.F .zero{stroke:color-mix(in srgb,var(--ink) 46%,transparent);stroke-width:2.5}
.F .grid{animation:dkfade .5s .25s ease-out both}
.F .fill path{fill:color-mix(in srgb,var(--accent) 17%,transparent)}
.F .line path{fill:none;stroke:var(--accent);stroke-width:7;stroke-linejoin:round;stroke-linecap:round;stroke-dasharray:1}
.F .dot{position:absolute;left:calc(-13px * var(--scale));top:calc(-13px * var(--scale));width:calc(26px * var(--scale));height:calc(26px * var(--scale));border-radius:50%;background:var(--accent);box-shadow:0 0 0 calc(6px * var(--scale)) var(--panel)}
.F .flag{position:absolute;left:0;top:0;padding:calc(3px * var(--scale)) calc(14px * var(--scale));border-radius:calc(10px * var(--scale));background:var(--accent);color:var(--panel);font-size:calc(32px * var(--scale));font-weight:700;letter-spacing:-.01em;transform:translate(-62%,calc(-100% - 24px * var(--scale)))}
"""


def _line_draw(item: dict[str, Any]) -> str:
    rows = kit.rows(item, "points")
    if len(rows) < 2:
        _fail("line_draw", "needs at least two points.")
    values = [_num(row.get("value"), "line_draw", "value") for row in rows]
    prefix, suffix = _affix(item)
    height = 420
    geo = _line_geometry(values, [str(row["text"]) for row in rows], height, prefix, suffix)
    begin = 0.55
    end = begin + min(kit.seconds(item) * 0.55, 3.0)

    def share_at(moment: float) -> float:
        u = (moment - begin) / (end - begin)
        return 1 - (1 - u) ** 2.1

    mark = _mark(item)
    style, layers = _line_layers(mark, geo, values, height, begin, end, share_at, _places(values), prefix, suffix)
    plot = f'<style>{style}</style><div class="plot" style="aspect-ratio:{W}/{height}"><div class="ly grid">{_svg(height, geo["grid"])}</div>{layers}</div>'
    return _card(item, plot)


def _chart_story(item: dict[str, Any]) -> str:
    rows = kit.rows(item, "points")
    calls = kit.rows(item, "callouts")
    if len(rows) < 2:
        _fail("chart_story", "needs at least two points.")
    labels = [str(row["text"]) for row in rows]
    values = [_num(row.get("value"), "chart_story", "value") for row in rows]
    total = kit.seconds(item)
    begin = 0.6
    marks = []
    for n, call in enumerate(calls):
        place = call.get("on")
        if isinstance(place, int | float) and not isinstance(place, bool):
            index = int(place) - 1 if 1 <= place <= len(labels) else -1
        else:
            index = labels.index(str(place)) if str(place) in labels else -1
        if index < 0:
            _fail("chart_story", f'a callout sits on "{place}", which is not a point. Use its label or its number.')
        wanted = call["delay"] if "at" in call else begin + (total * 0.7 - begin) * (n + 1) / (len(calls) + 0.3)
        marks.append({"index": index, "at": wanted, "text": call["text"]})
    marks.sort(key=lambda m: (m["index"], m["at"]))
    last = begin
    for spot in marks:
        spot["at"] = last = max(spot["at"], last + 0.8)
    end = min(max(last + 1.0, total * 0.74), max(total - 0.7, last + 0.3))
    span_index = len(labels) - 1
    stops = [(begin, 0.0), *[(m["at"], m["index"] / span_index) for m in marks], (end, 1.0)]

    def share_at(moment: float) -> float:
        for (a, sa), (b, sb) in zip(stops, stops[1:], strict=False):
            if moment <= b:
                return sa + (sb - sa) * _smooth((moment - a) / ((b - a) or 1.0))
        return 1.0

    prefix, suffix = _affix(item)
    height = 420
    geo = _line_geometry(values, labels, height, prefix, suffix, top=128)
    points = geo["points"]
    placed: list[tuple[float, float, float, float]] = []
    for spot in marks:
        x, y = points[spot["index"]]
        wide = min(len(spot["text"]) * 15.5 + 30, 320.0)
        tall = 58.0 + 33.0 * (math.ceil(len(spot["text"]) * 15.5 / 290) - 1)
        choices = []
        for side, low, high in (("left", x - wide + 26, x + 26), ("right", x - 26, x + wide - 26)):
            if low < geo["left"] or high > W:
                continue  # a box never sits on the axis labels or past the card
            under = min([y, *[py for px, py in points if low - 24 <= px <= high + 24]])
            choices.append((under, side, low, high))
        choices = choices or [(y, "right", x - 26, min(x + wide - 26, float(W)))]
        under, side, low, high = max(choices, key=lambda c: c[0])
        bottom = under - 34
        clashing = True
        while clashing:
            clashing = False
            for a0, a1, b0, b1 in placed:
                if low < a1 + 12 and high > a0 - 12 and bottom > b0 - 10 and bottom - tall < b1 + 10:
                    bottom, clashing = b0 - 10, True
        placed.append((low, high, bottom - tall, bottom))
        spot.update(side=side, bottom=bottom)
    mark = _mark(item)
    style, layers = _line_layers(mark, geo, values, height, begin, end, share_at, _places(values), prefix, suffix)
    notes = []
    for spot in marks:
        x, y = points[spot["index"]]
        leader = f'<line x1="{x:.1f}" x2="{x:.1f}" y1="{spot["bottom"]:.1f}" y2="{y - 12:.1f}" pathLength="1"/>'
        notes.append(f'<div class="ly lead" style="animation:dkdraw .36s var(--ease) {spot["at"]:.3f}s both">{_svg(height, leader)}</div>')
        notes.append(f'<i class="pin" style="left:{_pct(x, W)};top:{_pct(y, height)};animation-delay:{spot["at"]:.3f}s"></i>')
        edge = f"left:{_pct(max(x - 26, 0.0), W)}" if spot["side"] == "right" else f"right:{_pct(W - min(x + 26, W), W)}"
        notes.append(
            f'<div class="call" style="{edge};top:{_pct(spot["bottom"], height)}"><span style="animation-delay:{spot["at"] + 0.06:.3f}s">{kit.esc(spot["text"])}</span></div>'
        )
    plot = (
        f'<style>{style}</style><div class="plot" style="aspect-ratio:{W}/{height}"><div class="ly grid">{_svg(height, geo["grid"])}</div>{layers}{"".join(notes)}</div>'
    )
    return _card(item, plot)


# Single numbers


def _count_up(item: dict[str, Any]) -> str:
    value = _num(item["value"], "count_up", "value")
    places = _places([value])
    prefix, suffix = _affix(item)
    run = float(item.get("run") or min(kit.seconds(item) * 0.55, 1.7))
    width = len(_show(value, places, prefix, suffix))
    size = min(236, 1400 / max(width, 1))
    number = _count(value, places, prefix, suffix, run, 0.1, rate=float(item.get("rate") or 0))
    lead = f";--lead:{float(item['lead']):.3f}s" if item.get("lead") is not None else ""
    return (
        f'<div class="wrap" style="--r:{run:.3f}s{lead}"><div class="fig"><div class="num" style="font-size:calc({size:.0f}px * var(--scale))">{number}</div>'
        f'<i class="rule"></i></div><p class="lab">{kit.emphasis(item["label"])}</p></div>'
    )


def _number_wheel(item: dict[str, Any]) -> str:
    value = _num(item["value"], "number_wheel", "value")
    places = _places([value])
    prefix, suffix = _affix(item)
    text = _show(value, places)
    digits = sum(ch.isdigit() for ch in text)
    rest = len(prefix) + len(suffix) + len(text) - digits
    size = min(168, 760 / max(digits * 0.82 + rest * 0.5, 1))
    longest = min(kit.seconds(item) * 0.6, 2.0)
    cells, turn = [], 0
    for ch in prefix:
        cells.append(f'<span class="ch">{kit.esc(ch)}</span>')
    for ch in text:
        if not ch.isdigit():
            cells.append(f'<span class="ch">{kit.esc(ch)}</span>')
            continue
        run = longest * (0.62 + 0.38 * turn / max(digits - 1, 1))
        reel = "".join(f"<i>{n % 10}</i>" for n in range(30))
        cells.append(f'<span class="cell"><span class="reel" style="--to:{20 + int(ch)};--r:{run:.3f}s">{reel}</span></span>')
        turn += 1
    for ch in suffix:
        cells.append(f'<span class="ch">{kit.esc(ch)}</span>')
    dial = f'<div class="dial" style="font-size:calc({size:.0f}px * var(--scale))">{"".join(cells)}</div>'
    return f'<div class="card"><i class="tab"></i>{dial}<p class="lab">{kit.emphasis(item["label"])}</p>{_foot(item)}</div>'


def _progress_ring(item: dict[str, Any]) -> str:
    value = _num(item["value"], "progress_ring", "value")
    if not 0 <= value <= 100:
        _fail("progress_ring", f'"value" is a percentage from 0 to 100, and {value:g} is not.')
    run = min(kit.seconds(item) * 0.55, 1.9)
    rim = '<circle class="rim" cx="200" cy="200" r="164"/>'
    ticks = "".join(f'<line class="mk" x1="200" x2="200" y1="14" y2="26" transform="rotate({a} 200 200)"/>' for a in (0, 90, 180, 270))
    arc = f'<circle class="arc" cx="200" cy="200" r="164" pathLength="100" stroke-dasharray="{value:g} 100" transform="rotate(-90 200 200)"/>'
    number = _count(value, _places([value]), "", "%", run, 0.3)
    dial = (
        f'<div class="dial"><div class="ly"><svg viewBox="0 0 400 400" aria-hidden="true">{rim}{ticks}</svg></div>'
        f'<div class="ly sweep" style="--r:{run:.3f}s;--v:{value:g}"><svg viewBox="0 0 400 400" aria-hidden="true">{arc}</svg></div><div class="mid">{number}</div></div>'
    )
    text = f'<div class="say"><h2>{kit.emphasis(item["label"])}</h2></div>'
    return f'<div class="card"><i class="tab"></i><div class="row">{dial}{text}</div>{_foot(item)}</div>'


def _waffle(item: dict[str, Any]) -> str:
    value = _num(item["value"], "waffle", "value")
    if not 0 <= value <= 100:
        _fail("waffle", f'"value" is out of 100, and {value:g} is not.')
    run = min(kit.seconds(item) * 0.5, 1.7)
    step = run / max(math.ceil(value), 1)
    cells = []
    for row in range(10):
        for col in range(10):
            place = (9 - row) * 10 + col
            share = min(max(value - place, 0.0), 1.0)
            fill = f'<u style="--t:{0.35 + place * step:.3f}s;--p:{share:.3f}"></u>' if share else ""
            cells.append(f"<b>{fill}</b>")
    number = _count(value, _places([value]), "", str(item.get("suffix") or ""), run, 0.35, linear=True)
    side = f'<div class="say"><div class="num">{number}</div><p class="lab">{kit.emphasis(item["label"])}</p></div>'
    return _card(item, f'<div class="row"><div class="grid">{"".join(cells)}</div>{side}</div>')


# Bars


def _bar_build(item: dict[str, Any]) -> str:
    rows = kit.rows(item, "bars")
    values = _amounts(rows, "bar_build")
    top = max(values)
    prefix, suffix = _affix(item)
    places = _places(values)
    hot = _hot(rows, values)
    total = kit.seconds(item)
    run = min(total * 0.24, 0.9)
    slots, names = [], []
    width = (W - 14 * (len(rows) - 1)) / len(rows)
    longest = max(len(_show(v, places, prefix, suffix)) for v in values)
    size = min(36, width * 1.62 / max(longest, 1))
    for index, (row, value) in enumerate(zip(rows, values, strict=True)):
        wait = 0.45 + (row["delay"] if "at" in row else min(total * 0.42, len(rows) * 0.2) * index / len(rows))
        number = _count(value, places, prefix, suffix, run, wait)
        cls = "bar hot" if index == hot else "bar"
        slots.append(
            f'<div class="slot"><div class="climb" style="--f:{value / top:.4f};--r:{run:.3f}s;--t:{wait:.3f}s"><span class="v" style="font-size:calc({size:.1f}px * var(--scale))">{number}</span><b class="{cls}"></b></div></div>'
        )
        names.append(f'<span class="{"on" if index == hot else ""}">{kit.esc(row["text"])}</span>')
    return _card(item, f'<div class="cols">{"".join(slots)}</div><div class="names">{"".join(names)}</div>')


def _bar_race(item: dict[str, Any]) -> str:
    periods = [str(p) for p in item["periods"]]
    rows = kit.rows(item, "rows")
    if len(periods) < 2:
        _fail("bar_race", "needs at least two periods.")
    series = []
    for row in rows:
        raw = row.get("values")
        if not isinstance(raw, list) or len(raw) != len(periods):
            _fail("bar_race", f'each row needs one value per period, {len(periods)} in all, and "{row.get("text")}" does not have that.')
        series.append(_amounts([{"value": v} for v in raw], "bar_race"))
    top = max(max(s) for s in series)
    prefix, suffix = _affix(item)
    places = _places(v for s in series for v in s)
    total = kit.seconds(item)
    enter, lead = 0.3, 0.95
    run = max(total - lead - max(total * 0.16, 0.9), 0.5 * (len(periods) - 1))
    end = lead + run
    span = end + 0.02
    gaps = len(periods) - 1

    def amount(index: int, moment: float) -> float:
        place = min(max((moment - lead) / run * gaps, 0.0), gaps)
        low = min(int(place), gaps - 1)
        return series[index][low] + (series[index][low + 1] - series[index][low]) * (place - low)

    moments = [lead + run * k / round(run * 12) for k in range(round(run * 12) + 1)]
    order = [[0] * len(moments) for _ in rows]
    for k, moment in enumerate(moments):
        ranked = sorted(range(len(rows)), key=lambda r: (-amount(r, moment), r))
        for place, index in enumerate(ranked):
            order[index][k] = place
    reach = 3
    smooth = []
    for index in range(len(rows)):
        once = [sum(order[index][max(k - reach, 0) : k + reach + 1]) / len(order[index][max(k - reach, 0) : k + reach + 1]) for k in range(len(moments))]
        smooth.append([sum(once[max(k - reach, 0) : k + reach + 1]) / len(once[max(k - reach, 0) : k + reach + 1]) for k in range(len(moments))])
    mark = _mark(item)
    css, bars = [], []
    final = [series[i][-1] for i in range(len(rows))]
    hot = _hot(rows, final)
    for index, row in enumerate(rows):
        slide = [
            (0.0, f"transform:translateY({smooth[index][0] * 100:.1f}%)"),
            *[(m / span * 100, f"transform:translateY({smooth[index][k] * 100:.2f}%)") for k, m in enumerate(moments)],
        ]
        grow = [(0.0, "transform:scaleX(0)"), (enter / span * 100, "transform:scaleX(0);animation-timing-function:cubic-bezier(.16,1,.3,1)")]
        move = [(0.0, "transform:translateX(0%)"), (enter / span * 100, "transform:translateX(0%);animation-timing-function:cubic-bezier(.16,1,.3,1)")]
        for period in range(len(periods)):
            at = (lead + run * period / gaps) / span * 100
            share = series[index][period] / top
            grow.append((at, f"transform:scaleX({share:.4f});animation-timing-function:linear"))
            move.append((at, f"transform:translateX({share * 100:.2f}%);animation-timing-function:linear"))
        css += [_keys(f"{mark}y{index}", slide), _keys(f"{mark}b{index}", grow), _keys(f"{mark}v{index}", move)]
        count = round((lead - enter) * FPS)
        texts = [_show(series[index][0] * _out(k / count), places, prefix, suffix) for k in range(count)]
        ticks = round(run * 15)
        texts += [_show(amount(index, lead + run * k / ticks), places, prefix, suffix) for k in range(ticks)] + [_show(series[index][-1], places, prefix, suffix)]
        strip = _value_steps(texts, enter, lead, run, count, ticks, mark, index)
        css.append(strip[0])
        name = f'<span class="nm{" on" if index == hot else ""}">{kit.esc(row["text"])}</span>'
        bar = f'<b class="bar{" hot" if index == hot else ""}" style="animation:{mark}b{index} {span:.3f}s linear both"></b>'
        tag = f'<span class="val" style="animation:{mark}v{index} {span:.3f}s linear both"><span>{strip[1]}</span></span>'
        bars.append(
            f'<div class="r{" top" if index == hot else ""}" style="animation:{mark}y{index} {span:.3f}s linear both">{name}<span class="tr">{bar}{tag}</span></div>'
        )
    flips = [(0.0, "transform:translateY(0);animation-timing-function:steps(1,end)")]
    for period in range(1, len(periods)):
        at = (lead + run * (period - 0.5) / gaps) / span * 100
        flips.append((at, f"transform:translateY({-period * 1.1:.2f}em);animation-timing-function:steps(1,end)"))
    flips.append((100.0, f"transform:translateY({-(len(periods) - 1) * 1.1:.2f}em)"))
    css.append(_keys(f"{mark}p", flips))
    css.append(_keys(f"{mark}n", [(0.0, "transform:scaleX(0)"), (lead / span * 100, "transform:scaleX(0)"), ((lead + run) / span * 100, "transform:scaleX(1)")]))
    cells = "".join(f"<i>{kit.esc(p)}</i>" for p in periods)
    ticks_on_rail = "".join(f'<u style="left:{_pct(n, gaps)}"></u>' for n in range(len(periods)))
    clock = (
        f'<div class="clock"><div class="when"><span style="animation:{mark}p {span:.3f}s linear both">{cells}</span></div>'
        f'<div class="rail"><i style="animation:{mark}n {span:.3f}s linear both"></i>{ticks_on_rail}</div></div>'
    )
    return _card(item, f'<style>{"".join(css)}</style><div class="race" style="--rows:{len(rows)}">{"".join(bars)}</div>{clock}')


def _value_steps(texts: list[str], enter: float, lead: float, run: float, count: int, ticks: int, mark: str, index: int) -> tuple[str, str]:
    """A value flag with two speeds: the climb to the first period, then the run through the rest."""
    last = len(texts) - 1
    span = lead + run + 0.02
    frames = [
        (0.0, "transform:translateY(0);animation-timing-function:linear"),
        (enter / span * 100, f"transform:translateY(0);animation-timing-function:steps({count},end)"),
    ]
    frames.append((lead / span * 100, f"transform:translateY({-count * 1.16:.2f}em);animation-timing-function:steps({ticks},end)"))
    frames.append((100.0, f"transform:translateY({-last * 1.16:.2f}em)"))
    cells = "".join(f"<i>{kit.esc(text)}</i>" for text in texts)
    return (
        _keys(f"{mark}t{index}", frames),
        f'<span class="ct"><span class="st" data-layout-allow-overflow data-layout-allow-overlap style="animation:{mark}t{index} {span:.3f}s linear both">{cells}</span></span>',
    )


# Comparisons and flows


def _spread(wanted: list[float], gap: float) -> list[float]:
    """Move labels the least that stops them touching, keeping their order."""
    order = sorted(range(len(wanted)), key=lambda i: wanted[i])
    out = list(wanted)
    for _ in range(40):
        moved = False
        for a, b in zip(order, order[1:], strict=False):
            need = out[a] + gap - out[b]
            if need > 0.01:
                out[a] -= need / 2
                out[b] += need / 2
                moved = True
        if not moved:
            break
    return out


def _slope(item: dict[str, Any]) -> str:
    rows = kit.rows(item, "rows")
    before = [_num(row.get("before"), "slope", "before") for row in rows]
    after = [_num(row.get("after"), "slope", "after") for row in rows]
    prefix, suffix = _affix(item)
    places = _places([*before, *after])
    changes = [abs(b - a) for a, b in zip(before, after, strict=True)]
    hot = next((i for i, row in enumerate(rows) if row.get("hot")), changes.index(max(changes)))
    low, high = min(*before, *after), max(*before, *after)
    span = (high - low) or 1.0
    height = max(340, 64 * len(rows) + 92)
    top, bottom, x0, x1 = 66, 26, 128, 474
    plot = height - top - bottom

    def place(value: float) -> float:
        return top + plot * (high - value) / span

    left_y = _spread([place(v) for v in before], 40)
    right_y = _spread([place(v) for v in after], 44)
    total = kit.seconds(item)
    run = min(total * 0.2, 0.8)
    layers, marks = [], []
    for index, row in enumerate(rows):
        wait = 0.6 + (row["delay"] if "at" in row else min(total * 0.36, len(rows) * 0.34) * index / max(len(rows), 1))
        y0, y1 = place(before[index]), place(after[index])
        cls = "on" if index == hot else "off"
        line = f'<line x1="{x0}" x2="{x1}" y1="{y0:.1f}" y2="{y1:.1f}" pathLength="1"/>'
        layers.append(
            (index == hot, f'<div class="ly stroke {cls}" style="animation:dkdraw {run:.3f}s cubic-bezier(.45,0,.2,1) {wait:.3f}s both">{_svg(height, line)}</div>')
        )
        marks.append(f'<i class="pt {cls}" style="left:{_pct(x0, W)};top:{_pct(y0, height)};animation-delay:{wait - 0.1:.3f}s"></i>')
        marks.append(f'<i class="pt {cls}" style="left:{_pct(x1, W)};top:{_pct(y1, height)};animation-delay:{wait + run - 0.08:.3f}s"></i>')
        marks.append(
            f'<span class="lv {cls}" style="right:{_pct(W - x0 + 22, W)};top:{_pct(left_y[index], height)};animation-delay:{wait - 0.1:.3f}s">{kit.esc(_show(before[index], places, prefix, suffix))}</span>'
        )
        name = f"<b>{kit.esc(_show(after[index], places, prefix, suffix))}</b> {kit.esc(row['text'])}"
        marks.append(f'<span class="rv {cls}" style="left:{_pct(x1 + 22, W)};top:{_pct(right_y[index], height)};animation-delay:{wait + run - 0.08:.3f}s">{name}</span>')
    drawn = "".join(html for _, html in sorted(layers, key=lambda pair: pair[0]))
    heads = f'<span class="ty" style="left:{_pct(x0, W)}">{kit.esc(item["left"])}</span><span class="ty" style="left:{_pct(x1, W)}">{kit.esc(item["right"])}</span>'
    rails = "".join(f'<line class="rl" x1="{x}" x2="{x}" y1="{top - 18}" y2="{height - 6}"/>' for x in (x0, x1))
    return _card(item, f'<div class="plot" style="aspect-ratio:{W}/{height}"><div class="ly">{_svg(height, rails)}</div>{heads}{drawn}{"".join(marks)}</div>')


def _sankey(item: dict[str, Any]) -> str:
    nodes: dict[str, dict[str, Any]] = {}

    def node(name: Any) -> dict[str, Any]:
        return nodes.setdefault(str(name), {"name": str(name), "to": {}, "in": 0.0, "hot": False, "depth": 0, "from": {}})

    for row in kit.rows(item, "nodes"):
        here = node(row["text"])
        here["hot"] = here["hot"] or bool(row.get("hot"))
        for target, amount in (row.get("to") or {}).items():
            value = _num(amount, "sankey", "to")
            if value <= 0:
                _fail("sankey", "a flow must be above zero.")
            there = node(target)
            here["to"][there["name"]] = here["to"].get(there["name"], 0.0) + value
            there["from"][here["name"]] = there["from"].get(here["name"], 0.0) + value
            there["in"] += value
    if not any(n["to"] for n in nodes.values()):
        _fail("sankey", 'needs at least one flow in "to".')
    for _ in range(len(nodes) + 1):
        changed = False
        for here in nodes.values():
            for target in here["to"]:
                if target == here["name"] or nodes[target]["depth"] <= here["depth"]:
                    if nodes[target]["depth"] > len(nodes) or target == here["name"]:
                        _fail("sankey", "flows run in a loop, so they cannot be laid out left to right.")
                    nodes[target]["depth"] = here["depth"] + 1
                    changed = True
        if not changed:
            break
    last = max(n["depth"] for n in nodes.values())
    for here in nodes.values():
        here["size"] = max(here["in"], sum(here["to"].values()))
        if not here["to"]:
            here["depth"] = last
    layers = [[n for n in nodes.values() if n["depth"] == d] for d in range(last + 1)]
    height, gap, node_w = 420, 26, 14
    scale = min((height - gap * (len(layer) - 1)) / sum(n["size"] for n in layer) for layer in layers if layer)
    for _ in range(2):
        for depth, layer in enumerate(layers):
            if depth:
                layer.sort(key=lambda n: sum(nodes[s].get("slot", 0) * v for s, v in n["from"].items()) / (n["in"] or 1.0))
            used = sum(n["size"] for n in layer) * scale
            room = (height - used) / max(len(layer) - 1, 1)
            y = 0.0 if len(layer) > 1 else (height - used) / 2
            for slot, here in enumerate(layer):
                here.update(y=y, tall=here["size"] * scale, x=(W - node_w) * depth / max(last, 1), slot=slot)
                y += here["tall"] + room
    total = kit.seconds(item)
    hop = min((total * 0.62 - 0.5) / max(last, 1), 1.0)
    bands, marks = [], []
    wanted = {name for name, n in nodes.items() if n["hot"]}
    grow = list(wanted)
    while grow:
        for source in nodes[grow.pop()]["from"]:
            if source not in wanted:
                wanted.add(source)
                grow.append(source)
    used_in: dict[str, float] = {}
    for layer in layers:
        for here in layer:
            offset = 0.0
            for target in sorted(here["to"], key=lambda t: nodes[t]["y"]):
                there = nodes[target]
                width = here["to"][target] * scale
                y0 = here["y"] + offset + width / 2
                y1 = there["y"] + used_in.get(target, 0.0) + width / 2
                offset += width
                used_in[target] = used_in.get(target, 0.0) + width
                x0, x1 = here["x"] + node_w, there["x"]
                bend = (x1 - x0) * 0.5
                path = f"M{x0:.1f} {y0:.1f} C{x0 + bend:.1f} {y0:.1f} {x1 - bend:.1f} {y1:.1f} {x1:.1f} {y1:.1f}"
                on = target in wanted and here["name"] in wanted
                wait = 0.55 + here["depth"] * hop
                reach = max(there["depth"] - here["depth"], 1)
                html = f'<div class="ly band {"on" if on else "off"}" style="animation:dkdraw {hop * reach:.3f}s cubic-bezier(.45,0,.3,1) {wait:.3f}s both"><svg viewBox="0 0 {W} {height}" aria-hidden="true"><path d="{path}" pathLength="1" stroke-width="{width:.1f}"/></svg></div>'
                bands.append((on, html))
    places = _places(n["size"] for n in nodes.values())
    prefix, suffix = _affix(item)
    for here in nodes.values():
        wait = 0.45 + here["depth"] * hop
        on = here["name"] in wanted
        marks.append(
            f'<i class="nd{" on" if on else ""}" style="left:{_pct(here["x"], W)};top:{_pct(here["y"], height)};height:{_pct(here["tall"], height)};animation-delay:{wait:.3f}s"></i>'
        )
        if here["depth"] == last and last > 0:
            side = f"right:{_pct(W - here['x'] + 14, W)}"
        elif here["depth"] == 0:
            side = f"left:{_pct(here['x'] + node_w + 14, W)}"
        else:
            side = f"left:{_pct(here['x'] + node_w / 2, W)}"
        mid = here["y"] + here["tall"] / 2
        text = f"<b>{kit.esc(_show(here['size'], places, prefix, suffix))}</b> {kit.esc(here['name'])}"
        marks.append(
            f'<span class="lb{" on" if on else ""}{" mid" if 0 < here["depth"] < last else ""}" style="{side};top:{_pct(mid, height)};animation-delay:{wait + (hop * 0.8 if here["depth"] else 0.1):.3f}s">{text}</span>'
        )
    drawn = "".join(html for _, html in sorted(bands, key=lambda pair: pair[0]))
    return _card(item, f'<div class="plot" style="aspect-ratio:{W}/{height}">{drawn}{"".join(marks)}</div>')


def _squarify(values: list[float], x: float, y: float, w: float, h: float) -> list[tuple[float, float, float, float]]:
    """Blocks with area proportional to value and sides as square as the data allows. `values` must be largest first."""
    area = w * h / (sum(values) or 1.0)
    left = [v * area for v in values]
    out: list[tuple[float, float, float, float]] = []

    def worst(row: list[float], side: float) -> float:
        total = sum(row)
        return max(side * side * max(row) / (total * total), total * total / (side * side * min(row)))

    while left:
        side = min(w, h)
        row = [left.pop(0)]
        while left and worst([*row, left[0]], side) <= worst(row, side):
            row.append(left.pop(0))
        thick = sum(row) / side
        at = 0.0
        for size in row:
            run = size / thick
            out.append((x, y + at, thick, run) if w >= h else (x + at, y, run, thick))
            at += run
        if w >= h:
            x, w = x + thick, w - thick
        else:
            y, h = y + thick, h - thick
    return out


def _treemap_zoom(item: dict[str, Any]) -> str:
    rows = kit.rows(item, "blocks")
    values = _amounts(rows, "treemap_zoom")
    order = sorted(range(len(rows)), key=lambda i: -values[i])
    hot = _hot(rows, values)
    height = 420
    boxes = _squarify([values[i] for i in order], 0, 0, W, height)
    total = kit.seconds(item)
    zoom = min(total * 0.42, 2.4)
    prefix, suffix = _affix(item)
    places = _places(values)
    whole = sum(values)
    blocks, target = [], boxes[0]
    tone = 0
    for box, index in zip(boxes, order, strict=True):
        x, y, w, h = box
        name = str(rows[index]["text"])
        on = index == hot
        if on:
            target = box
            tint = ""
        else:
            tint = f";background:color-mix(in srgb,var(--ink) {30 - tone * 4}%,var(--surface))"
            tone += 1
        fits = w > 70 and h > 58
        room = w - 28
        size = 32 if room >= len(name) * 17 else 26 if room >= len(name) * 14 else 0
        label = ""
        if fits and size:
            label = f'<span class="tx" style="font-size:calc({size}px * var(--scale))">{kit.esc(name)}<small>{kit.esc(_show(values[index], places, prefix, suffix))}</small></span>'
        elif fits:
            label = f'<span class="tx" style="font-size:calc(26px * var(--scale))"><small>{kit.esc(_show(values[index], places, prefix, suffix))}</small></span>'
        wait = 0.4 + 0.07 * order.index(index)
        blocks.append(
            f'<div class="bk{" on" if on else ""}" style="left:{_pct(x, W)};top:{_pct(y, height)};width:{_pct(w, W)};height:{_pct(h, height)};animation-delay:{wait:.3f}s{tint}">{label}</div>'
        )
    x, y, w, h = target
    scale = max(W / w, height / h) * 1.05
    shift_x = ((W - w * scale) / 2 - x * scale) / W * 100
    shift_y = ((height - h * scale) / 2 - y * scale) / height * 100
    share = values[hot] / whole * 100
    detail = f"<p>{kit.esc(item['detail'])}</p>" if item.get("detail") else ""
    number = _count(share, 0 if abs(share - round(share)) < 0.05 else 1, "", "%", 0.8, zoom + 0.5)
    panel = f'<div class="zoomed" style="--z:{zoom + 0.42:.3f}s"><strong>{number}</strong><span>{kit.esc(rows[hot]["text"])}, {kit.esc(_show(values[hot], places, prefix, suffix))}</span>{detail}</div>'
    cam = f'<div class="cam" style="--tx:{shift_x:.3f}%;--ty:{shift_y:.3f}%;--s:{scale:.4f};--z:{zoom:.3f}s">{"".join(blocks)}</div>'
    return _card(item, f'<div class="plot map" style="aspect-ratio:{W}/{height};--z:{zoom:.3f}s">{cam}{panel}</div>')


# Grids and scatters


def _calendar_heat(item: dict[str, Any]) -> str:
    raw = item["values"]
    values = [_num(v, "calendar_heat", "values") for v in raw]
    if not values or max(values) <= 0 or min(values) < 0:
        _fail("calendar_heat", '"values" needs amounts of zero or more, with at least one above zero.')
    top = max(values)
    weeks = math.ceil(len(values) / 7)
    run = min(kit.seconds(item) * 0.42, 1.8)
    step = run / max(weeks, 1)
    peak = values.index(top)
    places = _places(values)
    cells = []
    for index, value in enumerate(values):
        col, row = index // 7, index % 7
        level = 0 if value <= 0 else min(math.ceil(value / top * 4), 4)
        cells.append(f'<b class="l{level}" style="--t:{0.45 + col * step + row * 0.012:.3f}s"></b>')
    days = "".join(f"<span>{d}</span>" for d in ("M", "", "W", "", "F", "", ""))
    months = "".join(f"<span>{kit.esc(m)}</span>" for m in item.get("months") or [])
    top_row = f'<div class="mo"><i></i><div>{months}</div></div>' if months else ""
    col, row = peak // 7, peak % 7
    across = (col + 0.5) / weeks * 100
    land = 0.5 + run
    flag = f'<span class="peak" style="animation-delay:{land:.3f}s">Peak {kit.esc(_show(top, places))}</span>'
    ring = f'<i class="ring" style="left:{across:.2f}%;top:{(row + 0.5) / 7 * 100:.2f}%;--w:{100 / weeks:.3f}%;animation-delay:{land:.3f}s"></i>'
    key = "".join(f'<b class="l{n}"></b>' for n in range(5))
    legend = f'<div class="key">{flag}<span class="low">0</span>{key}<span>{kit.esc(_show(top, places))}</span></div>'
    body = f'{top_row}<div class="cal"><div class="dy">{days}</div><div class="wrapg"><div class="grid">{"".join(cells)}</div>{ring}</div></div>'
    return _card(item, f'<div class="sized" style="--weeks:{weeks}">{body}</div>{legend}')


def _bubble_time(item: dict[str, Any]) -> str:
    periods = [str(p) for p in item["periods"]]
    rows = kit.rows(item, "rows")
    if len(periods) < 2:
        _fail("bubble_time", "needs at least two periods.")
    tracks = []
    for row in rows:
        points = row.get("points")
        if not isinstance(points, list) or len(points) != len(periods) or any(not isinstance(p, list) or len(p) != 3 for p in points):
            _fail("bubble_time", f'each row needs one [x, y, size] per period, and "{row.get("text")}" does not have that.')
        tracks.append([[_num(n, "bubble_time", "points") for n in p] for p in points])
    xs = [p[0] for t in tracks for p in t]
    ys = [p[1] for t in tracks for p in t]
    sizes = [p[2] for t in tracks for p in t]
    if min(sizes) < 0 or max(sizes) <= 0:
        _fail("bubble_time", "sizes must be zero or more, with at least one above zero.")
    x_ticks, y_ticks = _ticks(min(0.0, min(xs)), max(xs) * 1.14, 5), _ticks(min(0.0, min(ys)), max(ys) * 1.3, 5)
    height, left, right, top, bottom = 420, 76, 40, 14, 74
    biggest = max(sizes)

    def spot(point: list[float]) -> tuple[float, float, float]:
        x = left + (W - left - right) * (point[0] - x_ticks[0]) / (x_ticks[-1] - x_ticks[0])
        y = top + (height - top - bottom) * (y_ticks[-1] - point[1]) / (y_ticks[-1] - y_ticks[0])
        return x, y, math.sqrt(point[2] / biggest)

    grid = []
    for tick in y_ticks:
        y = spot([0, tick, 0])[1]
        grid.append(
            f'<line class="{"zero" if tick == y_ticks[0] else "gl"}" x1="{left}" x2="{W - right}" y1="{y:.1f}" y2="{y:.1f}"/><text x="{left - 12}" y="{y + 9:.1f}" text-anchor="end">{kit.esc(_short(tick))}</text>'
        )
    for tick in x_ticks:
        x = spot([tick, 0, 0])[0]
        grid.append(f'<text x="{x:.1f}" y="{height - bottom + 34}" text-anchor="middle">{kit.esc(_short(tick))}</text>')
    grid.append(f'<text class="ax" x="{W - right}" y="{height - 4}" text-anchor="end">{kit.esc(item["x_label"])}</text>')
    grid.append(f'<text class="ax" x="{left + 10}" y="{top + 30}">{kit.esc(item["y_label"])}</text>')
    total = kit.seconds(item)
    lead = 0.8
    run = max(total - lead - max(total * 0.16, 0.9), 0.5 * (len(periods) - 1))
    span = lead + run + 0.02
    hop = "animation-timing-function:cubic-bezier(.45,0,.25,1)"
    mark = _mark(item)
    finals = [t[-1][2] for t in tracks]
    hot = _hot(rows, finals)
    css, balls, trail = [], [], ""
    for index, (row, track) in enumerate(zip(rows, tracks, strict=True)):
        spots = [spot(p) for p in track]
        move, grow, name = [], [], []
        for period, (x, y, r) in enumerate(spots):
            at = (lead + run * period / (len(periods) - 1)) / span * 100
            move.append((at, f"transform:translate({x / W * 100:.3f}%,{y / height * 100:.3f}%);{hop}"))
            grow.append((at, f"transform:translate(-50%,-50%) scale({r:.4f});{hop}"))
            name.append((at, f"transform:translate(-50%,calc(-100% - {r * 62 + 8:.1f}px * var(--scale)));{hop}"))
        css += [
            _keys(f"{mark}m{index}", [(0.0, move[0][1]), *move]),
            _keys(f"{mark}g{index}", [(0.0, grow[0][1]), *grow]),
            _keys(f"{mark}n{index}", [(0.0, name[0][1]), *name]),
        ]
        on = index == hot
        if on:
            line = "M" + " L".join(f"{x:.1f} {y:.1f}" for x, y, _ in spots)
            lengths = [math.dist(spots[i][:2], spots[i + 1][:2]) for i in range(len(spots) - 1)]
            whole = sum(lengths) or 1.0
            walk, drawn = [(0.0, "stroke-dashoffset:1")], 0.0
            for period in range(len(periods)):
                drawn += lengths[period - 1] if period else 0.0
                walk.append(((lead + run * period / (len(periods) - 1)) / span * 100, f"stroke-dashoffset:{1 - drawn / whole:.4f};{hop}"))
            css.append(_keys(f"{mark}w", walk))
            trail = f'<div class="ly trail" style="animation:{mark}w {span:.3f}s linear both">{_svg(height, _path(line))}</div>'
        motion = f"{span:.3f}s linear both"
        balls.append(
            (
                on,
                f'<div class="ly bb{" on" if on else ""}" style="animation:{mark}m{index} {motion}"><i class="ball" style="animation:{mark}g{index} {motion}"></i><span class="nm" style="animation:{mark}n{index} {motion}">{kit.esc(row["text"])}</span></div>',
            )
        )
    flips = [(0.0, "transform:translateY(0);animation-timing-function:steps(1,end)")]
    for period in range(1, len(periods)):
        at = (lead + run * (period - 0.5) / (len(periods) - 1)) / span * 100
        flips.append((at, f"transform:translateY({-period * 1.1:.2f}em);animation-timing-function:steps(1,end)"))
    flips.append((100.0, f"transform:translateY({-(len(periods) - 1) * 1.1:.2f}em)"))
    css.append(_keys(f"{mark}p", flips))
    when = f'<div class="when"><span style="animation:{mark}p {span:.3f}s linear both">{"".join(f"<i>{kit.esc(p)}</i>" for p in periods)}</span></div>'
    drawn = "".join(html for _, html in sorted(balls, key=lambda pair: pair[0]))
    return _card(
        item,
        f'<style>{"".join(css)}</style><div class="plot" style="aspect-ratio:{W}/{height}"><div class="ly grid">{_svg(height, "".join(grid))}</div>{when}{trail}{drawn}</div>',
    )


SAMPLE = "Sample data for illustration"
TWELVE = ["Jan", "Feb", "Mar", "Apr", "May", "Jun", "Jul", "Aug", "Sep", "Oct", "Nov", "Dec"]


def _sample_days() -> list[int]:
    """Thirteen invented weeks: quiet weekends, a slow climb, one busy spell. Arithmetic, so the example never changes."""
    days = []
    for week in range(13):
        for day in range(7):
            weekend = 0.3 if day >= 5 else 1.0
            swell = 18 + week * 4 + 34 * math.exp(-(((week - 9) / 1.7) ** 2)) * (1 if day < 5 else 0.5)
            days.append(round(weekend * (swell + (day * 7 + week * 11) % 13)))
    return days


FORMATS = (
    Format(
        id="count_up",
        title="Count-up stat",
        summary="A big number climbs to its value while a rule draws beneath it.",
        group="data",
        jobs=("hook", "prove"),
        slots=(
            Slot("value", kind="number"),
            Slot("label", limit=70),
            Slot("prefix", limit=2, required=False),
            Slot("suffix", limit=4, required=False),
            Slot("run", kind="number", required=False),
            Slot("rate", kind="number", required=False),
            Slot("lead", kind="number", required=False),
        ),
        example={"value": 1284, "label": "Sample orders shipped last *quarter*"},
        draw=_count_up,
        seconds=3.0,
        shortest=1.8,
        longest=8.0,
        tall=375,
        sound="tick",
        css=_skin(
            "count_up",
            """
.f-count_up .wrap{position:relative;max-width:calc(880px * var(--scale))}
.f-count_up .wrap::after{content:"";position:absolute;z-index:-1;inset:-40% -24% -44% -30%;background:radial-gradient(closest-side,rgba(6,10,14,.56),rgba(6,10,14,.36) 50%,rgba(6,10,14,0))}
.f-count_up .num,.f-count_up .lab{filter:drop-shadow(0 2px 14px rgba(0,0,0,.45))}
.f-count_up .fig{display:inline-block;max-width:100%}
.f-count_up .num{font-weight:700;line-height:1.16;letter-spacing:-.028em;color:var(--ink);animation:culand .24s linear calc(var(--r) * .86) both}
.f-count_up .rule{display:block;height:calc(9px * var(--scale));margin:calc(8px * var(--scale)) 0 calc(26px * var(--scale));background:var(--accent);transform-origin:0 50%;animation:cugrow var(--r) var(--ease) .1s both}
.f-count_up .lab{margin:0;font-size:calc(46px * var(--scale));font-weight:500;line-height:1.18;letter-spacing:-.02em;text-wrap:balance;animation:dkrise .5s var(--ease) var(--lead,calc(var(--r) * .45)) both}
@keyframes culand{from{color:var(--ink)}to{color:var(--accent)}}
@keyframes cugrow{from{transform:scaleX(0)}to{transform:none}}
""",
        ),
    ),
    Format(
        id="number_wheel",
        title="Number wheel",
        summary="Digits roll like an odometer and lock into place from left to right.",
        group="data",
        jobs=("prove",),
        slots=(
            Slot("value", kind="number"),
            Slot("label", limit=64),
            Slot("prefix", limit=2, required=False),
            Slot("suffix", limit=4, required=False),
            Slot("note", limit=48, required=False),
        ),
        example={"value": 48210, "label": "Sample meters read this *season*", "note": SAMPLE},
        draw=_number_wheel,
        seconds=3.2,
        shortest=2.0,
        longest=8.0,
        tall=405,
        sound="tick",
        fonts=("mono",),
        css=_skin(
            "number_wheel",
            """
.f-number_wheel .dial{display:flex;align-items:center;gap:.05em;font-family:var(--mono);font-weight:600;letter-spacing:0;line-height:1.3}
.f-number_wheel .cell{position:relative;display:block;flex:none;width:.74em;height:1.3em;overflow:hidden;border-radius:.09em;background:var(--surface);box-shadow:0 0 0 1px var(--hair) inset}
.f-number_wheel .cell::after{content:"";position:absolute;inset:0;background:linear-gradient(180deg,var(--panel) 2%,transparent 30%,transparent 70%,var(--panel) 98%)}
.f-number_wheel .reel{display:block;animation:nwturn var(--r) cubic-bezier(.3,.05,.16,1) .25s both}
.f-number_wheel .reel i{display:block;height:1.3em;line-height:1.3em;text-align:center;font-style:normal;color:var(--ink)}
.f-number_wheel .ch{flex:none;color:var(--ink);font-size:.72em}
.f-number_wheel .lab{margin:calc(22px * var(--scale)) 0 0;font-size:calc(44px * var(--scale));font-weight:500;line-height:1.16;letter-spacing:-.02em;text-wrap:balance}
@keyframes nwturn{from{transform:none}to{transform:translateY(calc(var(--to) * -1.3em))}}
""",
        ),
    ),
    Format(
        id="bar_race",
        title="Bar chart race",
        summary="Ranked bars overtake each other on one fixed scale as the periods tick by.",
        group="data",
        jobs=("prove", "explain"),
        slots=(
            Slot("title", limit=52),
            Slot("periods", kind="list", limit=7, each=6),
            Slot("rows", kind="list", limit=6, each=13),
            Slot("sub", limit=56, required=False),
            Slot("prefix", limit=2, required=False),
            Slot("suffix", limit=3, required=False),
            Slot("note", limit=48, required=False),
        ),
        example={
            "title": "Sample regions change places",
            "sub": "Parcels sent, thousands",
            "periods": ["2019", "2020", "2021", "2022", "2023", "2024", "2025"],
            "rows": [
                {"text": "Northfield", "values": [62, 66, 71, 74, 78, 80, 83]},
                {"text": "Eastmoor", "values": [48, 55, 66, 79, 88, 101, 118], "hot": True},
                {"text": "Southbank", "values": [55, 57, 58, 60, 63, 69, 72]},
                {"text": "Westmere", "values": [30, 37, 49, 58, 74, 83, 96]},
                {"text": "Highgate", "values": [41, 42, 40, 44, 47, 49, 50]},
            ],
            "note": SAMPLE,
        },
        draw=_bar_race,
        seconds=7.5,
        shortest=5.0,
        longest=16.0,
        tall=745,
        sound="whoosh",
        css=_skin(
            "bar_race",
            """
.f-bar_race .card{background:var(--panel)}
.f-bar_race .race{position:relative;height:calc(var(--rows) * 64px * var(--scale));margin-top:calc(18px * var(--scale))}
.f-bar_race .r{position:absolute;left:0;right:0;top:0;height:calc(64px * var(--scale));display:flex;align-items:center;background:var(--panel)}
.f-bar_race .r.top{z-index:2}
.f-bar_race .nm{flex:none;width:calc(226px * var(--scale));font-size:calc(31px * var(--scale));font-weight:500;letter-spacing:-.012em;white-space:nowrap;color:var(--soft)}
.f-bar_race .nm.on{color:var(--ink);font-weight:600}
.f-bar_race .tr{position:relative;flex:1;height:72%;margin-right:calc(136px * var(--scale))}
.f-bar_race .bar{position:absolute;inset:0;transform-origin:0 50%;background:var(--mute)}
.f-bar_race .bar.hot{background:var(--accent)}
.f-bar_race .val{position:absolute;inset:0;display:flex;align-items:center}
.f-bar_race .val>span{padding-left:calc(12px * var(--scale));font-size:calc(31px * var(--scale));font-weight:600;letter-spacing:-.012em}
.f-bar_race .clock{display:flex;align-items:center;gap:calc(26px * var(--scale));margin-top:calc(14px * var(--scale))}
.f-bar_race .when{flex:none;height:1.1em;overflow:hidden;font-size:calc(58px * var(--scale));font-weight:700;line-height:1.1;letter-spacing:-.03em;color:var(--ink);font-variant-numeric:tabular-nums}
.f-bar_race .when span{display:block}
.f-bar_race .when i{display:block;height:1.1em;font-style:normal;white-space:nowrap}
.f-bar_race .rail{position:relative;flex:1;height:calc(4px * var(--scale));background:var(--hair)}
.f-bar_race .rail i{position:absolute;inset:0;background:var(--accent);transform-origin:0 50%}
.f-bar_race .rail u{position:absolute;top:calc(-6px * var(--scale));width:calc(3px * var(--scale));height:calc(16px * var(--scale));margin-left:calc(-1.5px * var(--scale));background:var(--mute)}
""",
        ),
    ),
    Format(
        id="line_draw",
        title="Line draw-on",
        summary="A trend line writes itself left to right, its value flag riding the tip.",
        group="data",
        jobs=("prove", "explain"),
        slots=(
            Slot("title", limit=52),
            Slot("points", kind="list", limit=14, each=6),
            Slot("sub", limit=56, required=False),
            Slot("prefix", limit=2, required=False),
            Slot("suffix", limit=3, required=False),
            Slot("note", limit=48, required=False),
        ),
        example={
            "title": "Sample visits *doubled* in a year",
            "sub": "Visits per month, thousands",
            "points": [{"text": m, "value": v} for m, v in zip(TWELVE, [41, 44, 43, 51, 56, 54, 62, 68, 66, 74, 79, 86], strict=True)],
            "note": SAMPLE,
        },
        draw=_line_draw,
        seconds=5.0,
        shortest=2.8,
        longest=12.0,
        tall=740,
        sound="rise",
        css=_skin("line_draw", LINE_CSS.replace(".F", ".f-line_draw")),
    ),
    Format(
        id="bar_build",
        title="Bar build",
        summary="Columns rise from a zero baseline in order, each carrying its own value.",
        group="data",
        jobs=("prove",),
        slots=(
            Slot("title", limit=52),
            Slot("bars", kind="list", limit=7, each=6),
            Slot("sub", limit=56, required=False),
            Slot("prefix", limit=2, required=False),
            Slot("suffix", limit=3, required=False),
            Slot("note", limit=48, required=False),
        ),
        example={
            "title": "Sample bookings peak on *Thursday*",
            "sub": "Bookings per day",
            "bars": [
                {"text": "Mon", "value": 118},
                {"text": "Tue", "value": 142},
                {"text": "Wed", "value": 169},
                {"text": "Thu", "value": 231, "hot": True},
                {"text": "Fri", "value": 194},
                {"text": "Sat", "value": 86},
            ],
            "note": SAMPLE,
        },
        draw=_bar_build,
        seconds=4.5,
        shortest=2.6,
        longest=10.0,
        tall=690,
        sound="rise",
        css=_skin(
            "bar_build",
            """
.f-bar_build .cols{display:flex;gap:calc(14px * var(--scale));margin-top:calc(22px * var(--scale));border-bottom:calc(3px * var(--scale)) solid color-mix(in srgb,var(--ink) 55%,transparent)}
.f-bar_build .slot{position:relative;flex:1 1 0;min-width:0;height:calc(350px * var(--scale));overflow:hidden}
.f-bar_build .climb{--lab:calc(50px * var(--scale));position:absolute;left:0;right:0;bottom:0;height:calc(var(--lab) + (100% - var(--lab)) * var(--f));display:flex;flex-direction:column;align-items:center;animation:bbrise var(--r) var(--ease) var(--t) both}
.f-bar_build .v{flex:none;height:var(--lab);display:flex;align-items:center;font-weight:600;letter-spacing:-.015em;white-space:nowrap}
.f-bar_build .bar{flex:1;width:100%;background:var(--mute)}
.f-bar_build .bar.hot{background:var(--accent)}
.f-bar_build .names{display:flex;gap:calc(14px * var(--scale));margin-top:calc(12px * var(--scale))}
.f-bar_build .names span{flex:1 1 0;min-width:0;text-align:center;font-size:calc(30px * var(--scale));color:var(--soft);white-space:nowrap}
.f-bar_build .names .on{color:var(--ink);font-weight:600}
@keyframes bbrise{from{transform:translateY(100%)}to{transform:none}}
""",
        ),
    ),
    Format(
        id="progress_ring",
        title="Progress ring",
        summary="An arc sweeps to a percentage while the figure at its centre counts up.",
        group="data",
        jobs=("prove",),
        slots=(Slot("value", kind="number"), Slot("label", limit=60), Slot("note", limit=48, required=False)),
        example={"value": 72, "label": "of sample tickets closed on the *first reply*", "note": SAMPLE},
        draw=_progress_ring,
        seconds=4.2,
        shortest=2.2,
        longest=8.0,
        tall=460,
        sound="rise",
        css=_skin(
            "progress_ring",
            """
.f-progress_ring .row{display:flex;align-items:center;gap:calc(40px * var(--scale))}
.f-progress_ring .dial{position:relative;flex:none;width:calc(340px * var(--scale));height:calc(340px * var(--scale))}
.f-progress_ring .dial svg{position:absolute;inset:0;width:100%;height:100%;overflow:visible}
.f-progress_ring .rim{fill:none;stroke:var(--hair);stroke-width:30}
.f-progress_ring .mk{stroke:var(--mute);stroke-width:4}
.f-progress_ring .arc{fill:none;stroke:var(--accent);stroke-width:30;stroke-linecap:round}
.f-progress_ring .sweep{animation:prsweep var(--r) var(--ease) .3s both}
.f-progress_ring .mid{position:absolute;inset:0;display:flex;align-items:center;justify-content:center;font-size:calc(88px * var(--scale));font-weight:700;letter-spacing:-.04em}
.f-progress_ring .say{min-width:0;flex:1}
.f-progress_ring .say h2{font-size:calc(50px * var(--scale));line-height:1.12}
@keyframes prsweep{from{stroke-dashoffset:var(--v)}to{stroke-dashoffset:0}}
""",
        ),
    ),
    Format(
        id="waffle",
        title="Waffle chart",
        summary="A hundred squares fill one by one to show a share you can actually count.",
        group="data",
        jobs=("prove", "explain"),
        slots=(
            Slot("value", kind="number"),
            Slot("label", limit=60),
            Slot("title", limit=52, required=False),
            Slot("suffix", limit=3, required=False),
            Slot("note", limit=48, required=False),
        ),
        example={"value": 38, "suffix": "%", "title": "Most sample visitors never return", "label": "of visitors come back *within a week*", "note": SAMPLE},
        draw=_waffle,
        seconds=5.6,
        shortest=2.4,
        longest=10.0,
        tall=645,
        sound="tick",
        css=_skin(
            "waffle",
            """
.f-waffle .row{display:flex;align-items:center;gap:calc(38px * var(--scale));margin-top:calc(24px * var(--scale))}
.f-waffle .grid{flex:none;display:grid;grid-template-columns:repeat(10,1fr);gap:calc(6px * var(--scale));width:calc(400px * var(--scale));height:calc(400px * var(--scale))}
.f-waffle .grid b{position:relative;display:block;overflow:hidden;border-radius:calc(4px * var(--scale));background:var(--hair)}
.f-waffle .grid u{position:absolute;inset:0;right:calc((1 - var(--p)) * 100%);background:var(--accent);animation:wfpop .22s var(--ease) var(--t) both}
.f-waffle .say{min-width:0;flex:1}
.f-waffle .num{font-size:calc(118px * var(--scale));font-weight:700;line-height:1.16;letter-spacing:-.045em}
.f-waffle .lab{margin:calc(6px * var(--scale)) 0 0;font-size:calc(38px * var(--scale));line-height:1.18;letter-spacing:-.015em;color:var(--ink);text-wrap:balance}
@keyframes wfpop{from{opacity:0;transform:scale(.3)}to{opacity:1;transform:none}}
""",
        ),
    ),
    Format(
        id="bubble_time",
        title="Bubble over time",
        summary="Dots travel and grow across periods on fixed axes, one leaving a trail.",
        group="data",
        jobs=("explain",),
        slots=(
            Slot("title", limit=52),
            Slot("x_label", limit=26),
            Slot("y_label", limit=26),
            Slot("periods", kind="list", limit=5, each=6),
            Slot("rows", kind="list", limit=4, each=11),
            Slot("sub", limit=56, required=False),
            Slot("note", limit=48, required=False),
        ),
        example={
            "title": "One sample town pulls *away*",
            "x_label": "Income per head, thousands",
            "y_label": "Green space, %",
            "periods": ["1985", "1995", "2005", "2015", "2025"],
            "rows": [
                {"text": "Larkford", "points": [[12, 14, 20], [16, 17, 24], [21, 23, 30], [29, 30, 36], [37, 36, 40]], "hot": True},
                {"text": "Oakhaven", "points": [[24, 31, 70], [26, 26, 74], [27, 22, 78], [29, 17, 82], [30, 12, 85]]},
                {"text": "Millbrook", "points": [[7, 26, 12], [8, 26, 16], [11, 27, 24], [14, 27, 34], [17, 28, 44]]},
            ],
            "note": SAMPLE,
        },
        draw=_bubble_time,
        seconds=8.0,
        shortest=4.5,
        longest=16.0,
        tall=740,
        sound=None,
        css=_skin(
            "bubble_time",
            LINE_CSS.replace(".F", ".f-bubble_time")
            + """
.f-bubble_time .plot text.ax{fill:var(--ink);font-size:26px}
.f-bubble_time .trail path{fill:none;stroke:var(--accent);stroke-width:4;stroke-dasharray:1;opacity:.75}
.f-bubble_time .ball{position:absolute;left:0;top:0;width:calc(124px * var(--scale));height:calc(124px * var(--scale));border-radius:50%;background:color-mix(in srgb,var(--ink) 34%,transparent);box-shadow:0 0 0 calc(2px * var(--scale)) color-mix(in srgb,var(--ink) 55%,transparent) inset}
.f-bubble_time .on .ball{background:color-mix(in srgb,var(--accent) 88%,transparent);box-shadow:none}
.f-bubble_time .nm{position:absolute;left:0;top:0;font-size:calc(29px * var(--scale));font-weight:600;white-space:nowrap;color:var(--soft)}
.f-bubble_time .on .nm{color:var(--ink)}
.f-bubble_time .when{position:absolute;right:calc(44px * var(--scale));bottom:calc(84px * var(--scale));height:1.1em;overflow:hidden;font-size:calc(104px * var(--scale));font-weight:700;line-height:1.1;letter-spacing:-.04em;color:var(--soft);opacity:.4;font-variant-numeric:tabular-nums}
.f-bubble_time .when span{display:block}
.f-bubble_time .when i{display:block;height:1.1em;font-style:normal;white-space:nowrap}
""",
        ),
    ),
    Format(
        id="calendar_heat",
        title="Calendar heatmap",
        summary="Days light up week by week in five honest steps, and the peak gets a flag.",
        group="data",
        jobs=("prove",),
        slots=(
            Slot("title", limit=52),
            Slot("values", kind="list", limit=182),
            Slot("months", kind="list", limit=6, each=3, required=False),
            Slot("sub", limit=56, required=False),
            Slot("note", limit=48, required=False),
        ),
        example={"title": "A sample team's *busiest* spring", "sub": "Tickets closed per day", "months": ["Jan", "Feb", "Mar"], "values": _sample_days(), "note": SAMPLE},
        draw=_calendar_heat,
        seconds=5,
        shortest=2.6,
        longest=10.0,
        tall=740,
        sound="tick",
        css=_skin(
            "calendar_heat",
            """
.f-calendar_heat{--c0:color-mix(in srgb,var(--ink) 9%,transparent);--c1:color-mix(in srgb,var(--accent) 26%,var(--surface));--c2:color-mix(in srgb,var(--accent) 50%,var(--surface));--c3:color-mix(in srgb,var(--accent) 74%,var(--surface));--c4:var(--accent)}
.f-calendar_heat .sized{max-width:calc(46px * var(--scale) + 400px * var(--scale) * var(--weeks) / 7)}
.f-calendar_heat .mo{display:flex;gap:calc(12px * var(--scale));margin-top:calc(20px * var(--scale))}
.f-calendar_heat .mo i{flex:none;width:calc(34px * var(--scale))}
.f-calendar_heat .mo div{flex:1;display:flex}
.f-calendar_heat .mo span{flex:1;font-size:calc(28px * var(--scale));color:var(--soft)}
.f-calendar_heat .cal{display:flex;gap:calc(12px * var(--scale));margin-top:calc(8px * var(--scale))}
.f-calendar_heat .dy{flex:none;width:calc(34px * var(--scale));display:grid;grid-template-rows:repeat(7,1fr);gap:calc(5px * var(--scale));align-items:center;font-size:calc(26px * var(--scale));color:var(--soft)}
.f-calendar_heat .wrapg{position:relative;flex:1;min-width:0}
.f-calendar_heat .grid{display:grid;grid-template-rows:repeat(7,1fr);grid-template-columns:repeat(var(--weeks),1fr);grid-auto-flow:column;gap:calc(5px * var(--scale));aspect-ratio:var(--weeks)/7}
.f-calendar_heat .grid b{border-radius:calc(4px * var(--scale));animation:chon .3s var(--ease) var(--t) both}
.f-calendar_heat .l0{background:var(--c0)}
.f-calendar_heat .l1{background:var(--c1)}
.f-calendar_heat .l2{background:var(--c2)}
.f-calendar_heat .l3{background:var(--c3)}
.f-calendar_heat .l4{background:var(--c4)}
.f-calendar_heat .ring{position:absolute;width:var(--w);aspect-ratio:1;border-radius:calc(8px * var(--scale));box-shadow:0 0 0 calc(4px * var(--scale)) var(--ink);transform:translate(-50%,-50%);animation:dkfade .24s ease-out both}
.f-calendar_heat .peak{margin-right:auto;padding:calc(3px * var(--scale)) calc(14px * var(--scale));border-radius:calc(10px * var(--scale));background:var(--ink);color:var(--panel);font-size:calc(32px * var(--scale));font-weight:700;white-space:nowrap;animation:dkfade .24s ease-out both}
.f-calendar_heat .key{display:flex;align-items:center;justify-content:flex-end;gap:calc(6px * var(--scale));margin-top:calc(16px * var(--scale));font-size:calc(26px * var(--scale));color:var(--soft)}
.f-calendar_heat .key b{width:calc(30px * var(--scale));height:calc(30px * var(--scale));border-radius:calc(4px * var(--scale))}
.f-calendar_heat .key .low,.f-calendar_heat .key span:last-child{margin:0 calc(6px * var(--scale))}
@keyframes chon{from{opacity:0;transform:scale(.5)}to{opacity:1;transform:none}}
""",
        ),
    ),
    Format(
        id="sankey",
        title="Sankey flow",
        summary="Bands split and merge between stages, each as wide as the amount it carries.",
        group="data",
        jobs=("explain",),
        slots=(
            Slot("title", limit=52),
            Slot("nodes", kind="list", limit=8, each=12),
            Slot("sub", limit=56, required=False),
            Slot("prefix", limit=2, required=False),
            Slot("suffix", limit=3, required=False),
            Slot("note", limit=48, required=False),
        ),
        example={
            "title": "Where a sample *1,000 sign-ups* go",
            "nodes": [
                {"text": "Sign-ups", "to": {"Trial": 640, "Dropped": 360}},
                {"text": "Trial", "to": {"Paid": 410, "Lapsed": 230}},
                {"text": "Paid", "hot": True},
            ],
            "note": SAMPLE,
        },
        draw=_sankey,
        seconds=6.0,
        shortest=3.4,
        longest=12.0,
        tall=740,
        sound="whoosh",
        css=_skin(
            "sankey",
            """
.f-sankey .band path{fill:none;stroke-dasharray:1}
.f-sankey .band.off path{stroke:color-mix(in srgb,var(--ink) 24%,transparent)}
.f-sankey .band.on path{stroke:color-mix(in srgb,var(--accent) 72%,transparent)}
.f-sankey .nd{position:absolute;width:1.733%;background:var(--ink);transform-origin:50% 0;animation:snnode .4s var(--ease) both}
.f-sankey .nd.on{background:var(--accent)}
.f-sankey .lb{position:absolute;transform:translateY(-50%);padding:calc(3px * var(--scale)) calc(11px * var(--scale));border-radius:calc(9px * var(--scale));background:var(--panel);font-size:calc(29px * var(--scale));line-height:1.14;color:var(--soft);white-space:nowrap;animation:dkfade .3s ease-out both}
.f-sankey .lb b{font-size:calc(33px * var(--scale));font-weight:700;color:var(--ink)}
.f-sankey .lb.mid{margin-left:0;transform:translate(-50%,-50%)}
.f-sankey .lb.on{color:var(--ink)}
.f-sankey .lb.on b{color:var(--accent)}
@keyframes snnode{from{transform:scaleY(0)}to{transform:none}}
""",
        ),
    ),
    Format(
        id="slope",
        title="Slope compare",
        summary="Lines join two moments on one scale, so the biggest change stands out.",
        group="data",
        jobs=("prove",),
        slots=(
            Slot("title", limit=52),
            Slot("left", limit=10),
            Slot("right", limit=10),
            Slot("rows", kind="list", limit=5, each=12),
            Slot("sub", limit=56, required=False),
            Slot("prefix", limit=2, required=False),
            Slot("suffix", limit=3, required=False),
            Slot("note", limit=48, required=False),
        ),
        example={
            "title": "Sample schools: one pulled *ahead*",
            "sub": "Pupils passing, %",
            "left": "2023",
            "right": "2025",
            "suffix": "%",
            "rows": [
                {"text": "Ashworth", "before": 58, "after": 84, "hot": True},
                {"text": "Birchway", "before": 71, "after": 74},
                {"text": "Cedar Hill", "before": 64, "after": 67},
                {"text": "Dunmore", "before": 52, "after": 49},
            ],
            "note": SAMPLE,
        },
        draw=_slope,
        seconds=5.0,
        shortest=3.0,
        longest=10.0,
        tall=730,
        sound="rise",
        css=_skin(
            "slope",
            """
.f-slope .rl{stroke:var(--hair);stroke-width:2}
.f-slope .ty{position:absolute;top:0;transform:translateX(-50%);font-size:calc(29px * var(--scale));font-weight:600;color:var(--soft);white-space:nowrap}
.f-slope .stroke line{stroke-linecap:round;stroke-dasharray:1}
.f-slope .stroke.off line{stroke:var(--mute);stroke-width:4}
.f-slope .stroke.on line{stroke:var(--accent);stroke-width:8}
.f-slope .pt{position:absolute;width:calc(18px * var(--scale));height:calc(18px * var(--scale));margin:calc(-9px * var(--scale));border-radius:50%;background:color-mix(in srgb,var(--ink) 52%,var(--panel));animation:dkfade .2s ease-out both}
.f-slope .pt.on{width:calc(26px * var(--scale));height:calc(26px * var(--scale));margin:calc(-13px * var(--scale));background:var(--accent)}
.f-slope .lv,.f-slope .rv{position:absolute;transform:translateY(-50%);font-size:calc(31px * var(--scale));white-space:nowrap;color:var(--soft);animation:dkfade .3s ease-out both}
.f-slope .rv b{font-weight:700;color:var(--ink)}
.f-slope .on.lv,.f-slope .on.rv{color:var(--ink);font-weight:600}
.f-slope .on.lv{color:var(--accent)}
.f-slope .on.rv b{color:var(--accent)}
""",
        ),
    ),
    Format(
        id="treemap_zoom",
        title="Treemap zoom",
        summary="Blocks sized by value assemble, then the camera dives into the one that matters.",
        group="data",
        jobs=("explain", "prove"),
        slots=(
            Slot("title", limit=52),
            Slot("blocks", kind="list", limit=6, each=14),
            Slot("detail", limit=64, required=False),
            Slot("sub", limit=56, required=False),
            Slot("prefix", limit=2, required=False),
            Slot("suffix", limit=3, required=False),
            Slot("note", limit=48, required=False),
        ),
        example={
            "title": "Where a sample budget goes",
            "sub": "Spending, thousands",
            "blocks": [
                {"text": "Staff", "value": 340, "hot": True},
                {"text": "Premises", "value": 210},
                {"text": "Equipment", "value": 150},
                {"text": "Travel", "value": 90},
                {"text": "Training", "value": 70},
                {"text": "Other", "value": 40},
            ],
            "detail": "Larger than premises and travel put together.",
            "note": SAMPLE,
        },
        draw=_treemap_zoom,
        seconds=6.5,
        shortest=4.0,
        longest=12.0,
        tall=740,
        sound="whoosh",
        css=_skin(
            "treemap_zoom",
            """
.f-treemap_zoom .map{overflow:hidden;border-radius:calc(12px * var(--scale))}
.f-treemap_zoom .cam{position:absolute;inset:0;transform-origin:0 0;animation:tmzoom 1s cubic-bezier(.72,0,.18,1) var(--z) both}
.f-treemap_zoom .bk{position:absolute;box-shadow:0 0 0 calc(2px * var(--scale)) var(--panel) inset;background:var(--accent);color:var(--ink);animation:tmin .42s var(--ease) both;overflow:hidden}
.f-treemap_zoom .bk.on{color:var(--panel)}
.f-treemap_zoom .tx{display:block;padding:calc(14px * var(--scale)) calc(16px * var(--scale));font-weight:600;line-height:1.12;letter-spacing:-.015em;animation:tmoff .2s ease-in var(--z) forwards}
.f-treemap_zoom .tx small{display:block;font-size:.86em;font-weight:500;opacity:.86}
.f-treemap_zoom .zoomed{position:absolute;inset:0;display:flex;flex-direction:column;justify-content:flex-end;padding:calc(36px * var(--scale));color:var(--panel);animation:dkfade .4s ease-out var(--z) both}
.f-treemap_zoom .zoomed strong{font-size:calc(150px * var(--scale));font-weight:700;line-height:1.16;letter-spacing:-.05em}
.f-treemap_zoom .zoomed span{font-weight:600}
.f-treemap_zoom .zoomed>span{font-size:calc(40px * var(--scale));letter-spacing:-.02em}
.f-treemap_zoom .zoomed p{margin:calc(8px * var(--scale)) 0 0;font-size:calc(34px * var(--scale));line-height:1.18;letter-spacing:-.012em}
@keyframes tmoff{to{opacity:0}}
@keyframes tmin{from{opacity:0;transform:scale(.94)}to{opacity:1;transform:none}}
@keyframes tmzoom{from{transform:none}to{transform:translate(var(--tx),var(--ty)) scale(var(--s))}}
""",
        ),
    ),
    Format(
        id="chart_story",
        title="Annotated chart story",
        summary="A line builds and pauses at each moment while its callouts land on cue.",
        group="data",
        jobs=("explain", "prove"),
        slots=(
            Slot("title", limit=52),
            Slot("points", kind="list", limit=12, each=6),
            Slot("callouts", kind="list", limit=3, each=34),
            Slot("sub", limit=56, required=False),
            Slot("prefix", limit=2, required=False),
            Slot("suffix", limit=3, required=False),
            Slot("note", limit=48, required=False),
        ),
        example={
            "title": "How a sample app found its feet",
            "sub": "Weekly users, thousands",
            "points": [{"text": m, "value": v} for m, v in zip(TWELVE[:9], [12, 13, 15, 14, 22, 29, 31, 44, 58], strict=True)],
            "callouts": [{"text": "Free tier opens", "on": 5}, {"text": "Referral scheme starts", "on": 8}],
            "note": SAMPLE,
        },
        draw=_chart_story,
        seconds=8.0,
        shortest=5.0,
        longest=16.0,
        tall=740,
        sound="pop",
        css=_skin(
            "chart_story",
            LINE_CSS.replace(".F", ".f-chart_story")
            + """
.f-chart_story .lead line{stroke:color-mix(in srgb,var(--ink) 62%,transparent);stroke-width:2.5;stroke-dasharray:1}
.f-chart_story .pin{position:absolute;width:calc(24px * var(--scale));height:calc(24px * var(--scale));margin:calc(-12px * var(--scale));border-radius:50%;box-shadow:0 0 0 calc(4px * var(--scale)) var(--ink) inset;background:var(--panel);animation:cspin .36s var(--ease) both}
.f-chart_story .call{position:absolute;max-width:39.6%;transform:translateY(-100%)}
.f-chart_story .call span{display:block;padding:calc(10px * var(--scale)) calc(14px * var(--scale));border-radius:calc(10px * var(--scale));background:color-mix(in srgb,var(--surface) 86%,var(--ink) 14%);font-size:calc(29px * var(--scale));font-weight:500;line-height:1.14;letter-spacing:-.01em;animation:dkrise .42s var(--ease) both}
@keyframes cspin{from{opacity:0;transform:scale(.3)}to{opacity:1;transform:none}}
""",
        ),
    ),
)
