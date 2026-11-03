"""Checks for film made in code: can it be read on a phone, does anything pop, does anything stop, and is every frame a pure function of time.

`measure_text`, `compare_cuts`, the loop seam check and the planned-silence check are adapted from
`qa_video.py` in win4r/video-director, under this licence:

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

import math
import re
import subprocess
import tempfile
from pathlib import Path
from typing import Any

import numpy as np
from PIL import Image

from .. import ffmpeg, flash, storyboard
from ..project import Project, write_json
from . import ocr

ANALYSIS_WIDTH = 192  # tiny greyscale copies are plenty for frame differences
TEXT_FLOORS = {"hook": 84, "cta": 48, "term": 48}  # font px at a 1080 short side
MIN_CONTRAST = 7.0  # for the hook, the call to action and any named term
BODY_CONTRAST = 4.5  # WCAG AA for everything else
PHONE_FLOOR = 6.7  # px on a 360 px wide phone, under which text cannot be read
PLAYER_FLOOR = 16.0  # px at an 854 px player
FROZEN_STEP = 0.25  # mean difference out of 255 that counts as no motion
DEAD_AIR = 1.0  # seconds with no motion
POP_RATIO = 3.0
POP_FLOOR = 2.0
CUT_FLOOR = 6.0
CUT_RATIO = 3.5
SEEK_LIMIT = 1.0  # mean difference out of 255 between the same frame reached two ways
SEEK_SAMPLES = 8
SILENCE_RELATIVE_DB = 15.0
SILENCE_ABSOLUTE_DB = -30.0
FLAT_SECOND = 0.12  # a second of the film that barely moves
FRAME_ZERO_CONTRAST = 6.0
FRAME_ZERO_MEAN = 8.0
HOOK_SHEET_STEP = 0.5
PHONE_WIDTH = 360
READ_MATCH = 0.6
SEAM_HOLD = 10  # frames of full black or white each side of a one-take seam. With 3, the next segment started from the last picture
SEAM_SLACK = 2  # frames past the hold where the picture may still be arriving at black or white
BLACK_MOST = 20.0  # mean grey out of 255
WHITE_LEAST = 235.0
HELD_SPREAD = 10.0  # a held black or white is flat, not a dark shot


def measure_text(image: Image.Image, box: list[float], short: int) -> dict[str, float]:
    """Cap height, as a font size at a 1080 short side, and contrast of the text inside `box` = x, y, w, h in pixels.

    Otsu splits the crop's luminance in two, and the smaller class is taken as the glyphs. Height runs from the
    top of the tallest glyphs to the last row still dense with ink, so descenders do not count. Caps sit at about
    0.72 of the font size in most faces.
    """
    x, y, w, h = (int(round(float(v))) for v in box)
    rgb = np.asarray(image.crop((x, y, x + w, y + h)).convert("RGB"), np.float64) / 255
    linear = np.where(rgb <= 0.04045, rgb / 12.92, ((rgb + 0.055) / 1.055) ** 2.4)
    luminance = linear @ np.array([0.2126, 0.7152, 0.0722])
    counts, edges = np.histogram(luminance, 256, (0, 1))
    share = counts / max(1, counts.sum())
    below = np.cumsum(share)
    mean = np.cumsum(share * edges[:-1])
    between = (mean[-1] * below - mean) ** 2 / (below * (1 - below) + 1e-12)
    high = luminance > edges[int(np.argmax(between))]
    ink = high if high.mean() <= 0.5 else ~high
    if ink.all() or not ink.any():
        return {"font_px": 0.0, "contrast": 1.0}
    level_ink, level_ground = float(np.median(luminance[ink])), float(np.median(luminance[~ink]))
    contrast = (max(level_ink, level_ground) + 0.05) / (min(level_ink, level_ground) + 0.05)
    rows = ink.sum(axis=1)
    dense = np.nonzero(rows >= 0.25 * rows.max())[0]
    top = np.nonzero(rows >= max(1, 0.03 * rows.max()))[0][0]
    cap = float(dense[-1] - top + 1) * 1080 / short
    return {"font_px": round(cap / 0.72, 1), "contrast": round(contrast, 1)}


def _words(text: str) -> list[str]:
    return re.findall(r"[\w$%']+", text.lower())


def matched(wanted: str, rows: list[dict[str, Any]]) -> list[dict[str, Any]]:
    """The text boxes in a still that carry these words. Long copy wraps, so several boxes can answer for one string."""
    target = set(_words(wanted))
    if not target:
        return []
    found = [row for row in rows if set(_words(str(row.get("text", "")))) & target]
    seen = set().union(*(set(_words(str(row.get("text", "")))) for row in found)) if found else set()
    return found if len(seen & target) / len(target) >= READ_MATCH else []


def read_size(width: int, height: int, font_px: float) -> dict[str, float]:
    """A measured font size as it lands on a phone and in a small player."""
    actual = font_px * min(width, height) / 1080
    return {"phone_px": round(actual * PHONE_WIDTH / width, 1), "player_px": round(actual * 854 / width, 1)}


def hold_needed(words: int) -> float:
    return 0.4 + 0.2 * words


def _read_row(
    still: Image.Image, rows: list[dict[str, Any]], size: tuple[int, int], *, scene: Any, wanted: str, role: str | None, seconds: float, held: bool
) -> dict[str, Any]:
    width, height = size
    hits = matched(wanted, rows)
    if not hits:
        return {"scene": scene, "text": wanted, "passed": False, "role": role, "detail": "not read in the still: it may be cut off, faint or mid-move"}
    biggest = max(hits, key=lambda hit: hit["box"][3])
    x, y, w, h = biggest["box"]
    pad = h * 0.15
    measured = measure_text(still, [x * width - pad * width, y * height - pad * height, w * width + 2 * pad * width, h * height + 2 * pad * height], min(width, height))
    sizes = read_size(width, height, measured["font_px"])
    floor = TEXT_FLOORS.get(role or "", 0)
    wanted_contrast = MIN_CONTRAST if role in TEXT_FLOORS else BODY_CONTRAST
    ok = sizes["phone_px"] >= PHONE_FLOOR and sizes["player_px"] >= PLAYER_FLOOR and measured["font_px"] >= floor and measured["contrast"] >= wanted_contrast and held
    detail = f"{measured['font_px']:g} px at 1080, {sizes['phone_px']:g} px on a phone, contrast {measured['contrast']:g}:1, held {seconds:.1f} s"
    return {"scene": scene, "text": wanted, "role": role, "passed": bool(ok), "detail": detail, **measured, **sizes}


def phone_read(project: Project | None, line: dict[str, Any], frames: list[Path], times: list[float]) -> dict[str, Any]:
    """Size, contrast and hold time for the words that matter, and for any caption page up, measured on the stills.

    A caption page keeps the pace of the speech, so it is held to size and contrast and never to a reading time.
    """
    size = (line["width"], line["height"])
    name, reason = ocr.reader()
    if name is None:
        return {"rows": [], "not_run": reason}
    boxes: dict[str, list[dict[str, Any]]] = {}
    for row in ocr.read_images(frames):
        boxes.setdefault(Path(row["file"]).name, []).append(row)
    first = line["scenes"][0]["id"] if line["scenes"] else None
    report = []
    for path, at, scene in zip(frames, times, line["scenes"], strict=True):
        rows = boxes.get(path.name, [])
        with Image.open(path) as opened:
            still = opened.convert("RGB")
        for item in line["items"]:
            if item["scene"] != scene["id"] or not item["start"] <= at <= item["end"] + 0.01:
                continue
            copies = storyboard.on_screen(item)
            if not copies or item.get("type") in storyboard.PASSING:
                continue
            seconds = item["end"] - item["start"]
            for wanted in copies[:1]:
                held = seconds >= hold_needed(len(wanted.split()))
                report.append(_read_row(still, rows, size, scene=scene["id"], wanted=wanted, role=storyboard._role(item, first), seconds=seconds, held=held))
        for page in line.get("captions", []):
            if page.get("scene") != scene["id"] or not page["start"] <= at <= page["end"] + 0.01:
                continue
            wanted = " ".join(word["text"] for word in page["words"])
            report.append(_read_row(still, rows, size, scene=scene["id"], wanted=wanted, role="caption", seconds=page["end"] - page["start"], held=True))
    return {"rows": report}


def frame_stats(path: Path) -> dict[str, Any]:
    """Tiny greyscale frames, streamed. Per-frame numbers are kept, and never the film."""
    clip = ffmpeg.probe_clip(path)
    width, height = clip.display_size
    tall = max(2, int(round(ANALYSIS_WIDTH * height / width / 2)) * 2)
    size = ANALYSIS_WIDTH * tall
    process = subprocess.Popen(
        [ffmpeg.require(), "-v", "error", "-i", str(path), "-vf", f"scale={ANALYSIS_WIDTH}:{tall}:flags=area,format=gray", "-f", "rawvideo", "-"], stdout=subprocess.PIPE
    )
    means, spreads, steps, skips = [], [], [], []
    before = before2 = first = None
    assert process.stdout is not None
    while True:
        chunk = process.stdout.read(size)
        if len(chunk) < size:
            break
        frame = np.frombuffer(chunk, np.uint8).reshape(tall, ANALYSIS_WIDTH).astype(np.int16)
        means.append(float(frame.mean()))
        spreads.append(float(frame.std()))
        steps.append(float(np.abs(frame - before).mean()) if before is not None else 0.0)
        skips.append(float(np.abs(frame - before2).mean()) if before2 is not None else math.nan)
        if first is None:
            first = frame
        before2, before = before, frame
    process.wait()
    seam = float(np.abs(before - first).mean()) if before is not None and first is not None else math.nan
    return {"fps": clip.fps, "count": len(means), "mean": np.array(means), "spread": np.array(spreads), "step": np.array(steps), "skip": np.array(skips), "seam": seam}


def runs(mask: np.ndarray) -> list[tuple[int, int]]:
    edges = np.flatnonzero(np.diff(np.r_[0, mask.astype(np.int8), 0]))
    return [(int(a), int(b)) for a, b in zip(edges[::2], edges[1::2], strict=True)]


def analyse(stats: dict[str, Any]) -> dict[str, Any]:
    """Pops, cuts and stretches with no motion, in film seconds."""
    step, count, fps = stats["step"], stats["count"], stats["fps"]
    median = float(np.median(step[1:])) if count > 2 else 0.0
    cuts = []
    for index in range(1, count):
        local = np.concatenate([step[max(1, index - 15) : index], step[index + 1 : min(count, index + 16)]])
        level = float(np.median(local)) if local.size else 0.0
        if step[index] > max(CUT_FLOOR, CUT_RATIO * (level + 0.5)):
            cuts.append(index)
    pops = [index for index in range(1, count - 1) if index not in cuts and step[index] > POP_FLOOR and step[index] > POP_RATIO * max(step[index - 1], step[index + 1])]
    still = step < FROZEN_STEP
    if count:
        still[0] = False
    frozen = [(a, b) for a, b in runs(still) if (b - a + 1) / fps >= DEAD_AIR]
    seconds = int(count / fps) + 1
    curve = [round(float(step[int(second * fps) : int((second + 1) * fps)].mean()), 3) for second in range(seconds) if int(second * fps) < count]
    return {
        "median_step": round(median, 3),
        "frame_zero": {"mean": round(float(stats["mean"][0]), 1), "contrast": round(float(stats["spread"][0]), 1)} if count else None,
        "cuts": [round(index / fps, 3) for index in cuts],
        "pops": [round(index / fps, 3) for index in pops],
        "frozen": [{"from": round((a - 1) / fps, 2), "to": round(b / fps, 2), "seconds": round((b - a + 1) / fps, 2), "to_end": b >= count} for a, b in frozen],
        "motion_curve": curve,
        "flat_seconds": [index for index, value in enumerate(curve) if value < FLAT_SECOND],
    }


def compare_cuts(detected: list[float], expected: list[float], fps: float) -> dict[str, Any] | None:
    """How far each planned cut landed from where it was meant to, in frames, and any cut nobody planned."""
    if not expected:
        return None
    errors, missing = [], []
    for at in expected:
        if not detected:
            missing.append(at)
            continue
        near = min(detected, key=lambda cut: abs(cut - at))
        if abs(near - at) > 0.5:
            missing.append(at)
        else:
            errors.append(round((near - at) * fps, 2))
    unplanned = [cut for cut in detected if all(abs(cut - at) > 0.5 for at in expected)]
    return {"max_error_frames": max((abs(error) for error in errors), default=0.0), "errors_frames": errors, "missing": missing, "unplanned": unplanned}


def loop_seam(stats: dict[str, Any], median: float) -> dict[str, Any]:
    """Where the last frame meets the first. A mostly still film has a tiny median, so a seam under 0.5 of 255 passes on its own."""
    step, count = stats["step"], stats["count"]
    ratio = stats["seam"] / (median + 1e-6)
    speed = (step[count - 1] + 0.05) / (step[1] + 0.05) if count > 3 else 1.0
    return {
        "seam": round(stats["seam"], 3),
        "seam_vs_median": round(float(ratio), 2),
        "speed_ratio": round(float(speed), 2),
        "passed": bool((ratio <= 2.0 or stats["seam"] < 0.5) and 0.5 <= speed <= 2.0),
    }


def seams_of(line: dict[str, Any]) -> list[dict[str, Any]]:
    """A one-take's planned seams: the end of every scene that names its hold."""
    return [{"at": float(scene["end"]), "hold": str(scene["seam"])} for scene in line.get("scenes", [])[:-1] if scene.get("seam")]


def one_take_seams(stats: dict[str, Any], found: dict[str, Any], seams: list[dict[str, Any]], frames: int | None) -> dict[str, Any]:
    """A one-take hides its seams in held black or white, runs an exact frame count, and has no cut a viewer can feel.

    Each seam needs `SEAM_HOLD` flat frames of its colour on both sides. Every detected cut must fall inside a planned hold.
    """
    fps, count, means, spreads = stats["fps"], stats["count"], stats["mean"], stats["spread"]
    problems = []
    for seam in seams:
        at = round(seam["at"] * fps)
        level = means <= BLACK_MOST if seam["hold"] == "black" else means >= WHITE_LEAST
        held = level & (spreads <= HELD_SPREAD)
        before = next((step for step in range(SEAM_HOLD) if at - 1 - step < 0 or not held[at - 1 - step]), SEAM_HOLD)
        after = next((step for step in range(SEAM_HOLD) if at + step >= count or not held[at + step]), SEAM_HOLD)
        if min(before, after) < SEAM_HOLD:
            problems.append(f"the seam at {seam['at']:.2f} s holds {seam['hold']} for {before} frames before and {after} after, and needs {SEAM_HOLD} each side")
    reach = (SEAM_HOLD + SEAM_SLACK) / fps
    felt = [cut for cut in found["cuts"] if not any(abs(cut - seam["at"]) <= reach for seam in seams)]
    if felt:
        problems.append(f"cuts a viewer can feel at {felt} s, outside every planned hold")
    if frames is not None and count != frames:
        problems.append(f"{count} frames, and the plan asks for exactly {frames}")
    return {"seams": len(seams), "frames": count, "problems": problems, "passed": not problems}


def levels(path: Path, *, rate: int = 16000) -> tuple[np.ndarray, int, int]:
    done = subprocess.run(
        [ffmpeg.require(), "-v", "error", "-i", str(path), "-map", "0:a:0", "-ac", "1", "-ar", str(rate), "-f", "f32le", "-"], capture_output=True, check=True
    )
    samples = np.frombuffer(done.stdout, np.float32)
    window, hop = int(0.1 * rate), int(0.05 * rate)
    if samples.size < window:
        return np.zeros(0), hop, rate
    count = 1 + (samples.size - window) // hop
    return 20 * np.log10(np.array([math.sqrt(float(np.mean(samples[k * hop : k * hop + window] ** 2)) + 1e-12) for k in range(count)])), hop, rate


def planned_silences(path: Path, planned: list[dict[str, float]]) -> list[dict[str, Any]]:
    """A planned silence has to be quiet: at least 15 dB under the film's loud moments and under -30 dBFS."""
    if not planned:
        return []
    trace, hop, rate = levels(path)
    if not trace.size:
        return []
    top = float(np.percentile(trace, 90))
    limit = min(top - SILENCE_RELATIVE_DB, SILENCE_ABSOLUTE_DB)
    window = int(0.1 * rate)
    report = []
    for entry in planned:
        begin, end = float(entry["from"]), float(entry["to"])
        inside = [trace[k] for k in range(len(trace)) if k * hop / rate >= begin and (k * hop + window) / rate <= end]
        if inside:
            loudest = float(max(inside))
            report.append(
                {
                    "from": begin,
                    "to": end,
                    "loudest_db": round(loudest, 1),
                    "under_loud_db": round(top - loudest, 1),
                    "limit_db": round(limit, 1),
                    "passed": loudest < limit,
                }
            )
    return report


def seek_check(stage: Path, times: list[float], snapshot: Any) -> dict[str, Any]:
    """Reach each instant in one forward sweep, then again from a cold page, last first. A frame that differs depends on history, not on time."""
    with tempfile.TemporaryDirectory(prefix="altocue-seek-") as scratch:
        forward = snapshot(stage, times, Path(scratch) / "forward")
        backward = [snapshot(stage, [at], Path(scratch) / f"back-{index}")[0] for index, at in enumerate(reversed(times))]
        rows = []
        for at, one, two in zip(times, forward, reversed(backward), strict=True):
            with Image.open(one) as a, Image.open(two) as b:
                first, second = np.asarray(a.convert("L"), np.int16), np.asarray(b.convert("L"), np.int16)
            rows.append({"at": at, "difference": round(float(np.abs(first - second).mean()), 3)})
    worst = max(rows, key=lambda row: row["difference"], default={"at": 0, "difference": 0.0})
    return {"frames": rows, "worst": worst, "passed": worst["difference"] <= SEEK_LIMIT}


def sheet_times(total: float, *, start: float = 0.0, stop: float | None = None, step: float = 1.0) -> list[float]:
    end = total if stop is None else min(stop, total)
    count = int(math.ceil((end - start) / step))
    return [round(start + index * step, 3) for index in range(count) if start + index * step < end]


def report_rows(path: Path, line: dict[str, Any], *, loop: bool = False) -> tuple[list[dict[str, Any]], dict[str, Any]]:
    """Every picture and sound check for one finished file."""
    stats = frame_stats(path)
    found = analyse(stats)
    rows: list[dict[str, Any]] = []

    def add(level: str, name: str, detail: str) -> None:
        rows.append({"level": level, "check": name, "detail": detail})

    zero = found["frame_zero"] or {"mean": 0, "contrast": 0}
    add(
        "FAIL" if zero["contrast"] < FRAME_ZERO_CONTRAST or zero["mean"] < FRAME_ZERO_MEAN else "PASS",
        "frame 0",
        f"mean {zero['mean']}, contrast {zero['contrast']}. Thumbnails and muted autoplay show this frame.",
    )
    add("WARN" if found["pops"] else "PASS", "pops", f"small spikes at {found['pops']}: intended hits, or glitches?" if found["pops"] else "none")
    for stretch in found["frozen"]:
        add("PASS" if stretch["to_end"] else "FAIL", "dead air", f"{stretch['from']} to {stretch['to']} s has no motion")
    if not found["frozen"]:
        add("PASS", "dead air", "nothing stops for a second or more")
    flat = found["flat_seconds"]
    add(
        "WARN" if flat else "PASS",
        "motion curve",
        f"flat seconds at {flat}" if flat else f"every second moves, from {min(found['motion_curve'])} to {max(found['motion_curve'])} of 255",
    )
    planned = [scene["start"] for scene in line["scenes"][1:]] if line.get("route") == "montage" else []
    compared = compare_cuts(found["cuts"], planned, stats["fps"])
    if compared:
        add(
            "PASS" if compared["max_error_frames"] <= 1 and not compared["missing"] else "FAIL",
            "cuts against plan",
            f"worst {compared['max_error_frames']} frames off, missing {compared['missing']}, unplanned {compared['unplanned']}",
        )
    if loop:
        seam = loop_seam(stats, found["median_step"])
        add(
            "PASS" if seam["passed"] else "FAIL",
            "loop seam",
            f"seam {seam['seam']} of 255, {seam['seam_vs_median']} times the median step, speed ratio {seam['speed_ratio']}",
        )
    if line.get("one_take"):
        held = one_take_seams(stats, found, seams_of(line), line["one_take"].get("frames"))
        detail = "; ".join(held["problems"]) or f"{held['seams']} seams held in black or white, {held['frames']} frames, no cut outside a hold"
        add("PASS" if held["passed"] else "FAIL", "one-take seams", detail)
    measured = flash.measure(path)
    add(
        "PASS" if measured["passed"] else "FAIL",
        "flash limit",
        f"{measured['peak_per_second']:g} flashes in the worst second, limit {measured['limit']} (WCAG 2.2, 2.3.1)",
    )
    for entry in planned_silences(path, line.get("silences") or []):
        add(
            "PASS" if entry["passed"] else "FAIL",
            "planned silence",
            f"{entry['from']:.2f} to {entry['to']:.2f} s peaks at {entry['loudest_db']} dBFS, needs under {entry['limit_db']}",
        )
    return rows, found


def gate_rows(path: Path, line: dict[str, Any]) -> list[dict[str, Any]]:
    """The same measures as fact gates, so `altocue qa` fails a film the way it fails any other."""
    stats = frame_stats(path)
    found = analyse(stats)
    rows: list[dict[str, Any]] = []

    def gate(name: str, passed: bool, value: Any, threshold: Any, detail: str) -> None:
        rows.append({"id": name, "passed": passed, "value": value, "threshold": threshold, "detail": detail})

    def absent(name: str, why: str) -> None:
        rows.append({"id": name, "passed": False, "value": "n/a", "threshold": None, "detail": why, "skipped": True})

    zero = found["frame_zero"] or {"mean": 0, "contrast": 0}
    gate(
        "frame_zero",
        zero["contrast"] >= FRAME_ZERO_CONTRAST and zero["mean"] >= FRAME_ZERO_MEAN,
        zero["contrast"],
        FRAME_ZERO_CONTRAST,
        f"mean {zero['mean']}, contrast {zero['contrast']}",
    )
    stuck = [row for row in found["frozen"] if not row["to_end"]]
    gate("dead_air", not stuck, len(stuck), 0, "; ".join(f"{row['from']} to {row['to']} s has no motion" for row in stuck) or "nothing stops for a second or more")
    gate("pops", True, len(found["pops"]), None, f"spikes at {found['pops']}" if found["pops"] else "none")
    quiet = planned_silences(path, line.get("silences") or [])
    if quiet:
        bad = [row for row in quiet if not row["passed"]]
        gate(
            "planned_silence",
            not bad,
            len(bad),
            0,
            "; ".join(f"{row['from']:.2f} s peaks at {row['loudest_db']} dBFS" for row in bad) or f"{len(quiet)} planned silences are quiet",
        )
    else:
        absent("planned_silence", "No silences planned.")
    planned = [scene["start"] for scene in line["scenes"][1:]] if line.get("route") == "montage" else []
    compared = compare_cuts(found["cuts"], planned, stats["fps"])
    if compared:
        ok = compared["max_error_frames"] <= 1 and not compared["missing"]
        gate("cuts_to_plan", ok, compared["max_error_frames"], 1, f"worst {compared['max_error_frames']} frames off, missing {compared['missing']}")
    else:
        absent("cuts_to_plan", "Only a montage plans hard cuts.")
    take = line.get("one_take")
    if line.get("loop") or take:
        ok, value, threshold, details = True, None, None, []
        if line.get("loop"):
            seam = loop_seam(stats, found["median_step"])
            ok, value, threshold = seam["passed"], seam["seam"], 0.5
            details.append(f"seam {seam['seam']} of 255, speed ratio {seam['speed_ratio']}")
        if take:
            held = one_take_seams(stats, found, seams_of(line), take.get("frames"))
            ok = ok and held["passed"]
            value, threshold = (value, threshold) if line.get("loop") else (len(held["problems"]), 0)
            details.append("; ".join(held["problems"]) or f"one take: {held['seams']} seams held, {held['frames']} frames, no cut outside a hold")
        gate("loop_seam", ok, value, threshold, "; ".join(details))
    else:
        absent("loop_seam", "This film does not loop and is not one take.")
    return rows


def run_film(project: Project, film: Any) -> int:
    """Motion and phone checks across every finished film, with the sheets a person reads."""
    from .. import film as films

    aspect = str(project.setting("output", "aspect", "16:9"))
    line, brand = films.film_line(film, aspect)
    stage = films.stage_for(project, line, brand, films.slug(aspect), blur=False)  # blur samples around an instant, so it stays out of this test
    results: dict[str, Any] = {}
    failed = False
    times = [round(line["total"] * (index + 0.5) / SEEK_SAMPLES, 3) for index in range(SEEK_SAMPLES)]
    seek = seek_check(stage, times, films.snapshot)
    results["seek"] = seek
    print(f"{'PASS' if seek['passed'] else 'FAIL'} seek forwards and backwards: worst difference {seek['worst']['difference']} of 255 at {seek['worst']['at']} s")
    failed = failed or not seek["passed"]
    sheets = project.review_dir / "sheets"
    for path in sorted(project.out.glob("final*.mp4")):
        rows, found = report_rows(path, line, loop=bool(film.board.get("loop")))
        if path.name == "final.mp4":  # a type's own checks read the master, which is the aspect its plan was made at
            from .. import kinds

            rows += kinds.finished(project, film, line, path)
        results[path.name] = {"checks": rows, "motion_curve": found["motion_curve"]}
        for row in rows:
            print(f"{row['level']:<4} {path.name} {row['check']}: {row['detail']}")
        failed = failed or any(row["level"] == "FAIL" for row in rows)
        grabs = sheets / path.stem
        grabs.mkdir(parents=True, exist_ok=True)
        hook_times = sheet_times(min(line["total"], 3.0) + 0.01, step=HOOK_SHEET_STEP)
        phone_times = [at + 0.5 for at in sheet_times(line["total"], step=max(line["total"] / 24, 1.0))]
        for label, moments, columns in (("hook", hook_times, 7), ("phone", phone_times, 6)):
            frames = []
            for index, at in enumerate(moments):
                still = grabs / f"{label}-{index:03d}.png"
                ffmpeg.frame(path, at, still, width=PHONE_WIDTH)
                frames.append(still)
            films.sheet(frames, [f"{at:.2f}s" for at in moments], sheets / f"{path.stem}-{label}.jpg", columns=columns, width=PHONE_WIDTH)
    write_json(project.review_dir / "film-qa.json", results)
    print(f"Sheets in {sheets}. Open them: numbers catch broken, not bad. The hook sheet is the muted test.")
    return 1 if failed else 0
