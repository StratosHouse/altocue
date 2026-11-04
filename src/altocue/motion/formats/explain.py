"""Explainers. Python lays a diagram out from the rows given, and CSS draws it on.

Every animation sits on an HTML element. The renderer seeks CSS animations on SVG elements and
pseudo-elements against the film's clock, not the clip's, so they would look finished in any clip
that starts after zero. A line is therefore an SVG path inside a div, and the div animates
`stroke-dashoffset`, which the path inherits.

Layouts come in pairs, narrow and wide, because draw() cannot know the canvas. A container query
on the graphic's own width shows the right one.
"""

from __future__ import annotations

import hashlib
import math
from collections.abc import Callable, Sequence

from ...errors import AltocueError
from . import Format, Slot, kit

Point = tuple[float, float]
WIDE_FROM = 1200  # the panel is 880 wide on a tall canvas, 888 square, 1728 wide
PULL = "cubic-bezier(.65,0,.2,1)"
BOUNCE = "cubic-bezier(.2,1.3,.3,1)"


def _scoped(format_id: str, *parts: str) -> str:
    return "".join(parts).replace("&", f".f-{format_id}")


BASE = """
& .d{position:relative;width:100%;container-type:inline-size;--s:var(--scale)}
& .nl{display:block}& .wl{display:none}
@container (min-width:1200px){& .nl{display:none}& .wl{display:block;--s:calc(var(--scale) * 1.2)}}
& .plate{position:relative;width:100%;padding:calc(36px * var(--s)) calc(40px * var(--s));border-radius:calc(30px * var(--s));
background:radial-gradient(calc(3px * var(--s)) calc(3px * var(--s)) at 50% 50%,rgba(255,255,255,.055) 60%,transparent 62%) 0 0/calc(36px * var(--s)) calc(36px * var(--s)),color-mix(in srgb,var(--panel) 94%,transparent);
box-shadow:0 0 0 1px rgba(255,255,255,.1) inset,0 30px 90px rgba(0,0,0,.46);animation:ex-plate .5s var(--ease) both}
& .ttl{margin:0 0 calc(26px * var(--s));font-size:calc(44px * var(--s));font-weight:600;line-height:1.08;letter-spacing:-.028em;color:var(--ink);text-wrap:balance;animation:rise .42s .08s var(--ease) both}
& .st{position:relative;margin:0 auto;width:calc(var(--w) * 1px * var(--s));height:calc(var(--h) * 1px * var(--s))}
& .at{position:absolute;left:calc(var(--x) * 1px * var(--s));top:calc(var(--y) * 1px * var(--s))}
& .ln,& .dot{position:absolute;inset:0;pointer-events:none}
& .ln svg,& .dot svg{position:absolute;inset:0;width:100%;height:100%;overflow:visible}
& .ln{stroke-dashoffset:1;animation:ex-draw var(--t) var(--d) cubic-bezier(.6,0,.2,1) both}
& .ln path{fill:none;stroke:var(--accent);stroke-width:3.5;stroke-linecap:round;stroke-linejoin:round;stroke-dasharray:1 1}
& .ln.dim path{stroke:color-mix(in srgb,var(--ink) 34%,transparent);stroke-width:2.5}
& .dot{stroke-dashoffset:0;animation:ex-go var(--t) var(--d) cubic-bezier(.55,0,.25,1) both}
& .dot path{fill:none;stroke-linecap:round;stroke-dasharray:.0001 2}
& .dot .halo{stroke:color-mix(in srgb,var(--accent) 30%,transparent);stroke-width:34}
& .dot .core{stroke:var(--accent);stroke-width:15}
@keyframes ex-plate{from{opacity:0;transform:translateY(calc(30px * var(--scale))) scale(.975)}to{opacity:1;transform:none}}
@keyframes ex-draw{from{opacity:0;stroke-dashoffset:1}1%{opacity:1}to{opacity:1;stroke-dashoffset:0}}
@keyframes ex-go{0%{opacity:0;stroke-dashoffset:0}10%{opacity:1}90%{opacity:1}100%{opacity:0;stroke-dashoffset:-1}}
@keyframes ex-pop{from{opacity:0;transform:scale(.82)}to{opacity:1;transform:none}}
"""


def _beats(item: dict, name: str, first: float = 0.2, last: float = 0.6) -> list[dict]:
    """Rows with their moment in seconds. Word-anchored rows keep their word. The rest share out a stretch of the clip, so the end holds still."""
    rows, span = kit.rows(item, name), kit.seconds(item)
    for index, row in enumerate(rows):
        if row.get("at") is None:
            row["delay"] = span * (first + (last - first) * index / max(len(rows) - 1, 1))
    return rows


def _n(value: float) -> str:
    return f"{value:.1f}".rstrip("0").rstrip(".")


def _svg(box: tuple[float, float], body: str) -> str:
    return f'<svg viewBox="0 0 {_n(box[0])} {_n(box[1])}" preserveAspectRatio="none" aria-hidden="true">{body}</svg>'


def _line(path: str, box: tuple[float, float], delay: float, length: float = 0.3, tone: str = "") -> str:
    return f'<div class="ln {tone}" style="--d:{max(delay, 0):.3f}s;--t:{length:.3f}s">{_svg(box, f"""<path pathLength="1" d="{path}"/>""")}</div>'


def _dot(path: str, box: tuple[float, float], delay: float, length: float = 0.4) -> str:
    body = f'<path class="halo" pathLength="1" d="{path}"/><path class="core" pathLength="1" d="{path}"/>'
    return f'<div class="dot" style="--d:{max(delay, 0):.3f}s;--t:{length:.3f}s">{_svg(box, body)}</div>'


def _head(tip: Point, angle: float, size: float = 15) -> str:
    """Two strokes back from the tip of a line."""
    left = angle + math.radians(150)
    right = angle - math.radians(150)
    a = (tip[0] + size * math.cos(left), tip[1] + size * math.sin(left))
    b = (tip[0] + size * math.cos(right), tip[1] + size * math.sin(right))
    return f"M{_n(a[0])} {_n(a[1])}L{_n(tip[0])} {_n(tip[1])}L{_n(b[0])} {_n(b[1])}"


def _elbow(points: Sequence[Point], radius: float = 22) -> str:
    """A path through the points with its corners rounded."""
    out = [f"M{_n(points[0][0])} {_n(points[0][1])}"]
    for before, corner, after in zip(points, points[1:], points[2:], strict=False):
        into = math.dist(before, corner)
        leave = math.dist(corner, after)
        r = min(radius, into / 2, leave / 2)
        p = (corner[0] + (before[0] - corner[0]) * r / into, corner[1] + (before[1] - corner[1]) * r / into)
        q = (corner[0] + (after[0] - corner[0]) * r / leave, corner[1] + (after[1] - corner[1]) * r / leave)
        out.append(f"L{_n(p[0])} {_n(p[1])}Q{_n(corner[0])} {_n(corner[1])} {_n(q[0])} {_n(q[1])}")
    out.append(f"L{_n(points[-1][0])} {_n(points[-1][1])}")
    return "".join(out)


def _stage(width: float, height: float, body: str) -> str:
    return f'<div class="st" style="--w:{_n(width)};--h:{_n(height)}">{body}</div>'


def _title(item: dict, name: str = "title") -> str:
    return f'<h2 class="ttl">{kit.emphasis(item[name])}</h2>' if item.get(name) else ""


def _plate(title: str, body: str, cls: str = "") -> str:
    return f'<div class="d"><div class="plate {cls}">{title}{body}</div></div>'


def _both(narrow: str, wide: str) -> str:
    return f'<div class="nl">{narrow}</div><div class="wl">{wide}</div>'


# Flowchart


def _flow(rows: list[dict], back: str, wide: bool) -> str:
    count = len(rows)
    bend = next((i for i, row in enumerate(rows) if row.get("check")), count - 1)
    looped = bool(back) and bend > 0
    if wide:
        width, gap, tall = 1520.0, 62.0, 164.0
        side = min(380.0, (width - gap * (count - 1)) / count)
        left = (width - side * count - gap * (count - 1)) / 2
        boxes = [(left + i * (side + gap), 0.0, side, tall) for i in range(count)]
        height = tall + (96 if looped else 0)
    else:
        width, gap, tall = 800.0, 46.0, 90.0
        side = 560.0 if looped else width
        boxes = [(0.0, i * (tall + gap), side, tall) for i in range(count)]
        height = count * tall + (count - 1) * gap
    box, parts = (width, height), []
    for index, row in enumerate(rows):
        x, y, w, h = boxes[index]
        if index:
            px, py, pw, ph = boxes[index - 1]
            start = (px + pw, py + ph / 2) if wide else (px + pw / 2, py + ph)
            tip = (x - 7, y + h / 2) if wide else (x + w / 2, y - 7)
            arrive = row["delay"] - 0.28
            parts.append(_line(_elbow([start, tip]), box, arrive, 0.24))
            parts.append(_line(_head(tip, 0 if wide else math.pi / 2), box, arrive + 0.2, 0.1))
        rest = f";--b:{rows[index + 1]['delay']:.3f}s" if index + 1 < count else ""
        mark = "<b>?</b>" if row.get("check") else f"{index + 1:02d}"
        parts.append(
            f'<div class="node at{"" if rest else " last"}{" ask" if row.get("check") else ""}" style="--x:{_n(x)};--y:{_n(y)};--nw:{_n(w)};--nh:{_n(h)};--a:{row["delay"]:.3f}s{rest}">'
            f"<i>{mark}</i><span>{kit.esc(row['text'])}</span></div>"
        )
    if looped:
        fx, fy, fw, fh = boxes[bend]
        tx, ty, tw, th = boxes[bend - 1]
        late = rows[bend]["delay"] + 0.34
        if wide:
            drop = height - 8
            spine = [(fx + fw / 2, fy + fh), (fx + fw / 2, drop), (tx + tw / 2, drop), (tx + tw / 2, ty + th + 7)]
            tip, angle, tag = spine[-1], -math.pi / 2, ((fx + tx + tw) / 2 + fw / 4, drop)
        else:
            lane = fw + 104
            spine = [(fx + fw, fy + fh / 2), (lane, fy + fh / 2), (lane, ty + th / 2), (tx + tw + 7, ty + th / 2)]
            tip, angle, tag = spine[-1], math.pi, (lane, (fy + ty + th) / 2)
        parts.append(_line(_elbow(spine, 26), box, late, 0.46, "dim"))
        parts.append(_line(_head(tip, angle), box, late + 0.42, 0.1, "dim"))
        parts.append(f'<div class="tag at" style="--x:{_n(tag[0])};--y:{_n(tag[1])};--a:{late + 0.3:.3f}s"><span>{kit.esc(back)}</span></div>')
    return _stage(width, height, "".join(parts))


def _flowchart(item: dict) -> str:
    rows = _beats(item, "steps", 0.05, 0.56)
    back = item.get("back", "")
    return _plate(_title(item), _both(_flow(rows, back, False), _flow(rows, back, True)))


# Decision tree


def _tree(root: str, levels: list[list[dict]], picks: list[int], times: list[float], labels: tuple[str, str], wide: bool) -> str:
    depth = len(levels)
    if wide:
        width, column, tall, pad = 1520.0, 330.0, 112.0, 70.0
        stride = (width - column) / max(depth, 1)
        height = 330.0
        spot = lambda level, side: (stride * (level + 1), 0.0 if side == 0 else height - tall)  # noqa: E731
        box_root = (0.0, (height - 176) / 2, column - pad + 20, 176.0)
    else:
        width, column, tall = 800.0, 384.0, 100.0
        stride = 178.0
        height = 112 + depth * stride
        spot = lambda level, side: (0.0 if side == 0 else width - column, 112 + stride * level + stride - tall)  # noqa: E731
        box_root = (120.0, 0.0, 560.0, 112.0)
    box, parts = (width, height), []

    def out(b: tuple[float, float, float, float]) -> Point:
        return (b[0] + b[2], b[1] + b[3] / 2) if wide else (b[0] + b[2] / 2, b[1] + b[3])

    def into(b: tuple[float, float, float, float]) -> Point:
        return (b[0], b[1] + b[3] / 2) if wide else (b[0] + b[2] / 2, b[1])

    def curve(a: Point, b: Point) -> str:
        if wide:
            mid = (a[0] + b[0]) / 2
            return f"M{_n(a[0])} {_n(a[1])}C{_n(mid)} {_n(a[1])} {_n(mid)} {_n(b[1])} {_n(b[0])} {_n(b[1])}"
        mid = (a[1] + b[1]) / 2
        return f"M{_n(a[0])} {_n(a[1])}C{_n(a[0])} {_n(mid)} {_n(b[0])} {_n(mid)} {_n(b[0])} {_n(b[1])}"

    parent = box_root
    nodes = [
        f'<div class="tn root at" style="--x:{_n(box_root[0])};--y:{_n(box_root[1])};--nw:{_n(box_root[2])};--nh:{_n(box_root[3])};--a:.12s"><span>{kit.esc(root)}</span></div>'
    ]
    for level, pair in enumerate(levels):
        lit = times[level]
        begin = lit - 0.86
        for side, row in enumerate(pair):
            x, y = spot(level, side)
            cell = (x, y, column, tall)
            path = curve(out(parent), into(cell))
            parts.append(_line(path, box, begin, 0.32, "dim"))
            chosen = side == picks[level]
            tagx = (out(parent)[0] + into(cell)[0]) / 2
            tagy = (out(parent)[1] + into(cell)[1]) / 2
            nodes.append(f'<div class="yn at" style="--x:{_n(tagx)};--y:{_n(tagy)};--a:{begin + 0.2:.3f}s"><span>{kit.esc(labels[side])}</span></div>')
            if chosen:
                parts.append(_line(path, box, lit - 0.4, 0.38))
                parts.append(_dot(path, box, lit - 0.42, 0.42))
            state = "pick" if chosen else "drop"
            nodes.append(
                f'<div class="tn {state} at" style="--x:{_n(x)};--y:{_n(y)};--nw:{_n(column)};--nh:{_n(tall)};--a:{begin + 0.22:.3f}s;--b:{lit:.3f}s"><span>{kit.esc(row["text"])}</span></div>'
            )
        x, y = spot(level, picks[level])
        parent = (x, y, column, tall)
    return _stage(width, height, "".join(parts) + "".join(nodes))


def _decision(item: dict) -> str:
    rows = _beats(item, "options", 0.3, 0.66)
    if len(rows) % 2 or not rows:
        raise AltocueError(
            f'decision_tree: "options" has {len(rows)} rows and they must come in pairs.',
            fix="Give each question two answers. The first pair branches from the question, and each next pair from the answer marked on.",
        )
    levels = [rows[i : i + 2] for i in range(0, len(rows), 2)]
    picks = [1 if pair[1].get("on") and not pair[0].get("on") else 0 for pair in levels]
    times = [pair[pick]["delay"] for pair, pick in zip(levels, picks, strict=True)]
    labels = (item.get("yes", "Yes"), item.get("no", "No"))
    root = item["question"]
    return _plate("", _both(_tree(root, levels, picks, times, labels, False), _tree(root, levels, picks, times, labels, True)))


# Pipeline


def _track(rows: list[dict], token: str, wide: bool) -> str:
    count = len(rows)
    pitch = (1240.0 / max(count - 1, 1) if wide else min(540.0 / max(count - 1, 1), 150.0)) if count > 1 else 0.0
    lead = (1520.0 - pitch * (count - 1)) / 2 if wide else 0.0
    rail = 96.0 if wide else 30.0 + (216.0 if token else 0.0)
    stops = [(lead + i * pitch, rail) if wide else (rail, 34 + i * pitch) for i in range(count)]
    width, height = (1520.0, 226.0) if wide else (800.0, 68 + pitch * (count - 1))
    begin, end = stops[0], stops[-1]
    span = (end[0] - begin[0]) if wide else (end[1] - begin[1])
    axis = "h" if wide else "v"
    parts = [f'<div class="rail {axis} at" style="--x:{_n(begin[0])};--y:{_n(begin[1])};--len:{_n(span)}"></div>']
    hop = 0.5
    token_body = '<div class="pk"></div>' + (f'<div class="tok"><span>{kit.esc(token)}</span></div>' if token else "")
    chain = token_body
    for index in range(count - 1, 0, -1):
        move = rows[index]["delay"] - hop
        chain = f'<div class="hop {axis}" style="--go:{_n(pitch)};--d:{max(move, 0):.3f}s;--t:{hop}s">{chain}</div>'
    for index in range(1, count):
        a = stops[index - 1]
        parts.append(
            f'<div class="fill {axis} at" style="--x:{_n(a[0])};--y:{_n(a[1])};--len:{_n(pitch)};--d:{max(rows[index]["delay"] - hop, 0):.3f}s;--t:{hop}s"></div>'
        )
    for index, row in enumerate(rows):
        x, y = stops[index]
        done = row["delay"]
        tick = ""
        if index == count - 1:
            tick = f'<div class="tick">{_line("M9 21L17 29L32 12", (42.0, 42.0), done + 0.1, 0.22)}</div>'
        parts.append(f'<div class="stn at" style="--x:{_n(x)};--y:{_n(y)};--a:{done:.3f}s"><i class="ring"></i><i class="core"></i>{tick}</div>')
        parts.append(f'<div class="lab {axis} at" style="--x:{_n(x)};--y:{_n(y)};--a:{done:.3f}s"><span>{kit.esc(row["text"])}</span></div>')
    parts.append(f'<div class="ride at" style="--x:{_n(begin[0])};--y:{_n(begin[1])}">{chain}</div>')
    return _stage(width, height, "".join(parts))


def _pipeline(item: dict) -> str:
    rows = _beats(item, "stages", 0.16, 0.66)
    token = item.get("token", "")
    return _plate(_title(item), _both(_track(rows, token, False), _track(rows, token, True)))


# Hub and spokes


def _ring(rows: list[dict], hub: str, wide: bool) -> str:
    width, height, rx, ry, radius = (1520.0, 500.0, 590.0, 194.0, 122.0) if wide else (800.0, 680.0, 244.0, 280.0, 122.0)
    centre = (width / 2, height / 2)
    box = (width, height)
    parts = [_orbit(centre, rx, ry, box)]
    nodes = []
    for index, row in enumerate(rows):
        angle = math.radians(-90 + 360 * index / len(rows))
        unit = (math.cos(angle), math.sin(angle))
        tip = (centre[0] + rx * unit[0], centre[1] + ry * unit[1])
        reach = math.hypot(rx * unit[0], ry * unit[1])
        root = (centre[0] + radius * (tip[0] - centre[0]) / reach, centre[1] + radius * (tip[1] - centre[1]) / reach)
        arrive = row["delay"]
        path = _elbow([root, tip])
        parts.append(_line(path, box, arrive - 0.32, 0.32))
        parts.append(_dot(path, box, arrive - 0.34, 0.36))
        nodes.append(f'<div class="sp at" style="--x:{_n(tip[0])};--y:{_n(tip[1])};--a:{arrive:.3f}s"><span>{kit.esc(row["text"])}</span></div>')
        parts.append(f'<div class="wave at" style="--x:{_n(centre[0])};--y:{_n(centre[1])};--a:{max(arrive - 0.34, 0):.3f}s"></div>')
    hub_html = f'<div class="hub at" style="--x:{_n(centre[0])};--y:{_n(centre[1])}"><span>{kit.emphasis(hub)}</span></div>'
    return _stage(width, height, "".join(parts) + "".join(nodes) + hub_html)


def _orbit(centre: Point, rx: float, ry: float, box: tuple[float, float]) -> str:
    x, y = centre
    path = f"M{_n(x - rx)} {_n(y)}A{_n(rx)} {_n(ry)} 0 1 1 {_n(x + rx)} {_n(y)}A{_n(rx)} {_n(ry)} 0 1 1 {_n(x - rx)} {_n(y)}"
    return f'<div class="orbit">{_svg(box, f"""<path pathLength="60" d="{path}"/>""")}</div>'


def _hub(item: dict) -> str:
    rows = _beats(item, "spokes", 0.2, 0.62)
    return _plate("", _both(_ring(rows, item["hub"], False), _ring(rows, item["hub"], True)))


# Stepped list


def _stepped(item: dict) -> str:
    rows = _beats(item, "items", 0.16, 0.66)
    title = f'<h2 class="ttl">{kit.emphasis(item["title"])}</h2>' if item.get("title") else ""
    cells = []
    counts = []
    for index, row in enumerate(rows):
        following = f";--b:{rows[index + 1]['delay']:.3f}s" if index + 1 < len(rows) else ""
        tick = _line("M9 22L18 31L34 12", (44.0, 44.0), row["delay"] + 0.1, 0.2)
        cells.append(
            f'<div class="row{"" if following else " last"}" style="--a:{row["delay"]:.3f}s{following}"><div class="band"></div>'
            f'<div class="bx"><i></i>{tick}</div><p>{kit.emphasis(row["text"])}</p></div>'
        )
        counts.append(f'<b class="{"" if following else "last"}" style="--a:{row["delay"]:.3f}s{following}">{index + 1}</b>')
    count = f'<div class="cnt"><span>{"".join(counts)}</span><em>/{len(rows)}</em></div>'
    return _plate(f'<div class="hd">{title}{count}</div>', f'<div class="list">{"".join(cells)}</div>', "grid" if title else "")


# Exploded view


def _diamond(cx: float, cy: float, a: float, b: float, t: float) -> str:
    top = f"{_n(cx)},{_n(cy - b)} {_n(cx + a)},{_n(cy)} {_n(cx)},{_n(cy + b)} {_n(cx - a)},{_n(cy)}"
    left = f"{_n(cx - a)},{_n(cy)} {_n(cx)},{_n(cy + b)} {_n(cx)},{_n(cy + b + t)} {_n(cx - a)},{_n(cy + t)}"
    right = f"{_n(cx + a)},{_n(cy)} {_n(cx)},{_n(cy + b)} {_n(cx)},{_n(cy + b + t)} {_n(cx + a)},{_n(cy + t)}"
    inset = 0.72
    inner = f"{_n(cx)},{_n(cy - b * inset)} {_n(cx + a * inset)},{_n(cy)} {_n(cx)},{_n(cy + b * inset)} {_n(cx - a * inset)},{_n(cy)}"
    return f'<polygon class="r" points="{right}"/><polygon class="l" points="{left}"/><polygon class="t" points="{top}"/><polygon class="i" points="{inner}"/>'


def _exploded(item: dict) -> str:
    rows = _beats(item, "layers", 0.26, 0.56)
    count = len(rows)
    width, height, a, t, cx = 800.0, 620.0, 214.0, 30.0, 226.0
    b = a * 0.5
    top, bottom = b + 6, height - b - t - 6
    gap = (bottom - top) / max(count - 1, 1) if count > 1 else 0.0
    gap = min(gap, 150.0)
    used = gap * (count - 1)
    first = (top + bottom - used) / 2
    box = (width, height)
    parts = []
    for index in range(count - 1, -1, -1):
        row = rows[index]
        cy = first + gap * index
        tight = (top + bottom) / 2 + (index - (count - 1) / 2) * t
        lands = row["delay"]
        hot = " hot" if row.get("on") else ""
        leader = _line(f"M{_n(cx + a)} {_n(cy)}L{_n(cx + a + 56)} {_n(cy)}", box, lands + 0.16, 0.2, "dim")
        label = f'<div class="lb at" style="--x:{_n(cx + a + 70)};--y:{_n(cy)};--a:{lands + 0.26:.3f}s"><span>{kit.esc(row["text"])}</span></div>'
        parts.append(
            f'<div class="sl{hot}" style="--dy:{_n(tight - cy)};--a:{lands:.3f}s"><div class="fc">{_svg(box, _diamond(cx, cy, a, b, t))}</div>{leader}{label}</div>'
        )
    return _plate(_title(item), _stage(width, height, "".join(parts)), "side" if item.get("title") else "")


# Analogy morph


def _polar(radius: Callable[[float], float], count: int = 96) -> list[Point]:
    points = []
    for index in range(count):
        angle = -math.pi / 2 + 2 * math.pi * index / count
        r = radius(angle)
        points.append((50 + r * math.cos(angle), 50 + r * math.sin(angle)))
    return points


def _heart() -> list[Point]:
    points = []
    for index in range(96):
        t = 2 * math.pi * index / 96
        x = 16 * math.sin(t) ** 3
        y = 13 * math.cos(t) - 5 * math.cos(2 * t) - 2 * math.cos(3 * t) - math.cos(4 * t)
        points.append((50 + x * 2.55, 46 - y * 2.55))
    return points


def _star() -> list[Point]:
    return [
        (50 + (48 if i % 2 == 0 else 21) * math.cos(-math.pi / 2 + math.pi * i / 5), 53 + (48 if i % 2 == 0 else 21) * math.sin(-math.pi / 2 + math.pi * i / 5))
        for i in range(10)
    ]


def _drop() -> list[Point]:
    points = [(50.0, 2.0)]
    for index in range(1, 40):
        angle = -math.pi / 2 + math.radians(52) + (2 * math.pi - 2 * math.radians(52)) * index / 40
        points.append((50 + 33 * math.cos(angle), 63 + 33 * math.sin(angle)))
    return points


SHAPES: dict[str, Sequence[Point]] = {
    "circle": _polar(lambda _: 46),
    "square": [(50, 8), (92, 8), (92, 92), (8, 92), (8, 8)],
    "triangle": [(50, 6), (97, 90), (3, 90)],
    "hexagon": [(50, 3), (91, 26), (91, 74), (50, 97), (9, 74), (9, 26)],
    "star": _star(),
    "heart": _heart(),
    "drop": _drop(),
    "bolt": [(58, 2), (20, 56), (46, 56), (38, 98), (82, 38), (54, 38), (70, 2)],
    "shield": [(50, 3), (88, 16), (88, 50), (80, 70), (66, 85), (50, 97), (34, 85), (20, 70), (12, 50), (12, 16)],
    "arrow": [(50, 4), (94, 50), (66, 50), (66, 96), (34, 96), (34, 50), (6, 50)],
}


def _resample(points: Sequence[Point], count: int = 64) -> list[Point]:
    """The same number of points, evenly spaced, starting from the top, so any shape can melt into any other."""
    ring = list(points) + [points[0]]
    lengths = [math.dist(a, b) for a, b in zip(ring, ring[1:], strict=False)]
    total = sum(lengths)
    out, walked, leg = [], 0.0, 0
    for index in range(count):
        target = total * index / count
        while walked + lengths[leg] < target and leg < len(lengths) - 1:
            walked += lengths[leg]
            leg += 1
        share = (target - walked) / lengths[leg] if lengths[leg] else 0
        a, b = ring[leg], ring[leg + 1]
        out.append((a[0] + (b[0] - a[0]) * share, a[1] + (b[1] - a[1]) * share))
    return out


def _clip(name: str) -> str:
    return "polygon(" + ",".join(f"{x:.2f}% {y:.2f}%" for x, y in _resample(SHAPES[name])) + ")"


def _morph(item: dict) -> str:
    span = kit.seconds(item)
    start = span * 0.3
    timing = f"--a:{_clip(item['from_shape'])};--b:{_clip(item['to_shape'])};--m:{start:.3f}s;--o:{span * 0.3 + 0.82:.3f}s"
    trail = "".join(f'<div class="sh g{i}"></div>' for i in (2, 1))
    burst = "".join(f'<i class="sk" style="--r:{turn * 30}deg"></i>' for turn in range(12))
    link = f'<p class="lk"><span>{kit.esc(item["link"])}</span></p>' if item.get("link") else ""
    return (
        f'<div class="d"><div class="mo plate" style="{timing}"><div class="spin"><div class="art">{trail}<div class="sh"></div></div>{burst}</div>'
        f'<div class="wd"><p class="w1">{kit.esc(item["from_word"])}</p>{link}<p class="w2">{kit.esc(item["to_word"])}</p></div></div></div>'
    )


# Document highlight


def _bars(count: int, seed: str) -> str:
    code = hashlib.sha256(seed.encode()).digest()
    return "".join(f'<i class="gb" style="--l:{62 + code[index % 32] % 34}%"></i>' for index in range(count))


def _document(item: dict) -> str:
    parts = str(item["text"]).split("*")
    body = "".join(f"<mark>{kit.esc(part)}</mark>" if i % 2 else kit.esc(part) for i, part in enumerate(parts))
    span = kit.seconds(item)
    seed = str(item["text"])
    heading = f"<h3>{kit.esc(item['heading'])}</h3>" if item.get("heading") else '<i class="gb hd"></i>'
    source = f'<div class="src"><span>{kit.esc(item["source"])}</span></div>' if item.get("source") else ""
    return (
        f'<div class="d"><div class="win" style="--z:{span * 0.08:.3f}s;--zt:{span * 0.26:.3f}s;--m:{span * 0.36:.3f}s;--mt:{max(span * 0.17, 0.5):.3f}s">'
        f'<div class="page">{heading}{_bars(6, seed + "a")}<p class="key">{body}</p>{_bars(9, seed + "b")}</div>{source}</div></div>'
    )


# Scale zoom


CELLS = 5
SHRINK = 5.8  # a whole grid fits inside one cell of the next, with a little air
LEGS = 3  # four levels at most: the seed, then three grids


def _level(index: int) -> str:
    """A grid the last level shrinks into. Its middle cell is exactly the size the last level ends up."""
    seed = '<circle class="glow" cx="50" cy="50" r="8.6"/><circle class="seed" cx="50" cy="50" r="7.2"/>' if index == 1 else ""
    tiles = []
    for row in range(CELLS):
        for column in range(CELLS):
            if row == CELLS // 2 and column == CELLS // 2:
                continue
            tiles.append(f'<rect x="{column * 20 + 1.2}" y="{row * 20 + 1.2}" width="17.6" height="17.6" rx="1.8"/>')
    return f'<div class="lv" style="--i:{index};--o:{1 if index == 1 else 0}"><svg viewBox="0 0 100 100" aria-hidden="true">{"".join(tiles)}<rect class="k" x="41.2" y="41.2" width="17.6" height="17.6" rx="1.8"/>{seed}</svg></div>'


def _scale(item: dict) -> str:
    rows = kit.rows(item, "levels")
    count = len(rows)
    if count < 2:
        raise AltocueError('scale_zoom needs at least two "levels".', fix="Give it a small thing and a bigger thing to pull back to.")
    span = kit.seconds(item)
    lead = span * 0.12  # hold on the first level before the camera moves
    beat = (span * 0.8 - lead) / (count - 1.3)
    levels = "".join(f'<div class="wrap" style="--legs:{(count - index) / LEGS:.4f}">{_level(index)}</div>' for index in range(1, count))
    texts, ticks = [], []
    for index, row in enumerate(rows):
        arrive = lead + (index - 1 + 0.45) * beat if index else 0.0
        leave = f";--b:{lead + (index + 0.3) * beat:.3f}s" if index + 1 < count else ""
        last = "" if leave else " last"
        texts.append(f'<p class="big{last}" style="--a:{arrive:.3f}s{leave}">{kit.emphasis(row["text"])}</p>')
        ticks.append(f'<i style="--a:{arrive:.3f}s"></i>')
    title = f'<h2 class="ttl">{kit.emphasis(item["title"])}</h2>' if item.get("title") else ""
    return (
        f'<div class="d" style="--beat:{beat:.3f}s;--lead:{lead:.3f}s"><div class="sky"><div class="cam">{levels}</div></div>'
        f'<div class="copy">{title}<div class="stack">{"".join(texts)}</div><div class="rule">{"".join(ticks)}</div></div></div>'
    )


def _zoom_frames() -> str:
    """One grid's whole journey, shrinking five times over each leg. Grids share it, each a beat later, so they stay locked together as the camera pulls back."""
    stops = [(0.0, 1.0, "var(--o)")]
    for leg in range(LEGS):
        begin, size = leg / LEGS, SHRINK ** -(leg + 1)
        stops.append((begin + 0.7 / LEGS, size, "1"))
        stops.append((begin + 1 / LEGS, size, "1"))
    lines = [f"{at * 100:.3f}%{{opacity:{fade};transform:translate(-50%,-50%) scale({size:.5f});animation-timing-function:{PULL}}}" for at, size, fade in stops]
    return "@keyframes ex-pull{" + "".join(lines) + f"{0.34 / LEGS * 100:.3f}%{{opacity:1}}" + "}"


# Split compare


def _side(cls: str, title: str, value: str, rows: list[dict], late: float) -> str:
    mark = (
        '<svg viewBox="0 0 20 20" aria-hidden="true"><path d="M4 11L8.5 15.5L16 5.5"/></svg>'
        if cls == "b"
        else '<svg viewBox="0 0 20 20" aria-hidden="true"><path d="M5 5L15 15M15 5L5 15"/></svg>'
    )
    lines = "".join(f'<li style="--a:{late + index * 0.1:.3f}s">{mark}<span>{kit.esc(row["text"])}</span></li>' for index, row in enumerate(rows))
    big = f"<b>{kit.esc(value)}</b>" if value else ""
    return f'<div class="sd {cls}"><div class="c"><h3>{kit.esc(title)}</h3>{big}<ul>{lines}</ul></div></div>'


def _split(item: dict) -> str:
    span = kit.seconds(item)
    sweep = span * 0.14
    takes = max(span * 0.3, 1.0)
    before = _side("a", item["before_title"], item.get("before_value", ""), kit.rows(item, "before"), 0.2)
    after = _side("b", item["after_title"], item.get("after_value", ""), kit.rows(item, "after"), sweep + takes * 0.5)
    return f'<div class="d"><div class="win" style="--go:{sweep:.3f}s;--take:{takes:.3f}s">{before}{after}<div class="bar"><i></i><div class="grip"><svg viewBox="0 0 40 24" aria-hidden="true"><path d="M13 5L6 12L13 19M27 5L34 12L27 19"/></svg></div></div></div></div>'


# Whiteboard


class _Hand:
    """A steady hand with a small wobble. The wobble comes from the copy, so a render never changes."""

    def __init__(self, seed: str) -> None:
        self.code = hashlib.sha256(seed.encode()).digest()
        self.turn = 0

    def wobble(self, size: float) -> float:
        self.turn += 1
        return (self.code[(self.turn * 7) % 32] / 255 - 0.5) * 2 * size

    def stroke(self, a: Point, b: Point, sag: float = 7) -> str:
        mid = ((a[0] + b[0]) / 2 + self.wobble(sag), (a[1] + b[1]) / 2 + self.wobble(sag))
        return f"M{_n(a[0])} {_n(a[1])}Q{_n(mid[0])} {_n(mid[1])} {_n(b[0])} {_n(b[1])}"

    def box(self, x: float, y: float, w: float, h: float) -> str:
        corners = [(x, y), (x + w, y), (x + w, y + h), (x, y + h), (x + 6, y - 5)]
        out = [f"M{_n(corners[0][0] - 8)} {_n(corners[0][1] + self.wobble(3))}"]
        for a, b in zip(corners, corners[1:], strict=False):
            mid = ((a[0] + b[0]) / 2 + self.wobble(6), (a[1] + b[1]) / 2 + self.wobble(6))
            out.append(f"Q{_n(mid[0])} {_n(mid[1])} {_n(b[0] + self.wobble(4))} {_n(b[1] + self.wobble(4))}")
        return "".join(out)

    def loop(self, cx: float, cy: float, rx: float, ry: float, begin: float = -100, turns: float = 1.015) -> str:
        points = []
        steps = 44
        grow = self.wobble(0.05)
        for index in range(steps + 1):
            angle = math.radians(begin) + 2 * math.pi * turns * index / steps
            drift = 1 + grow * index / steps + 0.012 * math.sin(index * 0.9 + self.code[0])
            points.append((cx + rx * drift * math.cos(angle), cy + ry * drift * math.sin(angle)))
        return "M" + "L".join(f"{_n(x)} {_n(y)}" for x, y in points)

    def arrow(self, a: Point, b: Point, bend: float = 0.0) -> tuple[str, str]:
        mx, my = (a[0] + b[0]) / 2, (a[1] + b[1]) / 2
        length = math.dist(a, b)
        nx, ny = -(b[1] - a[1]) / length, (b[0] - a[0]) / length
        c = (mx + nx * bend + self.wobble(4), my + ny * bend + self.wobble(4))
        angle = math.atan2(b[1] - c[1], b[0] - c[0])
        return f"M{_n(a[0])} {_n(a[1])}Q{_n(c[0])} {_n(c[1])} {_n(b[0])} {_n(b[1])}", _head(b, angle, 30)


def _rim(a: Point, b: Point, rx: float, ry: float, pad: float = 16) -> Point:
    """Where the line from one bubble's centre towards b leaves its edge, plus a little air."""
    dx, dy = b[0] - a[0], b[1] - a[1]
    reach = 1 / math.hypot(dx / rx, dy / ry)
    length = math.hypot(dx, dy)
    return (a[0] + dx * reach + dx / length * pad, a[1] + dy * reach + dy / length * pad)


def _sketch(rows: list[dict], kind: str, wide: bool, seed: str) -> str:
    hand = _Hand(seed + str(wide))
    size = (1700.0, 690.0) if wide else (936.0, 1000.0)
    parts: list[str] = []
    timed = [row["delay"] for row in rows]
    count = len(rows)

    def write(x: float, y: float, w: float, text: str, at: float, hot: bool = False) -> str:
        return f'<div class="hw at{" hot" if hot else ""}" style="--x:{_n(x)};--y:{_n(y)};--nw:{_n(w)};--a:{at:.3f}s"><span><b>{kit.esc(text)}</b></span></div>'

    if kind == "venn":
        r = 270.0 if wide else 268.0
        cx, cy = size[0] / 2, size[1] / 2
        shift = r * (0.72 if wide else 0.6)
        circles = [(cx - shift, cy), (cx + shift, cy)]
        spots = [(cx - shift * 1.58, cy), (cx + shift * 1.58, cy), (cx, cy)]
        for index, row in enumerate(rows[:3]):
            if index < 2:
                parts.append(_line(hand.loop(*circles[index], r, r * 0.98, -110 + index * 180), size, timed[index], 0.62, "pen"))
                parts.append(write(*spots[index], r * 0.86, row["text"], timed[index] + 0.42))
            else:
                parts.append(write(cx, cy, r * 0.74, row["text"], timed[index], True))
    elif kind == "cycle":
        rx, ry, wide_bubble, tall_bubble = (560.0, 226.0, 230.0, 112.0) if wide else (306.0 if count == 3 else 272.0, 372.0, 196.0, 122.0)
        spots = [(rx * math.cos(math.radians(-90 + 360 * i / count)), ry * math.sin(math.radians(-90 + 360 * i / count))) for i in range(count)]
        lift = size[1] / 2 - (max(y for _, y in spots) + min(y for _, y in spots)) / 2
        spots = [(size[0] / 2 + x, lift + y) for x, y in spots]
        for index, row in enumerate(rows):
            spot, after = spots[index], spots[(index + 1) % count]
            parts.append(_line(hand.loop(*spot, wide_bubble, tall_bubble, -130 + index * 40), size, timed[index], 0.44, "pen"))
            parts.append(write(*spot, wide_bubble * 1.7, row["text"], timed[index] + 0.28, index == count - 1))
            if count > 1:
                shaft, tip = hand.arrow(_rim(spot, after, wide_bubble, tall_bubble), _rim(after, spot, wide_bubble, tall_bubble), 34)
                parts.append(_line(shaft, size, timed[index] + 0.5, 0.24, "pen"))
                parts.append(_line(tip, size, timed[index] + 0.7, 0.1, "pen"))
    else:
        if wide:
            w, h = min(440.0, (1700 - 110 * (count - 1)) / count), 250.0
            left = (size[0] - w * count - 110 * (count - 1)) / 2
            boxes = [(left + i * (w + 110), (size[1] - h) / 2) for i in range(count)]
        else:
            w, h = 700.0, 190.0
            pitch = min((size[1] - h - 20) / max(count - 1, 1), 300.0)
            used = pitch * (count - 1) + h
            boxes = [(118.0, (size[1] - used) / 2 + i * pitch) for i in range(count)]
        for index, row in enumerate(rows):
            x, y = boxes[index]
            parts.append(_line(hand.box(x, y, w, h), size, timed[index], 0.5, "pen"))
            parts.append(write(x + w / 2, y + h / 2, w - 40, row["text"], timed[index] + 0.34, index == count - 1))
            if index + 1 < count:
                nx, ny = boxes[index + 1]
                start, end = (
                    ((x + w + 16, y + h / 2), (nx - 16, ny + h / 2)) if wide else ((x + w / 2 + hand.wobble(18), y + h + 14), (nx + w / 2 + hand.wobble(18), ny - 16))
                )
                shaft, tip = hand.arrow(start, end, 16)
                parts.append(_line(shaft, size, timed[index] + 0.62, 0.22, "pen"))
                parts.append(_line(tip, size, timed[index] + 0.82, 0.1, "pen"))
    return _stage(size[0], size[1], "".join(parts))


def _whiteboard(item: dict) -> str:
    rows = _beats(item, "points", 0.18, 0.66)
    kind = item.get("sketch", "chain")
    seed = str(item["title"])
    hand = _Hand(seed)
    swoop = hand.stroke((0, 16), (640, 12), 14)
    title = f'<h2 class="bt"><span>{kit.emphasis(item["title"])}</span><div class="ul">{_line(swoop, (640.0, 30.0), 0.5, 0.36, "pen")}</div></h2>'
    return f'<div class="d"><div class="board">{title}{_both(_sketch(rows, kind, False, seed), _sketch(rows, kind, True, seed))}</div></div>'


CSS_FLOW = """
& .node{position:absolute;left:calc(var(--x) * 1px * var(--s));top:calc(var(--y) * 1px * var(--s));width:calc(var(--nw) * 1px * var(--s));height:calc(var(--nh) * 1px * var(--s));
display:flex;align-items:center;gap:calc(20px * var(--s));padding:0 calc(26px * var(--s));border-radius:calc(20px * var(--s));background:var(--accent);color:var(--panel);
font-size:calc(35px * var(--s));font-weight:600;line-height:1.08;letter-spacing:-.022em;animation:ex-pop .42s var(--a) cubic-bezier(.2,1.3,.3,1) both,ex-rest .34s var(--b) ease forwards}
& .node.last{animation:ex-pop .42s var(--a) cubic-bezier(.2,1.3,.3,1) both}
& .node i{flex:none;font-family:var(--mono);font-style:normal;font-size:calc(25px * var(--s));font-weight:500;letter-spacing:.02em;opacity:.62}
& .node.ask i{display:grid;place-items:center;opacity:1;width:calc(38px * var(--s));height:calc(38px * var(--s));border-radius:calc(9px * var(--s));transform:rotate(45deg);box-shadow:0 0 0 calc(2.5px * var(--s)) currentColor inset}
& .node.ask i b{transform:rotate(-45deg);font-weight:600}
& .wl .node{flex-direction:column;align-items:flex-start;justify-content:center;gap:calc(8px * var(--s));line-height:1.06;padding:0 calc(22px * var(--s))}
& .tag{transform:translate(-50%,-50%);animation:ex-tag .34s var(--a) var(--ease) both}
& .tag span{display:block;padding:calc(8px * var(--s)) calc(18px * var(--s));border-radius:999px;background:var(--panel);box-shadow:0 0 0 1px rgba(255,255,255,.16) inset;font-size:calc(28px * var(--s));font-weight:500;color:var(--soft);white-space:nowrap}
@keyframes ex-rest{to{background:var(--surface);color:var(--ink);box-shadow:0 0 0 1px rgba(255,255,255,.13) inset}}
@keyframes ex-tag{from{opacity:0;scale:.8}to{opacity:1;scale:1}}
"""

CSS_TREE = """
& .tn{position:absolute;left:calc(var(--x) * 1px * var(--s));top:calc(var(--y) * 1px * var(--s));width:calc(var(--nw) * 1px * var(--s));height:calc(var(--nh) * 1px * var(--s));
display:flex;align-items:center;justify-content:center;padding:0 calc(20px * var(--s));text-align:center;border-radius:calc(20px * var(--s));background:var(--surface);color:var(--ink);
box-shadow:0 0 0 1px rgba(255,255,255,.14) inset;font-size:calc(33px * var(--s));font-weight:600;line-height:1.1;letter-spacing:-.02em;text-wrap:balance}
& .tn.root{background:var(--ink);color:var(--panel);font-size:calc(37px * var(--s));animation:ex-pop .42s var(--a) cubic-bezier(.2,1.3,.3,1) both}
& .wl .tn.root{font-size:calc(33px * var(--s));padding:0 calc(16px * var(--s))}
& .tn.pick{animation:ex-pop .4s var(--a) cubic-bezier(.2,1.3,.3,1) both,ex-lit .28s var(--b) ease forwards}
& .tn.drop{animation:ex-pop .4s var(--a) cubic-bezier(.2,1.3,.3,1) both,ex-dim .34s var(--b) ease forwards}
& .yn{transform:translate(-50%,-50%);animation:ex-tag .3s var(--a) var(--ease) both}
& .yn span{display:block;padding:calc(3px * var(--s)) calc(14px * var(--s));border-radius:999px;background:var(--panel);font-family:var(--mono);font-size:calc(25px * var(--s));font-weight:500;color:var(--soft);white-space:nowrap}
@keyframes ex-lit{to{background:var(--accent);color:var(--panel);box-shadow:0 0 calc(44px * var(--scale)) color-mix(in srgb,var(--accent) 42%,transparent)}}
@keyframes ex-dim{to{opacity:.36}}
@keyframes ex-tag{from{opacity:0;scale:.8}to{opacity:1;scale:1}}
"""

CSS_PIPE = """
& .rail{background:color-mix(in srgb,var(--ink) 18%,transparent);border-radius:9px}
& .rail.v{width:calc(6px * var(--s));height:calc(var(--len) * 1px * var(--s));margin-left:calc(-3px * var(--s))}
& .rail.h{height:calc(6px * var(--s));width:calc(var(--len) * 1px * var(--s));margin-top:calc(-3px * var(--s))}
& .fill{background:var(--accent);animation:ex-fill var(--t) var(--d) cubic-bezier(.66,0,.24,1) both}
& .fill.v{width:calc(6px * var(--s));height:calc(var(--len) * 1px * var(--s));margin-left:calc(-3px * var(--s));transform-origin:50% 0;--axis:scaleY(0)}
& .fill.h{height:calc(6px * var(--s));width:calc(var(--len) * 1px * var(--s));margin-top:calc(-3px * var(--s));transform-origin:0 50%;--axis:scaleX(0)}
& .stn{width:0;height:0}
& .stn .ring,& .stn .core,& .stn .tick{position:absolute;left:calc(-22px * var(--s));top:calc(-22px * var(--s));width:calc(44px * var(--s));height:calc(44px * var(--s));border-radius:50%}
& .stn .core{background:var(--surface);box-shadow:0 0 0 calc(3px * var(--s)) color-mix(in srgb,var(--ink) 30%,transparent) inset;animation:ex-stn .3s var(--a) cubic-bezier(.2,1.4,.3,1) both}
& .stn .ring{background:color-mix(in srgb,var(--accent) 34%,transparent);opacity:0;animation:ex-halo .62s var(--a) ease-out both}
& .stn .tick{position:absolute;--panel-line:1}
& .stn .tick .ln path{stroke:var(--panel);stroke-width:5}
& .lab{font-size:calc(42px * var(--s));font-weight:600;letter-spacing:-.024em;line-height:1.08;color:var(--ink);animation:ex-lab .4s var(--a) var(--ease) both}
& .lab.v{margin-left:calc(46px * var(--s));transform-origin:0 50%;translate:0 -50%;width:calc(500px * var(--s))}
& .lab.h{width:calc(290px * var(--s));margin-left:calc(-145px * var(--s));margin-top:calc(44px * var(--s));text-align:center;text-wrap:balance}
& .ride{width:0;height:0}
& .hop{position:absolute;left:0;top:0;width:0;height:0;animation:ex-hop var(--t) var(--d) cubic-bezier(.66,0,.24,1) both}
& .hop.v{--to:translateY(calc(var(--go) * 1px * var(--s)))}
& .hop.h{--to:translateX(calc(var(--go) * 1px * var(--s)))}
& .pk{position:absolute;left:calc(-14px * var(--s));top:calc(-14px * var(--s));width:calc(28px * var(--s));height:calc(28px * var(--s));border-radius:50%;background:var(--ink);box-shadow:0 0 0 calc(5px * var(--s)) var(--accent),0 0 calc(40px * var(--s)) calc(10px * var(--s)) color-mix(in srgb,var(--accent) 60%,transparent)}
& .tok{position:absolute;white-space:nowrap;animation:rise .4s var(--ease) both}
& .tok span{display:block;padding:calc(10px * var(--s)) calc(20px * var(--s));border-radius:calc(14px * var(--s));background:var(--ink);color:var(--panel);font-family:var(--mono);font-size:calc(27px * var(--s));font-weight:500}
& .nl .tok{right:calc(34px * var(--s));top:calc(-29px * var(--s))}
& .wl .tok{left:0;bottom:calc(30px * var(--s));translate:-50% 0}
@keyframes ex-fill{from{transform:var(--axis)}to{transform:none}}
@keyframes ex-hop{to{transform:var(--to)}}
@keyframes ex-stn{to{background:var(--accent);box-shadow:none}}
@keyframes ex-halo{from{opacity:.9;transform:scale(1)}to{opacity:0;transform:scale(3)}}
@keyframes ex-lab{from{opacity:.34}to{opacity:1}}
"""

CSS_HUB = """
& .orbit{position:absolute;inset:0;stroke-dashoffset:0;animation:ex-orbit var(--dur) linear both}
& .orbit svg{position:absolute;inset:0;width:100%;height:100%;overflow:visible}
& .orbit path{fill:none;stroke:color-mix(in srgb,var(--ink) 22%,transparent);stroke-width:2;stroke-dasharray:.5 1;stroke-linecap:round}
& .hub{width:calc(244px * var(--s));height:calc(244px * var(--s));margin:calc(-122px * var(--s)) 0 0 calc(-122px * var(--s));display:grid;place-items:center;padding:calc(20px * var(--s));border-radius:50%;background:var(--accent);color:var(--panel);text-align:center;
font-size:calc(40px * var(--s));font-weight:700;line-height:1.02;letter-spacing:-.03em;text-wrap:balance;box-shadow:0 0 calc(70px * var(--s)) color-mix(in srgb,var(--accent) 36%,transparent);animation:ex-pop .5s .06s cubic-bezier(.2,1.3,.3,1) both}
& .hub em{color:inherit}
& .wave{width:calc(244px * var(--s));height:calc(244px * var(--s));margin:calc(-122px * var(--s)) 0 0 calc(-122px * var(--s));border-radius:50%;box-shadow:0 0 0 calc(3px * var(--s)) var(--accent) inset;opacity:0;animation:ex-wave .6s var(--a) ease-out both}
& .sp{translate:-50% -50%;animation:ex-sp .46s var(--a) cubic-bezier(.2,1.35,.3,1) both}
& .sp span{display:block;width:max-content;max-width:calc(300px * var(--s));padding:calc(15px * var(--s)) calc(26px * var(--s));border-radius:calc(22px * var(--s));background:var(--surface);box-shadow:0 0 0 1px rgba(255,255,255,.15) inset,0 14px 34px rgba(0,0,0,.34);
font-size:calc(35px * var(--s));font-weight:600;line-height:1.08;letter-spacing:-.022em;color:var(--ink);text-align:center;text-wrap:balance}
@keyframes ex-orbit{to{stroke-dashoffset:-5}}
@keyframes ex-wave{from{opacity:.8;transform:scale(1)}to{opacity:0;transform:scale(1.7)}}
@keyframes ex-sp{from{opacity:0;scale:.6}to{opacity:1;scale:1}}
"""

CSS_STEPPED = """
& .hd{display:flex;align-items:flex-start;justify-content:space-between;gap:calc(24px * var(--s));margin-bottom:calc(14px * var(--s))}
& .hd .ttl{margin:0}
& .cnt{flex:none;margin-left:auto;display:flex;align-items:baseline;font-family:var(--mono);font-size:calc(30px * var(--s));font-weight:500;color:var(--soft)}
& .cnt span{position:relative;display:inline-block;width:1ch;height:1.2em;color:var(--accent)}
& .cnt b{position:absolute;inset:0;font-weight:500;opacity:0;animation:ex-on .01s var(--a) linear forwards,ex-off .01s var(--b) linear forwards}
& .cnt b.last{animation:ex-on .01s var(--a) linear forwards}
& .cnt em{color:inherit}
& .row{position:relative;display:flex;align-items:center;gap:calc(26px * var(--s));min-height:calc(102px * var(--s));padding:calc(12px * var(--s)) calc(18px * var(--s))}
& .row+.row{border-top:1px solid rgba(255,255,255,.07)}
& .row p{position:relative;margin:0;font-size:calc(42px * var(--s));font-weight:600;line-height:1.1;letter-spacing:-.026em;color:var(--ink);text-wrap:pretty;animation:ex-text .34s var(--a) var(--ease) both}
& .row .band{position:absolute;inset:calc(6px * var(--s)) 0;border-radius:calc(18px * var(--s));background:color-mix(in srgb,var(--accent) 14%,transparent);transform-origin:0 50%;animation:ex-band .36s var(--a) var(--ease) both,ex-off .36s var(--b) ease forwards}
& .row.last .band{animation:ex-band .36s var(--a) var(--ease) both}
& .bx{position:relative;flex:none;width:calc(52px * var(--s));height:calc(52px * var(--s));border-radius:calc(13px * var(--s));box-shadow:0 0 0 calc(3px * var(--s)) color-mix(in srgb,var(--ink) 34%,transparent) inset}
& .bx i{position:absolute;inset:0;border-radius:inherit;background:var(--accent);animation:ex-box .3s var(--a) cubic-bezier(.2,1.5,.3,1) both}
& .bx .ln path{stroke:var(--panel);stroke-width:5.5}
& .plate.grid .list{margin-top:calc(4px * var(--s))}
@container (min-width:1200px){& .plate.grid{display:grid;grid-template-columns:minmax(0,.72fr) minmax(0,1.28fr);gap:calc(56px * var(--s));align-items:start}
& .plate.grid .hd{flex-direction:column;margin:calc(10px * var(--s)) 0 0}
& .plate.grid .ttl{font-size:calc(66px * var(--s));letter-spacing:-.036em}
& .plate.grid .cnt{margin:calc(14px * var(--s)) 0 0}}
@keyframes ex-text{from{opacity:.3;transform:translateX(calc(-8px * var(--scale)))}to{opacity:1;transform:none}}
@keyframes ex-band{from{opacity:0;transform:scaleX(.2)}to{opacity:1;transform:none}}
@keyframes ex-off{to{opacity:0}}
@keyframes ex-on{to{opacity:1}}
@keyframes ex-box{from{opacity:0;transform:scale(.4)}to{opacity:1;transform:none}}
"""

CSS_EXPLODED = """
& .sl{position:absolute;inset:0;animation:ex-apart .7s var(--a) cubic-bezier(.3,1.32,.4,1) both}
& .fc{position:absolute;inset:0}
& .fc svg{position:absolute;inset:0;width:100%;height:100%;overflow:visible}
& .fc .t{fill:color-mix(in srgb,var(--surface) 76%,var(--ink));stroke:color-mix(in srgb,var(--ink) 34%,transparent);stroke-width:1.5;stroke-linejoin:round}
& .fc .l{fill:color-mix(in srgb,var(--surface) 86%,var(--ink))}
& .fc .r{fill:var(--surface)}
& .fc .i{fill:none;stroke:color-mix(in srgb,var(--ink) 18%,transparent);stroke-width:1.5;stroke-dasharray:6 7}
& .sl.hot .t{fill:var(--accent);stroke:color-mix(in srgb,var(--accent) 70%,#fff)}
& .sl.hot .l{fill:color-mix(in srgb,var(--accent) 66%,#000)}
& .sl.hot .r{fill:color-mix(in srgb,var(--accent) 44%,#000)}
& .sl.hot .i{stroke:color-mix(in srgb,var(--panel) 44%,transparent)}
& .lb{width:calc(304px * var(--s));translate:0 -50%;font-size:calc(34px * var(--s));font-weight:600;line-height:1.08;letter-spacing:-.022em;color:var(--ink);text-wrap:balance;animation:ex-lb .36s var(--a) var(--ease) both}
& .sl.hot .lb{color:var(--accent)}
@container (min-width:1200px){& .plate.side{display:grid;grid-template-columns:minmax(0,.8fr) minmax(0,1.2fr);gap:calc(30px * var(--s));align-items:center}
& .plate.side .ttl{font-size:calc(70px * var(--s));letter-spacing:-.038em;margin:0}}
@keyframes ex-apart{from{transform:translateY(calc(var(--dy) * 1px * var(--s)))}to{transform:none}}
@keyframes ex-lb{from{opacity:0;transform:translateX(calc(-14px * var(--scale)))}to{opacity:1;transform:none}}
"""

CSS_MORPH = """
& .mo{display:flex;flex-direction:column;align-items:center;gap:calc(30px * var(--s));padding:calc(44px * var(--s)) calc(36px * var(--s)) calc(40px * var(--s))}
& .spin{position:relative;width:calc(400px * var(--s));height:calc(400px * var(--s))}
& .art{position:absolute;inset:0;filter:drop-shadow(0 0 calc(50px * var(--s)) color-mix(in srgb,var(--accent) 34%,transparent));animation:ex-art .56s var(--ease) both,ex-squash .9s var(--m) cubic-bezier(.5,0,.3,1) both}
& .sk{position:absolute;left:50%;top:50%;width:calc(6px * var(--s));height:calc(38px * var(--s));margin:calc(-19px * var(--s)) 0 0 calc(-3px * var(--s));border-radius:9px;background:var(--accent);opacity:0;animation:ex-spark .56s calc(var(--m) + .46s) cubic-bezier(.16,1,.3,1) both}
& .sh{position:absolute;inset:0;clip-path:var(--a);background:linear-gradient(160deg,var(--ink),color-mix(in srgb,var(--ink) 66%,var(--panel)));animation:ex-melt .82s var(--m) cubic-bezier(.7,0,.2,1) both,ex-tint .5s calc(var(--m) + .26s) ease both}
& .sh.g1{opacity:.3;animation-delay:calc(var(--m) + .05s),calc(var(--m) + .3s)}
& .sh.g2{opacity:.16;animation-delay:calc(var(--m) + .1s),calc(var(--m) + .34s)}
& .wd{text-align:center;min-height:calc(186px * var(--s))}
& .wd p{margin:0;font-size:calc(66px * var(--s));font-weight:700;line-height:1.04;letter-spacing:-.036em;text-wrap:balance}
& .wd .w1{color:var(--ink);animation:rise .42s .2s var(--ease) both,ex-w1 .4s var(--m) var(--ease) forwards}
& .wd .lk{font-size:calc(36px * var(--s));font-weight:500;letter-spacing:-.01em;color:var(--soft);margin:calc(4px * var(--s)) 0;animation:rise .4s calc(var(--m) + .3s) var(--ease) both}
& .wd .w2{color:var(--accent);animation:rise .46s var(--o) var(--ease) both}
@container (min-width:1200px){& .mo{flex-direction:row;justify-content:center;gap:calc(70px * var(--s))}
& .wd{text-align:left;min-width:calc(620px * var(--s))}}
@keyframes ex-melt{from{clip-path:var(--a)}to{clip-path:var(--b)}}
@keyframes ex-tint{to{background:linear-gradient(160deg,var(--accent),color-mix(in srgb,var(--accent) 62%,var(--panel)))}}
@keyframes ex-art{from{opacity:0;transform:scale(.72) rotate(-8deg)}to{opacity:1;transform:none}}
@keyframes ex-squash{0%{scale:1 1;rotate:0deg}38%{scale:.84 1.12}72%{scale:1.09 .93}100%{scale:1 1;rotate:360deg}}
@keyframes ex-w1{to{opacity:.5}}
@keyframes ex-spark{0%{opacity:0;transform:rotate(var(--r)) translateY(calc(-150px * var(--s))) scaleY(.4)}30%{opacity:1}100%{opacity:0;transform:rotate(var(--r)) translateY(calc(-226px * var(--s))) scaleY(1)}}
"""

CSS_DOC = """
& .win{position:relative;width:100%;height:calc(690px * var(--s));overflow:hidden;border-radius:calc(30px * var(--s));background:color-mix(in srgb,var(--panel) 95%,transparent);box-shadow:0 0 0 1px rgba(255,255,255,.1) inset,0 30px 90px rgba(0,0,0,.46);animation:ex-plate .5s var(--ease) both;--pw:980}
& .page{position:absolute;left:50%;top:0;width:calc(var(--pw) * 1px * var(--s));margin-left:calc(var(--pw) * -.5px * var(--s));padding:calc(96px * var(--s)) calc(92px * var(--s));height:calc(1420px * var(--s));background:var(--ink);color:var(--panel);box-shadow:0 34px 90px rgba(0,0,0,.6);
transform-origin:50% 0;animation:ex-find var(--zt) var(--z) cubic-bezier(.72,0,.16,1) both}
& .page h3{margin:0 0 calc(44px * var(--s));font-family:var(--serif);font-size:calc(68px * var(--s));font-weight:400;line-height:1.02;letter-spacing:-.015em}
& .gb{display:block;width:var(--l);height:calc(15px * var(--s));margin:calc(23px * var(--s)) 0;border-radius:9px;background:color-mix(in srgb,var(--panel) 15%,transparent)}
& .gb.hd{--l:58%;height:calc(40px * var(--s));margin:0 0 calc(44px * var(--s));background:color-mix(in srgb,var(--panel) 36%,transparent)}
& .key{margin:calc(40px * var(--s)) 0;font-family:var(--serif);font-size:calc(56px * var(--s));line-height:1.2;letter-spacing:-.008em}
& .key mark{color:inherit;background:linear-gradient(var(--accent),var(--accent)) 0 86%/0% 92% no-repeat;padding:0 .08em;margin:0 -.08em;animation:ex-mark var(--mt) var(--m) cubic-bezier(.6,0,.2,1) both}
& .src{position:absolute;left:calc(22px * var(--s));bottom:calc(22px * var(--s));animation:rise .4s var(--m) var(--ease) both}
& .src span{display:block;padding:calc(10px * var(--s)) calc(20px * var(--s));border-radius:999px;background:var(--panel);color:var(--ink);font-size:calc(28px * var(--s));font-weight:500;box-shadow:0 0 0 1px rgba(255,255,255,.14) inset,0 10px 26px rgba(0,0,0,.34)}
@container (min-width:1200px){& .win{--pw:1400}& .key{font-size:calc(60px * var(--s))}}
@keyframes ex-find{from{transform:translateY(calc(26px * var(--s))) scale(.44)}to{transform:translateY(calc(-294px * var(--s))) scale(1)}}
@keyframes ex-mark{to{background-size:100% 92%}}
"""

CSS_SCALE = """
& .d{position:absolute;inset:0;container-type:size}
& .sky{position:absolute;inset:0;overflow:hidden;background:radial-gradient(110% 70% at 50% 38%,var(--surface),var(--panel) 74%)}
& .cam{position:absolute;left:50%;top:38%;width:0;height:0}
& .wrap{position:absolute;left:0;top:0;width:0;height:0}
& .lv{position:absolute;left:0;top:0;width:calc(4930px * var(--s));height:calc(4930px * var(--s));animation:ex-pull calc(var(--beat) * 3) calc(var(--lead) + (var(--i) - 1) * var(--beat)) linear var(--legs) both}
& .lv svg{position:absolute;inset:0;width:100%;height:100%}
& .lv rect{fill:color-mix(in srgb,var(--ink) 7%,transparent);stroke:color-mix(in srgb,var(--ink) 42%,transparent);stroke-width:.2}
& .lv .k{fill:color-mix(in srgb,var(--accent) 12%,transparent);stroke:var(--accent);stroke-width:.42}
& .lv .seed{fill:var(--accent)}
& .lv .glow{fill:color-mix(in srgb,var(--accent) 22%,transparent)}
& .sky::after{content:"";position:absolute;left:0;right:0;bottom:0;height:46%;background:linear-gradient(transparent,var(--panel) 62%)}
& .copy{position:absolute;left:var(--gutter);right:var(--gutter);bottom:16cqh;display:flex;flex-direction:column;gap:calc(20px * var(--s))}
& .ttl{margin:0;font-size:calc(40px * var(--s));font-weight:500;color:var(--soft);letter-spacing:-.016em}
& .stack{position:relative;height:calc(190px * var(--s))}
& .big{position:absolute;left:0;right:0;bottom:0;margin:0;font-size:calc(96px * var(--s));font-weight:700;line-height:1;letter-spacing:-.044em;color:var(--ink);text-wrap:balance;opacity:0;animation:ex-bigin .46s var(--a) var(--ease) forwards,ex-bigout .24s var(--b) ease-in forwards}
& .big.last{animation:ex-bigin .46s var(--a) var(--ease) forwards}
& .rule{display:flex;gap:calc(10px * var(--s))}
& .rule i{flex:1;height:calc(6px * var(--s));border-radius:9px;background:color-mix(in srgb,var(--ink) 18%,transparent);animation:ex-tick .2s var(--a) ease forwards}
@container (min-aspect-ratio:4/5) and (max-aspect-ratio:5/4){& .sky{--s:calc(var(--scale) * .78)}& .cam{top:37%}& .copy{bottom:6cqh}}
@container (min-aspect-ratio:5/4){& .cam{left:70%;top:50%}
& .sky::after{left:0;right:auto;top:0;bottom:0;width:58%;height:auto;background:linear-gradient(90deg,var(--panel) 60%,transparent)}
& .copy{right:53%;bottom:auto;top:50%;translate:0 -50%}}
@keyframes ex-bigin{from{opacity:0;transform:translateY(calc(30px * var(--scale)))}to{opacity:1;transform:none}}
@keyframes ex-bigout{from{opacity:1}to{opacity:0;transform:translateY(calc(-22px * var(--scale)))}}
@keyframes ex-tick{to{background:var(--accent)}}
""" + _zoom_frames()

CSS_SPLIT = """
@property --ex-at{syntax:'<percentage>';inherits:true;initial-value:100%}
& .win{position:relative;width:100%;height:calc(500px * var(--s));overflow:hidden;border-radius:calc(30px * var(--s));box-shadow:0 0 0 1px rgba(255,255,255,.12) inset,0 30px 90px rgba(0,0,0,.46);--ex-at:100%;animation:ex-plate .46s var(--ease) both,ex-wipe var(--take) var(--go) linear both}
& .sd{position:absolute;inset:0;display:grid;grid-template-columns:1fr 1fr}
& .sd .c{padding:calc(34px * var(--s)) calc(30px * var(--s));display:flex;flex-direction:column;gap:calc(12px * var(--s));min-width:0}
& .sd.a{background:var(--surface);color:var(--ink)}
& .sd.b{background:var(--accent);color:var(--panel);clip-path:inset(0 0 0 var(--ex-at))}
& .sd.b .c{grid-column:2}
& .c h3{margin:0;font-family:var(--mono);font-size:calc(27px * var(--s));font-weight:500;letter-spacing:.01em;text-transform:uppercase;opacity:.72}
& .c b{font-size:calc(92px * var(--s));font-weight:700;line-height:1;letter-spacing:-.05em;margin-bottom:calc(10px * var(--s))}
& .c ul{list-style:none;margin:auto 0 0;padding:0;display:flex;flex-direction:column;gap:calc(14px * var(--s))}
& .c li{display:flex;align-items:flex-start;gap:calc(12px * var(--s));font-size:calc(33px * var(--s));font-weight:500;line-height:1.1;letter-spacing:-.018em;animation:rise .4s var(--a) var(--ease) both}
& .c li svg{flex:none;width:calc(30px * var(--s));height:calc(30px * var(--s));margin-top:calc(2px * var(--s));fill:none;stroke:currentColor;stroke-width:2.6;stroke-linecap:round;stroke-linejoin:round;opacity:.7}
& .bar{position:absolute;top:0;bottom:0;left:var(--ex-at);width:0}
& .bar i{position:absolute;top:0;bottom:0;left:calc(-2.5px * var(--s));width:calc(5px * var(--s));background:var(--ink);box-shadow:0 0 calc(24px * var(--s)) rgba(0,0,0,.5)}
& .grip{position:absolute;left:calc(-38px * var(--s));top:50%;margin-top:calc(-38px * var(--s));width:calc(76px * var(--s));height:calc(76px * var(--s));display:grid;place-items:center;border-radius:50%;background:var(--ink);box-shadow:0 10px 30px rgba(0,0,0,.5)}
& .grip svg{width:calc(44px * var(--s));height:calc(26px * var(--s));fill:none;stroke:var(--panel);stroke-width:3.4;stroke-linecap:round;stroke-linejoin:round}
@container (min-width:1200px){& .c b{font-size:calc(120px * var(--s))}& .c li{font-size:calc(38px * var(--s))}& .sd .c{padding:calc(44px * var(--s)) calc(56px * var(--s))}}
@keyframes ex-wipe{0%{--ex-at:100%;animation-timing-function:cubic-bezier(.7,0,.2,1)}66%{--ex-at:34%;animation-timing-function:cubic-bezier(.4,0,.2,1)}100%{--ex-at:50%}}
"""

CSS_WHITEBOARD = """
& .d{position:absolute;inset:0;container-type:size}
& .board{position:absolute;inset:0;display:flex;flex-direction:column;padding:12cqh var(--gutter) 9cqh;color:var(--panel);background:radial-gradient(calc(3px * var(--s)) calc(3px * var(--s)) at 50% 50%,color-mix(in srgb,var(--panel) 12%,transparent) 60%,transparent 62%) 0 0/calc(44px * var(--s)) calc(44px * var(--s)),var(--ink);animation:ex-wipein .3s var(--ease) both}
& .board .nl,& .board .wl{margin:auto 0}
& .bt{position:relative;display:block;flex:none;width:fit-content;max-width:100%;margin:0 auto calc(40px * var(--s));font-family:var(--hand);font-size:calc(126px * var(--s));font-weight:700;line-height:.96;text-align:center;text-wrap:balance}
& .bt span{display:block;animation:ex-write .5s .1s steps(14) both}
& .bt em{color:inherit}
& .ul{position:relative;height:calc(30px * var(--s));margin-top:calc(2px * var(--s))}
& .ln.pen path{stroke:var(--panel);stroke-width:6.5}
& .ul .ln.pen path{stroke:var(--accent);stroke-width:15}
& .hw{width:calc(var(--nw) * 1px * var(--s));translate:-50% -50%;font-family:var(--hand);font-size:calc(78px * var(--s));font-weight:600;line-height:.96;text-align:center;text-wrap:balance}
& .hw span{display:block;animation:ex-write .34s var(--a) steps(10) both}
& .hw b{font-weight:inherit}
& .hw.hot b{padding:0 .12em;background:linear-gradient(transparent 46%,var(--accent) 46%,var(--accent) 92%,transparent 92%);-webkit-box-decoration-break:clone;box-decoration-break:clone}
@container (min-aspect-ratio:4/5) and (max-aspect-ratio:5/4){& .nl{--s:calc(var(--scale) * .8)}& .board{padding-top:6cqh;padding-bottom:4cqh}}
@container (min-aspect-ratio:5/4){& .board{padding-top:7cqh;padding-bottom:4cqh}}
@keyframes ex-wipein{from{opacity:0}to{opacity:1}}
@keyframes ex-write{from{clip-path:inset(-20% 100% -20% -2%)}to{clip-path:inset(-20% -2% -20% -2%)}}
"""

FORMATS = (
    Format(
        id="flowchart",
        title="Flowchart build",
        summary="Boxes join up step by step, with a line leading each one and an optional loop back.",
        group="explain",
        jobs=("explain",),
        slots=(Slot("steps", "list", limit=5, each=24), Slot("title", limit=40, required=False), Slot("back", limit=14, required=False)),
        example={
            "title": "How a change ships",
            "steps": [{"text": "Write the change"}, {"text": "Run the checks"}, {"text": "Do they pass?", "check": True}, {"text": "Ship it"}],
            "back": "Fix and retry",
        },
        draw=_flowchart,
        css=_scoped("flowchart", BASE, CSS_FLOW),
        seconds=5.0,
        shortest=3.0,
        longest=14.0,
        tall=830,
        sound="tick",
        fonts=("mono",),
    ),
    Format(
        id="decision_tree",
        title="Decision tree",
        summary="A question branches, and a light runs down the path taken while the rest falls away.",
        group="explain",
        jobs=("explain",),
        slots=(Slot("question", limit=40), Slot("options", "list", limit=6, each=26), Slot("yes", limit=6, required=False), Slot("no", limit=6, required=False)),
        example={
            "question": "Will you do this again?",
            "options": [{"text": "Does it change often?", "on": True}, {"text": "Do it by hand"}, {"text": "Write a checklist"}, {"text": "Automate it", "on": True}],
            "no": "No",
        },
        draw=_decision,
        css=_scoped("decision_tree", BASE, CSS_TREE),
        seconds=6.0,
        shortest=4.0,
        longest=14.0,
        tall=720,
        sound="tick",
        fonts=("mono",),
    ),
    Format(
        id="pipeline",
        title="Pipeline",
        summary="A token travels a line of stages, and each stage lights as it arrives.",
        group="explain",
        jobs=("explain", "prove"),
        slots=(Slot("stages", "list", limit=5, each=22), Slot("token", limit=12, required=False), Slot("title", limit=40, required=False)),
        example={
            "title": "From clip to post",
            "token": "clip.mp4",
            "stages": [{"text": "Transcribe"}, {"text": "Cut the silence"}, {"text": "Add the captions"}, {"text": "Publish"}],
        },
        draw=_pipeline,
        css=_scoped("pipeline", BASE, CSS_PIPE),
        seconds=5.0,
        shortest=3.0,
        longest=14.0,
        tall=800,
        sound="tick",
        fonts=("mono",),
    ),
    Format(
        id="hub_spokes",
        title="Hub and spokes",
        summary="Ideas shoot out from a centre on lines, one at a time, each landing with a bounce.",
        group="explain",
        jobs=("explain",),
        slots=(Slot("hub", limit=18), Slot("spokes", "list", limit=6, each=18)),
        example={"hub": "One *brand kit*", "spokes": [{"text": "Fonts"}, {"text": "Colours"}, {"text": "Logo"}, {"text": "Caption style"}, {"text": "Sound"}]},
        draw=_hub,
        css=_scoped("hub_spokes", BASE, CSS_HUB),
        seconds=4.5,
        shortest=3.0,
        longest=12.0,
        tall=752,
        sound="pop",
    ),
    Format(
        id="stepped_list",
        title="Stepped list",
        summary="Points tick off as they are spoken, with a count running and the live row lit.",
        group="explain",
        jobs=("explain", "prove"),
        slots=(Slot("items", "list", limit=5, each=32), Slot("title", limit=36, required=False)),
        example={
            "title": "Before you post",
            "items": [{"text": "Cut the first ten seconds"}, {"text": "Check every *name*"}, {"text": "Read the captions aloud"}, {"text": "Pick the frame"}],
        },
        draw=_stepped,
        css=_scoped("stepped_list", BASE, CSS_STEPPED),
        seconds=4.5,
        shortest=2.5,
        longest=14.0,
        tall=710,
        sound="tick",
        fonts=("mono",),
    ),
    Format(
        id="exploded_view",
        title="Exploded view",
        summary="A solid stack pulls apart into its layers, and each layer gets named.",
        group="explain",
        jobs=("explain",),
        slots=(Slot("layers", "list", limit=5, each=24), Slot("title", limit=36, required=False)),
        example={
            "title": "What sits under the app",
            "layers": [{"text": "The screen you see"}, {"text": "The rules", "on": True}, {"text": "The engine"}, {"text": "Your data"}],
        },
        draw=_exploded,
        css=_scoped("exploded_view", BASE, CSS_EXPLODED),
        seconds=4.5,
        shortest=3.0,
        longest=12.0,
        tall=815,
        sound="whoosh",
    ),
    Format(
        id="analogy_morph",
        title="Analogy morph",
        summary="One shape melts into another, so a new idea lands on one people already know.",
        group="explain",
        jobs=("explain", "hook"),
        slots=(
            Slot("from_word", limit=20),
            Slot("to_word", limit=20),
            Slot("link", limit=16, required=False),
            Slot("from_shape", "choice", choices=tuple(SHAPES)),
            Slot("to_shape", "choice", choices=tuple(SHAPES)),
        ),
        example={"from_word": "A cache", "link": "works like", "to_word": "A full pantry", "from_shape": "hexagon", "to_shape": "heart"},
        draw=_morph,
        css=_scoped("analogy_morph", BASE, CSS_MORPH),
        seconds=4.0,
        shortest=2.8,
        longest=8.0,
        tall=705,
        sound="whoosh",
    ),
    Format(
        id="doc_highlight",
        title="Document highlight",
        summary="A page comes up, the camera finds one passage, and a marker sweeps the words that count.",
        group="explain",
        jobs=("prove", "explain"),
        slots=(Slot("text", limit=110), Slot("heading", limit=30, required=False), Slot("source", limit=36, required=False)),
        example={
            "heading": "Terms of service",
            "text": "You keep the rights to your work, and *we may end this plan with 30 days of notice*.",
            "source": "Section 4, page 12",
        },
        draw=_document,
        css=_scoped("doc_highlight", BASE, CSS_DOC),
        seconds=8.6,
        shortest=3.5,
        longest=10.0,
        tall=690,
        sound="whoosh",
        fonts=("serif",),
    ),
    Format(
        id="scale_zoom",
        title="Scale zoom",
        summary="The camera pulls back through orders of magnitude, each level shrinking into one cell.",
        group="explain",
        jobs=("explain", "hook"),
        slots=(Slot("levels", "list", limit=4, each=30), Slot("title", limit=36, required=False)),
        example={"levels": [{"text": "One viewer"}, {"text": "A *full* cinema"}, {"text": "A whole city"}, {"text": "*Every* phone"}]},
        draw=_scale,
        css=_scoped("scale_zoom", BASE, CSS_SCALE),
        seconds=6.0,
        shortest=4.0,
        longest=12.0,
        area="full",
        tall=1920,
        sound="whoosh",
        needs_alpha=False,
    ),
    Format(
        id="split_compare",
        title="Split compare",
        summary="A divider sweeps across and settles, leaving two states side by side.",
        group="explain",
        jobs=("prove", "explain"),
        slots=(
            Slot("before_title", limit=14),
            Slot("after_title", limit=14),
            Slot("before", "list", limit=3, each=22),
            Slot("after", "list", limit=3, each=22),
            Slot("before_value", limit=6, required=False),
            Slot("after_value", limit=6, required=False),
        ),
        example={
            "before_title": "By hand",
            "after_title": "Scripted",
            "before_value": "14 hrs",
            "after_value": "20 min",
            "before": [{"text": "Copied one by one"}, {"text": "Errors found late"}],
            "after": [{"text": "Runs on every save"}, {"text": "Errors stop the build"}],
        },
        draw=_split,
        css=_scoped("split_compare", BASE, CSS_SPLIT),
        seconds=4.5,
        shortest=3.0,
        longest=10.0,
        tall=500,
        sound="whoosh",
        fonts=("mono",),
    ),
    Format(
        id="whiteboard",
        title="Whiteboard draw-on",
        summary="Marker strokes sketch the idea on a clean board as it is said.",
        group="explain",
        jobs=("explain",),
        slots=(Slot("title", limit=30), Slot("points", "list", limit=4, each=18), Slot("sketch", "choice", required=False, choices=("chain", "cycle", "venn"))),
        example={"title": "Why it *compounds*", "points": [{"text": "Post daily"}, {"text": "Learn what lands"}, {"text": "Post better"}], "sketch": "cycle"},
        draw=_whiteboard,
        css=_scoped("whiteboard", BASE, CSS_WHITEBOARD),
        seconds=6.0,
        shortest=4.0,
        longest=14.0,
        area="full",
        tall=1920,
        sound="tick",
        fonts=("hand",),
        needs_alpha=False,
    ),
)
