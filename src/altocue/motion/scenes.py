"""Lay a storyboard out as one page: a world that keeps moving, a camera on each scene, and links that carry motion across each cut.

The camera is CSS on plain HTML wrappers, because the renderer seeks those to film time. A wrapper is not
a clip, so the clips inside it keep their own film times. Every move is sampled from a curve through its poses,
so the speed never drops to nothing at a pose and nothing fully stops.
"""

from __future__ import annotations

import math
import re
from html import escape
from pathlib import Path
from typing import Any

from .. import storyboard
from ..brand import Brand
from . import compositions, formats
from .formats import kit
from .html import base_css, item_html, root_tokens

PANEL_WIDTH = 880.0  # what a panel format fills at full scale
SLOW_SAMPLES = 6  # keys a second on a slow drift
FAST_SAMPLES = 30  # keys a second through a link
FAST_STRETCH = 1.2  # seconds. Anything shorter between poses counts as fast
SOFT_DRIFT = 0.018
LAST_HOLD = 0.5
WORLD_DRIFT = 0.012  # scale added each scene, so the backdrop never settles
WORLD_KICK = 0.05  # and a little more when the camera goes through a cut
SHUTTER = 180

# Named places on the canvas as left, top, right, bottom shares. Left and right stack on a tall canvas, so a two-column scene recomposes instead of cropping.
ZONES = {
    "wide": {
        "center": (0.14, 0.12, 0.86, 0.88),
        "left": (0.07, 0.12, 0.43, 0.88),
        "right": (0.44, 0.05, 0.965, 0.95),
        "top": (0.14, 0.05, 0.86, 0.46),
        "bottom": (0.14, 0.54, 0.86, 0.95),
        "full": (0.05, 0.06, 0.95, 0.94),
        "low": (0.14, 0.62, 0.86, 0.94),
        "high": (0.14, 0.06, 0.86, 0.5),
        "phone_a": (0.47, 0.13, 0.705, 0.87),
        "phone_b": (0.725, 0.13, 0.96, 0.87),
        "phone": (0.62, 0.07, 0.885, 0.93),
    },
    "tall": {
        "center": (0.07, 0.2, 0.93, 0.78),
        "left": (0.07, 0.13, 0.93, 0.4),
        "right": (0.05, 0.42, 0.95, 0.78),
        "top": (0.07, 0.13, 0.93, 0.42),
        "bottom": (0.07, 0.46, 0.93, 0.8),
        "full": (0.05, 0.12, 0.95, 0.8),
        "low": (0.07, 0.58, 0.93, 0.8),
        "high": (0.07, 0.13, 0.93, 0.55),
        "phone_a": (0.05, 0.36, 0.49, 0.8),
        "phone_b": (0.51, 0.36, 0.95, 0.8),
        "phone": (0.22, 0.41, 0.78, 0.86),
    },
    "square": {
        "center": (0.08, 0.12, 0.92, 0.88),
        "left": (0.07, 0.06, 0.93, 0.4),
        "right": (0.07, 0.4, 0.93, 0.88),
        "top": (0.07, 0.06, 0.93, 0.46),
        "bottom": (0.07, 0.52, 0.93, 0.94),
        "full": (0.05, 0.06, 0.95, 0.94),
        "low": (0.08, 0.62, 0.92, 0.94),
        "high": (0.08, 0.06, 0.92, 0.5),
        "phone_a": (0.14, 0.33, 0.48, 0.93),
        "phone_b": (0.52, 0.33, 0.86, 0.93),
        "phone": (0.36, 0.38, 0.64, 0.93),
    },
}

FILM_CSS = """
.ground{position:absolute;inset:0;background:var(--panel)}
.world,.cam,.over{position:absolute;inset:0;transform-origin:50% 50%}
.cam .bg{display:none}
.zone{display:flex;flex-direction:column;justify-content:center}
.zone.mid{align-items:center;text-align:center}
.zone.mid>*{margin-left:auto;margin-right:auto}
.footage{position:absolute;inset:0;width:100%;height:100%;object-fit:cover;background:var(--panel)}
.footage.framed{border-radius:calc(30px * var(--scale));box-shadow:0 0 0 2px color-mix(in srgb,var(--ink) 16%,transparent),0 34px 90px #00000080}
.footnote{display:flex;justify-content:center;font-size:calc(34px * var(--scale));font-weight:600;letter-spacing:-.01em;color:var(--ink)}
.footnote span{padding:.28em .7em;border-radius:99px;background:color-mix(in srgb,var(--panel) 72%,transparent)}
"""


def shape(size: tuple[int, int]) -> str:
    width, height = size
    ratio = width / height
    return "wide" if ratio > 1.2 else "tall" if ratio < 0.85 else "square"


def zone_box(zone: str, size: tuple[int, int]) -> tuple[float, float, float, float]:
    places = ZONES[shape(size)]
    return places.get(zone, places["center"])


def fit_scale(fmt: formats.Format | None, zone: str, size: tuple[int, int], factor: float) -> float:
    """As large as the zone allows, by width and by the height the format promises."""
    left, top, right, bottom = zone_box(zone, size)
    wide = (right - left) * size[0] / PANEL_WIDTH
    tall = (bottom - top) * size[1] / (fmt.tall if fmt else 420)
    return round(min(wide, tall) * factor, 3)


def _place(item: dict[str, Any], size: tuple[int, int]) -> dict[str, Any]:
    """A place in the frame for one graphic, as inline style. Full-frame formats stay full unless a zone is named."""
    fmt = formats.find(item["type"])
    area = fmt.area if fmt else ("full" if item["type"] in ("title", "endcard", "composition", "footage") else "panel")
    named = item.get("zone")
    if item["type"] == "composition":
        return {**item, "width": size[0], "height": size[1]}
    if area == "edge" or (area == "full" and not named):
        return item
    zone = str(named or "center")
    left, top, right, bottom = zone_box(zone, size)
    width, height = size
    box = f"left:{left * width:.0f}px;right:{(1 - right) * width:.0f}px;top:{top * height:.0f}px;bottom:{(1 - bottom) * height:.0f}px"
    if item["type"] == "footage":
        return {**item, "place": f"left:{left * width:.0f}px;top:{top * height:.0f}px;width:{(right - left) * width:.0f}px;height:{(bottom - top) * height:.0f}px"}
    if area == "full":
        return {**item, "place": box}
    scale = fit_scale(fmt, zone, size, float(item.get("scale", 1.0)))
    return {**item, "place": f"{box};--scale:{scale};--gutter:0px;--rail:0px", "zone_class": "zone mid" if item.get("align") == "center" else "zone"}


def _link(scene: dict[str, Any]) -> dict[str, Any]:
    link = scene.get("link") or {}
    made = {"type": link} if isinstance(link, str) else dict(link)
    kind = made.setdefault("type", "cut")
    made.setdefault("seconds", storyboard.LINK_SECONDS.get(kind, storyboard.WIPE_SECONDS.get(kind, 0.0)))
    return made


def _poses(scene: dict[str, Any]) -> list[tuple[float, dict[str, float]]] | None:
    """A hand-written path: poses in seconds from the scene start, which the camera flows through without stopping."""
    written = (scene.get("camera") or {}).get("poses")
    if not written:
        return None
    return [(scene["start"] + float(pose["at"]), {key: float(value) for key, value in pose.items() if key != "at"}) for pose in written]


def _ends(scene: dict[str, Any]) -> dict[str, Any]:
    """Default camera for a scene that sets none: a slow push, so a scene never sits still."""
    camera = dict(scene.get("camera") or {})
    written = _poses(scene)
    if written:
        blank = {"scale": 1.0, "x": 0.0, "y": 0.0, "rx": 0.0, "ry": 0.0}
        return {"first": {**blank, **written[0][1]}, "last": {**blank, **written[-1][1]}, "origin": camera.get("origin")}
    move = camera.get("move", "drift")
    amount = float(camera.get("amount", SOFT_DRIFT if move == "drift" else 0.08))
    x, y = float(camera.get("x", 0.0)), float(camera.get("y", 0.0))
    first = {"scale": 1.0, "x": 0.0, "y": 0.0, "rx": 0.0, "ry": 0.0}
    last = dict(first)
    if move in ("push", "drift"):
        last["scale"] = 1 + amount
    elif move == "pull":
        first["scale"] = 1 + amount
    elif move == "pan":
        x = x or 0.05
        first["x"], last["x"] = -x, x
        first["scale"] = last["scale"] = 1 + abs(x) * 2 + 0.01
    elif move == "rise":
        y = y or 0.04
        first["y"], last["y"] = -y, y
        first["scale"] = last["scale"] = 1 + abs(y) * 2 + 0.01
    elif move == "orbit":
        turn = float(camera.get("turn", 6.0))
        first["ry"], last["ry"] = -turn, turn
        first["rx"], last["rx"] = float(camera.get("tilt", 6.0)), float(camera.get("tilt", 6.0)) * 0.4
        first["scale"], last["scale"] = 1.0, 1 + amount
    for side, pose in (("from", first), ("to", last)):
        pose.update({key: float(value) for key, value in (camera.get(side) or {}).items()})
    return {"first": first, "last": last, "origin": camera.get("origin")}


def _focus(scene: dict[str, Any], size: tuple[int, int]) -> tuple[float, float]:
    """Where a camera aims: a share of the frame, or the middle of a named zone so it follows the layout at every aspect."""
    camera = scene.get("camera") or {}
    origin = camera.get("origin") or (scene.get("link") or {}).get("focus") if isinstance(scene.get("link") or {}, dict) else camera.get("origin")
    if isinstance(origin, str):
        left, top, right, bottom = zone_box(origin, size)
        return (left + right) / 2, (top + bottom) / 2
    if isinstance(origin, list | tuple) and len(origin) == 2:
        return float(origin[0]), float(origin[1])
    return 0.5, 0.5


def _exit(pose: dict[str, float], link: dict[str, Any]) -> dict[str, float]:
    kind, move = link["type"], dict(pose)
    direction = float(link.get("direction", -1))
    if kind == "through":
        move.update(scale=pose["scale"] * 1.8, opacity=0.0)
    elif kind == "dive":
        move.update(scale=pose["scale"] * float(link.get("zoom", 5.0)), opacity=0.0)
    elif kind == "pan":
        move.update(x=pose["x"] + direction * 1.06)
    elif kind == "rise":
        move.update(y=pose["y"] + direction * 1.06)
    elif kind == "dissolve":
        move.update(opacity=0.0)
    return move


def _entry(pose: dict[str, float], link: dict[str, Any]) -> dict[str, float]:
    kind, move = link["type"], dict(pose)
    direction = float(link.get("direction", -1))
    if kind == "through":
        move.update(scale=pose["scale"] * 0.6, opacity=0.0)
    elif kind == "dive":
        move.update(scale=pose["scale"] * 0.56, opacity=0.0)
    elif kind == "pan":
        move.update(x=pose["x"] - direction * 1.06)
    elif kind == "rise":
        move.update(y=pose["y"] - direction * 1.06)
    elif kind == "dissolve":
        move.update(opacity=0.0)
    return move


def _spans(scene: dict[str, Any], before: dict[str, Any] | None, after: dict[str, Any] | None) -> dict[str, Any]:
    """When the camera wrapper runs, and how far each link reaches into the scene. A camera link overlaps the two scenes it joins."""
    arrive = _link(before) if before else {"type": "cut", "seconds": 0.0}
    leave = _link(scene) if after else {"type": "cut", "seconds": 0.0}
    lap_in = arrive["seconds"] / 2 if arrive["type"] in storyboard.CAMERA_LINKS else 0.0
    lap_out = leave["seconds"] / 2 if leave["type"] in storyboard.CAMERA_LINKS else 0.0
    return {"arrive": arrive, "leave": leave, "lap_in": lap_in, "lap_out": lap_out, "first": scene["start"] - lap_in, "last": scene["end"] + lap_out}


def _css_pose(pose: dict[str, float]) -> str:
    parts = []
    if pose.get("rx") or pose.get("ry"):
        parts.append("perspective(1700px)")
    parts.append(f"translate({pose.get('x', 0) * 100:.3f}%,{pose.get('y', 0) * 100:.3f}%)")
    if pose.get("rx"):
        parts.append(f"rotateX({pose['rx']:.3f}deg)")
    if pose.get("ry"):
        parts.append(f"rotateY({pose['ry']:.3f}deg)")
    parts.append(f"scale({pose.get('scale', 1):.5f})")
    return f"transform:{' '.join(parts)};opacity:{pose.get('opacity', 1):.3f}"


def _sample_times(times: list[float]) -> list[float]:
    """Slow stretches need few keys. A link lasts under a second and moves fast, so it gets one for about every other frame."""
    samples = [times[0]]
    for begin, end in zip(times, times[1:], strict=False):
        rate = FAST_SAMPLES if end - begin < FAST_STRETCH else SLOW_SAMPLES
        count = max(int(math.ceil((end - begin) * rate)), 2)
        samples += [begin + (end - begin) * index / count for index in range(1, count + 1)]
    return samples


def _keyframes(name: str, poses: list[tuple[float, dict[str, float]]], span: float) -> str:
    """Sample a curve through the poses. Each property flows on its own, so nothing stops at a pose."""
    times = [at for at, _ in poses]
    keys = ("scale", "x", "y", "rx", "ry", "opacity")
    curves = {key: kit.flow(times, [pose.get(key, 1.0 if key in ("scale", "opacity") else 0.0) for _, pose in poses]) for key in keys}
    steps = []
    for at in _sample_times(times):
        steps.append(f"{100 * (at - times[0]) / span:.3f}%{{{_css_pose({key: curve(at) for key, curve in curves.items()})}}}")
    return f"@keyframes {name}{{{''.join(steps)}}}"


def camera_css(scene: dict[str, Any], spans: dict[str, Any]) -> str:
    ends = _ends(scene)
    first, last = ends["first"], ends["last"]
    begin, end = scene["start"], scene["end"]
    arrive, leave = spans["arrive"], spans["leave"]
    lap_in, lap_out = spans["lap_in"], spans["lap_out"]
    span = spans["last"] - spans["first"]
    middle = [(at, {**first, **pose}) for at, pose in (_poses(scene) or [])]
    inner = [pose for pose in middle if begin + lap_in < pose[0] < end - lap_out]
    poses: list[tuple[float, dict[str, float]]] = []
    if lap_in:
        poses.append((begin - lap_in, _entry(first, arrive)))
        if arrive["type"] == "through":  # the two scenes pass each other at speed, they do not stack
            poses.append((begin - 0.02, {**first, "scale": first["scale"] * 0.68, "opacity": 0.0}))
            poses.append((begin + lap_in * 0.35, {**first, "scale": first["scale"] * 0.88, "opacity": 0.85}))
        poses.append((begin + lap_in, first))
    else:
        poses.append((begin, first))
    poses += inner
    if lap_out:
        poses.append((end - lap_out, last))
        if leave["type"] == "through":
            poses.append((end - lap_out * 0.35, {**last, "scale": last["scale"] * 1.2, "opacity": 0.85}))
            poses.append((end + 0.02, {**last, "scale": last["scale"] * 1.62, "opacity": 0.0}))
        poses.append((end + lap_out, _exit(last, leave)))
    else:
        poses.append((end, last))
    return _keyframes(f"cam-{scene['id']}", poses, span)


def world_css(scenes: list[dict[str, Any]], total: float) -> str:
    """The backdrop keeps travelling for the whole film, and leans into every camera link."""
    poses: list[tuple[float, dict[str, float]]] = [(0.0, {"scale": 1.03, "x": 0.0})]
    scale, x = 1.03, 0.0
    for scene in scenes[:-1]:
        link = _link(scene)
        cut, reach = scene["end"], max(link["seconds"] / 2, 0.25)
        scale += WORLD_DRIFT
        ends = _ends(scene)
        x -= (ends["last"]["x"] - ends["first"]["x"]) * 0.25
        poses.append((cut - reach, {"scale": scale, "x": x}))
        if link["type"] in ("through", "dive"):
            scale += WORLD_KICK
        elif link["type"] == "pan":
            x += float(link.get("direction", -1)) * 0.06
        poses.append((cut + reach, {"scale": scale, "x": x}))
    poses.append((total, {"scale": scale + WORLD_DRIFT, "x": x}))
    kept = [poses[0]]
    for pose in poses[1:]:
        if pose[0] > kept[-1][0] + 1e-6:
            kept.append(pose)
    return _keyframes("world", kept, total)


def link_items(scenes: list[dict[str, Any]]) -> list[dict[str, Any]]:
    """A wipe sits centred on its cut, so the cut happens while the frame is covered."""
    links = []
    for scene in scenes[:-1]:
        link = _link(scene)
        if link["type"] in storyboard.WIPES:
            half = link["seconds"] / 2
            extras = {key: value for key, value in link.items() if key not in ("type", "seconds", "why", "direction", "focus")}
            links.append({**extras, "type": link["type"], "start": scene["end"] - half, "end": scene["end"] + half, "scene": scene["id"]})
    return links


def backdrops(board: dict[str, Any], scenes: list[dict[str, Any]], total: float) -> list[dict[str, Any]]:
    """Film-wide layers first. A scene's own backdrop plays over them for that scene."""
    made = []
    for entry in board.get("backdrop") or []:
        made.append({**entry, "start": 0.0, "end": total, "scene": None})
    for scene in scenes:
        for entry in scene.get("backdrop") or []:
            made.append({**entry, "start": scene["start"], "end": scene["end"], "scene": scene["id"]})
    return made


def timeline(done: dict[str, Any], *, size: tuple[int, int], fps: int, brand: Brand, board: dict[str, Any] | None = None) -> dict[str, Any]:
    """The same shape `altocue render` writes, so every later stage can read a film like any other project."""
    scenes = done["scenes"]
    total = done["total"]
    items = [_place({**item, "fixed": item.get("fixed", item["type"] == "corner_stamp" or bool(item.get("carry")))}, size) for item in done["items"]]
    return {
        "width": size[0],
        "height": size[1],
        "zone": None,
        "fps": fps,
        "duration": total,
        "tail": 0.0,
        "total": total,
        "brand": brand.data,
        "captions": [],
        "items": items,
        "music": None,
        "sfx": True,
        "inserts": [],
        "scenes": scenes,
        "links": link_items(scenes),
        "backdrops": backdrops(board or {}, scenes, total),
        "cues": done["cues"],
        "silences": done.get("silences", []),
        "hero": done.get("hero"),
        "route": (board or {}).get("route"),
        "loop": bool((board or {}).get("loop")),
    }


def everything(timeline_: dict[str, Any]) -> list[dict[str, Any]]:
    return [*timeline_.get("backdrops", []), *timeline_["items"], *timeline_.get("links", [])]


def _clip(item: dict[str, Any], brand: Brand, track: int) -> str:
    if item["type"] == "footage":
        attrs = f'data-start="{item["start"]:.3f}" data-duration="{item["end"] - item["start"]:.3f}" data-track-index="{track}"'
        name = escape(formats.stage_name(item["file"]))
        place = f' style="{item["place"]}"' if item.get("place") else ""
        look = "clip footage framed" if item.get("zone") else "clip footage"
        trim = f' data-media-start="{float(item["from"]):.3f}"' if item.get("from") else ""
        moving = Path(item["file"]).suffix.lower() in (".mp4", ".mov", ".webm")
        tag = (
            f'<video id="footage-{track}" class="{look}" {attrs}{trim}{place} src="{name}" muted playsinline></video>'
            if moving
            else f'<img id="footage-{track}" class="{look}" {attrs}{place} src="{name}" alt="">'
        )
        if not item.get("label"):
            return tag
        timing = f'data-start="{item["start"]:.3f}" data-duration="{item["end"] - item["start"]:.3f}" data-track-index="{track + 1000}"'
        note = f'<div class="clip footnote" {timing}{_below(item)}><span>{escape(str(item["label"]))}</span></div>'
        return f"{tag}\n{note}"
    made = item_html(item, brand, track)
    extra = item.get("zone_class")
    return made.replace('class="clip ', f'class="clip {extra} ', 1) if extra else made


def _below(item: dict[str, Any]) -> str:
    """A footage label sits across the foot of its picture."""
    found = dict(part.split(":", 1) for part in str(item.get("place") or "").split(";") if ":" in part)
    if not found:
        return ""
    foot = float(found["top"].removesuffix("px")) + float(found["height"].removesuffix("px"))
    return f' style="left:{found["left"]};width:{found["width"]};top:{foot - 92:.0f}px"'


def tail(scene: dict[str, Any], lap: float) -> float:
    return scene["end"] + lap


def compose(line: dict[str, Any], brand: Brand) -> str:
    """One page. Ground, then the world, then a camera per scene, then anything laid over the lot."""
    width, height, total = line["width"], line["height"], line["total"]
    css, geo = base_css(line, brand)
    scenes = line["scenes"]
    track = 1
    keyframes = [world_css(scenes, total)] if scenes else []
    behind, ahead = [], []
    for item in line.get("backdrops", []):
        track += 1
        (ahead if item.get("over") else behind).append(_clip(item, brand, track))
    cameras = []
    for index, scene in enumerate(scenes):
        before = scenes[index - 1] if index else None
        after = scenes[index + 1] if index + 1 < len(scenes) else None
        spans = _spans(scene, before, after)
        keyframes.append(camera_css(scene, spans))
        fx, fy = _focus(scene, (width, height))
        inner = []
        for item in line["items"]:
            if item["scene"] != scene["id"] or item.get("fixed"):
                continue
            track += 1
            shown = {**item, "end": tail(scene, spans["lap_out"])} if item.get("hold") else item
            if shown["end"] >= total - 1 / line["fps"]:
                shown = {**shown, "end": total + LAST_HOLD}  # the last thing on screen stays up, so the film does not end on a fade
            inner.append(_clip(shown, brand, track))
        seconds = spans["last"] - spans["first"]
        style = f"transform-origin:{fx * 100:.2f}% {fy * 100:.2f}%;animation:cam-{scene['id']} {seconds:.3f}s linear {max(spans['first'], 0):.3f}s both"
        cameras.append(f'<div class="cam" id="cam-{escape(str(scene["id"]))}" style="{style}">\n' + "\n".join(inner) + "\n</div>")
    fixed = [item for item in line["items"] if item.get("fixed")]
    for item in [*fixed, *line.get("links", [])]:
        track += 1
        ahead.append(_clip(item, brand, track))
    tokens = root_tokens(line, brand, geo)
    sheet = css + formats.stylesheet(everything(line)) + FILM_CSS + "\n".join(keyframes)
    blur = f'data-motion-blur="on" data-motion-blur-shutter="{SHUTTER}"'
    return f"""<!doctype html>
<html lang="en"><head><meta charset="utf-8">
<meta http-equiv="Content-Security-Policy" content="default-src 'self' 'unsafe-inline' 'unsafe-eval' data: blob:">
<style>{sheet}</style></head>
<body>
<div id="root" data-composition-id="root" data-start="0" data-duration="{total:.3f}" data-width="{width}" data-height="{height}" {blur} style="{tokens}">
<div class="ground"></div>
<div class="world" style="animation:world {total:.3f}s linear 0s both">
{chr(10).join(behind)}
</div>
{chr(10).join(cameras)}
<div class="over">
{chr(10).join(ahead)}
</div>
</div>
<script>window.__timelines=window.__timelines||{{}};</script>
</body></html>
"""


def bundled_composition(name: str) -> Path | None:
    return compositions.bundled(name)


def clean_id(text: str) -> str:
    return re.sub(r"[^a-z0-9-]+", "-", text.lower()).strip("-") or "scene"
