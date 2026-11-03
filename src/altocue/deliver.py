"""One finished edit into what a release needs: subtitles, chapters, thumbnails and platform versions.

The master is only ever read. Everything lands in the project's `deliver/` folder, and each platform
version is rendered from its own workspace under `work/versions`, so `out/` and `edl.json` never change.
"""

from __future__ import annotations

import argparse
import copy
import json
import os
import shutil
import struct
from collections.abc import Iterator
from dataclasses import dataclass
from pathlib import Path
from typing import Any, BinaryIO

from . import brand as brands
from . import cut, ffmpeg, locks, master, motion, platforms, render, subtitles, thumbnails
from . import timeline as timelines
from .errors import AltocueError
from .generate.store import generated_dir
from .models import Edl, GateResult
from .motion.html import layout
from .platforms import Platform
from .project import SIZES, Project, write_json
from .qa import failed, gates, not_run, sha256

FOLDER = "deliver"
SIDE_FILES = ("chapters.json", "chapters"), ("beats.json", "beats")
TITLE_KEYS = ("title", "name", "beat")
YOUTUBE_FEWEST = 3  # YouTube Help, video chapters: https://support.google.com/youtube/answer/9884579
YOUTUBE_SHORTEST = 10.0
LANGUAGES = {
    "en": "eng",
    "es": "spa",
    "fr": "fra",
    "de": "deu",
    "it": "ita",
    "pt": "por",
    "nl": "nld",
    "ar": "ara",
    "zh": "zho",
    "ja": "jpn",
    "ko": "kor",
    "hi": "hin",
    "ru": "rus",
    "fa": "fas",
}
VERSION_GATES = (
    (("loudness", "true_peak"), gates.gate_loudness),
    (("duration",), gates.gate_duration),
    (("safe_zone",), gates.gate_safe_zone),
    (("face_clear",), gates.gate_face_clear),
    (("graphics_clear",), gates.gate_graphics_clear),
    (("format",), gates.gate_format),
)


def add_arguments(name: str, parser: argparse.ArgumentParser) -> None:
    parser.add_argument("--project", "-p", default=".", help="Project folder")
    parser.add_argument("--for", dest="platforms", help=f"Comma-separated versions to render: {', '.join(platforms.PLATFORMS)}")
    parser.add_argument("--loudness", type=master.loudness_arg, help=f"Integrated LUFS, or a named target: {', '.join(master.TARGETS)}")
    parser.add_argument("--renderer", choices=[*motion.RENDERERS, "auto"], default="auto")
    parser.add_argument("--stems", action="store_true", help="Also write voice, music and effects as WAV files, for a finisher")


def finished_film(project: Project) -> Path:
    final = project.out / "final.mp4"
    if not final.is_file():
        raise AltocueError(f"No finished film at {final}.", fix="Run `altocue render`, then `altocue master`.")
    if project.edl_path.exists() and project.edl_path.stat().st_mtime > final.stat().st_mtime:
        raise AltocueError(f"{final.name} is older than the cut.", fix="Run `altocue run`, then deliver again.")
    return final


# Subtitles


def spoken(project: Project, edl: Edl) -> tuple[list[dict[str, Any]], str]:
    """The kept words in output seconds, the same ones the captions use, or the reason there are none."""
    try:
        words = [w for w in cut.map_words(project, edl) if w.get("kind", "word") == "word"]
    except AltocueError as error:
        return [], str(error).splitlines()[0]
    return words, "" if words else "Nobody speaks in this cut."


def write_subtitles(words: list[dict[str, Any]], edl: Edl, folder: Path) -> tuple[Path | None, dict[str, Any]]:
    cues = subtitles.build_cues(words, cut.segment_ends(edl), fps=edl.fps)
    srt, vtt = folder / "subtitles.srt", folder / "subtitles.vtt"
    if not cues:
        srt.unlink(missing_ok=True)
        vtt.unlink(missing_ok=True)
        return None, {"files": [], "reading": subtitles.reading_report([])}
    srt.write_text(subtitles.to_srt(cues))
    vtt.write_text(subtitles.to_vtt(cues))
    return srt, {"files": [srt.name, vtt.name], "reading": subtitles.reading_report(cues)}


# Chapters


def _title(row: dict[str, Any]) -> str:
    return next((str(row[key]).strip() for key in TITLE_KEYS if str(row.get(key, "")).strip()), "")


def chapter_list(project: Project, words: list[dict[str, Any]], total: float) -> tuple[list[dict[str, Any]], str]:
    """Read `chapters.json`, or the director's `beats.json`. Each row has a title and a start, in seconds or on a spoken word."""
    for name, key in SIDE_FILES:
        path = project.root / name
        if path.exists():
            data = json.loads(path.read_text())
            return _chapters(data.get(key, []) if isinstance(data, dict) else data, name, words, total), name
    return [], ""


def _chapters(rows: list[dict[str, Any]], name: str, words: list[dict[str, Any]], total: float) -> list[dict[str, Any]]:
    found = []
    for number, row in enumerate(rows, 1):
        title, start = _title(row), row.get("start", row.get("at"))
        if not title:
            raise AltocueError(f"Chapter {number} in {name} has no title.", fix='Give each row a "title".')
        if start is None:
            raise AltocueError(f'Chapter "{title}" in {name} has no start.', fix='Give it a "start" in seconds, or {"word": "..."}.')
        at = timelines.resolve_time(start, words)
        if at >= total:
            raise AltocueError(f'Chapter "{title}" starts past the end of the film, at {at:g} s of {total:g} s.', fix=f"Fix {name}.")
        found.append({"title": title, "start": at})
    found.sort(key=lambda c: c["start"])
    for chapter, following in zip(found, found[1:], strict=False):
        if chapter["start"] == following["start"]:
            raise AltocueError(f'Chapters "{chapter["title"]}" and "{following["title"]}" start at the same moment.', fix=f"Fix {name}.")
    if found:
        found[0]["start"] = 0.0  # a chapter list always opens at the top of the film
    for chapter, end in zip(found, [*[c["start"] for c in found[1:]], total], strict=True):
        chapter["end"] = end
    return found


def youtube_problems(chapters: list[dict[str, Any]]) -> list[str]:
    """Why YouTube would ignore this list. Its Help page asks for three chapters, of 10 seconds each, from 00:00."""
    problems = []
    if len(chapters) < YOUTUBE_FEWEST:
        problems.append(f"YouTube needs at least three chapters, and this list has {len(chapters)}.")
    problems += [
        f'YouTube ignores chapters under 10 seconds: "{c["title"]}" runs {c["end"] - c["start"]:.1f} s.'
        for c in chapters
        if c["end"] - c["start"] < YOUTUBE_SHORTEST
    ]
    return problems


def youtube_block(chapters: list[dict[str, Any]]) -> str:
    """The lines to paste into a YouTube description."""
    long = chapters[-1]["end"] >= 3600
    lines = []
    for chapter in chapters:
        hours, rest = divmod(int(chapter["start"]), 3600)
        minutes, seconds = divmod(rest, 60)
        lines.append(f"{hours:02d}:{minutes:02d}:{seconds:02d} {chapter['title']}" if long else f"{minutes:02d}:{seconds:02d} {chapter['title']}")
    return "\n".join(lines) + "\n"


def _escaped(title: str) -> str:
    for mark in "\\=;#\n":
        title = title.replace(mark, f"\\{mark}")
    return title


def ffmetadata(chapters: list[dict[str, Any]]) -> str:
    """Chapter marks in ffmpeg's own metadata format, in milliseconds."""
    lines = [";FFMETADATA1"]
    for c in chapters:
        lines += ["[CHAPTER]", "TIMEBASE=1/1000", f"START={round(c['start'] * 1000)}", f"END={round(c['end'] * 1000)}", f"title={_escaped(c['title'])}"]
    return "\n".join(lines) + "\n"


def write_chapters(chapters: list[dict[str, Any]], source: str, folder: Path) -> dict[str, Any]:
    block = folder / "chapters.txt"
    block.unlink(missing_ok=True)
    problems = youtube_problems(chapters) if chapters else []
    if chapters and not problems:
        block.write_text(youtube_block(chapters))
    written = block.name if chapters and not problems else None
    return {"source": source, "marks": len(chapters), "chapters": chapters, "youtube": {"file": written, "problems": problems}}


# Loudness


def target_for(project: Project, override: float | master.Target | None, platform: Platform | None = None) -> dict[str, Any]:
    """The loudness a file should hit and where that number comes from. `social` means the project's own target."""
    level, peak = master.project_target(project)
    named = override if isinstance(override, master.Target) else None
    if isinstance(override, float | int):
        return {"name": "custom", "target": float(override), "true_peak": peak, "source": "Set by hand on the command line.", "primary": False}
    named = named or master.TARGETS[platform.loudness if platform else "social"]
    if named.name != "social":
        level, peak = named.loudness, named.true_peak
    return {"name": named.name, "target": level, "true_peak": peak, "source": named.source, "primary": named.primary}


def measured(project: Project, path: Path, target: dict[str, Any]) -> dict[str, Any]:
    """What the file really reads, against what it was meant to."""
    if master.no_sound_in(project):
        return {**target, "measured": None, "passed": None, "note": "No clip in this project has sound."}
    found = ffmpeg.loudness(path)
    passed = abs(found["integrated"] - target["target"]) <= master.PASS_TOLERANCE and found["true_peak"] <= target["true_peak"]
    return {**target, "measured": found, "passed": passed}


def _spoken_as(found: dict[str, Any]) -> str:
    if found["measured"] is None:
        return found["note"]
    level = found["measured"]
    label = "no primary source" if not found["primary"] else found["name"]
    return f"{level['integrated']:g} LUFS, {level['true_peak']:g} dBTP against {found['target']:g} LUFS, {found['true_peak']:g} dBTP ({label})"


# The soft copy


def _boxes(handle: BinaryIO, start: int, end: int) -> Iterator[tuple[bytes, int, int]]:
    """MP4 boxes between two offsets, as name, body start and end."""
    at = start
    while at + 8 <= end:
        handle.seek(at)
        size, kind = struct.unpack(">I4s", handle.read(8))
        body = at + 8
        if size == 1:
            size = struct.unpack(">Q", handle.read(8))[0]
            body = at + 16
        elif size == 0:
            size = end - at
        yield kind, body, at + size
        at += size


def subtitles_off_by_default(path: Path) -> None:
    """Captions are already burned in, so a player must not add the soft track unasked.

    ffmpeg's MP4 muxer always enables the first subtitle track, whatever its disposition says,
    so the flag is cleared here. Only that one bit in the track header changes.
    """
    with path.open("r+b") as handle:
        for name, body, end in _boxes(handle, 0, path.stat().st_size):
            if name != b"moov":
                continue
            for trak in [box for box in _boxes(handle, body, end) if box[0] == b"trak"]:
                parts = {kind: (start, stop) for kind, start, stop in _boxes(handle, trak[1], trak[2])}
                media = {kind: start for kind, start, _ in _boxes(handle, *parts[b"mdia"])}
                handle.seek(media[b"hdlr"] + 8)
                if handle.read(4) == b"sbtl":
                    flags_at = parts[b"tkhd"][0] + 3
                    handle.seek(flags_at)
                    flags = handle.read(1)[0]
                    handle.seek(flags_at)
                    handle.write(bytes([flags & ~1]))


def package(base: Path, dest: Path, srt: Path | None, chapters: list[dict[str, Any]], language: str) -> None:
    """Add the subtitle track and chapter marks to a copy. Picture and sound pass through untouched."""
    inputs, maps = ["-i", str(base)], ["-map", "0"]
    extra: list[str] = []
    count = 1
    if srt:
        inputs += ["-i", str(srt)]
        maps += ["-map", f"{count}:0"]
        extra += ["-c:s", "mov_text", "-metadata:s:s:0", f"language={LANGUAGES.get(language, 'und')}", "-metadata:s:s:0", "title=Subtitles"]
        count += 1
    meta = dest.with_name(".chapters.ffmeta")
    if chapters:
        meta.write_text(ffmetadata(chapters))
        inputs += ["-i", str(meta)]
        extra += ["-map_chapters", str(count)]
    part = dest.with_name(f".{dest.stem}.part{dest.suffix}")
    try:
        ffmpeg.run([*inputs, *maps, "-map_metadata", "0", "-c", "copy", *extra, "-movflags", "+faststart", str(part)])
        if srt:
            subtitles_off_by_default(part)
        os.replace(part, dest)
    finally:
        part.unlink(missing_ok=True)
        meta.unlink(missing_ok=True)


def make_film(
    project: Project, final: Path, folder: Path, srt: Path | None, chapters: list[dict[str, Any]], override: float | master.Target | None
) -> dict[str, Any]:
    """`deliver/film.mp4`. With a loudness asked for, the sound is mastered again from the render, never from the final."""
    target = target_for(project, override)
    dest, base = folder / "film.mp4", final
    remaster = folder / ".film-sound.mp4"
    try:
        if override is not None:
            source = project.out / "render.mp4" if (project.out / "render.mp4").is_file() else final
            with locks.heavy("mastering the film"):
                master.master_file(source, remaster, target["target"], target["true_peak"], silent_film=master.no_sound_in(project))
            base = remaster
        package(base, dest, srt, chapters, str(project.setting("transcribe", "language", "en")))
    finally:
        remaster.unlink(missing_ok=True)
    return {"file": dest.name, "subtitle_track": srt is not None, "chapter_marks": len(chapters), "loudness": measured(project, dest, target)}


# Platform versions


@dataclass
class Version(Project):
    """One platform's copy of a project. It reads the footage, words and sound, and writes only under `home`."""

    home: Path = Path()

    @property
    def work(self) -> Path:
        return self.home

    @property
    def out(self) -> Path:
        return self.home / "out"

    @property
    def review_dir(self) -> Path:
        return self.home / "review"

    @property
    def edl_path(self) -> Path:
        return self.home / "edl.json"

    @property
    def timeline_path(self) -> Path:
        return self.home / "timeline.json"

    @property
    def words_dir(self) -> Path:
        return self.root / "work" / "words"

    @property
    def audio_dir(self) -> Path:
        return self.root / "work" / "audio"

    @property
    def lut_dir(self) -> Path:
        return self.root / "work" / "luts"

    @property
    def media_path(self) -> Path:
        return self.root / "work" / "media.json"


def length_problem(platform: Platform, seconds: float) -> str | None:
    if platform.longest is not None and seconds > platform.longest:
        return f"{seconds:.1f} s is over the {platform.longest:g} s {platform.name} allows."
    if platform.shortest is not None and seconds < platform.shortest:
        return f"{seconds:.1f} s is under the {platform.shortest:g} s {platform.name} needs."
    return None


def same_picture(project: Project, platform: Platform) -> bool:
    """True when this platform wants the shape and layout the master already has."""
    if platform.aspect != project.setting("output", "aspect"):
        return False
    brand = brands.load(project)
    return layout(*project.size, brand, platform.zone) == layout(*project.size, brand, platforms.zone_of(project))


def _share_cached_segments(project: Project, home: Path) -> None:
    """The same aspect means the same encodes, so hard links save the work. A cache miss still encodes, in its own folder."""
    folder = home / "segments"
    folder.mkdir(exist_ok=True)
    try:
        for segment in (project.work / "segments").glob("*.mp4"):
            os.link(segment, folder / segment.name)
    except OSError:
        pass  # no hard links on this disk, so the segments encode again


def open_version(project: Project, platform: Platform, target: dict[str, Any], *, reuse: bool) -> Version:
    """A fresh workspace with the same cut. A new shape clears the crops, so the frame stage solves them again from the faces."""
    home = project.work / "versions" / platform.name
    shutil.rmtree(home, ignore_errors=True)
    (home / "out").mkdir(parents=True)
    (home / "review").mkdir()
    if generated_dir(project).exists():  # the one folder under work/ a render reads and never writes
        (home / generated_dir(project).name).symlink_to(generated_dir(project))
    same_shape = platform.aspect == project.setting("output", "aspect")
    config = copy.deepcopy(project.config)
    config["output"] = {**config.get("output", {}), "aspect": platform.aspect, "platform": platform.name, "fps": project.fps}
    config["audio"] = {**config.get("audio", {}), "loudness": target["target"], "true_peak": target["true_peak"]}
    if not same_shape:
        config["frame"] = {**config.get("frame", {}), "enabled": True}
    version = Version(root=project.root, config=config, home=home)
    edl = project.edl()
    if same_shape:
        if (project.work / "frame.json").exists():
            shutil.copy(project.work / "frame.json", home / "frame.json")
        _share_cached_segments(project, home)
    else:
        for segment in edl.segments:
            segment.crop, segment.zoom_from, segment.zoom_to = None, 1.0, 1.0
            segment.held = [name for name in segment.held if name == "gain"]
    version.save_edl(edl)
    if reuse:
        shutil.copy(project.timeline_path, version.timeline_path)
        for name in ("plate.mp4", "layers"):
            if (project.work / name).exists():
                (home / name).symlink_to(project.work / name)
    return version


def render_version(version: Version, renderer: str) -> Path:
    return render.build(version, renderer=renderer)


def check_version(version: Version, file: Path) -> list[GateResult]:
    """The gates a platform can change: level, length, zone, faces and format. The rest belong to the master's QA."""
    context = gates.Context.open(version, file)
    return [result for ids, gate in VERSION_GATES for result in gates.guarded(context, ids, gate)]


def _verdict(entry: dict[str, Any]) -> str:
    if entry["passed"]:
        return "Passed."
    if entry["failed"]:
        return f"Failed {', '.join(entry['failed'])}."
    return f"Not checked: {', '.join(entry['not_checked'])}." if entry["not_checked"] else "Missed its loudness."


def make_version(
    project: Project, platform: Platform, folder: Path, seconds: float, override: float | master.Target | None, renderer: str
) -> dict[str, Any]:
    entry: dict[str, Any] = {
        "platform": platform.name,
        "aspect": platform.aspect,
        "size": list(SIZES[platform.aspect]),
        "zone": platform.zone,
        "zone_source": platform.zone_source,
    }
    problem = length_problem(platform, seconds)
    longest, shortest = platform.longest, platform.shortest
    entry["length"] = {"seconds": seconds, "longest": longest, "shortest": shortest, "source": platform.length_source, "passed": problem is None}
    if problem:
        print(f"{platform.name}: not rendered. {problem}")
        return {**entry, "file": None, "passed": False, "problem": problem}
    target = target_for(project, override, platform)
    reuse = same_picture(project, platform) and (project.out / "render.mp4").is_file()
    print(f"{platform.name}: " + ("the master's picture already fits, mastering the sound." if reuse else f"rendering {platform.aspect}."), flush=True)
    with locks.heavy(f"the {platform.name} version"):
        version = open_version(project, platform, target, reuse=reuse)
        source = project.out / "render.mp4" if reuse else render_version(version, renderer)
        dest = folder / f"{platform.name}.mp4"
        master.master_file(source, dest, target["target"], target["true_peak"], silent_film=master.no_sound_in(project))
    results = check_version(version, dest)
    bad, skipped = failed(results), not_run(results)
    loudness = measured(project, dest, target)
    passed = not bad and not skipped and loudness["passed"] is not False
    if passed:
        shutil.rmtree(version.home, ignore_errors=True)
    entry |= {
        "file": dest.name,
        "reused_render": reuse,
        "loudness": loudness,
        "gates": [g.to_dict() for g in results],
        "failed": [g.id for g in bad],
        "not_checked": [g.id for g in skipped],
        "passed": passed,
    }
    print(f"{platform.name}: {FOLDER}/{dest.name}, {_spoken_as(loudness)}. {_verdict(entry)}")
    for gate in bad:
        print(f"  {gate.id}: {gate.detail}")
    if not passed:
        print(f"  Workspace kept at {version.home}")
    return entry


# The stage


def plate_or_final(project: Project, final: Path) -> Path:
    """The plate has no captions or graphics on it, which is what a thumbnail wants."""
    plate = project.work / "plate.mp4"
    return plate if plate.exists() else final


def shots_of(edl: Edl) -> list[tuple[float, float]]:
    ends = list(cut.segment_ends(edl).values())
    return list(zip([0.0, *ends], ends, strict=False))


def write_stems(project: Project, folder: Path) -> dict[str, Any]:
    """The stems of the finished film, remade from the render's own voice track and timeline, so they add up to its mix."""
    voice = project.work / "voice.wav"
    if not voice.exists() or not project.timeline_path.exists():
        raise AltocueError("The stems come from the render's own voice track and timeline, and one is missing.", fix="Run `altocue render`, then deliver again.")
    files = render.write_stems(project, json.loads(project.timeline_path.read_text()), voice, folder / "stems")
    return {"files": [str(path.relative_to(folder)) for path in files], "level": "as mixed, before mastering"}


def run(args: argparse.Namespace) -> int:
    project = Project.open(args.project)
    wanted = platforms.parse(args.platforms) if args.platforms else []
    final = finished_film(project)
    edl = project.edl()
    seconds = ffmpeg.probe_clip(final).duration
    folder = project.root / FOLDER
    folder.mkdir(exist_ok=True)
    stems = write_stems(project, folder) if args.stems else None
    if stems:
        print(f"Stems: {FOLDER}/stems holds voice, music and effects, as mixed and before mastering.")

    words, silent_why = spoken(project, edl)
    srt, subs = write_subtitles(words, edl, folder)
    print(f"Subtitles: {FOLDER}/subtitles.srt and {FOLDER}/subtitles.vtt. {_reading_line(subs['reading'])}" if srt else f"No subtitles. {silent_why}")

    chapters, source = chapter_list(project, words, seconds)
    marks = write_chapters(chapters, source, folder)
    print(_chapters_line(marks))

    film = make_film(project, final, folder, srt, chapters, args.loudness)
    print(f"Film: {FOLDER}/{film['file']}, {_spoken_as(film['loudness'])}")

    pictures = thumbnails.write(plate_or_final(project, final), shots_of(edl), folder / "thumbnails")
    print(f"Thumbnails: {len(pictures['candidates'])} candidates from {pictures['read']} frames. Compare them in {FOLDER}/thumbnails/{pictures['sheet']}")

    digest = sha256(final)
    made = [make_version(project, platform, folder, seconds, args.loudness, args.renderer) for platform in wanted]
    versions = _earlier_versions(folder, digest, made) + made
    report = {"final_sha256": digest, "subtitles": subs, "chapters": marks, "film": film, "thumbnails": pictures, "versions": versions}
    if stems:
        report["stems"] = stems
    write_json(folder / "deliver.json", report)
    print(f"Saved {FOLDER}/deliver.json")
    missed = film["loudness"]["passed"] is False or any(v.get("failed") or v.get("problem") or v["loudness"]["passed"] is False for v in made)
    return 1 if missed else 0


def _earlier_versions(folder: Path, digest: str, made: list[dict[str, Any]]) -> list[dict[str, Any]]:
    """Versions from an earlier run still stand, if they came from this same film."""
    path = folder / "deliver.json"
    earlier = json.loads(path.read_text()) if path.exists() else {}
    if earlier.get("final_sha256") != digest:
        return []
    fresh = {entry["platform"] for entry in made}
    return [entry for entry in earlier.get("versions", []) if entry["platform"] not in fresh]


def _reading_line(reading: dict[str, Any]) -> str:
    line = f"{reading['cues']} cues, fastest {reading['fastest_cps']:g} characters a second, longest line {reading['longest_line']}."
    return line + (f" {reading['over_limit']} read past {reading['limit_cps']:g} because a cut leaves no room." if reading["over_limit"] else "")


def _chapters_line(marks: dict[str, Any]) -> str:
    if not marks["marks"]:
        return "No chapters. Add chapters.json, or beats.json from the director, to get them."
    if marks["youtube"]["problems"]:
        return f"Chapters: {marks['marks']} marks from {marks['source']}. No YouTube block: {' '.join(marks['youtube']['problems'])}"
    return f"Chapters: {marks['marks']} marks from {marks['source']}, and {FOLDER}/{marks['youtube']['file']} for the YouTube description."
