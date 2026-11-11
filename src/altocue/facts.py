"""Facts about the finished film, measured from the file. No score, no verdict and no key.

Only `text_readable` turns any of this into a gate, because reading speed is the one threshold
with published numbers behind it. Everything else is reported and left to the editor.
"""

from __future__ import annotations

import argparse
import json
import re
import tempfile
from datetime import UTC, datetime
from pathlib import Path
from typing import Any

import numpy as np

from . import __version__, ffmpeg, qa
from .errors import AltocueError
from .models import Word
from .project import Project, write_json
from .qa import gates, ocr, rules

HOOK_SECONDS = gates.HOOK_SECONDS
SCENE_THRESHOLD = 0.3  # the usual ffmpeg starting point, not a finding
SCENE_WIDTH = 320
OCR_MOST = 480  # frames read for first text, so a long film stays quick and its precision stays stated
SEARCH_AFTER = 2.0  # a graphic can take this long to land after its planned start
LOOP_WINDOW = 0.2
SILENT_LUFS = -69.0
SILENT_DB = -99.0
BUCKETS = ((10.0, "0-10"), (30.0, "10-30"), (60.0, "30-60"))
LAST_BUCKET = "60+"
SENTENCE_END = ".?!"
LAST_WORD_NEAR = 0.5
META_REELS_AD = {"left": 0.06, "right": 0.94, "top": 0.14, "bottom": 0.65}  # Meta's Reels ad guidance: 14% top, 35% bottom, 6% sides. An ad figure

CAUTION = "These are measurements of the file. None is a score, and none says how a film will do."
AFTER_SCORING = f"Measured after scoring, and never shown to the reviewer. {CAUTION}"
PLANNED_ONLY = "Planned on the timeline. Not read in the picture"
OPENING = f"The first {HOOK_SECONDS:g} seconds"

NAMES = {
    "first_word_s": "Time to first word",
    "first_text_s": "Time to first on-screen text",
    "hook_word_count": f"Words in the first {HOOK_SECONDS:g} seconds",
    "hook_wps": f"Words a second in the first {HOOK_SECONDS:g} seconds",
    "first_cut_s": "First cut",
    "cuts_per_10s": "Cuts per 10 seconds, from the edit",
    "scene_changes_per_10s": "Scene changes per 10 seconds, from the picture",
    "longest_static_s": "Longest stretch with no cut, insert or graphic change",
    "caption_coverage": "Share of speech time with a caption up",
    "opening_lufs": f"Loudness of the first {HOOK_SECONDS:g} seconds",
    "opening_minus_integrated": f"First {HOOK_SECONDS:g} seconds against the whole film",
    "text_reading_margin_s": "Tightest graphic against its reading time",
    "text_under_reading_time": "Graphics shorter than their reading time",
    "caption_wps_peak": "Fastest caption page, words a second",
    "safe_zone_project": "Text inside this project's safe zone",
    "safe_zone_meta_reels_ad": "Text inside Meta's Reels ad safe zone",
    "loop_ssim": "Last frame against the first, SSIM from 0 to 1",
    "loop_audio_delta_db": f"Last {LOOP_WINDOW:g}s of sound against the first {LOOP_WINDOW:g}s, dB",
    "ends_mid_sentence": "Speech stops mid sentence at the end",
}


# What each number is counted in, shown beside it in the table. The saved JSON keeps the bare number.
UNITS = {
    "first_word_s": "s",
    "first_text_s": "s",
    "first_cut_s": "s",
    "longest_static_s": "s",
    "text_reading_margin_s": "s",
    "hook_wps": "words/s",
    "caption_wps_peak": "words/s",
    "opening_lufs": "LUFS",
    "opening_minus_integrated": "LU",
    "loop_audio_delta_db": "dB",
}
SHARES = ("caption_coverage", "safe_zone_project", "safe_zone_meta_reels_ad")  # saved from 0 to 1, shown as a percentage


def known(value: Any, how: str, **more: Any) -> dict[str, Any]:
    return {"value": value, "how": how, **more}


def unknown(why: str) -> dict[str, Any]:
    """Could not be measured. A reader must never take this for a zero."""
    return {"value": None, "why": why}


def duration_bucket(seconds: float) -> str:
    """The same length bands Spyglass filters on, so later comparisons can hold length still."""
    return next((name for limit, name in BUCKETS if seconds < limit), LAST_BUCKET)


def merged(ranges: list[tuple[float, float]]) -> list[tuple[float, float]]:
    joined: list[tuple[float, float]] = []
    for start, end in sorted(ranges):
        if joined and start <= joined[-1][1]:
            joined[-1] = (joined[-1][0], max(joined[-1][1], end))
        else:
            joined.append((start, end))
    return joined


def coverage(speech: list[tuple[float, float]], shown: list[tuple[float, float]]) -> float:
    """Seconds of speech with something on screen, as a share of all speech seconds."""
    said, up = merged(speech), merged(shown)
    total = sum(end - start for start, end in said)
    if not total:
        return 0.0
    overlap = sum(max(0.0, min(a_end, b_end) - max(a_start, b_start)) for a_start, a_end in said for b_start, b_end in up)
    return overlap / total


def longest_gap(events: list[float], end: float) -> tuple[float, float, float]:
    """The longest run between events, counting from the first frame to the last."""
    marks = sorted({0.0, end, *(at for at in events if 0 < at < end)})
    length, start, stop = max(((b - a, a, b) for a, b in zip(marks, marks[1:], strict=False)), key=lambda gap: gap[0], default=(0.0, 0.0, 0.0))
    return round(length, 3), round(start, 3), round(stop, 3)


def ends_mid_sentence(words: list[dict[str, Any]], end: float) -> bool | None:
    """Only a transcript with punctuation can say. Speech that stops early is not mid sentence."""
    if not any(w["text"].rstrip()[-1:] in SENTENCE_END for w in words):
        return None
    last = words[-1]
    return last["text"].rstrip()[-1:] not in SENTENCE_END and end - float(last["end"]) <= LAST_WORD_NEAR


def recall_transcript(c: gates.Context, digest: str, *, listen: bool) -> None:
    """QA already heard this exact file. Otherwise transcribe it, unless told to stay quiet."""
    saved = c.project.review_dir / "transcript.json"
    data = json.loads(saved.read_text()) if saved.exists() else {}
    if data.get("sha256") == digest:
        words = [Word(text=w["text"], start=w["start"], end=w["end"]) for w in data.get("words", [])] if data.get("available") else None
        c._heard = (words, data.get("reason", ""))
    elif not listen:
        c._heard = (None, "No transcript of this file is saved.")


def speech_facts(c: gates.Context) -> dict[str, Any]:
    words = c.spoken()
    if words is None:
        why = c.heard()[1] or "No transcript of this file."
        return {name: unknown(why) for name in ("first_word_s", "hook_word_count", "hook_wps", "caption_coverage", "ends_mid_sentence")}
    how = "re-transcribed from the rendered file" if c.heard()[0] is not None else "mapped from the cut, not heard"
    window = min(HOOK_SECONDS, c.clip.duration) or HOOK_SECONDS
    hook = sum(1 for w in words if float(w["end"]) <= window)
    found: dict[str, Any] = {
        "first_word_s": known(round(float(words[0]["start"]), 3), how) if words else unknown("No speech was heard."),
        "hook_word_count": known(hook, how),
        "hook_wps": known(round(hook / window, 2), how),
    }
    if c.timeline is None:
        found["caption_coverage"] = unknown(gates.NO_TIMELINE)
    elif not words:
        found["caption_coverage"] = unknown("No speech was heard.")
    else:
        speech = [(float(w["start"]), float(w["end"])) for w in words]
        pages = [(float(p["start"]), float(p["end"])) for p in c.timeline.get("captions", [])]
        found["caption_coverage"] = known(round(coverage(speech, pages), 3), f"speech seconds {how}, against caption pages on the timeline")
    ended = ends_mid_sentence(words, c.clip.duration) if words else None
    found["ends_mid_sentence"] = known(ended, how) if ended is not None else unknown("The transcript has no punctuation to judge by.")
    return found


def scene_changes(file: Path, end: float) -> list[float]:
    chain = f"scale={SCENE_WIDTH}:-2,select='gt(scene,{SCENE_THRESHOLD})',showinfo"
    log = gates.log_of(["-t", f"{end:.3f}", "-i", str(file), "-vf", chain, "-an", "-f", "null", "-"])
    return [float(at) for at in re.findall(r"pts_time:([\d.]+)", log)]


def edit_facts(c: gates.Context) -> dict[str, Any]:
    end = max(c.footage_end, 0.001)
    scenes = scene_changes(c.file, end)
    insert_marks = [at for pair in c.inserts() for at in pair if 0 < at < end]
    cuts = sorted({round(s.start, 3) for s in c.shots[1:]} | {round(at, 3) for at in insert_marks})
    graphics = [float(item[side]) for item in (c.timeline or {}).get("items", []) for side in ("start", "end")]
    length, start, stop = longest_gap([*cuts, *scenes, *graphics], end)
    no_edit = unknown("No edl.json, so the cuts are unknown.")
    spread = "the film before any end card"
    cut_how = f"edit points and insert edges over {spread}. A punch-in counts, though it hardly reads as a cut"
    static_how = "gap between cuts, inserts, graphics going on or off, and scene changes. Movement inside a shot and caption pages do not count"
    first_cut = known(cuts[0], "first edit point or insert edge") if cuts else unknown("This film is one continuous shot.")
    return {
        "cuts_per_10s": known(round(len(cuts) * 10 / end, 2), cut_how) if c.edl else no_edit,
        "first_cut_s": first_cut if c.edl else no_edit,
        "scene_changes_per_10s": known(round(len(scenes) * 10 / end, 2), f"ffmpeg scene detection at {SCENE_THRESHOLD:g} in the picture, over {spread}"),
        "longest_static_s": known(length, static_how, **{"from": start, "to": stop}),
    }


def first_text(c: gates.Context, fps: float) -> dict[str, Any]:
    """When text first reads in the picture, checked against what the timeline planned."""
    strings = rules.on_screen(c.timeline) if c.timeline else []
    planned = min((float(s["start"]) for s in strings), default=None)
    chosen, reason = ocr.reader()
    if chosen is None:
        return known(planned, f"{PLANNED_ONLY}: {reason}", confirmed=False, planned_s=planned) if planned is not None else unknown(reason)
    until = c.clip.duration if planned is None else min(c.clip.duration, planned + SEARCH_AFTER)
    rate = min(fps, OCR_MOST / max(until, 0.001))
    wanted = set(rules.tokens(" ".join(s["text"] for s in strings)))
    with tempfile.TemporaryDirectory(prefix="altocue-facts-") as folder:
        ffmpeg.run(["-t", f"{until:.3f}", "-i", str(c.file), "-vf", f"fps={rate:.4f}", "-q:v", "2", str(Path(folder) / "f_%05d.jpg")])
        records = ocr.read_images(sorted(Path(folder).glob("f_*.jpg")))
    times: set[float] = set()
    for record in records:
        said = rules.tokens(record["text"])
        if sum(len(t) for t in said) >= 3 and (not strings or len(wanted.intersection(said)) / len(said) >= gates.TEXT_MATCH):
            times.add((int(Path(record["file"]).stem.removeprefix("f_")) - 1) / rate)
    ordered = sorted(times)
    if strings:
        first = ordered[0] if ordered else None
    else:  # text nobody planned must last, because one sample of texture can read as letters
        first = next((a for a, b in zip(ordered, ordered[1:], strict=False) if b - a <= 1.5 / rate), None)
    step = round(1 / rate, 3)
    if first is None:
        if planned is not None:
            return known(planned, PLANNED_ONLY, confirmed=False, planned_s=planned, precision_s=step)
        return unknown("No text was read anywhere in the picture.")
    how = f"read in the picture by {chosen}" + (", matched to the timeline" if strings else ". Nothing planned, so this may be text in the footage")
    return known(round(first, 3), how, confirmed=True, planned_s=planned, precision_s=step)


def text_facts(c: gates.Context) -> dict[str, Any]:
    names = ("text_reading_margin_s", "text_under_reading_time", "caption_wps_peak")
    if c.timeline is None:
        return {name: unknown(gates.NO_TIMELINE) for name in names}
    wps = float(c.project.setting("qa", "reading_wps", rules.READING_WPS))
    rows = rules.reading_time(c.timeline, wps)
    pages = [len(p["words"]) / max(float(p["end"]) - float(p["start"]), 0.001) for p in c.timeline.get("captions", [])]
    how = f"seconds on screen against words at {wps:g} a second, in the graphics. Entrances and exits count as on screen"
    none = unknown("No text in the graphics.")
    return {
        "text_reading_margin_s": known(round(min(r["shown"] - r["needed"] for r in rows), 3), how) if rows else none,
        "text_under_reading_time": known(sum(1 for r in rows if r["short_by"] > 0), how) if rows else none,
        "caption_wps_peak": known(round(max(pages), 2), "words over seconds on the busiest caption page") if pages else unknown("No captions."),
    }


def inside_share(boxes: list[dict[str, Any]], zone: dict[str, float]) -> float:
    return round(sum(1 for hit in boxes if not gates.outside(hit["box"], zone)) / len(boxes), 3)


def zone_facts(c: gates.Context) -> dict[str, Any]:
    names = ("safe_zone_project", "safe_zone_meta_reels_ad")
    seen = gates.sightings(c)
    why = "Nothing on screen to place." if seen.get("empty") else seen.get("reason")
    if not why and not seen.get("boxes"):
        why = "No planned text could be read in the picture."
    if why:
        return {name: unknown(why) for name in names}
    boxes, (width, height) = seen["boxes"], c.project.size
    how = f"{len(boxes)} text boxes read in sampled frames"
    ad_zone = known(inside_share(boxes, META_REELS_AD), f"{how}. An ad profile: 14% top, 35% bottom, 6% sides")
    return {
        "safe_zone_project": known(inside_share(boxes, gates.zone_for(c)), how),
        "safe_zone_meta_reels_ad": ad_zone if height > width else unknown("A profile for tall films."),
    }


def opening_facts(c: gates.Context) -> dict[str, Any]:
    names = ("opening_lufs", "opening_minus_integrated")
    if c.audio is None:
        return {name: unknown("The file has no audio stream.") for name in names}
    try:
        opening = ffmpeg.loudness(c.file, seconds=HOOK_SECONDS)["integrated"]
        whole = ffmpeg.loudness(c.file)["integrated"]
    except AltocueError:
        return {name: unknown("The sound measures as silent.") for name in names}
    if opening <= SILENT_LUFS:
        return {name: unknown(f"{OPENING} measure as silent.") for name in names}
    how = f"EBU R128 integrated loudness over the first {HOOK_SECONDS:g} seconds, a short window"
    return {"opening_lufs": known(opening, how), "opening_minus_integrated": known(round(opening - whole, 1), f"{how}, minus the whole film")}


def rms_db(samples: np.ndarray) -> float:
    """Level in dB, floored at -99 as the cut gate floors it, so a silent end reads as silence and not as a huge number."""
    level = float(np.sqrt(np.mean(samples**2))) if len(samples) else 0.0
    return round(max(20 * float(np.log10(level + 1e-9)), SILENT_DB), 1)


def loop_facts(c: gates.Context) -> dict[str, Any]:
    found: dict[str, Any] = {"loop_ssim": unknown("Could not read the first and last frames.")}
    last_at = max(float(c.video.get("duration") or c.clip.duration) - 1 / c.fps, 0.0)
    how = "ffmpeg ssim between the first and last frames. Measured on every film, whether or not it is meant to loop"
    with tempfile.TemporaryDirectory(prefix="altocue-facts-") as folder:
        first = gates.grab(c.file, 0.0, Path(folder) / "first.png")
        last = gates.grab(c.file, last_at, Path(folder) / "last.png")
        if first and last:
            match = re.search(r"All:([\d.]+)", gates.log_of(["-i", str(first), "-i", str(last), "-lavfi", "ssim", "-f", "null", "-"]))
            if match:
                found["loop_ssim"] = known(round(float(match.group(1)), 3), how)
    if c.audio is None:
        found["loop_audio_delta_db"] = unknown("The file has no audio stream.")
    else:
        samples, rate = c.samples()
        size = int(LOOP_WINDOW * rate)
        delta = rms_db(samples[-size:]) - rms_db(samples[:size])
        found["loop_audio_delta_db"] = known(round(delta, 1), f"RMS of the last {LOOP_WINDOW:g}s minus the first {LOOP_WINDOW:g}s")
    return found


def guarded(names: tuple[str, ...], measure: Any, *args: Any) -> dict[str, Any]:
    """One fact that cannot be measured must not cost the others."""
    try:
        return measure(*args)
    except AltocueError as error:
        return {name: unknown(f"Could not run: {error}") for name in names}


def measure(project: Project, file: str | Path, *, stage: str = "final", ocr_fps: float = gates.OCR_FPS, listen: bool = True) -> dict[str, Any]:
    """Every fact about one rendered file. `listen=False` uses only a transcript already saved."""
    target = Path(file)
    c = gates.Context.open(project, target, stage)
    digest = qa.sha256(target)
    recall_transcript(c, digest, listen=listen)
    found: dict[str, Any] = {
        "first_text_s": guarded(("first_text_s",), lambda: {"first_text_s": first_text(c, ocr_fps)})["first_text_s"],
    }
    for names, step in (
        (("first_word_s", "hook_word_count", "hook_wps", "caption_coverage", "ends_mid_sentence"), speech_facts),
        (("cuts_per_10s", "first_cut_s", "scene_changes_per_10s", "longest_static_s"), edit_facts),
        (("text_reading_margin_s", "text_under_reading_time", "caption_wps_peak"), text_facts),
        (("safe_zone_project", "safe_zone_meta_reels_ad"), zone_facts),
        (("opening_lufs", "opening_minus_integrated"), opening_facts),
        (("loop_ssim", "loop_audio_delta_db"), loop_facts),
    ):
        found.update(guarded(names, step, c))
    return {
        "file": target.name,
        "sha256": digest,
        "stage": stage,
        "measured_by": f"altocue {__version__}",
        "measured_at": datetime.now(UTC).isoformat(timespec="seconds"),
        "duration_s": round(c.clip.duration, 3),
        "duration_bucket": duration_bucket(c.clip.duration),
        "facts": {name: found[name] for name in NAMES},
    }


def _shown(value: Any, name: str = "") -> str:
    """A value with its unit, so "0.1" reads as "0.1 s" and a share reads as a percentage."""
    if isinstance(value, bool):
        return "yes" if value else "no"
    if name in SHARES and isinstance(value, int | float):
        return f"{value * 100:.0f}%"
    text = f"{value:g}" if isinstance(value, float) else str(value)
    return f"{text} {UNITS[name]}" if name in UNITS and isinstance(value, int | float) else text


def rows(data: dict[str, Any]) -> list[str]:
    lines = ["| Fact | Value | How it was measured |", "|---|---|---|"]
    for name, label in NAMES.items():
        entry = data["facts"][name]
        value = "not measured" if entry["value"] is None else _shown(entry["value"], name)
        lines.append(f"| {label} | {value} | {str(entry.get('how') or entry.get('why', '')).replace('|', '/')} |")
    return lines


def report_text(data: dict[str, Any]) -> str:
    head = f"{data['file']}, {data['duration_s']:g}s, sha256 {data['sha256'][:12]}, measured {data['measured_at']} by {data['measured_by']}."
    return "\n".join(["# Facts", "", head, "", CAUTION, "", *rows(data), ""])


def write(project: Project, data: dict[str, Any]) -> tuple[Path, Path]:
    report = project.review_dir / "facts.md"
    path = write_json(project.review_dir / "facts.json", data)
    report.write_text(report_text(data))
    return path, report


def after_review(project: Project, file: Path) -> dict[str, Any]:
    """For the report, once scoring is done. It must never cost a verdict, so failure is only noted."""
    try:
        data = measure(project, file, listen=False)
    except (AltocueError, KeyError, ValueError, OSError) as error:
        return {"error": str(error)}
    write(project, data)
    return data


def add_arguments(name: str, parser: argparse.ArgumentParser) -> None:
    parser.add_argument("--project", "-p", default=".", help="Project folder")
    parser.add_argument("--file", help="The render to measure. Defaults to out/final.mp4")
    parser.add_argument("--stage", choices=["final", "draft"], default="final", help="Which render to measure")


def run(args: argparse.Namespace) -> int:
    project = Project.open(args.project)
    data = measure(project, qa.resolve(project, args.file, args.stage), stage=args.stage)
    path, report = write(project, data)
    print(report_text(data))
    print(f"Saved {path} and {report}")
    return 0
