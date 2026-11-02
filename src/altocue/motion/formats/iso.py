"""Isometric and depth. No GPU, no key, no model: planes in CSS 3D, and dioramas Python projects from a scene spec.

Every animation sits on an HTML element, because the renderer seeks animations on SVG elements
against the film's clock, not the clip's. A diorama is one stacked SVG per solid, each inside a
div, and the div's registered `--is-k` custom property drives the faces inside.
"""

from __future__ import annotations

import math
from collections.abc import Iterable
from typing import Any

from ...errors import AltocueError
from . import Format, Slot, kit

GROUNDS = ("dark", "light", "clear")
COS30 = math.cos(math.radians(30))
UNIT = 100.0  # one grid cell, in the diorama's own coordinates
Point = tuple[float, float]


def _picture(row: dict) -> str:
    return f'<img src="{kit.esc(row["src"])}" alt="">' if row.get("src") else ""


def _scoped(format_id: str, *parts: str) -> str:
    return "".join(parts).replace("&", f".f-{format_id}")


def _n(value: float) -> str:
    return f"{value:.1f}".rstrip("0").rstrip(".")


def _beats(item: dict, name: str, first: float, last: float) -> list[dict]:
    rows, span = kit.rows(item, name), kit.seconds(item)
    for index, row in enumerate(rows):
        if row.get("at") is None:
            row["delay"] = span * (first + (last - first) * index / max(len(rows) - 1, 1))
    return rows


GROUND = """
& .d{position:absolute;inset:0;container-type:size;overflow:hidden;--s:var(--scale);--bg:var(--panel);--fg:var(--ink);--mid:var(--surface);--wire:color-mix(in srgb,var(--ink) 12%,transparent)}
& .d.dark{background:radial-gradient(90% 70% at 50% 46%,color-mix(in srgb,var(--surface) 88%,var(--accent)),var(--panel) 78%)}
& .d.light{--bg:var(--ink);--fg:var(--panel);--mid:color-mix(in srgb,var(--ink) 90%,var(--panel));--wire:color-mix(in srgb,var(--panel) 12%,transparent);background:radial-gradient(90% 70% at 50% 46%,var(--ink),color-mix(in srgb,var(--ink) 88%,var(--panel)) 90%)}
& .ttl{position:absolute;left:var(--gutter);right:var(--gutter);top:11cqh;margin:0;font-size:calc(66px * var(--s));font-weight:700;line-height:1.02;letter-spacing:-.036em;color:var(--fg);text-wrap:balance;animation:rise .5s .05s var(--ease) both;z-index:4}
& .d.clear .ttl{text-shadow:0 2px 6px rgba(0,0,0,.34),0 10px 44px rgba(0,0,0,.5)}
@container (min-aspect-ratio:5/4){& .ttl{right:58%;top:50%;translate:0 -50%;font-size:calc(84px * var(--s))}}
@container (min-aspect-ratio:4/5) and (max-aspect-ratio:5/4){& .d{--s:calc(var(--scale) * .9)}& .ttl{top:6cqh}}
"""

# Iso Stack


def _stack_plan(move: str, count: int) -> list[tuple[float, float, float, float]]:
    """For each layer: where it rests, and where it starts, in design pixels from the stage centre."""
    middle = (count - 1) / 2
    plan = []
    for index in range(count):
        offset = index - middle
        if move == "fan":
            step = min(260.0, 538.0 / max(count - 1, 1))
            x, y = -offset * step * 0.866, offset * step * 0.5
            plan.append((x, y, 0.0, offset * 14))
        elif move == "stack":
            y = offset * min(116.0, 380.0 / max(count - 1, 1))
            plan.append((0.0, y, 0.0, y - 1300))
        else:
            y = offset * min(200.0, 620.0 / max(count - 1, 1))
            plan.append((0.0, y, 0.0, offset * 15))
    return plan


def _layer_face(row: dict, index: int, size: int) -> str:
    if row.get("src"):
        return f'<div class="face shot">{_picture(row)}</div>'
    return f'<div class="face card" style="--fs:{size}px"><i>{index + 1:02d}</i><b>{kit.emphasis(row["text"])}</b></div>'


def _iso_stack(item: dict) -> str:
    move = item.get("move", "explode")
    rows = _beats(item, "layers", 0.14, 0.42)
    plan = _stack_plan(move, len(rows))
    if move == "stack":  # the bottom layer lands first, so unanchored rows swap their moments end for end
        free = [row for row in rows if row.get("at") is None]
        for row, moment in zip(free, reversed([row["delay"] for row in free]), strict=True):
            row["delay"] = moment
    planes, tags = [], []
    for index in range(len(rows) - 1, -1, -1):
        row = rows[index]
        x, y, x0, y0 = plan[index]
        hot = " hot" if row.get("on") else ""
        place = f"--x:{_n(x)};--y:{_n(y)};--x0:{_n(x0)};--y0:{_n(y0)};--a:{row['delay']:.3f}s"
        planes.append(
            f'<div class="lay{hot}" style="{place}"><div class="tilt"><div class="side b"></div><div class="side l"></div>{_layer_face(row, index, 82 if len(rows) < 4 else 70)}</div></div>'
        )
        if row.get("src"):
            tags.append(f'<div class="tag" style="--x:{_n(x)};--y:{_n(y)};--a:{row["delay"] + 0.5:.3f}s"><span>{kit.esc(row["text"])}</span></div>')
    title = f'<h2 class="ttl">{kit.emphasis(item["title"])}</h2>' if item.get("title") else ""
    ground = item.get("ground", "dark")
    floor = '<div class="floor"></div>' if ground != "clear" else ""
    centred = " lone" if not title else ""
    return f'<div class="d {ground} {move}{centred}">{title}{floor}<div class="scene"><div class="pool"></div>{"".join(planes)}{"".join(tags)}</div></div>'


CSS_STACK = """
& .scene{position:absolute;left:50%;top:56cqh;width:0;height:0;animation:is-push var(--dur) linear both}
& .d.lone .scene{top:46cqh}
& .floor{position:absolute;left:50%;top:50cqh;width:calc(2400px * var(--s));height:calc(2400px * var(--s));margin:calc(-1200px * var(--s)) 0 0 calc(-1200px * var(--s));transform:rotateX(54.736deg) rotateZ(-45deg);
background:linear-gradient(var(--wire) 0 calc(2px * var(--s)),transparent 0) 0 0/calc(120px * var(--s)) calc(120px * var(--s)),linear-gradient(90deg,var(--wire) 0 calc(2px * var(--s)),transparent 0) 0 0/calc(120px * var(--s)) calc(120px * var(--s));
-webkit-mask-image:radial-gradient(closest-side,#000 30%,transparent);mask-image:radial-gradient(closest-side,#000 30%,transparent)}
& .pool{position:absolute;left:calc(-420px * var(--s));top:calc(300px * var(--s));width:calc(840px * var(--s));height:calc(400px * var(--s));border-radius:50%;background:radial-gradient(closest-side,rgba(0,0,0,.5),transparent);animation:rise .6s var(--ease) both}
& .d.light .pool{background:radial-gradient(closest-side,rgba(0,0,0,.22),transparent)}
& .d.clear .pool{display:none}
& .lay{position:absolute;left:0;top:0;width:0;height:0;transform:translate(calc(var(--x) * 1px * var(--s)),calc(var(--y) * 1px * var(--s)));animation:is-apart .8s var(--a) cubic-bezier(.3,1.34,.42,1) both}
& .explode .lay,& .fan .lay{animation:is-in .46s var(--ease) both,is-apart .8s var(--a) cubic-bezier(.3,1.34,.42,1) both}
& .stack .lay{animation:is-apart .62s var(--a) cubic-bezier(.3,1.26,.5,1) both}
& .tilt{position:absolute;left:calc(-360px * var(--s));top:calc(-230px * var(--s));width:calc(720px * var(--s));height:calc(460px * var(--s));transform-style:preserve-3d;transform:rotateX(54.736deg) rotateZ(-45deg);animation:is-drift var(--dur) linear both}
& .face{position:absolute;inset:0;overflow:hidden;background:linear-gradient(135deg,color-mix(in srgb,var(--mid) 74%,var(--fg)),color-mix(in srgb,var(--mid) 92%,var(--fg)));color:var(--fg);box-shadow:0 0 0 calc(1.5px * var(--s)) color-mix(in srgb,var(--fg) 28%,transparent) inset,calc(-22px * var(--s)) calc(22px * var(--s)) calc(50px * var(--s)) rgba(0,0,0,.42)}
& .face img{position:absolute;inset:0;width:100%;height:100%;object-fit:cover;object-position:50% 0}
& .card{display:flex;flex-direction:column;justify-content:space-between;padding:calc(30px * var(--s)) calc(38px * var(--s)) calc(24px * var(--s))}
& .card i{font-family:var(--mono);font-style:normal;font-size:calc(36px * var(--s));font-weight:500;opacity:.6}
& .card b{font-size:calc(var(--fs) * var(--s));font-weight:700;line-height:1.02;letter-spacing:-.034em;text-wrap:balance}
& .lay.hot .face{background:linear-gradient(135deg,color-mix(in srgb,var(--accent) 86%,#fff),var(--accent));color:var(--panel)}
& .lay.hot .card em{color:inherit}
& .side{position:absolute;background:color-mix(in srgb,var(--mid) 62%,#000)}
& .side.b{left:0;right:0;top:100%;height:calc(20px * var(--s));transform-origin:50% 0;transform:rotateX(-90deg)}
& .side.l{left:0;top:0;bottom:0;width:calc(20px * var(--s));transform-origin:0 50%;transform:rotateY(90deg);background:color-mix(in srgb,var(--mid) 44%,#000)}
& .lay.hot .side.b{background:color-mix(in srgb,var(--accent) 66%,#000)}
& .lay.hot .side.l{background:color-mix(in srgb,var(--accent) 44%,#000)}
& .tag{position:absolute;left:calc((var(--x) + 196) * 1px * var(--s));top:calc((var(--y) + 126) * 1px * var(--s));white-space:nowrap;translate:-50% -50%;animation:is-tag .4s var(--a) cubic-bezier(.2,1.3,.3,1) both}
& .tag span{display:block;padding:calc(9px * var(--s)) calc(20px * var(--s));border-radius:calc(14px * var(--s));background:var(--fg);color:var(--bg);box-shadow:0 12px 30px rgba(0,0,0,.36);font-size:calc(33px * var(--s));font-weight:650;letter-spacing:-.02em}
& .d.fan .lay,& .d.fan .tag{--s:calc(var(--scale) * .72)}
@container (min-aspect-ratio:5/4){& .scene{left:66cqw;top:50cqh}& .d.lone .scene{left:50cqw;top:48cqh}}
@keyframes is-apart{from{transform:translate(calc(var(--x0) * 1px * var(--s)),calc(var(--y0) * 1px * var(--s)))}to{transform:translate(calc(var(--x) * 1px * var(--s)),calc(var(--y) * 1px * var(--s)))}}
@keyframes is-in{from{opacity:0;translate:0 calc(60px * var(--s))}to{opacity:1;translate:0 0}}
@keyframes is-drift{from{transform:rotateX(54.736deg) rotateZ(-48deg)}to{transform:rotateX(54.736deg) rotateZ(-43deg)}}
@keyframes is-push{from{scale:.96}to{scale:1.02}}
@keyframes is-tag{from{opacity:0;scale:.72}to{opacity:1;scale:1}}
"""

# Iso Board


def _iso(x: float, y: float, z: float = 0.0) -> Point:
    return ((x - y) * COS30 * UNIT, (x + y) * 0.5 * UNIT - z * UNIT)


def _pts(points: Iterable[Point]) -> str:
    return " ".join(f"{_n(x)},{_n(y)}" for x, y in points)


def _hull(points: list[Point]) -> list[Point]:
    points = sorted(set(points))

    def half(run: list[Point]) -> list[Point]:
        out: list[Point] = []
        for p in run:
            while len(out) > 1 and (out[-1][0] - out[-2][0]) * (p[1] - out[-2][1]) - (out[-1][1] - out[-2][1]) * (p[0] - out[-2][0]) <= 0:
                out.pop()
            out.append(p)
        return out[:-1]

    return half(points) + half(points[::-1])


def _spec(row: dict) -> dict:
    x, y = (list(row.get("cell") or (0, 0)) + [0, 0])[:2]
    size = (list(row.get("size") or (1, 1, 1)) + [1, 1, 1])[:3]
    return {**row, "x": float(x), "y": float(y), "w": float(size[0]), "d": float(size[1]), "h": float(size[2])}


def _order(blocks: list[dict]) -> list[dict]:
    """Back to front. A solid goes first when it sits wholly behind another along either ground axis."""
    remaining, out = list(blocks), []

    def behind(a: dict, b: dict) -> bool:
        return a["x"] + a["w"] <= b["x"] + 1e-6 or a["y"] + a["d"] <= b["y"] + 1e-6

    while remaining:
        free = [a for a in remaining if not any(behind(b, a) and not behind(a, b) for b in remaining if b is not a)]
        pick = min(free or remaining, key=lambda s: s["x"] + s["y"] + s["w"] + s["d"])
        remaining.remove(pick)
        out.append(pick)
    return out


def _face(a: Point, b: Point, height: float, tone: str) -> str:
    """A wall from the ground edge a-b, as a unit square mapped into place, so `--is-k` can raise it by scaling one axis."""
    return f'<g transform="matrix({_n(b[0] - a[0])} {_n(b[1] - a[1])} 0 {_n(-height)} {_n(a[0])} {_n(a[1])})"><rect class="{tone} up" width="1" height="1"/></g>'


def _box(block: dict, shift: Point) -> str:
    x, y, w, d, h = block["x"], block["y"], block["w"], block["d"], block["h"]
    def at(px: float, py: float, pz: float = 0.0) -> Point:
        sx, sy = _iso(px, py, pz)
        return (sx + shift[0], sy + shift[1])

    tall = h * UNIT
    left = _face(at(x, y + d), at(x + w, y + d), tall, "fl")
    right = _face(at(x + w, y + d), at(x + w, y), tall, "fr")
    top = _pts((at(x, y), at(x + w, y), at(x + w, y + d), at(x, y + d)))
    inset = min(w, d) * 0.12
    lip = _pts((at(x + inset, y + inset), at(x + w - inset, y + inset), at(x + w - inset, y + d - inset), at(x + inset, y + d - inset)))
    return f'{left}{right}<g class="lift" style="--u:{_n(tall)}px"><polygon class="ft" points="{top}"/><polygon class="lip" points="{lip}"/></g>'


def _can(block: dict, shift: Point) -> str:
    x, y, w, d, h = block["x"], block["y"], block["w"], block["d"], block["h"]
    radius = min(w, d) / 2
    cx, cy = _iso(x + w / 2, y + d / 2)
    cx, cy = cx + shift[0], cy + shift[1]
    rx, ry = radius * math.sqrt(1.5) * UNIT, radius * math.sqrt(0.5) * UNIT
    tall = h * UNIT
    bottom = f"M{_n(cx - rx)} {_n(cy)}A{_n(rx)} {_n(ry)} 0 0 0 {_n(cx)} {_n(cy + ry)}L{_n(cx)} {_n(cy)}Z"
    side = f"M{_n(cx)} {_n(cy + ry)}A{_n(rx)} {_n(ry)} 0 0 0 {_n(cx + rx)} {_n(cy)}L{_n(cx)} {_n(cy)}Z"
    walls = (
        f'<g transform="matrix({_n(rx)} 0 0 {_n(-tall)} {_n(cx - rx)} {_n(cy)})"><rect class="fl up" width="1" height="1"/></g>'
        f'<g transform="matrix({_n(rx)} 0 0 {_n(-tall)} {_n(cx)} {_n(cy)})"><rect class="fr up" width="1" height="1"/></g>'
    )
    tiers = "".join(
        f'<g class="lift" style="--u:{_n(tall * share)}px"><path class="band" d="M{_n(cx - rx)} {_n(cy)}A{_n(rx)} {_n(ry)} 0 0 0 {_n(cx + rx)} {_n(cy)}"/></g>' for share in (1 / 3, 2 / 3)
    )
    top = f'<g class="lift" style="--u:{_n(tall)}px"><ellipse class="ft" cx="{_n(cx)}" cy="{_n(cy)}" rx="{_n(rx)}" ry="{_n(ry)}"/><ellipse class="lip" cx="{_n(cx)}" cy="{_n(cy)}" rx="{_n(rx * 0.7)}" ry="{_n(ry * 0.7)}"/></g>'
    return f'<path class="fl" d="{bottom}"/><path class="fr" d="{side}"/>{walls}{tiers}{top}'


def _shadow(block: dict, shift: Point) -> str:
    x, y, w, d, h = block["x"], block["y"], block["w"], block["d"], block["h"]
    reach = 0.5 + h * 0.34
    corners = [(x, y), (x + w, y), (x + w, y + d), (x, y + d)]
    cast = corners + [(px + reach, py - reach * 0.34) for px, py in corners]
    shape = [(px + shift[0], py + shift[1]) for px, py in (_iso(a, b) for a, b in cast)]
    return f'<polygon class="shade" points="{_pts(_hull(shape))}"/>'


def _svg(box: tuple[float, float], body: str) -> str:
    return f'<svg viewBox="0 0 {_n(box[0])} {_n(box[1])}" aria-hidden="true">{body}</svg>'


def _elbow(points: list[Point], radius: float = 26) -> str:
    out = [f"M{_n(points[0][0])} {_n(points[0][1])}"]
    for before, corner, after in zip(points, points[1:], points[2:], strict=False):
        into, leave = math.dist(before, corner), math.dist(corner, after)
        if not into or not leave:
            continue
        r = min(radius, into / 2, leave / 2)
        p = (corner[0] + (before[0] - corner[0]) * r / into, corner[1] + (before[1] - corner[1]) * r / into)
        q = (corner[0] + (after[0] - corner[0]) * r / leave, corner[1] + (after[1] - corner[1]) * r / leave)
        out.append(f"L{_n(p[0])} {_n(p[1])}Q{_n(corner[0])} {_n(corner[1])} {_n(q[0])} {_n(q[1])}")
    out.append(f"L{_n(points[-1][0])} {_n(points[-1][1])}")
    return "".join(out)


def _board_units(blocks: list[dict]) -> list[dict]:
    """A counted block becomes that many small cubes in its footprint, for an Isotype read."""
    out = []
    for block in blocks:
        count = int(block.get("count") or 0)
        if block.get("shape") != "count" and not count:
            out.append(block)
            continue
        count = max(min(count or 9, 36), 1)
        columns = max(math.ceil(math.sqrt(count * block["w"] / max(block["d"], 0.1))), 1)
        rows = math.ceil(count / columns)
        cell = min(block["w"] / columns, block["d"] / rows)
        size = cell * 0.74
        for index in range(count):
            column, row = index % columns, index // columns
            out.append(
                {
                    **block,
                    "x": block["x"] + column * cell + (cell - size) / 2,
                    "y": block["y"] + row * cell + (cell - size) / 2,
                    "w": size,
                    "d": size,
                    "h": size,
                    "shape": "box",
                    "text": "",
                    "unit": index,
                }
            )
    return out


def _iso_board(item: dict) -> str:
    span = kit.seconds(item)
    rows = _beats(item, "blocks", 0.1, 0.52)
    blocks = [_spec(row) for row in rows]
    named = {block.get("id"): block for block in blocks if block.get("id")}
    routes = kit.rows(item, "routes")
    if routes and all(route.get("at") is None for route in routes):
        for index, route in enumerate(routes):
            route["delay"] = span * (0.58 + 0.16 * index / max(len(routes) - 1, 1))

    pad = 1.0
    x0 = min(b["x"] for b in blocks) - pad
    y0 = min(b["y"] for b in blocks) - pad
    x1 = max(b["x"] + b["w"] for b in blocks) + pad
    y1 = max(b["y"] + b["d"] for b in blocks) + pad
    edge = 0.34
    plan = [_iso(x0, y0), _iso(x1, y0), _iso(x1, y1), _iso(x0, y1)]
    tops = [_iso(b["x"], b["y"], b["h"]) for b in blocks]
    left = min(p[0] for p in plan) - 26
    right = max(p[0] for p in plan) + 26
    above = min(min(p[1] for p in plan), min(p[1] for p in tops)) - 190
    below = max(p[1] for p in plan) + edge * UNIT + 26
    shift = (-left, -above)
    box = (right - left, below - above)

    def at(px: float, py: float, pz: float = 0.0) -> Point:
        sx, sy = _iso(px, py, pz)
        return (sx + shift[0], sy + shift[1])

    grid = []
    for column in range(int(x1 - x0) + 1):
        grid.append(f'<path d="M{_n(at(x0 + column, y0)[0])} {_n(at(x0 + column, y0)[1])}L{_n(at(x0 + column, y1)[0])} {_n(at(x0 + column, y1)[1])}"/>')
    for row in range(int(y1 - y0) + 1):
        grid.append(f'<path d="M{_n(at(x0, y0 + row)[0])} {_n(at(x0, y0 + row)[1])}L{_n(at(x1, y0 + row)[0])} {_n(at(x1, y0 + row)[1])}"/>')
    down = (0.0, edge * UNIT)
    a, b, c = at(x1, y0), at(x1, y1), at(x0, y1)
    slab = (
        f'<polygon class="sr" points="{_pts((a, b, (b[0] + down[0], b[1] + down[1]), (a[0] + down[0], a[1] + down[1])))}"/>'
        f'<polygon class="sl" points="{_pts((c, b, (b[0] + down[0], b[1] + down[1]), (c[0] + down[0], c[1] + down[1])))}"/>'
        f'<polygon class="st" points="{_pts((at(x0, y0), a, b, c))}"/><g class="gr">{"".join(grid)}</g>'
    )
    ground = [f'<div class="base">{_svg(box, slab)}</div>']
    units = _order(_board_units(blocks))
    for block in units:
        delay = block["delay"] + block.get("unit", 0) * 0.035
        ground.append(f'<div class="cast" style="--a:{delay + 0.1:.3f}s">{_svg(box, _shadow(block, shift))}</div>')
    for route in routes:
        start, end = named.get(route.get("from")), named.get(route.get("to"))
        if not start or not end:
            missing = route.get("from") if not start else route.get("to")
            raise AltocueError(f'iso_board: a route names "{missing}" and no block has that id.', fix='Give the block an "id", and use it in the route\'s "from" and "to".')
        ax, ay = start["x"] + start["w"] / 2, start["y"] + start["d"] / 2
        bx, by = end["x"] + end["w"] / 2, end["y"] + end["d"] / 2
        corner = (ax, by) if route.get("via") == "y" else (bx, ay)
        path = _elbow([at(ax, ay), at(*corner), at(bx, by)])
        delay = route["delay"]
        ground.append(f'<div class="road" style="--d:{delay:.3f}s">{_svg(box, f"""<path pathLength="1" d="{path}"/>""")}</div>')
        ground.append(f'<div class="ln" style="--d:{delay:.3f}s;--t:.6s">{_svg(box, f"""<path pathLength="1" d="{path}"/>""")}</div>')
        repeats = max(int((span - delay - 0.8) / 1.0), 1)
        body = f'<path class="halo" pathLength="1" d="{path}"/><path class="core" pathLength="1" d="{path}"/>'
        ground.append(f'<div class="pk" style="--d:{delay + 0.6:.3f}s;--t:1s;--n:{repeats}">{_svg(box, body)}</div>')
    solids = []
    pins = []
    taken: list[tuple[float, float, float, float]] = []
    for block in units:
        delay = block["delay"] + block.get("unit", 0) * 0.035
        shape = _can(block, shift) if block.get("shape") == "can" else _box(block, shift)
        tone = " hot" if block.get("on") else ""
        solids.append(f'<div class="bk{tone}" style="--a:{delay:.3f}s">{_svg(box, shape)}</div>')
    for block in blocks:
        text = block.get("text")
        if not text:
            continue
        cx, cy = at(block["x"] + block["w"] / 2, block["y"] + block["d"] / 2, block["h"])
        half = len(str(text)) * 13 + 34
        stem = 54.0
        while any(abs(cx - ox) < half + ow and abs(cy - stem - oy) < 64 for ox, oy, ow, _ in taken) and stem < 200:
            stem += 54
        taken.append((cx, cy - stem, half, 0.0))
        pins.append(
            f'<div class="pin" style="--x:{cx / box[0] * 100:.2f}%;--y:{cy / box[1] * 100:.2f}%;--stem:{stem / box[1] * 100:.2f}cqh;--a:{block["delay"] + 0.36:.3f}s"><i></i><span>{kit.esc(text)}</span></div>'
        )
    scene = "".join(ground) + "".join(solids) + "".join(pins)
    scene = _camera(item, at, box, scene)
    title = f'<h2 class="ttl">{kit.emphasis(item["title"])}</h2>' if item.get("title") else ""
    ground_name = item.get("ground", "dark")
    centred = " lone" if not title else ""
    return f'<div class="d {ground_name}{centred}">{title}<div class="fit" style="--ar:{box[0] / box[1]:.4f}">{scene}</div></div>'


def _camera(item: dict, at: Any, box: tuple[float, float], scene: str) -> str:
    """Each move is one wrapper from the last view to the next, so a camera path of any length stays plain CSS."""
    moves = kit.rows(item, "camera")
    if not moves:
        return f'<div class="cam drift">{scene}</div>'
    span = kit.seconds(item)
    place, zoom = (0.5, 0.5), 1.0
    for index, move in enumerate(moves):
        if move.get("at") is None:
            move["delay"] = span * (0.5 + 0.3 * index / max(len(moves), 1))
        point = at(*(list(move.get("cell") or (0, 0)) + [0, 0])[:2])
        target = (point[0] / box[0], point[1] / box[1])
        next_zoom = float(move.get("zoom", 1.0))
        ratio = next_zoom / zoom
        tx = 0.5 - 0.5 * ratio + next_zoom * (place[0] - target[0])
        ty = 0.5 - 0.5 * ratio + next_zoom * (place[1] - target[1])
        scene = f'<div class="cam" style="--to:translate({tx * 100:.2f}%,{ty * 100:.2f}%) scale({ratio:.4f});--d:{move["delay"]:.3f}s;--t:{float(move.get("length", 1.1)):.3f}s">{scene}</div>'
        place, zoom = target, next_zoom
    return scene


CSS_BOARD = """
@property --is-k{syntax:'<number>';inherits:true;initial-value:0}
& .fit{position:absolute;left:50%;top:54cqh;translate:-50% -50%;width:min(calc(100cqw - 2 * var(--gutter) + calc(60px * var(--s))),calc(70cqh * var(--ar)));aspect-ratio:var(--ar)}
& .d.lone .fit{top:48cqh}
& .cam{position:absolute;inset:0;transform-origin:0 0;animation:is-cam var(--t) var(--d) cubic-bezier(.66,0,.2,1) both}
& .cam.drift{transform-origin:50% 50%;animation:is-drift2 var(--dur) linear both}
& .base,& .cast,& .road,& .ln,& .pk,& .bk{position:absolute;inset:0;pointer-events:none}
& svg{position:absolute;inset:0;width:100%;height:100%;overflow:visible}
& .base{animation:is-base .7s var(--ease) both}
& .st{fill:color-mix(in srgb,var(--mid) 88%,var(--fg))}
& .sl{fill:color-mix(in srgb,var(--mid) 70%,#000)}
& .sr{fill:color-mix(in srgb,var(--mid) 50%,#000)}
& .gr path{stroke:var(--wire);stroke-width:1.6;fill:none}
& .cast{filter:blur(calc(5px * var(--s)));opacity:0;animation:is-cast .5s var(--a) ease forwards}
& .shade{fill:rgba(0,0,0,.34)}
& .road path{fill:none;stroke:color-mix(in srgb,var(--fg) 16%,transparent);stroke-width:18;stroke-linecap:round;stroke-linejoin:round}
& .road{opacity:0;animation:is-cast .3s var(--d) ease forwards}
& .ln{stroke-dashoffset:1;animation:is-draw var(--t) var(--d) cubic-bezier(.6,0,.2,1) both}
& .ln path{fill:none;stroke:var(--accent);stroke-width:5;stroke-linecap:round;stroke-linejoin:round;stroke-dasharray:1 1}
& .pk{stroke-dashoffset:0;opacity:0;animation:is-run var(--t) var(--d) cubic-bezier(.45,0,.55,1) var(--n)}
& .pk path{fill:none;stroke-linecap:round;stroke-dasharray:.0001 2}
& .pk .halo{stroke:color-mix(in srgb,var(--accent) 34%,transparent);stroke-width:30}
& .pk .core{stroke:var(--fg);stroke-width:13}
& .bk{--is-k:0;animation:is-rise .62s var(--a) cubic-bezier(.3,0,.2,1) both}
& .up{transform:scaleY(var(--is-k));stroke-width:1;vector-effect:non-scaling-stroke}
& .lift{transform:translateY(calc(var(--is-k) * var(--u) * -1))}
& .d{--tone:color-mix(in srgb,color-mix(in srgb,var(--surface) 48%,var(--ink)) 80%,var(--accent))}
& .d.light{--tone:color-mix(in srgb,color-mix(in srgb,var(--ink) 74%,var(--panel)) 72%,var(--accent))}
& .bk .ft{fill:color-mix(in srgb,var(--tone) 62%,#fff);stroke:color-mix(in srgb,var(--tone) 40%,#fff);stroke-width:1.5;stroke-linejoin:round}
& .bk .fl{fill:var(--tone);stroke:var(--tone)}
& .bk .fr{fill:color-mix(in srgb,var(--tone) 62%,#000);stroke:color-mix(in srgb,var(--tone) 62%,#000)}
& .bk .lip{fill:none;stroke:color-mix(in srgb,var(--tone) 80%,#fff);stroke-width:2}
& .bk .band{fill:none;stroke:rgba(0,0,0,.3);stroke-width:3}
& .bk.hot .ft{fill:var(--accent);stroke:color-mix(in srgb,var(--accent) 70%,#fff)}
& .bk.hot .fl{fill:color-mix(in srgb,var(--accent) 68%,#000);stroke:color-mix(in srgb,var(--accent) 68%,#000)}
& .bk.hot .fr{fill:color-mix(in srgb,var(--accent) 44%,#000);stroke:color-mix(in srgb,var(--accent) 44%,#000)}
& .bk.hot .lip{stroke:color-mix(in srgb,var(--accent) 74%,#000)}
& .pin{position:absolute;left:var(--x);top:var(--y);width:0;height:0;animation:is-pin .36s var(--a) cubic-bezier(.2,1.3,.3,1) both}
& .pin i{position:absolute;left:calc(-1.5px * var(--s));bottom:0;width:calc(3px * var(--s));height:var(--stem);background:var(--fg)}
& .pin span{position:absolute;left:0;bottom:var(--stem);translate:-50% 0;padding:calc(8px * var(--s)) calc(18px * var(--s));border-radius:calc(14px * var(--s));background:var(--fg);color:var(--bg);font-size:calc(31px * var(--s));font-weight:650;letter-spacing:-.02em;white-space:nowrap;box-shadow:0 10px 26px rgba(0,0,0,.34)}
@container (min-aspect-ratio:5/4){& .fit{left:64cqw;top:52cqh;width:min(60cqw,calc(82cqh * var(--ar)))}& .d.lone .fit{left:50cqw;width:min(84cqw,calc(86cqh * var(--ar)))}}
@container (min-aspect-ratio:4/5) and (max-aspect-ratio:5/4){& .fit{top:56cqh;width:min(calc(100cqw - var(--gutter)),calc(68cqh * var(--ar)))}}
@keyframes is-rise{0%{--is-k:0}70%{--is-k:1.07}100%{--is-k:1}}
@keyframes is-base{from{opacity:0;transform:translateY(calc(70px * var(--s)))}to{opacity:1;transform:none}}
@keyframes is-cast{to{opacity:1}}
@keyframes is-draw{from{opacity:0;stroke-dashoffset:1}1%{opacity:1}to{opacity:1;stroke-dashoffset:0}}
@keyframes is-run{0%{opacity:0;stroke-dashoffset:0}12%{opacity:1}88%{opacity:1}100%{opacity:0;stroke-dashoffset:-1}}
@keyframes is-pin{from{opacity:0;scale:.7}to{opacity:1;scale:1}}
@keyframes is-cam{to{transform:var(--to)}}
@keyframes is-drift2{from{scale:.98}to{scale:1.03}}
"""

# Orbit cards


def _orbit(item: dict) -> str:
    rows = _beats(item, "cards", 0.04, 0.3)
    count = len(rows)
    cards = []
    for index, row in enumerate(rows):
        angle = 360 * index / count + 180 / count  # no card rests dead in front of the centre
        body = _picture(row)
        text = f"<span>{kit.emphasis(row['text'])}</span>"
        cards.append(
            f'<div class="ob" style="--o:{angle:.2f}deg"><div class="arm" style="--a:{row["delay"]:.3f}s"><div class="cn"><div class="cd{" pic" if body else ""}{" hot" if row.get("on") else ""}">{body}{text}</div></div></div></div>'
        )
    hub = f'<div class="core"><b>{kit.emphasis(item["center"])}</b></div>' if item.get("center") else '<div class="core"></div>'
    fog = "".join(f'<div class="fog" style="--z:{depth}"></div>' for depth in (-40, -130, -240))
    ground = item.get("ground", "dark")
    return f'<div class="d {ground}"><div class="lens"><div class="ring"><div class="path"></div>{fog}{hub}{"".join(cards)}</div></div></div>'


CSS_ORBIT = """
& .lens{position:absolute;left:50%;top:42cqh;width:0;height:0;perspective:calc(1600px * var(--s));animation:is-up .6s var(--ease) both}
& .ring{position:absolute;left:0;top:0;width:0;height:0;transform-style:preserve-3d;transform:rotateX(-38deg);--r:calc(330px * var(--s))}
& .ob,& .arm,& .cn{position:absolute;left:0;top:0;width:0;height:0;transform-style:preserve-3d}
& .ob{animation:is-orbit var(--dur) cubic-bezier(.16,.74,.3,1) both}
& .arm{animation:is-arm .8s var(--a) cubic-bezier(.3,1.3,.4,1) both}
& .cn{animation:is-face var(--dur) cubic-bezier(.16,.74,.3,1) both}
& .cd{position:absolute;left:calc(-134px * var(--s));top:calc(-78px * var(--s));width:calc(268px * var(--s));height:calc(156px * var(--s));display:flex;align-items:flex-end;overflow:hidden;padding:calc(18px * var(--s)) calc(20px * var(--s));border-radius:calc(22px * var(--s));
background:linear-gradient(150deg,color-mix(in srgb,var(--surface) 70%,var(--ink)),color-mix(in srgb,var(--surface) 90%,var(--ink)));color:var(--ink);box-shadow:0 0 0 1px rgba(255,255,255,.2) inset,0 24px 60px rgba(0,0,0,.5);font-size:calc(36px * var(--s));font-weight:650;line-height:1.04;letter-spacing:-.024em}
& .cd.hot{background:linear-gradient(150deg,color-mix(in srgb,var(--accent) 84%,#fff),var(--accent));color:var(--panel)}
& .cd.hot em{color:inherit}
& .cd img{position:absolute;inset:0;width:100%;height:100%;object-fit:cover;object-position:50% 0}
& .cd.pic span{position:relative;padding:calc(4px * var(--s)) calc(12px * var(--s));border-radius:calc(10px * var(--s));background:color-mix(in srgb,var(--panel) 84%,transparent);color:var(--ink);font-size:calc(29px * var(--s))}
& .core{position:absolute;left:calc(-158px * var(--s));top:calc(-158px * var(--s));width:calc(316px * var(--s));height:calc(316px * var(--s));display:grid;place-items:center;padding:calc(30px * var(--s));text-align:center;border-radius:50%;transform:rotateX(38deg);
background:radial-gradient(circle at 36% 30%,color-mix(in srgb,var(--accent) 72%,#fff),var(--accent) 46%,color-mix(in srgb,var(--accent) 62%,#000));box-shadow:0 0 calc(110px * var(--s)) color-mix(in srgb,var(--accent) 34%,transparent);color:var(--panel);font-size:calc(50px * var(--s));font-weight:750;line-height:1;letter-spacing:-.04em;text-wrap:balance;animation:is-core .56s cubic-bezier(.2,1.3,.3,1) both}
& .core em{color:inherit}
& .path{position:absolute;left:calc(var(--r) * -1);top:calc(var(--r) * -1);width:calc(var(--r) * 2);height:calc(var(--r) * 2);border-radius:50%;box-shadow:0 0 0 calc(3px * var(--s)) color-mix(in srgb,var(--ink) 24%,transparent) inset,0 0 calc(70px * var(--s)) color-mix(in srgb,var(--accent) 10%,transparent) inset;transform:translateY(calc(112px * var(--s))) rotateX(90deg);animation:is-core .7s .1s var(--ease) both}
& .d.clear .fog{display:none}
& .fog{position:absolute;left:calc(-560px * var(--s));top:calc(-340px * var(--s));width:calc(1120px * var(--s));height:calc(680px * var(--s));transform:translateZ(calc(var(--z) * 1px * var(--s))) rotateX(38deg);background:radial-gradient(closest-side,color-mix(in srgb,var(--panel) 30%,transparent),transparent)}
@container (min-aspect-ratio:5/4){& .lens{top:48cqh}& .ring{--r:calc(470px * var(--s))}& .fog{transform:translateZ(calc(var(--z) * 1px * var(--s))) rotateX(38deg) scaleX(1.5)}}
@container (min-aspect-ratio:4/5) and (max-aspect-ratio:5/4){& .ring{--r:calc(390px * var(--s))}& .lens{top:46cqh}}
@keyframes is-orbit{from{transform:rotateY(calc(var(--o) - 200deg))}to{transform:rotateY(var(--o))}}
@keyframes is-face{from{transform:rotateY(calc(200deg - var(--o))) rotateX(38deg)}to{transform:rotateY(calc(0deg - var(--o))) rotateX(38deg)}}
@keyframes is-arm{from{transform:translateZ(0) scale(.2)}to{transform:translateZ(var(--r)) scale(1)}}
@keyframes is-core{from{opacity:0;scale:.86}to{opacity:1;scale:1}}
@keyframes is-up{from{opacity:0;translate:0 calc(50px * var(--scale))}to{opacity:1;translate:0 0}}
"""

# Dolly zoom


def _dolly(item: dict) -> str:
    frames = "".join(f'<i class="fr" style="--z:{-(index + 1) * 300};--f:{1 - index / 13:.3f}"></i>' for index in range(13))
    label = f'<p class="lb">{kit.esc(item["label"])}</p>' if item.get("label") else ""
    return (
        f'<div class="d"><div class="lens"><div class="tun"><i class="pl flr"></i><i class="pl cei"></i><i class="pl lft"></i><i class="pl rgt"></i>{frames}<i class="far"></i></div></div>'
        f'<div class="sub"><div class="disc"></div><h1>{kit.emphasis(item["text"])}</h1>{label}</div></div>'
    )


CSS_DOLLY = """
& .d{position:absolute;inset:0;container-type:size;overflow:hidden;background:var(--panel);--s:var(--scale)}
& .lens{position:absolute;inset:0;perspective:calc(5200px * var(--s));perspective-origin:50% 46%;animation:is-lens var(--dur) cubic-bezier(.7,0,.2,1) both}
& .tun{position:absolute;inset:0;transform-style:preserve-3d}
& .fr{position:absolute;inset:-2%;box-shadow:0 0 0 calc(3px * var(--s)) color-mix(in srgb,var(--accent) calc(var(--f) * 80%),transparent) inset;transform:translateZ(calc(var(--z) * 1px * var(--s)))}
& .far{position:absolute;inset:0;transform:translateZ(calc(-4200px * var(--s)));background:radial-gradient(closest-side,var(--accent),color-mix(in srgb,var(--accent) 18%,var(--panel)) 42%,var(--panel));opacity:.9}
& .pl{position:absolute;background:repeating-linear-gradient(90deg,color-mix(in srgb,var(--ink) 16%,transparent) 0 calc(2px * var(--s)),transparent 0 calc(120px * var(--s))),linear-gradient(90deg,color-mix(in srgb,var(--surface) 90%,var(--ink)),var(--surface))}
& .flr,& .cei{left:-2%;right:-2%;height:calc(4400px * var(--s))}
& .flr{top:102%;transform-origin:50% 0;transform:rotateX(-90deg)}
& .cei{bottom:102%;transform-origin:50% 100%;transform:rotateX(90deg)}
& .lft,& .rgt{top:-2%;bottom:-2%;width:calc(4400px * var(--s));background:repeating-linear-gradient(0deg,color-mix(in srgb,var(--ink) 16%,transparent) 0 calc(2px * var(--s)),transparent 0 calc(120px * var(--s))),var(--surface)}
& .lft{right:102%;transform-origin:100% 50%;transform:rotateY(-90deg)}
& .rgt{left:102%;transform-origin:0 50%;transform:rotateY(90deg)}
& .sub{position:absolute;left:var(--gutter);right:var(--gutter);top:46cqh;translate:0 -50%;text-align:center;animation:is-sub .5s var(--ease) both}
& .disc{position:absolute;left:50%;top:50%;width:150%;aspect-ratio:1.5;translate:-50% -50%;background:radial-gradient(closest-side,color-mix(in srgb,var(--panel) 66%,transparent),transparent)}
& .sub h1{position:relative;margin:0;font-size:calc(150px * var(--s));font-weight:800;line-height:.94;letter-spacing:-.05em;color:var(--ink);text-wrap:balance}
& .lb{position:relative;margin:calc(26px * var(--s)) 0 0;font-size:calc(42px * var(--s));font-weight:500;letter-spacing:-.016em;color:var(--soft)}
@keyframes is-lens{from{perspective:calc(5200px * var(--s))}to{perspective:calc(620px * var(--s))}}
@keyframes is-sub{from{opacity:0;scale:.94}to{opacity:1;scale:1}}
"""

# Rack focus


def _rack(item: dict) -> str:
    rows = _beats(item, "layers", 0.05, 0.62)
    count = len(rows)
    cells = []
    needle = '<div class="pip"></div>'
    for index in range(count - 1, 0, -1):
        needle = f'<div class="slide" style="--to:{100 / max(count - 1, 1):.2f}%;--a:{rows[index]["delay"] - 0.12:.3f}s">{needle}</div>'
    for index, row in enumerate(rows):
        leave = f";--b:{rows[index + 1]['delay'] - 0.12:.3f}s" if index + 1 < count else ""
        last = "" if leave else " last"
        size = 72 + 32 * index / max(count - 1, 1)
        cells.append(f'<p class="ly{last}" style="--fs:{size:.0f}px;--a:{row["delay"]:.3f}s{leave}"><span>{kit.emphasis(row["text"])}</span></p>')
    ticks = "".join("<i></i>" for _ in range(25))
    return f'<div class="d"><div class="rack">{"".join(cells)}</div><div class="bar"><div class="tk">{ticks}</div><div class="nd">{needle}</div></div></div>'


CSS_RACK = """
& .d{position:relative;width:100%;container-type:inline-size;--s:var(--scale)}
& .rack{display:flex;flex-direction:column;gap:calc(26px * var(--s))}
& .ly{margin:0;transform-origin:0 50%;font-size:calc(var(--fs) * var(--s));font-weight:750;line-height:.98;letter-spacing:-.046em;color:var(--ink);text-wrap:balance;text-shadow:0 2px 6px rgba(0,0,0,.34),0 10px 44px rgba(0,0,0,.5);
filter:blur(calc(14px * var(--s)));opacity:.5;transform:scale(1.03);animation:is-focus .56s var(--a) cubic-bezier(.5,0,.2,1) both,is-defocus .56s var(--b) cubic-bezier(.5,0,.2,1) forwards}
& .ly.last{animation:is-focus .56s var(--a) cubic-bezier(.5,0,.2,1) both}
& .bar{position:relative;margin-top:calc(38px * var(--s));height:calc(40px * var(--s));animation:rise .5s var(--ease) both}
& .tk{display:flex;justify-content:space-between;align-items:flex-end;height:100%}
& .tk i{width:calc(3px * var(--s));height:38%;background:rgba(255,255,255,.5)}
& .tk i:nth-child(6n+1){height:72%;background:var(--ink)}
& .nd{position:absolute;left:0;right:calc(6px * var(--s));top:0;bottom:0}
& .slide{position:absolute;inset:0;animation:is-slide .56s var(--a) cubic-bezier(.5,0,.2,1) both}
& .pip{position:absolute;left:0;bottom:0;width:calc(6px * var(--s));height:130%;border-radius:9px;background:var(--accent);box-shadow:0 0 calc(18px * var(--s)) var(--accent)}
@container (min-width:1200px){& .ly{font-size:calc(var(--fs) * 1.3 * var(--s))}}
@keyframes is-focus{to{filter:blur(0);opacity:1;transform:none}}
@keyframes is-defocus{to{filter:blur(calc(14px * var(--s)));opacity:.5;transform:scale(.985)}}
@keyframes is-slide{to{transform:translateX(var(--to))}}
"""

FORMATS = (
    Format(
        id="iso_stack",
        title="Isometric stack",
        summary="Your screenshots or text cards float on isometric planes that part, drop or fan out.",
        group="iso",
        jobs=("explain", "prove"),
        slots=(
            Slot("layers", "list", limit=5, each=24),
            Slot("title", limit=44, required=False),
            Slot("move", "choice", required=False, choices=("explode", "stack", "fan")),
            Slot("ground", "choice", required=False, choices=GROUNDS),
        ),
        example={"title": "Three steps, *one screen*", "layers": [{"text": "Draft the script", "on": True}, {"text": "Cut to the words"}, {"text": "Render the film"}], "move": "explode"},
        draw=_iso_stack,
        css=_scoped("iso_stack", GROUND, CSS_STACK),
        seconds=5.0,
        shortest=3.0,
        longest=12.0,
        area="full",
        tall=1920,
        sound="whoosh",
        fonts=("mono",),
    ),
    Format(
        id="iso_board",
        title="Isometric board",
        summary="A little diorama builds itself from a scene spec, shaded from your brand colour.",
        group="iso",
        jobs=("explain",),
        slots=(
            Slot("blocks", "list", limit=10, each=18),
            Slot("routes", "list", limit=6, required=False),
            Slot("camera", "list", limit=3, required=False),
            Slot("title", limit=44, required=False),
            Slot("ground", "choice", required=False, choices=GROUNDS),
        ),
        example={
            "title": "How a *video* gets made",
            "blocks": [
                {"id": "app", "text": "Your app", "cell": [0, 0], "size": [2, 2, 1.2], "on": True},
                {"id": "api", "text": "The API", "cell": [5, 0], "size": [2, 2, 0.7]},
                {"id": "data", "text": "Your data", "cell": [5, 5], "size": [2, 2, 1.2], "shape": "can"},
                {"id": "jobs", "text": "Render jobs", "cell": [0, 5], "size": [2, 2, 0.5], "shape": "count", "count": 9},
            ],
            "routes": [{"from": "app", "to": "api"}, {"from": "api", "to": "data"}, {"from": "data", "to": "jobs"}],
        },
        draw=_iso_board,
        css=_scoped("iso_board", GROUND, CSS_BOARD),
        seconds=6.0,
        shortest=3.5,
        longest=14.0,
        area="full",
        tall=1920,
        sound="pop",
    ),
    Format(
        id="orbit_cards",
        title="Orbit cards",
        summary="Cards swing round a centre in real depth, passing in front and behind.",
        group="iso",
        jobs=("explain", "prove"),
        slots=(Slot("cards", "list", limit=6, each=20), Slot("center", limit=18, required=False), Slot("ground", "choice", required=False, choices=GROUNDS)),
        example={"center": "One *timeline*", "cards": [{"text": "Captions"}, {"text": "Music", "on": True}, {"text": "B-roll"}, {"text": "Graphics"}, {"text": "Sound"}]},
        draw=_orbit,
        css=_scoped("orbit_cards", GROUND, CSS_ORBIT),
        seconds=5.0,
        shortest=3.0,
        longest=12.0,
        area="full",
        tall=1920,
        sound="whoosh",
    ),
    Format(
        id="dolly_zoom",
        title="Dolly zoom",
        summary="The words hold still while the world behind them stretches away.",
        group="iso",
        jobs=("hook",),
        slots=(Slot("text", limit=24), Slot("label", limit=50, required=False)),
        example={"text": "It gets *worse*", "label": "Every month you wait"},
        draw=_dolly,
        css=_scoped("dolly_zoom", CSS_DOLLY),
        seconds=3.5,
        shortest=2.0,
        longest=7.0,
        area="full",
        tall=1920,
        sound="whoosh",
        needs_alpha=False,
    ),
    Format(
        id="rack_focus",
        title="Rack focus",
        summary="Focus pulls from line to line, so one thought at a time comes sharp.",
        group="iso",
        jobs=("hook", "explain"),
        slots=(Slot("layers", "list", limit=4, each=24),),
        example={"layers": [{"text": "Not the views"}, {"text": "Not the likes"}, {"text": "The *one* who buys"}]},
        draw=_rack,
        css=_scoped("rack_focus", CSS_RACK),
        seconds=4.0,
        shortest=2.5,
        longest=10.0,
        tall=840,
        sound="whoosh",
    ),
)
