"""Maps from public domain shapes. All geometry is worked out here in Python, so the page only plays CSS on HTML wrappers."""

from __future__ import annotations

import hashlib
import json
import math
import re
from collections.abc import Iterable, Sequence
from dataclasses import dataclass
from functools import cache
from typing import Any

from ...brand import data_path
from ...errors import AltocueError
from . import Format, Slot, kit

Point = tuple[float, float]
Ring = list[Point]
EDGE = 2600  # stage units kept around the subject, so a camera that starts wide still has land to show
NUMBER = re.compile(r"-?\d+")


@cache
def _atlas() -> dict[str, Any]:
    return json.loads(data_path("maps", "countries-110m.json").read_text(encoding="utf-8"))


def _decode(path: str) -> list[Ring]:
    """Stored paths are integers. A ring that crosses the date line is unwrapped, and one around a pole is closed along it."""
    units = _atlas()["units_per_degree"]
    rings = []
    for part in path.split("M")[1:]:
        numbers = [int(n) for n in NUMBER.findall(part)]
        x, y = numbers[0], numbers[1]
        ring = [(x / units, y / units)]
        shift = 0.0
        for step in range(2, len(numbers), 2):
            x, y = x + numbers[step], y + numbers[step + 1]
            lon = x / units + shift
            if lon - ring[-1][0] > 180:
                shift -= 360
            elif lon - ring[-1][0] < -180:
                shift += 360
            ring.append((x / units + shift, y / units))
        if abs(ring[-1][0] - ring[0][0]) > 180:
            pole = 90.0 if sum(p[1] for p in ring) > 0 else -90.0
            ring += [(ring[-1][0], pole), (ring[0][0], pole)]
        rings.append(ring)
    return rings


@cache
def _country_rings(name: str) -> tuple[tuple[Point, ...], ...]:
    return tuple(tuple(ring) for ring in _decode(_atlas()["countries"][name]))


def _names() -> tuple[str, ...]:
    return tuple(_atlas()["countries"])


def _area(ring: Sequence[Point]) -> float:
    total = 0.0
    for (x0, y0), (x1, y1) in zip(ring, ring[1:], strict=False):
        total += (x0 * y1 - x1 * y0) * math.cos(math.radians((y0 + y1) / 2))
    return abs(total) / 2


def _main_rings(name: str) -> list[Sequence[Point]]:
    """The parts that make up the body of a country: big, and near the biggest. A far part still draws, it just does not decide the framing."""
    rings = _country_rings(name)
    main = max(rings, key=_area)
    x0, x1, y0, y1 = _extent(main)
    middle, reach = ((x0 + x1) / 2, (y0 + y1) / 2), 1.2 * math.hypot(x1 - x0, y1 - y0) + 4
    near = []
    for ring in rings:
        a0, a1, b0, b1 = _extent(ring)
        if _area(ring) >= _area(main) * 0.2 and math.dist(((a0 + a1) / 2, (b0 + b1) / 2), middle) <= reach:
            near.append(ring)
    return near


def _centre(name: str) -> Point:
    ring = max(_country_rings(name), key=_area)
    return (sum(p[0] for p in ring) / len(ring), sum(p[1] for p in ring) / len(ring))


def _known(name: Any, owner: str) -> str:
    if name not in _atlas()["countries"]:
        raise AltocueError(
            f'{owner}: "{name}" is not a country in the map data.', fix="Use the Natural Earth short name, for example United States of America or Dem. Rep. Congo."
        )
    return str(name)


def _number(row: dict[str, Any], key: str, owner: str, slot: str) -> float:
    try:
        return float(row[key])
    except (KeyError, TypeError, ValueError):
        raise AltocueError(f'{owner}: every row in "{slot}" needs a number in "{key}".', fix=f"See `altocue library show {owner}`.") from None


def _natural(lam: float, phi: float) -> Point:
    """The Natural Earth projection, from longitude and latitude in radians."""
    p2 = phi * phi
    p4 = p2 * p2
    x = lam * (0.8707 - 0.131979 * p2 + p4 * (-0.013791 + p4 * (0.003971 * p2 - 0.001529 * p4)))
    y = phi * (1.007226 + p2 * (0.015085 + p4 * (-0.044475 + 0.028874 * p2 - 0.005916 * p4)))
    return x, y


@dataclass(frozen=True)
class View:
    """Longitude and latitude to stage units. The stage origin is the middle of the subject."""

    lon: float
    k: float
    cx: float = 0.0
    cy: float = 0.0

    def at(self, lon: float, lat: float) -> Point:
        lam = (lon - self.lon + 180) % 360 - 180
        x, y = _natural(math.radians(lam), math.radians(max(min(lat, 89.9), -89.9)))
        return (x - self.cx) * self.k, (self.cy - y) * self.k

    def ring(self, ring: Sequence[Point]) -> Ring:
        """A whole ring, kept in one piece. The ring moves by a full turn when it sits on the far side of the view."""
        middle = sum(p[0] for p in ring) / len(ring)
        move = round((self.lon - middle) / 360) * 360
        out = []
        for lon, lat in ring:
            x, y = _natural(math.radians(lon + move - self.lon), math.radians(max(min(lat, 89.9), -89.9)))
            out.append(((x - self.cx) * self.k, (self.cy - y) * self.k))
        return out


def _mean_lon(lons: Iterable[float]) -> float:
    values = [math.radians(lon) for lon in lons]
    return math.degrees(math.atan2(sum(map(math.sin, values)), sum(map(math.cos, values))))


def _fit(points: Sequence[Point], *, width: float = 860, height: float = 860, least: float = 0.16, lon: float | None = None) -> View:
    """A view that puts these places in the middle of the stage. `least` is the smallest span in radians, so close places keep some context."""
    lon0 = _mean_lon(p[0] for p in points) if lon is None else lon
    flat = [_natural(math.radians((p[0] - lon0 + 180) % 360 - 180), math.radians(max(min(p[1], 89.9), -89.9))) for p in points]
    x0, x1 = min(p[0] for p in flat), max(p[0] for p in flat)
    y0, y1 = min(p[1] for p in flat), max(p[1] for p in flat)
    k = min(width / max(x1 - x0, least), height / max(y1 - y0, least))
    return View(lon0, k, (x0 + x1) / 2, (y0 + y1) / 2)


def _extent(points: Sequence[Point]) -> tuple[float, float, float, float]:
    return min(p[0] for p in points), max(p[0] for p in points), min(p[1] for p in points), max(p[1] for p in points)


def _zoom_values(
    width: float, height: float, *, tall: Point = (880, 860), wide: Point = (1500, 800), square: Point = (940, 640), most: float = 2.0
) -> tuple[float, float, float]:
    """How far each canvas shape enlarges a subject this size. Boxes are in canvas pixels."""
    width, height = max(width, 1.0), max(height, 1.0)
    fit = (min(tall[0] / width, tall[1] / height), min(wide[0] / 0.9 / width, wide[1] / 0.9 / height), min(square[0] / 0.86 / width, square[1] / 0.86 / height))
    return (min(fit[0], most), min(fit[1], most), min(fit[2], most))


def _zoom_css(zooms: tuple[float, float, float]) -> str:
    return f"--zt:{zooms[0]:.3f};--zw:{zooms[1]:.3f};--zs:{zooms[2]:.3f}"


def _zooms(width: float, height: float, **boxes: Point) -> str:
    return _zoom_css(_zoom_values(width, height, **boxes))


def _tag_box(x: float, y: float, text: str, side: str, under: bool, zoom: float) -> tuple[float, float, float, float]:
    """Where a tag lands in stage units, from a close guess at its width."""
    width, height = (len(text) * 22 + 44) / zoom, 66 / zoom
    left = x - 44 if side == "l" else x + 44 - width
    top = y + 30 if under else y - 30 - height
    return left, top, left + width, top + height


def _tag_spots(points: Sequence[Point], texts: Sequence[str], zoom: float, reach: float) -> list[tuple[str, bool]]:
    """Each tag opens towards the middle of the frame, under its dot. A tag that would hit one already placed moves above its dot, then to the other side.
    A spot past `reach`, in stage units from the middle, is never used: a label that overlaps can still be read, one cut by the frame cannot."""
    placed: list[tuple[float, float, float, float]] = []
    spots: list[tuple[str, bool]] = []
    for (x, y), text in zip(points, texts, strict=True):
        home = "l" if x <= 0 else "r"
        other = "r" if home == "l" else "l"
        tries = [(home, True), (home, False), (other, True), (other, False)]
        pick = tries[0]
        for side, under in tries:
            box = _tag_box(x, y, text, side, under, zoom)
            inside = box[0] > -reach and box[2] < reach
            if inside and not any(box[0] < b[2] and box[2] > b[0] and box[1] < b[3] and box[3] > b[1] for b in placed):
                pick = (side, under)
                break
        placed.append(_tag_box(x, y, text, pick[0], pick[1], zoom))
        spots.append(pick)
    return spots


def _numbers(values: Iterable[int]) -> str:
    out = ""
    for value in values:
        out += f"{'' if value < 0 or not out else ' '}{value}"
    return out


def _d(rings: Iterable[Sequence[Point]]) -> str:
    """Closed shapes as whole stage units, relative after the first point. Shapes that round away are dropped."""
    parts = []
    for ring in rings:
        points = [(round(x), round(y)) for x, y in ring]
        kept = [points[0]]
        for point in points[1:]:
            if point != kept[-1]:
                kept.append(point)
        if len(kept) < 3:
            continue
        steps: list[int] = []
        for (x0, y0), (x1, y1) in zip(kept, kept[1:], strict=False):
            steps += [x1 - x0, y1 - y0]
        parts.append(f"M{_numbers(kept[0])}l{_numbers(steps)}z")
    return "".join(parts)


def _line(points: Sequence[Point]) -> str:
    return "M" + "L".join(f"{x:.1f} {y:.1f}" for x, y in points)


def _on_stage(ring: Sequence[Point], edge: float = EDGE) -> bool:
    x0, x1, y0, y1 = _extent(ring)
    return x1 > -edge and x0 < edge and y1 > -edge and y0 < edge


def _svg(body: str) -> str:
    return f'<svg class="geo" viewBox="-1200 -1200 2400 2400" aria-hidden="true">{body}</svg>'


def _world(view: View, *, leave: Iterable[str] = ()) -> str:
    """Every country in one path, so land and borders are a single cheap shape. Nothing is left out unless a format redraws it on top."""
    skip = set(leave)
    rings = [view.ring(ring) for name in _names() if name not in skip for ring in _country_rings(name)]
    south = view.at(view.lon, -74.0)[1]
    return _svg(f'<path class="land" d="{_d(ring for ring in rings if _on_stage(ring))}"/>') + f'<div class="south" style="top:{south:.0f}px"></div>'


def _ramp_share(value: float, low: float, high: float) -> float:
    return 0.5 if high <= low else (value - low) / (high - low)


def _short(value: Any) -> str:
    number = float(value)
    return f"{number:,.0f}" if abs(number) >= 100 or number == int(number) else f"{number:,.1f}"


def _title(heading: Any, sub: Any = None) -> str:
    head = f"<h1>{kit.emphasis(str(heading))}</h1>" if heading else ""
    line = f"<p>{kit.esc(sub)}</p>" if sub else ""
    return f'<div class="ttl">{head}{line}</div>' if head or line else ""


def _scene(inner: str, zooms: str, *, title: str = "", extra: str = "", start: float = 0.62, push: float = 1.15) -> str:
    """Background, a camera that pushes in and then keeps drifting, and whatever sits above the map."""
    return (
        f'<div class="bg"></div><div class="view" style="{zooms}"><div class="anchor"><div class="cam">'
        f'<div class="push" style="--from:{start:.3f};--pn:{push:.2f}s"><div class="drift">{inner}</div></div></div></div></div>'
        f'<div class="scrim"></div>{title}{extra}'
    )


def _node(x: float, y: float, text: str, delay: float, *, cls: str = "", rings: int = 2, spot: tuple[str, bool] | None = None) -> str:
    """A place: a dot, rings that fire once, and a tag. Left alone the tag opens towards the middle of the frame, so it never reaches the edge."""
    side, under = spot or ("l" if x <= 0 else "r", True)
    tag = f'<span class="tag {side}{"" if under else " up"}"><span>{text}</span></span>' if text else ""
    pulses = "".join(f'<b class="ring" style="animation-delay:calc(var(--t) + {index * 0.2:.2f}s)"></b>' for index in range(rings))
    return f'<div class="node {cls}" style="left:{x:.1f}px;top:{y:.1f}px;--t:{delay:.3f}s">{pulses}<b class="dot"></b>{tag}</div>'


def _scoped(fmt: str, css: str) -> str:
    return (BASE + css).replace("$", f".f-{fmt}")


BASE = """
$ {container-type:size;overflow:hidden}
$ .bg{position:absolute;inset:0;background:radial-gradient(130% 90% at 50% 48%,color-mix(in srgb,var(--surface) 62%,var(--panel)),var(--panel) 74%);animation:map-in .3s ease-out both}
$ .view{position:absolute;inset:0;--z:var(--zt);--ox:-28px;--oy:var(--oyt,60px);--land:color-mix(in srgb,var(--surface) 80%,var(--ink) 20%);--seam:color-mix(in srgb,var(--panel) 62%,var(--soft));animation:map-in .3s ease-out both}
@container (min-aspect-ratio:5/4){$ .view{--z:var(--zw);--ox:var(--oxw,0px);--oy:var(--oyw,40px)}}
@container (min-aspect-ratio:4/5) and (max-aspect-ratio:5/4){$ .view{--z:var(--zs);--ox:0px;--oy:var(--oys,100px)}}
$ .anchor{position:absolute;left:calc(50% + var(--ox));top:calc(50% + var(--oy));width:0;height:0}
$ .cam,$ .push,$ .drift{position:absolute;left:0;top:0;width:0;height:0}
$ .cam{transform:scale(calc(var(--scale) * var(--z)))}
$ .push{animation:map-push var(--pn) cubic-bezier(.2,.75,.12,1) both}
$ .drift{animation:map-drift var(--dur) linear both}
$ .geo{position:absolute;left:-1200px;top:-1200px;width:2400px;height:2400px;overflow:visible}
$ .land{fill:var(--land);stroke:var(--seam);stroke-width:calc(1.5px / var(--z));stroke-linejoin:round}
$ .south{position:absolute;left:-7000px;width:14000px;height:9000px;background:linear-gradient(to bottom,transparent,var(--panel) 150px)}
$ .scrim{position:absolute;left:0;right:0;top:0;height:calc(var(--top) + 300px * var(--scale));background:linear-gradient(to bottom,color-mix(in srgb,var(--panel) 88%,transparent) 42%,transparent);animation:map-in .3s ease-out both}
$ .ttl{position:absolute;left:var(--gutter);right:calc(var(--gutter) + var(--rail,0px));top:var(--top);animation:rise .6s .16s var(--ease) both}
$ .ttl h1{margin:0;font-size:calc(92px * var(--scale));font-weight:600;line-height:1;letter-spacing:-.04em;text-wrap:balance}
$ .ttl p{margin:calc(16px * var(--scale)) 0 0;font-size:calc(40px * var(--scale));line-height:1.18;letter-spacing:-.015em;color:var(--soft);text-wrap:balance}
$ .node{position:absolute;width:0;height:0}
$ .node b{position:absolute;display:block;border-radius:50%}
$ .dot{left:-13px;top:-13px;width:26px;height:26px;background:var(--ink);box-shadow:0 0 0 7px color-mix(in srgb,var(--accent) 34%,transparent),0 0 34px var(--accent);animation:map-dot .42s cubic-bezier(.2,1.6,.4,1) var(--t) both}
$ .ring{left:-58px;top:-58px;width:116px;height:116px;border:3px solid var(--accent);opacity:0;animation:map-ring 1.05s cubic-bezier(.16,.7,.3,1) both}
$ .tag{position:absolute;top:30px;display:block;white-space:nowrap;transform:scale(calc(1 / var(--z)))}
$ .tag.l{left:-44px;transform-origin:44px -30px}
$ .tag.r{right:-44px;transform-origin:calc(100% - 44px) -30px}
$ .tag.up{top:auto;bottom:30px}
$ .tag.l.up{transform-origin:44px calc(100% + 30px)}
$ .tag.r.up{transform-origin:calc(100% - 44px) calc(100% + 30px)}
$ .tag>span{display:block;padding:9px 20px 11px;border-radius:14px;font-size:40px;font-weight:600;letter-spacing:-.02em;color:var(--ink);background:color-mix(in srgb,var(--panel) 84%,transparent);box-shadow:0 0 0 1px rgba(255,255,255,.12) inset,0 10px 30px rgba(0,0,0,.4);animation:map-tag .46s var(--ease) calc(var(--t) + .06s) both}
@keyframes map-in{from{opacity:0}to{opacity:1}}
@keyframes map-push{from{transform:scale(var(--from))}to{transform:none}}
@keyframes map-drift{to{transform:scale(1.05)}}
@keyframes map-dot{from{opacity:0;transform:scale(0)}to{opacity:1;transform:none}}
@keyframes map-ring{0%{opacity:.9;transform:scale(.2)}100%{opacity:0;transform:scale(1.5)}}
@keyframes map-tag{from{opacity:0;transform:translateY(-14px)}to{opacity:1;transform:none}}
"""


# Flight route


def _great_circle(a: Point, b: Point, steps: int = 56) -> Ring:
    """The short way round the Earth, as longitude and latitude. Longitude keeps counting past the date line so the line stays in one piece."""
    lon1, lat1, lon2, lat2 = map(math.radians, (a[0], a[1], b[0], b[1]))
    p1 = (math.cos(lat1) * math.cos(lon1), math.cos(lat1) * math.sin(lon1), math.sin(lat1))
    p2 = (math.cos(lat2) * math.cos(lon2), math.cos(lat2) * math.sin(lon2), math.sin(lat2))
    angle = math.acos(max(min(sum(x * y for x, y in zip(p1, p2, strict=True)), 1.0), -1.0))
    if angle < 1e-6:
        return [a, b]
    out: Ring = []
    for index in range(steps + 1):
        t = index / steps
        s1, s2 = math.sin((1 - t) * angle) / math.sin(angle), math.sin(t * angle) / math.sin(angle)
        x, y, z = (s1 * p1[n] + s2 * p2[n] for n in range(3))
        lon, lat = math.degrees(math.atan2(y, x)), math.degrees(math.atan2(z, math.hypot(x, y)))
        if out:
            lon += round((out[-1][0] - lon) / 360) * 360
        out.append((lon, lat))
    return out


def _lifted(points: Sequence[Point], share: float) -> Ring:
    """A flat line bowed upward, as a flight is drawn on a map. The bow is a share of the line's length."""
    (x0, y0), (x1, y1) = points[0], points[-1]
    length = math.hypot(x1 - x0, y1 - y0) or 1.0
    nx, ny = (y1 - y0) / length, (x0 - x1) / length
    if ny > 0:
        nx, ny = -nx, -ny
    count = len(points) - 1
    return [(x + nx * length * share * math.sin(math.pi * i / count), y + ny * length * share * math.sin(math.pi * i / count)) for i, (x, y) in enumerate(points)]


def _flight_route(item: dict[str, Any]) -> str:
    stops = kit.rows(item, "stops")
    places = [(_number(row, "lon", "flight_route", "stops"), _number(row, "lat", "flight_route", "stops")) for row in stops]
    if len(places) < 2:
        raise AltocueError('flight_route needs at least two rows in "stops".', fix="See `altocue library show flight_route`.")
    legs = [_great_circle(a, b) for a, b in zip(places, places[1:], strict=False)]
    view = _fit([p for leg in legs for p in leg], width=860, height=620, least=0.2, lon=_mean_lon(p[0] for p in places))
    flat = [[view.at(*p) for p in leg] for leg in legs]
    arcs = [_lifted(leg, 0.17) for leg in flat]
    lengths = [sum(math.dist(a, b) for a, b in zip(arc, arc[1:], strict=False)) for arc in arcs]
    total = kit.seconds(item)
    begin, end = min(0.62, total * 0.16), total * 0.76
    x0, x1, y0, y1 = _extent([p for arc in arcs for p in arc])
    fit = _zoom_values(x1 - x0 + 140, y1 - y0 + 170)
    ends = [flat[0][0], *(leg[-1] for leg in flat)]
    spots = _tag_spots(ends, [str(row["text"]) for row in stops], min(fit), 470 / fit[0])
    clock, nodes, lines, heads = begin, [], [], []
    x, y = flat[0][0]
    nodes.append(_node(x, y, kit.esc(stops[0]["text"]), max(begin - 0.28, 0.08), rings=1, spot=spots[0]))
    for index, (arc, shadow) in enumerate(zip(arcs, flat, strict=True)):
        share = (end - begin) * lengths[index] / sum(lengths)
        path = _line(arc)
        lines.append(
            f'<div class="leg" style="--t:{clock:.3f}s;--n:{share:.3f}s"><svg class="geo" viewBox="-1200 -1200 2400 2400" aria-hidden="true">'
            f'<path class="fall" pathLength="1" d="{_line(shadow)}"/><path class="wire" pathLength="1" d="{path}"/></svg></div>'
            f'<div class="tail" style="--t:{clock:.3f}s;--n:{share:.3f}s"><svg class="geo" viewBox="-1200 -1200 2400 2400" aria-hidden="true"><path class="hot" pathLength="1" d="{path}"/></svg></div>'
        )
        heads.append(f'<div class="head" style="offset-path:path(\'{path}\');--t:{clock:.3f}s;--n:{share:.3f}s"><i></i></div>')
        clock += share
        x, y = shadow[-1]
        nodes.append(_node(x, y, kit.esc(stops[index + 1]["text"]), clock - 0.04, spot=spots[index + 1]))
    middle = (x0 + x1) / 2, (y0 + y1) / 2
    body = f'<div class="shift" style="transform:translate({-middle[0]:.1f}px,{-middle[1]:.1f}px)">{_world(view)}{"".join(lines)}{"".join(nodes)}{"".join(heads)}</div>'
    return _scene(body, _zoom_css(fit), title=_title(item.get("text")), start=0.66)


FLIGHT_CSS = """
$ .shift{position:absolute;left:0;top:0;width:0;height:0}
$ .leg{position:absolute;left:0;top:0;animation:map-draw var(--n) cubic-bezier(.45,.02,.2,1) var(--t) both}
$ .tail{position:absolute;left:0;top:0;animation:map-tail var(--n) cubic-bezier(.45,.02,.2,1) var(--t) both}
$ .fall{fill:none;stroke:#000;stroke-opacity:.42;stroke-width:calc(5px / var(--z));stroke-dasharray:1 1;stroke-linecap:round}
$ .wire{fill:none;stroke:var(--accent);stroke-width:calc(6px / var(--z));stroke-dasharray:1 1;stroke-linecap:round;filter:drop-shadow(0 0 10px color-mix(in srgb,var(--accent) 70%,transparent))}
$ .hot{fill:none;stroke:var(--ink);stroke-width:calc(7px / var(--z));stroke-dasharray:.09 2;stroke-linecap:round}
$ .head{position:absolute;left:0;top:0;width:0;height:0;offset-rotate:auto;animation:map-fly var(--n) cubic-bezier(.45,.02,.2,1) var(--t) both,map-show .12s linear var(--t) both,map-gone .16s linear calc(var(--t) + var(--n) - .1s) forwards}
$ .head i{position:absolute;left:-22px;top:-16px;width:44px;height:32px;background:var(--ink);clip-path:polygon(0 0,100% 50%,0 100%,16% 50%);transform:scale(calc(1 / var(--z)))}
@keyframes map-draw{from{stroke-dashoffset:1}to{stroke-dashoffset:0}}
@keyframes map-tail{from{stroke-dashoffset:.09}to{stroke-dashoffset:-1}}
@keyframes map-fly{from{offset-distance:0%}to{offset-distance:100%}}
@keyframes map-show{from{opacity:0}to{opacity:1}}
@keyframes map-gone{to{opacity:0}}
"""


# Country lift


def _country_lift(item: dict[str, Any]) -> str:
    name = _known(item["country"], "country_lift")
    body_rings = _main_rings(name)
    middle_lon = _centre(name)[0]
    view = _fit([p for ring in body_rings for p in ring], width=800, height=700, least=0.34, lon=middle_lon)
    shape = [view.ring(ring) for ring in _country_rings(name)]
    x0, x1, y0, y1 = _extent([p for ring in body_rings for p in view.ring(ring)])
    path = _d(shape)
    layers = "".join(
        f'<div class="ly{" top" if index == LAYERS - 1 else ""}" style="--i:{index}"><svg class="geo" viewBox="-1200 -1200 2400 2400" aria-hidden="true"><path d="{path}"/></svg></div>'
        for index in range(LAYERS)
    )
    plane = (
        f'<div class="plane"><div class="dim">{_world(view, leave=[name])}</div>'
        f'<div class="pit"><svg class="geo" viewBox="-1200 -1200 2400 2400" aria-hidden="true"><path d="{path}"/></svg></div>{layers}</div>'
    )
    return _scene(
        f'<div class="room">{plane}</div>',
        _zooms(x1 - x0, (y1 - y0) * 0.9 + 80, wide=(1300, 700), square=(860, 600), tall=(860, 780)),
        title=_title(item["text"], item.get("label")),
        start=0.5,
        push=1.5,
    )


LAYERS = 12

LIFT_CSS = """
$ .view{--oyt:90px;--oyw:60px;--oys:130px}
$ .room{position:absolute;left:0;top:0;width:0;height:0;perspective:2300px}
$ .plane{position:absolute;left:0;top:0;width:0;height:0;transform-style:preserve-3d;animation:map-tilt 1.5s cubic-bezier(.5,0,.12,1) .25s both}
$ .dim{position:absolute;left:0;top:0;animation:map-dim .9s ease-out .7s both}
$ .pit{position:absolute;left:0;top:0;fill:#000;filter:blur(20px);animation:map-pit 1.1s ease-out .75s both}
$ .ly{position:absolute;left:0;top:0;opacity:0;fill:color-mix(in srgb,var(--accent) 44%,var(--panel));stroke:color-mix(in srgb,var(--accent) 44%,var(--panel));stroke-width:calc(5px / var(--z));stroke-linejoin:round;animation:map-lift 1.1s cubic-bezier(.3,1.25,.4,1) .72s both,map-charge .34s ease-out .5s forwards}
$ .ly.top{fill:var(--accent);stroke:color-mix(in srgb,var(--accent) 60%,var(--ink));stroke-width:calc(2.5px / var(--z))}
@keyframes map-tilt{from{transform:none}to{transform:rotateX(50deg) rotateZ(-7deg) scale(1.16)}}
@keyframes map-charge{to{opacity:1}}
@keyframes map-dim{to{opacity:.5}}
@keyframes map-pit{from{opacity:0}to{opacity:.7}}
@keyframes map-lift{from{transform:translateZ(0)}to{transform:translateZ(calc(var(--i) * 8px / var(--z)))}}
"""


# Choropleth reveal


def _ramp(low: Any, high: Any, unit: Any, delay: float) -> str:
    label = f"<em>{kit.esc(unit)}</em>" if unit else ""
    return f'<div class="key" style="--t:{delay:.2f}s">{label}<div class="bar"><span>{kit.esc(_short(low))}</span><i></i><span>{kit.esc(_short(high))}</span></div></div>'


def _sweep_map(item: dict[str, Any]) -> str:
    rows = kit.rows(item, "regions")
    picked: dict[str, float] = {}
    for row in rows:
        picked[_known(row["text"], "region_fill")] = _number(row, "value", "region_fill", "regions")
    low, high = min(picked.values()), max(picked.values())
    view = _fit([p for name in picked for ring in _main_rings(name) for p in ring], width=860, height=680, least=0.3)
    total = kit.seconds(item)
    begin, span = min(0.62, total * 0.18), total * 0.44
    shapes = {name: [view.ring(ring) for ring in _country_rings(name)] for name in picked}
    middles = {name: _extent([p for ring in shapes[name] for p in ring]) for name in picked}
    body = [p for name in picked for ring in _main_rings(name) for p in view.ring(ring)]
    x0, x1, y0, y1 = _extent(body)
    tiles = []
    for name, value in sorted(picked.items(), key=lambda pair: middles[pair[0]][0]):
        share = _ramp_share(value, low, high)
        ax, bx, ay, by = middles[name]
        where = (max(min((ax + bx) / 2, x1), x0) - x0) / max(x1 - x0, 1.0)
        tiles.append(
            f'<div class="reg" style="--t:{begin + span * where:.3f}s;--v:{18 + share * 82:.0f}%;transform-origin:{(ax + bx) / 2:.0f}px {(ay + by) / 2:.0f}px">'
            f'<svg class="geo" viewBox="-1200 -1200 2400 2400" aria-hidden="true"><path d="{_d(shapes[name])}"/></svg></div>'
        )
    best = max(picked, key=lambda name: picked[name])
    bx, by = view.at(*_centre(best))
    tag = _node(bx, by, f"{kit.esc(best)} <em>{kit.esc(_short(picked[best]))}</em>", begin + span + 0.2, rings=1)
    scan = f'<div class="scan" style="left:{x0:.0f}px;--far:{x1 - x0:.0f}px;--t:{begin:.3f}s;--n:{span:.3f}s"></div>'
    middle = (x0 + x1) / 2, (y0 + y1) / 2
    inner = f'<div class="shift" style="transform:translate({-middle[0]:.1f}px,{-middle[1]:.1f}px)">{_world(view, leave=picked)}{"".join(tiles)}{scan}{tag}</div>'
    return _scene(
        inner,
        _zooms(x1 - x0 + 60, y1 - y0 + 60, tall=(880, 780), wide=(1300, 680), square=(900, 500)),
        title=_title(item["text"]),
        extra=_ramp(low, high, item.get("unit"), begin + span * 0.6),
        start=0.82,
    )


LEGEND_CSS = """
$ .key{position:absolute;left:var(--gutter);right:calc(var(--gutter) + var(--rail,0px));bottom:calc(var(--top) * .9);max-width:calc(760px * var(--scale));padding:calc(20px * var(--scale)) calc(26px * var(--scale)) calc(22px * var(--scale));border-radius:calc(22px * var(--scale));background:color-mix(in srgb,var(--panel) 86%,transparent);box-shadow:0 0 0 1px rgba(255,255,255,.1) inset,0 20px 50px rgba(0,0,0,.36);animation:rise .55s var(--ease) var(--t) both}
$ .key em{display:block;margin-bottom:calc(12px * var(--scale));font-style:normal;font-size:calc(34px * var(--scale));letter-spacing:-.01em;color:var(--soft)}
$ .bar{display:flex;align-items:center;gap:calc(20px * var(--scale));font-size:calc(38px * var(--scale));font-weight:600;font-variant-numeric:tabular-nums;letter-spacing:-.02em}
$ .bar i{flex:1;height:calc(18px * var(--scale));border-radius:99px;background:linear-gradient(to right,color-mix(in srgb,var(--accent) 18%,var(--low)),var(--accent));box-shadow:0 0 0 1px rgba(255,255,255,.08)}
$ {--low:color-mix(in srgb,var(--surface) 70%,var(--ink) 16%)}
"""

SWEEP_CSS = (
    LEGEND_CSS
    + """
$ .view{--oyt:30px;--oys:40px;--oyw:30px}
$ .shift{position:absolute;left:0;top:0;width:0;height:0}
$ .reg{position:absolute;left:0;top:0;width:0;height:0;fill:color-mix(in srgb,var(--accent) var(--v),var(--low));stroke:var(--panel);stroke-width:calc(1.6px / var(--z));stroke-linejoin:round;animation:map-reg .62s cubic-bezier(.2,.8,.2,1) var(--t) both}
$ .scan{position:absolute;top:-1100px;height:2200px;width:3px;margin-left:-1px;background:linear-gradient(to bottom,transparent,var(--ink) 30%,var(--ink) 70%,transparent);box-shadow:0 0 44px 10px color-mix(in srgb,var(--accent) 62%,transparent);animation:map-scan var(--n) linear var(--t) both,map-gone .3s linear calc(var(--t) + var(--n)) forwards}
$ .tag em{color:var(--accent);font-style:normal}
@keyframes map-reg{from{opacity:0;filter:brightness(2.1);transform:scale(1.05)}to{opacity:1;filter:none;transform:none}}
@keyframes map-scan{from{transform:translateX(0)}to{transform:translateX(var(--far))}}
@keyframes map-gone{to{opacity:0}}
"""
)


# Bubble map


def _bubble_map(item: dict[str, Any]) -> str:
    rows = kit.rows(item, "places")
    values = [abs(_number(row, "value", "bubble_map", "places")) for row in rows]
    places = [(_number(row, "lon", "bubble_map", "places"), _number(row, "lat", "bubble_map", "places")) for row in rows]
    view = _fit(places, width=800, height=560, least=0.5)
    points = [view.at(*p) for p in places]
    top = max(values) or 1.0
    order = sorted(range(len(rows)), key=lambda n: -values[n])
    total = kit.seconds(item)
    anchored = any(row.get("at") is not None for row in rows)
    bubbles, keys = [], []
    for rank, index in enumerate(order):
        x, y = points[index]
        size = 2 * (24 + 92 * math.sqrt(values[index] / top))
        delay = rows[index]["delay"] if anchored else 0.55 + (total * 0.42) * rank / max(len(rows), 1)
        label = f"<em>{rank + 1}</em>" if rank < 5 else ""
        bubbles.append(f'<div class="bub" style="left:{x:.1f}px;top:{y:.1f}px;--s:{size:.0f}px;--t:{delay:.3f}s;z-index:{len(rows) - rank}"><b></b>{label}</div>')
        if rank < 5:
            shown = rows[index].get("show") or _short(values[index])
            keys.append(f'<li style="--t:{delay + 0.08:.3f}s"><i>{rank + 1}</i><span>{kit.esc(rows[index]["text"])}</span><b>{kit.esc(shown)}</b></li>')
    x0, x1, y0, y1 = _extent(points)
    middle = (x0 + x1) / 2, (y0 + y1) / 2
    inner = f'<div class="shift" style="transform:translate({-middle[0]:.1f}px,{-middle[1]:.1f}px)">{_world(view)}{"".join(bubbles)}</div>'
    return _scene(
        inner,
        _zooms(x1 - x0 + 170, y1 - y0 + 170, tall=(900, 620), wide=(1080, 760), square=(880, 430)),
        title=_title(item["text"]),
        extra=f'<ol class="rank">{"".join(keys)}</ol>',
        start=0.8,
    )


BUBBLE_CSS = """
$ .view{--oyt:-90px;--oxw:330px;--oys:-40px}
$ .shift{position:absolute;left:0;top:0;width:0;height:0}
$ .bub{position:absolute;width:0;height:0}
$ .bub{transform:scale(calc(1 / var(--z)))}
$ .bub b{position:absolute;left:calc(var(--s) / -2);top:calc(var(--s) / -2);width:var(--s);height:var(--s);border-radius:50%;background:color-mix(in srgb,var(--accent) 34%,transparent);box-shadow:0 0 0 3px var(--accent) inset,0 0 40px color-mix(in srgb,var(--accent) 34%,transparent);animation:map-bub .7s cubic-bezier(.2,1.5,.36,1) var(--t) both}
$ .bub em{position:absolute;left:-60px;width:120px;top:-.6em;text-align:center;font-style:normal;font-size:44px;line-height:1.2;font-weight:700;color:var(--ink);animation:map-in .3s ease-out calc(var(--t) + .22s) both}
$ .rank{position:absolute;left:var(--gutter);right:calc(var(--gutter) + var(--rail,0px));bottom:calc(var(--top) * .72);margin:0;padding:0;list-style:none;max-width:calc(780px * var(--scale))}
$ .rank li{display:flex;align-items:center;gap:calc(20px * var(--scale));padding:calc(11px * var(--scale)) 0;font-size:calc(40px * var(--scale));letter-spacing:-.02em;border-top:1px solid rgba(255,255,255,.11);animation:rise .46s var(--ease) var(--t) both}
$ .rank i{flex:none;width:calc(52px * var(--scale));height:calc(52px * var(--scale));display:grid;place-items:center;border-radius:50%;font-style:normal;font-size:calc(30px * var(--scale));font-weight:700;color:var(--panel);background:var(--accent)}
$ .rank span{flex:1;min-width:0;white-space:nowrap;overflow:hidden;text-overflow:ellipsis}
$ .rank b{font-weight:600;font-variant-numeric:tabular-nums}
@container (min-aspect-ratio:5/4){$ .rank{right:auto;width:calc(640px * var(--scale));bottom:auto;top:calc(var(--top) + 250px * var(--scale))}}
@container (min-aspect-ratio:4/5) and (max-aspect-ratio:5/4){$ .rank{bottom:calc(var(--gutter) * .7);max-width:none}$ .rank li{padding:calc(6px * var(--scale)) 0;font-size:calc(34px * var(--scale))}$ .rank li:nth-child(n+4){display:none}}
@keyframes map-bub{from{opacity:0;transform:scale(0)}to{opacity:1;transform:none}}
"""


# Hex map


HEX_NEAR = ((1, 0), (-1, 0), (0, 1), (0, -1), (1, -1), (-1, 1))


def _hex_cells(flat: dict[str, Point]) -> dict[str, tuple[int, int]]:
    """Give each place one tile near where it really is. Places claim tiles from the middle outward, in a fixed order, so the same data gives the same map."""
    count = len(flat)
    x0, x1, y0, y1 = _extent(list(flat.values()))
    across = max(x1 - x0, 1.0)
    pitch = max(across, y1 - y0, 1.0) / max(math.sqrt(count) * 1.5, 1.0)
    taken: dict[tuple[int, int], str] = {}
    middle = (x0 + x1) / 2, (y0 + y1) / 2
    for name in sorted(flat, key=lambda n: (math.dist(flat[n], middle), n)):
        x, y = flat[name]
        q = (x - middle[0]) / pitch - (y - middle[1]) / pitch / math.sqrt(3)
        r = (y - middle[1]) / pitch * 2 / math.sqrt(3)
        home = (round(q), round(r))
        tried, reach = home, 0
        while tried in taken:
            reach += 1
            ring = [
                (home[0] + dq * reach + dr * step, home[1] + dq2 * reach + ds * step)
                for (dq, dq2), (dr, ds) in zip(HEX_NEAR, HEX_NEAR[2:] + HEX_NEAR[:2], strict=True)
                for step in range(reach + 1)
            ]
            free = [cell for cell in ring if cell not in taken]
            if free:
                tried = min(free, key=lambda c: (math.dist((c[0] + c[1] / 2, c[1] * math.sqrt(3) / 2), (q + r / 2, r * math.sqrt(3) / 2)), c))
        taken[tried] = name
    return _swapped(_closed_up(taken), flat)


def _swapped(cells: dict[tuple[int, int], str], flat: dict[str, Point]) -> dict[str, tuple[int, int]]:
    """Trade tiles between pairs while that puts both nearer their real place, so north stays north once the gaps have closed."""

    def spot(cell: tuple[int, int]) -> Point:
        return cell[0] + cell[1] / 2, cell[1] * math.sqrt(3) / 2

    sites = {cell: spot(cell) for cell in cells}
    ax0, ax1, ay0, ay1 = _extent(list(sites.values()))
    bx0, bx1, by0, by1 = _extent(list(flat.values()))
    ideal = {name: (ax0 + (x - bx0) / max(bx1 - bx0, 1.0) * (ax1 - ax0), ay0 + (y - by0) / max(by1 - by0, 1.0) * (ay1 - ay0)) for name, (x, y) in flat.items()}

    def cost(name: str, cell: tuple[int, int]) -> float:
        return math.dist(ideal[name], sites[cell]) ** 2

    order = sorted(cells)
    for _ in range(10):
        moved = False
        for first in order:
            for second in order:
                a, b = cells[first], cells[second]
                if first < second and cost(a, second) + cost(b, first) < cost(a, first) + cost(b, second) - 1e-6:
                    cells[first], cells[second] = b, a
                    moved = True
        if not moved:
            break
    return {name: cell for cell, name in cells.items()}


def _closed_up(taken: dict[tuple[int, int], str]) -> dict[tuple[int, int], str]:
    """Slide tiles inward through free neighbours until the gaps close, keeping each one on its own side of the map."""

    def spot(cell: tuple[int, int]) -> Point:
        return cell[0] + cell[1] / 2, cell[1] * math.sqrt(3) / 2

    middle = (sum(spot(c)[0] for c in taken) / len(taken), sum(spot(c)[1] for c in taken) / len(taken))
    cells = dict(taken)
    for _ in range(12):
        moved = False
        for cell in sorted(cells, key=lambda c: (math.dist(spot(c), middle), c)):
            options = [(cell[0] + dq, cell[1] + dr) for dq, dr in HEX_NEAR]
            free = [c for c in options if c not in cells and math.dist(spot(c), middle) < math.dist(spot(cell), middle) - 0.05]
            if free:
                best = min(free, key=lambda c: (math.dist(spot(c), middle), c))
                cells[best] = cells.pop(cell)
                moved = True
        if not moved:
            break
    return cells


def _hex_map(item: dict[str, Any]) -> str:
    rows = kit.rows(item, "regions")
    data: dict[str, dict[str, Any]] = {}
    for row in rows:
        data[_known(row["text"], "hex_map")] = row
    lon0 = _mean_lon(_centre(name)[0] for name in data)
    view = View(lon0, 1000.0)
    flat = {name: view.at(*_centre(name)) for name in data}
    cells = _hex_cells(flat)
    size = 96.0
    tiles = {name: (size * math.sqrt(3) * (q + r / 2), size * 1.5 * r) for name, (q, r) in cells.items()}
    x0, x1, y0, y1 = _extent(list(tiles.values()))
    middle = (x0 + x1) / 2, (y0 + y1) / 2
    values = {name: _number(row, "value", "hex_map", "regions") for name, row in data.items()}
    low, high = min(values.values()), max(values.values())
    reach = max(math.dist(p, middle) for p in tiles.values()) or 1.0
    total = kit.seconds(item)
    out = []
    shape = '<svg viewBox="-100 -100 200 200" aria-hidden="true"><polygon points="0,-90 78,-45 78,45 0,90 -78,45 -78,-45"/></svg>'
    for name, (x, y) in tiles.items():
        share = _ramp_share(values[name], low, high)
        fill = 18 + share * 82
        wave = math.dist((x, y), middle) / reach
        short = data[name].get("short") or name[:3].upper()
        ink = "var(--panel)" if fill > 56 else "var(--ink)"
        out.append(
            f'<div class="tile" style="left:{x - middle[0]:.1f}px;top:{y - middle[1]:.1f}px;--t:{0.3 + wave * 0.62:.3f}s;--c:{total * 0.42 + wave * 0.5:.3f}s;--v:{fill:.0f}%;--tx:{ink}">'
            f'<div class="base">{shape}</div><div class="fill">{shape}</div><b>{kit.esc(str(short)[:3])}</b><i>{kit.esc(data[name].get("show") or _short(values[name]))}</i></div>'
        )
    return _scene(
        "".join(out),
        _zooms(x1 - x0 + 2 * size, y1 - y0 + 2 * size, tall=(880, 760), wide=(1400, 640), square=(900, 500), most=1.5),
        title=_title(item["text"]),
        extra=_ramp(low, high, item.get("unit"), total * 0.46),
        start=1.0,
        push=0.01,
    )


HEX_CSS = (
    LEGEND_CSS
    + """
$ .view{--oyt:30px;--oys:30px;--oyw:40px}
$ .tile{position:absolute;width:0;height:0;animation:map-pop .52s cubic-bezier(.2,1.5,.36,1) var(--t) both}
$ .base,$ .fill{position:absolute;left:-100px;top:-100px;width:200px;height:200px}
$ .tile svg{display:block;width:100%;height:100%}
$ .tile polygon{stroke-width:16;stroke-linejoin:round}
$ .base polygon{fill:var(--low);stroke:var(--low)}
$ .fill{animation:map-in .5s ease-out var(--c) both}
$ .fill polygon{fill:color-mix(in srgb,var(--accent) var(--v),var(--low));stroke:color-mix(in srgb,var(--accent) var(--v),var(--low))}
$ .tile b,$ .tile i{position:absolute;left:-90px;width:180px;text-align:center;font-style:normal;color:var(--ink)}
$ .tile b{top:-42px;font-size:46px;font-weight:700;letter-spacing:-.01em;line-height:1.1;animation:map-tint .4s linear var(--c) both}
$ .tile i{top:8px;font-size:36px;font-weight:500;font-variant-numeric:tabular-nums;line-height:1.1;animation:map-in .4s ease-out var(--c) both,map-tint .4s linear var(--c) both}
@keyframes map-pop{from{opacity:0;transform:scale(.2)}to{opacity:1;transform:none}}
@keyframes map-tint{to{color:var(--tx)}}
"""
)


# Flow map


def _flow_map(item: dict[str, Any]) -> str:
    rows = kit.rows(item, "flows")
    hub = (float(item["lon"]), float(item["lat"]))
    places = [(_number(row, "lon", "flow_map", "flows"), _number(row, "lat", "flow_map", "flows")) for row in rows]
    values = [abs(_number({"value": row.get("value", 1)}, "value", "flow_map", "flows")) for row in rows]
    view = _fit([hub, *places], width=840, height=640, least=0.4)
    centre = view.at(*hub)
    points = [view.at(*p) for p in places]
    top = max(values) or 1.0
    total = kit.seconds(item)
    outward = item.get("direction") == "out"
    anchored = any(row.get("at") is not None for row in rows)
    x0, x1, y0, y1 = _extent([centre, *points])
    fit = _zoom_values(x1 - x0 + 200, y1 - y0 + 300, tall=(900, 860), wide=(1500, 760), square=(920, 600))
    shown = [rows[index].get("show") or (_short(rows[index]["value"]) if "value" in rows[index] else "") for index in range(len(rows))]
    spots = _tag_spots(
        [centre, *points], [str(item["place"]), *(f"{row['text']} {extra}".strip() for row, extra in zip(rows, shown, strict=True))], min(fit), 470 / fit[0]
    )
    strands, nodes = [], []
    for index, point in enumerate(points):
        a, b = (centre, point) if outward else (point, centre)
        arc = _lifted([a, *((a[0] + (b[0] - a[0]) * i / 40, a[1] + (b[1] - a[1]) * i / 40) for i in range(1, 40)), b], 0.15)
        width = 7 + 15 * math.sqrt(values[index] / top)
        delay = rows[index]["delay"] if anchored else 0.5 + total * 0.26 * index / max(len(points), 1)
        path = _line(arc)
        strands.append(
            f'<div class="draw" style="--t:{delay:.3f}s;--w:{width:.1f}"><svg class="geo" viewBox="-1200 -1200 2400 2400" aria-hidden="true"><path class="pipe" pathLength="1" d="{path}"/></svg></div>'
            f'<div class="march" style="--t:{delay + 0.5:.3f}s;--w:{width:.1f}"><svg class="geo" viewBox="-1200 -1200 2400 2400" aria-hidden="true"><path class="beads" pathLength="1" d="{path}"/></svg></div>'
        )
        label = f"{kit.esc(rows[index]['text'])}{f' <em>{kit.esc(shown[index])}</em>' if shown[index] else ''}"
        nodes.append(_node(point[0], point[1], label, delay, cls="from", rings=1, spot=spots[index + 1]))
    hub_node = _node(centre[0], centre[1], kit.emphasis(str(item["place"])), 0.3 + total * 0.3 * (0 if outward else 1), cls="hub", rings=2, spot=spots[0])
    middle = (x0 + x1) / 2, (y0 + y1) / 2
    inner = f'<div class="shift" style="transform:translate({-middle[0]:.1f}px,{-middle[1]:.1f}px)">{_world(view)}{"".join(strands)}{"".join(nodes)}{hub_node}</div>'
    return _scene(inner, _zoom_css(fit), title=_title(item.get("text")), start=0.78)


FLOW_CSS = """
$ .view{--oyt:30px;--oys:60px;--oyw:50px}
$ .shift{position:absolute;left:0;top:0;width:0;height:0}
$ .draw{position:absolute;left:0;top:0;animation:map-draw 1.15s cubic-bezier(.5,0,.15,1) var(--t) both}
$ .march{position:absolute;left:0;top:0;animation:map-in .3s linear var(--t) both,map-march var(--dur) linear var(--t) both}
$ .pipe{fill:none;stroke:color-mix(in srgb,var(--accent) 42%,var(--surface));stroke-width:calc(var(--w) * 1px);stroke-dasharray:1 1;stroke-linecap:round}
$ .beads{fill:none;stroke:var(--accent);stroke-width:calc(var(--w) * .46px);stroke-dasharray:.018 .062;stroke-linecap:round}
$ .hub .dot{left:-21px;top:-21px;width:42px;height:42px;background:var(--accent);box-shadow:0 0 0 12px color-mix(in srgb,var(--accent) 26%,transparent),0 0 60px var(--accent)}
$ .tag em{font-style:normal;color:var(--accent)}
@keyframes map-draw{from{stroke-dashoffset:1}to{stroke-dashoffset:0}}
@keyframes map-march{from{stroke-dashoffset:0}to{stroke-dashoffset:-1.2}}
"""


# Globe


TURN_STEPS = 13
TURN_DEGREES = 118.0


def _globe_rings() -> list[Sequence[Point]]:
    """Country rings, not the continents: each is under a hemisphere wide, so it can never wrap the far side of the Earth and invert the fill."""
    return [ring for name in _names() for ring in _country_rings(name) if _area(ring) > 0.25]


def _ortho(lon: float, lat: float, lon0: float, lat0: float) -> tuple[float, float, float]:
    lam, phi, tilt = math.radians(lon - lon0), math.radians(lat), math.radians(lat0)
    x = math.cos(phi) * math.sin(lam)
    y = math.cos(tilt) * math.sin(phi) - math.sin(tilt) * math.cos(phi) * math.cos(lam)
    return x, y, math.sin(tilt) * math.sin(phi) + math.cos(tilt) * math.cos(phi) * math.cos(lam)


def _turn_ease(share: float) -> float:
    return 0.3 * share * share * (3 - 2 * share) + 0.7 * (1 - (1 - share) ** 3)


def _globe_path(rings: Sequence[Sequence[Point]], lon0: float, lat0: float) -> str:
    """Every point stays in every frame, so CSS can tween between frames. A point round the back rests on the rim, and a ring fully round the back shrinks to one point."""
    parts = []
    for ring in rings:
        flat = [_ortho(lon, lat, lon0, lat0) for lon, lat in ring]
        if all(near < 0 for _, _, near in flat):
            mx, my = sum(p[0] for p in flat), sum(p[1] for p in flat)
            length = math.hypot(mx, my) or 1.0
            home = (round(500 + 500 * mx / length), round(500 - 500 * my / length))
            parts.append(f"M{_numbers(home)}l{_numbers([0] * (2 * (len(ring) - 1)))}z")
            continue
        points = []
        for x, y, near in flat:
            if near < 0:
                length = math.hypot(x, y) or 1.0
                x, y = x / length, y / length
            points.append((round(500 + 500 * x), round(500 - 500 * y)))
        steps: list[int] = []
        for (x0, y0), (x1, y1) in zip(points, points[1:], strict=False):
            steps += [x1 - x0, y1 - y0]
        parts.append(f"M{_numbers(points[0])}l{_numbers(steps)}z")
    return "".join(parts)


def _globe_turn(item: dict[str, Any]) -> str:
    lon, lat = float(item["lon"]), float(item["lat"])
    frames = [(lon - TURN_DEGREES * (1 - _turn_ease(i / (TURN_STEPS - 1))), lat * (0.2 + 0.68 * _turn_ease(i / (TURN_STEPS - 1)))) for i in range(TURN_STEPS)]
    rings = [ring for ring in _globe_rings() if any(_ortho(p[0], p[1], a, b)[2] > 0 for a, b in frames for p in ring)]
    name = "map-t" + hashlib.sha256(f"{lon:.3f},{lat:.3f}".encode()).hexdigest()[:8]
    stops = "".join(f'{100 * i / (TURN_STEPS - 1):.2f}%{{clip-path:path("{_globe_path(rings, a, b)}")}}' for i, (a, b) in enumerate(frames))
    total = kit.seconds(item)
    turn = total * 0.62
    x, y, _ = _ortho(lon, lat, *frames[-1])
    pin = f'<div class="spot" style="left:{500 + 500 * x:.1f}px;top:{500 - 500 * y:.1f}px;--t:{turn - 0.12:.3f}s"><b class="ring"></b><b class="ring two"></b><div class="fall"><b class="dot"></b></div></div>'
    orb = (
        f'<style>@keyframes {name}{{{stops}}}</style><div class="orb"><div class="air"></div><div class="sea"></div>'
        f'<div class="lnd" style="animation:{name} {turn:.3f}s linear both"></div><div class="limb"></div>{pin}</div>'
    )
    return _scene(
        orb, _zooms(1040, 1040, tall=(900, 900), wide=(860, 860), square=(760, 760), most=1.2), title=_title(item["text"], item.get("label")), start=0.92, push=1.6
    )


GLOBE_CSS = """
$ .view{--oyt:130px;--oxw:400px;--oyw:20px;--oys:150px}
$ .orb{position:absolute;left:-500px;top:-500px;width:1000px;height:1000px}
$ .air{position:absolute;inset:-120px;border-radius:50%;background:radial-gradient(closest-side,transparent 79%,color-mix(in srgb,var(--accent) 40%,transparent) 81%,color-mix(in srgb,var(--accent) 10%,transparent) 87%,transparent 95%)}
$ .sea{position:absolute;inset:0;border-radius:50%;background:radial-gradient(circle at 37% 30%,color-mix(in srgb,var(--surface) 64%,var(--accent) 22%),var(--surface) 44%,var(--panel) 96%)}
$ .lnd{position:absolute;inset:0;background:radial-gradient(circle at 37% 30%,color-mix(in srgb,var(--accent) 62%,var(--ink)),color-mix(in srgb,var(--accent) 40%,var(--surface)) 52%,color-mix(in srgb,var(--accent) 12%,var(--surface)) 96%)}
$ .limb{position:absolute;inset:0;border-radius:50%;box-shadow:inset 0 0 90px 10px color-mix(in srgb,var(--panel) 72%,transparent),inset 0 0 14px 2px color-mix(in srgb,var(--accent) 34%,transparent)}
$ .spot{position:absolute;width:0;height:0}
$ .spot b{position:absolute;display:block;border-radius:50%}
$ .spot .ring{left:-70px;top:-70px;width:140px;height:140px;border-color:var(--ink);animation-delay:calc(var(--t) + .26s)}
$ .spot .two{animation-delay:calc(var(--t) + .46s)}
$ .fall{position:absolute;animation:map-fall .42s cubic-bezier(.5,0,.8,.6) var(--t) both}
$ .spot .dot{left:-20px;top:-20px;width:40px;height:40px;background:var(--panel);box-shadow:0 0 0 11px var(--ink),0 0 0 24px color-mix(in srgb,var(--panel) 44%,transparent),0 0 60px var(--ink);animation:none}
@keyframes map-fall{from{opacity:0;transform:translateY(-190px)}40%{opacity:1}to{opacity:1;transform:none}}
"""


# Pin drop


def _pin_drop(item: dict[str, Any]) -> str:
    place = (float(item["lon"]), float(item["lat"]))
    view = _fit([place, (place[0] - 15, place[1] - 11), (place[0] + 15, place[1] + 11)], width=900, height=900, lon=place[0])
    x, y = view.at(*place)
    total = kit.seconds(item)
    land = min(1.2, total * 0.34)
    pin = '<svg viewBox="0 0 120 160" aria-hidden="true"><path d="M60 4C29 4 6 28 6 58c0 38 46 84 52 96a2.4 2.4 0 0 0 4 0c6-12 52-58 52-96C114 28 91 4 60 4z"/><circle cx="60" cy="58" r="21"/></svg>'
    sub = f"<small>{kit.esc(item['label'])}</small>" if item.get("label") else ""
    marker = (
        f'<div class="site" style="left:{x:.1f}px;top:{y:.1f}px;--t:{land:.3f}s"><b class="pool"></b><b class="ring"></b><b class="ring two"></b>'
        f'<div class="count"><div class="dropper"><div class="squash"><div class="pin">{pin}</div></div></div></div>'
        f'<div class="card"><div><strong>{kit.emphasis(str(item["text"]))}</strong>{sub}</div></div></div>'
    )
    inner = f'<div class="shift" style="transform:translate({-x:.1f}px,{-y:.1f}px)">{_world(view)}{marker}</div>'
    return _scene(inner, "--zt:1;--zw:1;--zs:.86", start=0.3, push=max(land + 0.25, 1.1))


PIN_CSS = """
$ .view{--oyt:70px;--oyw:70px;--oys:90px}
$ .shift{position:absolute;left:0;top:0;width:0;height:0}
$ .site{position:absolute;width:0;height:0}
$ .site>b{position:absolute;display:block;border-radius:50%}
$ .pool{left:-54px;top:-17px;width:108px;height:34px;background:#000;filter:blur(9px);opacity:0;animation:map-pool .5s cubic-bezier(.6,0,.9,.5) calc(var(--t) - .44s) both}
$ .site .ring{left:-90px;top:-34px;width:180px;height:68px;border:3px solid var(--accent);border-radius:50%;animation:map-spread 1s cubic-bezier(.16,.7,.3,1) var(--t) both}
$ .site .two{animation-delay:calc(var(--t) + .22s)}
$ .count{position:absolute;transform:scale(calc(1 / var(--z)))}
$ .dropper{position:absolute;animation:map-drop .46s cubic-bezier(.55,0,.9,.55) calc(var(--t) - .46s) both}
$ .squash{position:absolute;transform-origin:0 0;animation:map-squash .62s cubic-bezier(.2,.9,.3,1) var(--t) both}
$ .pin{position:absolute;left:-66px;top:-172px;width:132px;height:176px;filter:drop-shadow(0 14px 22px rgba(0,0,0,.5))}
$ .pin svg{display:block;width:100%;height:100%}
$ .pin path{fill:var(--accent)}
$ .pin circle{fill:var(--panel)}
$ .card{position:absolute;left:0;top:calc(-226px / var(--z));width:0;height:0;transform:scale(calc(1 / var(--z)))}
$ .card>div{position:absolute;left:calc(-390px);bottom:0;width:780px;padding:26px 38px 30px;border-radius:28px;background:color-mix(in srgb,var(--panel) 90%,transparent);box-shadow:0 0 0 1px rgba(255,255,255,.13) inset,0 28px 70px rgba(0,0,0,.5);text-align:center;transform-origin:50% 100%;animation:map-card .6s cubic-bezier(.2,1.35,.36,1) calc(var(--t) + .2s) both}
$ .card strong{display:block;font-size:66px;font-weight:600;line-height:1.06;letter-spacing:-.035em;text-wrap:balance}
$ .card small{display:block;margin-top:10px;font-size:38px;line-height:1.16;letter-spacing:-.012em;color:var(--soft);text-wrap:balance}
@keyframes map-pool{from{opacity:0;transform:scale(.3)}to{opacity:.62;transform:none}}
@keyframes map-spread{from{opacity:.95;transform:scale(.25)}to{opacity:0;transform:scale(2)}}
@keyframes map-drop{from{opacity:0;transform:translateY(-820px)}30%{opacity:1}to{opacity:1;transform:none}}
@keyframes map-squash{0%{transform:scale(1.2,.76)}38%{transform:scale(.93,1.1) translateY(-22px)}70%{transform:scale(1.03,.97)}100%{transform:none}}
@keyframes map-card{from{opacity:0;transform:scale(.72) translateY(26px)}to{opacity:1;transform:none}}
"""


FORMATS = (
    Format(
        id="flight_route",
        title="Flight route",
        summary="A route arcs between places on a real map while a marker flies it.",
        group="maps",
        jobs=("explain", "prove"),
        slots=(Slot("stops", kind="list", limit=4, each=20), Slot("text", limit=40, required=False)),
        example={
            "text": "Three cities in *one week*",
            "stops": [{"text": "Lisbon", "lon": -9.14, "lat": 38.72}, {"text": "Nairobi", "lon": 36.82, "lat": -1.29}, {"text": "Singapore", "lon": 103.82, "lat": 1.35}],
        },
        draw=_flight_route,
        seconds=4.4,
        shortest=2.6,
        longest=10.0,
        area="full",
        tall=1080,
        sound="whoosh",
        needs_alpha=False,
        css=_scoped("flight_route", FLIGHT_CSS),
    ),
    Format(
        id="country_lift",
        title="Country lift",
        summary="One country rises out of the map as a solid slab, with a label.",
        group="maps",
        jobs=("explain", "prove"),
        slots=(Slot("country", kind="choice", choices=_names()), Slot("text", limit=30), Slot("label", limit=64, required=False)),
        example={"country": "Italy", "text": "*Italy* is next", "label": "Our second market opens here in June"},
        draw=_country_lift,
        seconds=3.6,
        shortest=2.4,
        longest=9.0,
        area="full",
        tall=1080,
        sound="rise",
        needs_alpha=False,
        css=_scoped("country_lift", LIFT_CSS),
    ),
    Format(
        id="region_fill",
        title="Region fill",
        summary="A scan line crosses the map and each country fills by its value.",
        group="maps",
        jobs=("prove", "explain"),
        slots=(Slot("text", limit=44), Slot("regions", kind="list", limit=40), Slot("unit", limit=40, required=False)),
        example={
            "text": "Where the *orders* come from",
            "unit": "Orders per thousand people",
            "regions": [
                {"text": "Portugal", "value": 12},
                {"text": "Spain", "value": 26},
                {"text": "France", "value": 41},
                {"text": "Belgium", "value": 33},
                {"text": "Netherlands", "value": 58},
                {"text": "Germany", "value": 74},
                {"text": "Italy", "value": 29},
                {"text": "Switzerland", "value": 37},
                {"text": "Austria", "value": 45},
                {"text": "Poland", "value": 52},
                {"text": "Czechia", "value": 48},
                {"text": "Denmark", "value": 61},
                {"text": "Sweden", "value": 92},
                {"text": "Norway", "value": 68},
                {"text": "Finland", "value": 79},
            ],
        },
        draw=_sweep_map,
        seconds=4.4,
        shortest=2.8,
        longest=10.0,
        area="full",
        tall=1080,
        sound="whoosh",
        needs_alpha=False,
        css=_scoped("region_fill", SWEEP_CSS),
    ),
    Format(
        id="bubble_map",
        title="Bubble map",
        summary="Sized circles grow on real places, with a ranked key beside them.",
        group="maps",
        jobs=("prove", "explain"),
        slots=(Slot("text", limit=40), Slot("places", kind="list", limit=8, each=18)),
        example={
            "text": "Biggest *cities* by riders",
            "places": [
                {"text": "Sao Paulo", "lon": -46.63, "lat": -23.55, "value": 41, "show": "41k"},
                {"text": "Lagos", "lon": 3.38, "lat": 6.52, "value": 27, "show": "27k"},
                {"text": "Paris", "lon": 2.35, "lat": 48.86, "value": 22, "show": "22k"},
                {"text": "Mumbai", "lon": 72.88, "lat": 19.08, "value": 35, "show": "35k"},
                {"text": "Seoul", "lon": 126.98, "lat": 37.57, "value": 18, "show": "18k"},
                {"text": "Chicago", "lon": -87.63, "lat": 41.88, "value": 13, "show": "13k"},
            ],
        },
        draw=_bubble_map,
        seconds=4.6,
        shortest=2.8,
        longest=10.0,
        area="full",
        tall=1080,
        sound="pop",
        needs_alpha=False,
        css=_scoped("bubble_map", BUBBLE_CSS),
    ),
    Format(
        id="hex_map",
        title="Hex map",
        summary="Every country gets one equal tile near its real place, coloured by value.",
        group="maps",
        jobs=("prove", "explain"),
        slots=(Slot("text", limit=44), Slot("regions", kind="list", limit=24), Slot("unit", limit=40, required=False)),
        example={
            "text": "Same size tile, *fair* comparison",
            "unit": "Support replies under an hour, percent",
            "regions": [
                {"text": "Ireland", "short": "IE", "value": 71},
                {"text": "United Kingdom", "short": "UK", "value": 64},
                {"text": "Portugal", "short": "PT", "value": 83},
                {"text": "Spain", "short": "ES", "value": 58},
                {"text": "France", "short": "FR", "value": 49},
                {"text": "Belgium", "short": "BE", "value": 77},
                {"text": "Netherlands", "short": "NL", "value": 88},
                {"text": "Germany", "short": "DE", "value": 62},
                {"text": "Denmark", "short": "DK", "value": 91},
                {"text": "Italy", "short": "IT", "value": 41},
                {"text": "Austria", "short": "AT", "value": 69},
                {"text": "Czechia", "short": "CZ", "value": 56},
                {"text": "Poland", "short": "PL", "value": 47},
                {"text": "Sweden", "short": "SE", "value": 94},
                {"text": "Norway", "short": "NO", "value": 86},
                {"text": "Finland", "short": "FI", "value": 80},
            ],
        },
        draw=_hex_map,
        seconds=4.4,
        shortest=2.8,
        longest=10.0,
        area="full",
        tall=1080,
        sound="pop",
        needs_alpha=False,
        css=_scoped("hex_map", HEX_CSS),
    ),
    Format(
        id="flow_map",
        title="Flow map",
        summary="Moving lines carry value between real places and one hub.",
        group="maps",
        jobs=("explain", "prove"),
        slots=(
            Slot("place", limit=20),
            Slot("lon", kind="number"),
            Slot("lat", kind="number"),
            Slot("flows", kind="list", limit=6, each=14),
            Slot("text", limit=40, required=False),
            Slot("direction", kind="choice", choices=("in", "out"), required=False),
        ),
        example={
            "text": "Everything ships *through one port*",
            "place": "Rotterdam",
            "lon": 4.48,
            "lat": 51.92,
            "flows": [
                {"text": "Santos", "lon": -46.33, "lat": -23.96, "value": 48, "show": "48%"},
                {"text": "Lagos", "lon": 3.38, "lat": 6.52, "value": 21, "show": "21%"},
                {"text": "Halifax", "lon": -63.57, "lat": 44.65, "value": 18, "show": "18%"},
                {"text": "Mombasa", "lon": 39.67, "lat": -4.04, "value": 13, "show": "13%"},
            ],
        },
        draw=_flow_map,
        seconds=4.6,
        shortest=2.8,
        longest=10.0,
        area="full",
        tall=1080,
        sound="whoosh",
        needs_alpha=False,
        css=_scoped("flow_map", FLOW_CSS),
    ),
    Format(
        id="globe_turn",
        title="Globe turn",
        summary="A true orthographic Earth turns to a place and marks it.",
        group="maps",
        jobs=("hook", "explain"),
        slots=(Slot("text", limit=30), Slot("lon", kind="number"), Slot("lat", kind="number"), Slot("label", limit=60, required=False)),
        example={"text": "It started in *Nairobi*", "lon": 36.82, "lat": -1.29, "label": "Two founders, one spare room"},
        draw=_globe_turn,
        seconds=3.8,
        shortest=2.6,
        longest=8.0,
        area="full",
        tall=1080,
        sound="whoosh",
        needs_alpha=False,
        css=_scoped("globe_turn", GLOBE_CSS),
    ),
    Format(
        id="pin_drop",
        title="Pin drop",
        summary="A marker falls onto the map, lands with weight and opens a label.",
        group="maps",
        jobs=("explain", "prove"),
        slots=(Slot("text", limit=28), Slot("lon", kind="number"), Slot("lat", kind="number"), Slot("label", limit=44, required=False)),
        example={"text": "*Porto*, Portugal", "lon": -8.61, "lat": 41.15, "label": "Where the first shop opened"},
        draw=_pin_drop,
        seconds=3.2,
        shortest=2.0,
        longest=8.0,
        area="full",
        tall=1080,
        sound="thud",
        needs_alpha=False,
        css=_scoped("pin_drop", PIN_CSS),
    ),
)
