"""One storyboard drives the picture, the sound and the checks. Change a scene's length and everything after it moves.

The storyboard is the source of truth. Nothing here writes to it. Scene order is approved once, and a
later change to that order has to be approved again, so an agent cannot quietly drop a scene.

The plan checks are adapted from `check_timeline.py` in win4r/video-director, under this licence:

MIT License

Copyright (c) 2026 win4r

Permission is hereby granted, free of charge, to any person obtaining a copy
of this software and associated documentation files (the "Software"), to deal
in the Software without restriction, including without limitation the rights
to use, copy, modify, merge, publish, distribute, sublicense, and/or sell
copies of the Software, and to permit persons to whom the Software is
furnished to do so, subject to the following conditions:

The above copyright notice and this permission notice shall be included in all
copies or substantial portions of the Software.

THE SOFTWARE IS PROVIDED "AS IS", WITHOUT WARRANTY OF ANY KIND, EXPRESS OR
IMPLIED, INCLUDING BUT NOT LIMITED TO THE WARRANTIES OF MERCHANTABILITY,
FITNESS FOR A PARTICULAR PURPOSE AND NONINFRINGEMENT. IN NO EVENT SHALL THE
AUTHORS OR COPYRIGHT HOLDERS BE LIABLE FOR ANY CLAIM, DAMAGES OR OTHER
LIABILITY, WHETHER IN AN ACTION OF CONTRACT, TORT OR OTHERWISE, ARISING FROM,
OUT OF OR IN CONNECTION WITH THE SOFTWARE OR THE USE OR OTHER DEALINGS IN THE
SOFTWARE.
"""

from __future__ import annotations

import copy
import re
from dataclasses import dataclass
from typing import Any

from .errors import AltocueError
from .motion import formats
from .qa import rules

HOOK_BY = 3.0  # sound off, large type says what this is by here
WORDS_BY = 1.0
HOLD = 1.5  # seconds important text stays settled on screen
ARRIVAL = 0.4  # an entrance is not reading time
SHORT_READ = 0.6
SHORTEST_SILENCE = 0.4
HERO_NOT_BEFORE = 0.4  # share of the film. Spent earlier, there is nothing to build to
SILENCE_WITHIN = 4.0
MOST_FLASHES = 3  # WCAG 2.2 criterion 2.3.1, Level A: no more than three flashes in any one second
FLASHES = {"white_flash": 1}
LENGTH_SLACK = 0.05  # share of the brief's length
VOICE_PAD = 0.3
WORDS_A_SECOND = 2.6  # narration, for an estimate before the voice exists
ROUTES = ("continuous", "montage", "hybrid")
CLOCKS = ("music", "voice", "reads")
ROLES = ("hook", "cta", "term")
BRIEF_FIELDS = ("company", "audience", "goal", "message", "must_show", "tone", "deliverables")
CAMERA_MOVES = ("push", "pull", "pan", "rise", "drift", "orbit")
CAMERA_LINKS = ("cut", "through", "dive", "pan", "rise", "dissolve")
WIPES = ("iris", "white_flash", "shape_match", "shutter", "curtain", "torn_wipe")
LINK_SECONDS = {"through": 0.6, "dive": 0.8, "pan": 0.6, "rise": 0.6, "dissolve": 0.5}
WIPE_SECONDS = {"iris": 1.0, "white_flash": 0.8, "shape_match": 1.1, "shutter": 0.9, "curtain": 1.0, "torn_wipe": 1.0}
IMPORTANT_JOBS = {"hook", "prove", "close"}
PASSING = {"chip", "trust_strip", "ticker", "corner_stamp"}
LAYER = re.compile(r"\{\{\s*([\w-]+)\s*\}\}")
TIME_KEYS = ("start", "end")


@dataclass(frozen=True)
class Problem:
    level: str  # error or warning
    where: str
    message: str
    fix: str = ""


def _walk(value: Any, change: Any) -> Any:
    if isinstance(value, str):
        return change(value)
    if isinstance(value, list):
        return [_walk(entry, change) for entry in value]
    if isinstance(value, dict):
        return {key: _walk(entry, change) for key, entry in value.items()}
    return value


def fill(board: dict[str, Any]) -> dict[str, Any]:
    """Put each layer's value everywhere it is named, so one change reaches every scene that uses it."""
    layers = {str(key): str(value) for key, value in (board.get("layers") or {}).items()}

    def swap(text: str) -> str:
        def one(found: re.Match[str]) -> str:
            name = found.group(1)
            if name not in layers:
                raise AltocueError(f'The storyboard uses the layer "{name}", which has no value.', fix=f'Add "{name}" under "layers" in storyboard.json.')
            return layers[name]

        return LAYER.sub(one, text)

    body = {key: value for key, value in board.items() if key != "layers"}
    return {**_walk(body, swap), "layers": dict(board.get("layers") or {})}


def number(value: Any) -> bool:
    return isinstance(value, int | float) and not isinstance(value, bool)


def estimated(scene: dict[str, Any]) -> float:
    """How long a line takes to say, before anyone has said it."""
    return max(len(str(scene.get("says", "")).split()) / WORDS_A_SECOND, 1.0)


def _length(scene: dict[str, Any], voice: dict[str, float] | None) -> float:
    value = scene.get("seconds")
    if value == "voice":
        taken = (voice or {}).get(str(scene.get("id")))
        return (taken if taken else estimated(scene)) + VOICE_PAD
    if not number(value) or value <= 0:
        raise AltocueError(f"Scene {scene.get('id')} has no length.", fix='Set "seconds" to a positive number, or to "voice" when a recording sets it.')
    return float(value)


def nearest(value: float, grid: list[float]) -> float:
    return min(grid, key=lambda beat: abs(beat - value))


def lay_out(board: dict[str, Any], fps: int, beats: list[float] | None, voice: dict[str, float] | None) -> list[dict[str, Any]]:
    """Scenes end to end on whole frames. With `snap` set, each cut moves to the nearest beat and later scenes keep their planned place."""
    planned, frame, laid = 0.0, 0, []
    for scene in board.get("scenes", []):
        planned += _length(scene, voice)
        end = max(frame + 1, round(planned * fps))
        if board.get("snap") and beats:
            end = max(frame + 1, round(nearest(planned, beats) * fps))
        laid.append({**scene, "start": frame / fps, "end": end / fps})
        frame = end
    return laid


class Clock:
    """Turns a time written in the storyboard into film seconds."""

    def __init__(self, scenes: list[dict[str, Any]], fps: int) -> None:
        self.scenes = {str(scene.get("id")): scene for scene in scenes}
        self.fps = fps

    def frame(self, seconds: float) -> float:
        return round(seconds * self.fps) / self.fps

    def at(self, value: Any, scene: dict[str, Any]) -> float:
        if number(value):
            return self.frame(scene["start"] + float(value))
        if isinstance(value, dict) and "scene" in value:
            named = self.scenes.get(str(value["scene"]))
            if named is None:
                raise AltocueError(f'A time names the scene "{value["scene"]}", which is not in the storyboard.', fix=f"Use one of {', '.join(self.scenes)}.")
            base = named["end"] if value.get("from") == "end" else named["start"]
            return self.frame(base + float(value.get("offset", 0)))
        raise AltocueError(f"Cannot read the time {value!r} in scene {scene.get('id')}.", fix='Use seconds from the scene start, or {"scene": "payoff", "offset": 0.4}.')


def _item(entry: dict[str, Any], scene: dict[str, Any], clock: Clock, aspect: str) -> dict[str, Any]:
    item = {key: value for key, value in copy.deepcopy(entry).items() if key != "aspects"}
    item.update(copy.deepcopy((entry.get("aspects") or {}).get(aspect, {})))
    item["scene"] = scene.get("id")
    item["start"] = clock.at(entry.get("start", 0), scene)
    if "end" in entry:
        item["end"] = clock.at(entry["end"], scene)
    elif "duration" in entry:
        item["end"] = clock.frame(item["start"] + float(entry["duration"]))
    else:
        item["end"], item["hold"] = scene["end"], True
    for key, value in item.items():
        if isinstance(value, list):
            item[key] = _timed_rows(value, scene, clock)
    return item


def _timed_rows(value: Any, scene: dict[str, Any], clock: Clock) -> Any:
    """A row's `at` follows the timeline like any other time, at any depth."""
    if isinstance(value, list):
        return [_timed_rows(row, scene, clock) for row in value]
    if isinstance(value, dict):
        return {key: clock.at(entry, scene) if key == "at" else _timed_rows(entry, scene, clock) for key, entry in value.items()}
    return value


def resolve(board: dict[str, Any], *, fps: int, aspect: str = "16:9", beats: list[float] | None = None, voice: dict[str, float] | None = None) -> dict[str, Any]:
    """Plain seconds for everything: scenes, items and sound cues, all on one timeline."""
    scenes = lay_out(board, fps, beats, voice)
    clock = Clock(scenes, fps)
    items, cues, silences = [], [], []
    for scene in scenes:
        items += [_item(entry, scene, clock, aspect) for entry in scene.get("items", [])]
        for entry in scene.get("sound", []):
            at = clock.at(entry.get("at", 0), scene)
            if entry.get("snap") and beats:
                at = clock.frame(nearest(at, beats))
            cues.append({**{key: value for key, value in entry.items() if key not in ("at", "snap")}, "at": at, "planned": True})
        for entry in scene.get("silences", []):
            start = clock.at(entry.get("from", 0), scene)
            silences.append({"from": start, "to": clock.at(entry["to"], scene) if "to" in entry else clock.frame(start + float(entry.get("seconds", 0.5)))})
    total = scenes[-1]["end"] if scenes else 0.0
    hero = board.get("hero")
    hero_at = None
    if hero is not None and scenes:
        hero_at = float(hero) if number(hero) else clock.at(hero, scenes[0])
    return {
        "fps": fps,
        "aspect": aspect,
        "total": total,
        "scenes": [{key: value for key, value in s.items() if key not in ("items", "sound", "silences")} for s in scenes],
        "items": items,
        "cues": sorted(cues, key=lambda c: c["at"]),
        "silences": silences,
        "hero": hero_at,
    }


def _bare(text: Any) -> str:
    return " ".join(re.sub(r"[^\w]+", " ", str(text).lower()).split())


def on_screen(item: dict[str, Any]) -> list[str]:
    if item.get("type") not in formats.load():
        return [str(item[key]) for key in ("text", "headline", "cta", "title", "subtitle", "label", "value") if item.get(key)]
    return formats.strings(item)


def _important(item: dict[str, Any]) -> bool:
    if item.get("important") is not None:
        return bool(item["important"])
    fmt = formats.find(item.get("type"))
    if item.get("type") in PASSING:
        return False
    if fmt:
        return bool(set(fmt.jobs) & IMPORTANT_JOBS) and any(slot.kind in ("text", "list") for slot in fmt.slots)
    return item.get("type") in ("hook", "stat", "quote", "title", "endcard")


def _role(item: dict[str, Any], first_scene: Any) -> str | None:
    if item.get("role"):
        return str(item["role"])
    fmt = formats.find(item.get("type"))
    if item.get("scene") == first_scene and (item.get("type") == "hook" or (fmt and "hook" in fmt.jobs)):
        return "hook"
    return None


def _check_brief(brief: dict[str, Any], record: dict[str, Any], add: Any) -> None:
    for field in dict.fromkeys((*BRIEF_FIELDS, *record.get("brief_fields", ()))):
        if field == "brand":
            continue
        if brief.get(field) in (None, "", []):
            add("error", "brief", f'The brief has no "{field}".', f'Fill in "{field}" in brief.json.')
    for aspect in brief.get("deliverables") or []:
        if not re.fullmatch(r"\d+:\d+", str(aspect)):
            add("error", "brief", f"The deliverable {aspect} is not an aspect.", "Use 16:9, 9:16, 1:1 or 4:5.")
    if brief.get("example"):
        add(
            "warning",
            "brief",
            "This is still the example, and its product and numbers are invented.",
            'Replace every claim with a true one, then remove "example" from brief.json.',
        )
    if not brief.get("terms"):
        add("warning", "brief", "No search terms listed.", 'Add "terms" to brief.json: the one to three words this audience would search for.')


def _check_scenes(board: dict[str, Any], record: dict[str, Any], approved: list[str] | None, add: Any) -> None:
    scenes = board.get("scenes") or []
    if not scenes:
        add("error", "storyboard", "The storyboard has no scenes.", "Add at least one scene.")
    ids = [str(scene.get("id", "")) for scene in scenes]
    for name in sorted({name for name in ids if ids.count(name) > 1 or not name}):
        add("error", name or "storyboard", f'Two scenes share the id "{name}".' if name else "A scene has no id.", "Give every scene its own id.")
    beats = board.get("beats") or record.get("beats") or []
    for scene in scenes:
        where, beat = str(scene.get("id")), scene.get("beat")
        if beat not in beats:
            add("error", where, f'This scene has the job "{beat}", which is not one this film needs.', f"Use one of {', '.join(beats)}, or cut the scene.")
        if not scene.get("items"):
            add("error", where, "This scene shows nothing.", "Add an item, or cut the scene.")
        camera = scene.get("camera") or {}
        if camera.get("move", "drift") not in CAMERA_MOVES:
            add("error", where, f'Unknown camera move "{camera.get("move")}".', f"Use one of {', '.join(CAMERA_MOVES)}.")
        link = scene.get("link") or {}
        kind = link.get("type", "cut") if isinstance(link, dict) else link
        if kind not in (*CAMERA_LINKS, *WIPES):
            add("error", where, f'Unknown link "{kind}".', f"Use one of {', '.join((*CAMERA_LINKS, *WIPES))}.")
        elif kind in WIPES:
            try:
                formats.check({**(link if isinstance(link, dict) else {}), "type": kind})
            except AltocueError as error:
                add("error", where, str(error.args[0]), error.fix or "")
        energy = scene.get("energy")
        if energy is not None and not (number(energy) and 0 <= energy <= 10):
            add("error", where, f"Energy is {energy}.", "Use a number from 0 to 10.")
    for beat in beats:
        if not record.get("beats_optional") and beat not in [scene.get("beat") for scene in scenes]:
            add("error", "storyboard", f'No scene does the job "{beat}".', "Add a scene for it. Every beat of this film type is needed.")
    if approved is not None and approved != ids:
        fix = "Put it back. If the person asked for this, run `altocue film approve`."
        for name in approved:
            if name not in ids:
                add("error", name, f'The approved scene "{name}" is gone.', fix)
        if [name for name in ids if name in approved] != [name for name in approved if name in ids]:
            add("error", "storyboard", f"The scene order changed. The approved order is {', '.join(approved)}.", fix)
    for before, after in zip(scenes, scenes[1:], strict=False):
        a, b = before.get("camera") or {}, after.get("camera") or {}
        if a.get("move") == "pan" and b.get("move") == "pan" and float(a.get("x", 0)) * float(b.get("x", 0)) < 0:
            add("warning", str(after.get("id")), "The pan flips direction across the cut.", "Keep the camera travelling the same way, or give the turn a reason.")
    for scene in scenes[:-1]:
        link = scene.get("link") or {}
        if isinstance(link, dict) and link and not link.get("why"):
            add("warning", str(scene.get("id")), "This seam has no reason.", 'Add "why" to the link: what crosses the cut, and why it moves that way.')
    for scene in scenes:
        for key, hint in (("verb", "the one verb this scene does"), ("carry", "what crosses to the next scene")):
            if key == "carry" and scene is scenes[-1]:
                continue
            if not scene.get(key):
                add("warning", str(scene.get("id")), f'No "{key}".', f'Add "{key}": {hint}.')


def arrival(scene: dict[str, Any] | None) -> float:
    """How long before a scene's start the camera link from the scene before it begins."""
    link = (scene or {}).get("link") or {}
    made = {"type": link} if isinstance(link, str) else link
    kind = made.get("type", "cut")
    return float(made.get("seconds", LINK_SECONDS.get(kind, 0.0))) / 2 if kind in CAMERA_LINKS else 0.0


def _check_timing(done: dict[str, Any], add: Any, reading: float = rules.READING_WPS) -> None:
    scenes = {scene["id"]: scene for scene in done["scenes"]}
    leads = {scene["id"]: arrival(before) for before, scene in zip([None, *done["scenes"]], done["scenes"], strict=False)}
    first_scene = done["scenes"][0]["id"] if done["scenes"] else None
    flashes: list[float] = []
    hooks: list[float] = []
    frame = 1 / done["fps"]
    for item in done["items"]:
        kind, where = item.get("type"), str(item.get("scene"))
        fmt = formats.find(kind)
        if fmt is None and kind not in formats.CORE_TYPES and kind != "footage":
            add("error", where, f'Unknown format "{kind}".', "Run `altocue library search <what you want>` to find one.")
            continue
        try:
            formats.check(item)
            if fmt:
                fmt.draw({**item, "image": None, "src": None})  # a slot in the wrong shape fails here, not in the render
        except AltocueError as error:
            add("error", where, str(error.args[0]), error.fix or "Shorten the copy.")
        except (KeyError, TypeError, ValueError, IndexError, AttributeError) as error:
            add("error", where, f'{kind} cannot be drawn from what it was given: {error!r}.', f"See `altocue library show {kind}`.")
        scene = scenes[item["scene"]]
        seconds = item["end"] - item["start"]
        if item["start"] < scene["start"] - leads[item["scene"]] - frame:
            add(
                "error",
                where,
                f"{kind} starts {scene['start'] - item['start']:.2f} s before its scene.",
                "It may lead by half the link at most, so it arrives with the camera.",
            )
        if item["end"] > scene["end"] + frame and not item.get("carry"):
            add(
                "error",
                where,
                f"{kind} runs {item['end'] - scene['end']:.2f} s past the end of its scene.",
                'Shorten it, or add "carry": true if it crosses the cut on purpose.',
            )
        if fmt and seconds + frame < fmt.shortest and fmt.id not in FLASHES and fmt.group != "wipe":
            add("error", where, f"{kind} runs {seconds:.2f} s, and it needs at least {fmt.shortest:g} s to play.", "Give it longer.")
        if fmt and seconds > fmt.longest + frame and fmt.group != "backdrop":
            add("warning", where, f"{kind} runs {seconds:.2f} s, longer than the {fmt.longest:g} s it was made for.", "Shorten it, or split the moment in two.")
        shown = " ".join(on_screen(item))
        if _important(item) and shown and seconds - ARRIVAL + frame < HOLD:
            add(
                "error",
                where,
                f'"{shown[:40]}" holds {seconds - ARRIVAL:.2f} s after it lands, and important text needs about {HOLD:g} s.',
                "Give it longer, or say less.",
            )
        for _ in range(FLASHES.get(str(kind), int(item.get("flashes", 0)))):
            flashes.append((item["start"] + item["end"]) / 2)
        if _role(item, first_scene) == "hook" and shown:
            hooks.append(item["start"])
    for row in rules.reading_time({"items": done["items"], "total": done["total"]}, reading):
        if row["short_by"] > frame:
            add(
                "error",
                str(row["layer"]),
                f"{row['words']} words at {row['start']:.2f} s get {row['shown']:.1f} s and need {row['needed']:.1f} s at {reading:g} words a second.",
                "Give it longer, or cut words.",
            )
    for index, at in enumerate(flashes):
        count = sum(1 for other in flashes[index:] if other - at < 1.0)
        if count > MOST_FLASHES:
            add(
                "error",
                "storyboard",
                f"{count} flashes in one second at {at:.2f} s. The limit is three flashes a second (WCAG 2.2, criterion 2.3.1).",
                "Space them out, or drop a flash.",
            )
            break
    if done["scenes"] and not done.get("no_hook"):
        first = min(hooks, default=None)
        if first is None or first > HOOK_BY:
            add("error", "storyboard", f"No hook text is up by {HOOK_BY:g} s.", "Put large words in the first scene. With the sound off, they must say what this is.")
    for cue in done["cues"]:
        if not 0 <= cue["at"] < done["total"]:
            add("error", "storyboard", f"The sound {cue.get('name', cue.get('file'))} at {cue['at']:.2f} s is outside the film.", "Move it inside, or remove it.")
    hero = done.get("hero")
    if hero is None:
        add("warning", "storyboard", "No hero moment named.", 'Add "hero": the one moment the film builds to.')
        return
    if not 0 <= hero <= done["total"]:
        add("error", "storyboard", f"The hero moment at {hero:.2f} s is outside the film.", "Move it inside the film.")
    elif hero < HERO_NOT_BEFORE * done["total"]:
        add("warning", "storyboard", f"The hero moment comes {hero / done['total']:.0%} of the way in.", "Spent this early, the film has nothing left to build to.")
    silences = done.get("silences") or []
    if not any(s["to"] <= hero + 0.05 and hero - s["to"] <= SILENCE_WITHIN for s in silences):
        add(
            "warning",
            "storyboard",
            f"No planned silence in the {SILENCE_WITHIN:g} s before the hero moment.",
            'Add a short drop-out with "silences" on the scene before it.',
        )
    for entry in silences:
        if entry["to"] - entry["from"] < SHORTEST_SILENCE:
            add(
                "warning",
                "storyboard",
                f"The silence at {entry['from']:.2f} s is under {SHORTEST_SILENCE:g} s, so it will not register.",
                "Make it longer, or remove it.",
            )


def _check_cover(brief: dict[str, Any], board: dict[str, Any], done: dict[str, Any], add: Any) -> None:
    shown = " ".join(_bare(text) for item in done["items"] for text in on_screen(item))
    named = {_bare(entry) for scene in board.get("scenes", []) for entry in scene.get("shows", [])}
    for want in brief.get("must_show") or []:
        if _bare(want) not in named and _bare(want) not in shown:
            add("error", "brief", f'The must-show "{want}" is in no scene.', 'Put it on screen, and name it under "shows" on that scene.')
    for term in brief.get("terms") or []:
        said = _bare(" ".join(str(scene.get("says", "")) for scene in board.get("scenes", [])))
        if _bare(term) not in shown and _bare(term) not in said:
            add("error", "brief", f'The search term "{term}" is never said or shown.', "Name it right after the moment that explains it.")
    for read in (entry for scene in board.get("scenes", []) for entry in scene.get("reads", [])):
        if number(read.get("seconds")) and read["seconds"] < SHORT_READ:
            add("warning", "storyboard", f'The read "{read.get("what", "?")}" gets {read["seconds"]:.2f} s.', "Too short to find, understand and register.")


def check(
    brief: dict[str, Any],
    board: dict[str, Any],
    record: dict[str, Any],
    *,
    approved: list[str] | None = None,
    voice: dict[str, float] | None = None,
    card: dict[str, Any] | None = None,
    fps: int = 60,
) -> list[Problem]:
    """Every problem at once, each with what to do about it. Nothing here raises on a bad storyboard."""
    problems: list[Problem] = []

    def add(level: str, where: str, message: str, fix: str = "") -> None:
        problems.append(Problem(level, where, message, fix))

    _check_brief(brief, record, add)
    _check_scenes(board, record, approved, add)
    try:
        filled = fill(board)
        done = resolve(filled, fps=fps, voice=voice)
    except AltocueError as error:
        add("error", "storyboard", str(error.args[0]), error.fix or "")
        return problems
    done["no_hook"] = bool(record.get("no_hook"))
    _check_timing(done, add)
    _check_cover(brief, filled, done, add)
    wanted = brief.get("length")
    if number(wanted) and abs(done["total"] - float(wanted)) > wanted * LENGTH_SLACK:
        add("error", "storyboard", f"The film runs {done['total']:.1f} s and the brief asks for {wanted:g} s.", "Change a scene's seconds, or the brief.")
    if record.get("route_required") and board.get("route") not in ROUTES:
        add("warning", "storyboard", "No route stated.", f'Set "route" to one of {", ".join(ROUTES)}.')
    clock = board.get("clock")
    if clock is not None and clock not in CLOCKS:
        add("error", "storyboard", f'Unknown clock "{clock}".', f"Use one of {', '.join(CLOCKS)}.")
    if board.get("loop") and number(board.get("bpm")):
        bar = 4 * 60.0 / float(board["bpm"])
        count = done["total"] / bar
        if abs(count - round(count)) * bar > 0.002:
            add("error", "storyboard", f"A loop must be whole bars, and this is {count:.3f} bars of {bar:.3f} s.", "Change the scene lengths or the tempo.")
    if card and number(card.get("average_shot")) and done["scenes"]:
        average = done["total"] / len(done["scenes"])
        if not 0.5 <= average / float(card["average_shot"]) <= 2.0:
            add(
                "warning",
                "storyboard",
                f"Scenes average {average:.1f} s, and the style card measured {card['average_shot']:.1f} s a shot.",
                "Follow the card's pacing, or say why not.",
            )
    return problems
