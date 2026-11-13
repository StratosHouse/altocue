"""Frames for the editor's own eyes: the sources before the cut, and a render after it.

An agent cannot play a video, but it can look at pictures. `altocue watch --sources` lays every line of the
recording out as frames with its words under them, so a take is chosen by how it looks as well as how it reads.
`altocue watch` does the same for a draft or a final: the opening, both sides of every cut, each graphic once it
has settled, each insert's first and last frame, and the ending. Everything lands in `work/watch/`, with an
index that says what each sheet is for. Nothing here judges a frame. It only makes looking cheap.
"""

from __future__ import annotations

import argparse
import shutil
import tempfile
from pathlib import Path
from typing import Any

import numpy as np
from PIL import Image, ImageDraw, ImageFont

from . import ffmpeg
from .brand import data_path
from .errors import AltocueError
from .models import Clip, Word
from .project import Project, write_json

FONT = ("fonts", "Geist-Variable.ttf")
SHEET = 1560  # the long side most vision models take without shrinking the picture
INK, PANEL, TEXT, MUTED, MARK = (8, 13, 19), (17, 25, 32), (243, 247, 250), (143, 160, 173), (181, 244, 217)
STRIP = 26  # one line of label under a tile
OPENING = (0.0, 0.25, 0.5, 1.0, 1.5, 2.0, 3.0, 4.0)
SETTLED = 0.6  # how far through a graphic it is looked at: past its arrival, before it leaves
EDGE = 0.12  # seconds inside a line or an insert, so a frame shows the line and not its neighbour
LEVEL_RATE = 16000
MOST_LINES = 240  # past this many lines a first look should be narrowed with --clip, --from and --to


def add_arguments(name: str, parser: argparse.ArgumentParser) -> None:
    parser.add_argument("--project", "-p", default=".", help="Project folder")
    parser.add_argument("--sources", action="store_true", help="Look at the recordings line by line, before or during the cut")
    parser.add_argument("--clip", help="With --sources: only this clip")
    parser.add_argument("--from", dest="start", type=float, default=None, help="With --sources: only lines that start at or after this second of the clip")
    parser.add_argument("--to", dest="end", type=float, default=None, help="With --sources: only lines that start before this second of the clip")
    parser.add_argument("--file", help="The render to look at. Defaults to the newest of out/final.mp4 and out/draft.mp4")
    parser.add_argument("--at", type=float, nargs="+", metavar="SECONDS", help="Full-size frames of the render at these seconds, and nothing else")
    parser.add_argument("--every", type=float, default=0.0, help="Also write contact sheets of the render with one frame every this many seconds")
    parser.add_argument("--json", action="store_true", help="Print the index as JSON")


def _font(size: int) -> ImageFont.FreeTypeFont | ImageFont.ImageFont:
    try:
        return ImageFont.truetype(str(data_path(*FONT)), size)
    except OSError:
        return ImageFont.load_default(size=size)


def stamp(seconds: float) -> str:
    minutes, rest = divmod(max(seconds, 0.0), 60)
    return f"{int(minutes)}:{rest:05.2f}"


def fit(text: str, font: ImageFont.FreeTypeFont | ImageFont.ImageFont, width: int, lines: int) -> list[str]:
    """Wrap to at most `lines` rows of `width` pixels. What does not fit ends in three dots, so nothing is silently lost."""
    rows: list[str] = []
    row = ""
    for word in text.split():
        trial = f"{row} {word}".strip()
        if font.getlength(trial) <= width or not row:
            row = trial
            continue
        rows.append(row)
        row = word
    rows.append(row)
    if len(rows) > lines:
        rows = rows[:lines]
        while rows[-1] and font.getlength(rows[-1] + "...") > width:
            rows[-1] = rows[-1][:-1]
        rows[-1] = rows[-1].rstrip() + "..."
    return [r for r in rows if r]


def tile(frames: list[Image.Image], label: str, text: str = "", text_lines: int = 0) -> Image.Image:
    """Frames side by side over a label, and under that the words, when there are any."""
    seam = 2  # a hairline between frames, so two sides of a cut never read as one picture
    width = sum(f.width for f in frames) + seam * (len(frames) - 1)
    height = max(f.height for f in frames)
    small = _font(15)
    wrapped = fit(text, small, width - 12, text_lines) if text and text_lines else []
    out = Image.new("RGB", (width, height + STRIP + 19 * text_lines + (6 if text_lines else 0)), PANEL)
    x = 0
    for frame in frames:
        out.paste(frame, (x, 0))
        x += frame.width + seam
    draw = ImageDraw.Draw(out)
    draw.text((6, height + 4), label, font=_font(16), fill=MARK)
    for index, row in enumerate(wrapped):
        draw.text((6, height + STRIP + 19 * index), row, font=small, fill=TEXT)
    return out


def sheets_of(tiles: list[Image.Image], dest: Path, stem: str) -> list[Path]:
    """Pack same-sized tiles onto as few sheets as stay readable. Returns the sheets written."""
    if not tiles:
        return []
    width, height = tiles[0].size
    gap = 6
    columns = max(1, (SHEET + gap) // (width + gap))
    rows = max(1, (SHEET + gap) // (height + gap))
    per = columns * rows
    written = []
    for first in range(0, len(tiles), per):
        batch = tiles[first : first + per]
        used = (len(batch) + columns - 1) // columns
        sheet = Image.new("RGB", (columns * (width + gap) - gap, used * (height + gap) - gap), INK)
        for index, one in enumerate(batch):
            sheet.paste(one, (index % columns * (width + gap), index // columns * (height + gap)))
        path = dest / f"{stem}-{len(written) + 1:02d}.jpg"
        sheet.save(path, quality=88)
        written.append(path)
    return written


def blank(size: tuple[int, int]) -> Image.Image:
    return Image.new("RGB", size, (40, 20, 24))


# The sources, line by line


def frame_size(clip: Clip) -> tuple[int, int]:
    """A frame small enough that three sit in a tile and several tiles sit on a sheet, tall or wide."""
    width, height = clip.display_size
    long_side = 256 if width >= height else 288
    factor = long_side / max(width, height)
    return max(2, round(width * factor / 2) * 2), max(2, round(height * factor / 2) * 2)


def source_frame(clip: Clip, at: float, size: tuple[int, int]) -> tuple[Image.Image, np.ndarray | None]:
    """One upright display-ready frame as a picture, with its pixels for measuring. A frame that cannot be read is a dark tile."""
    from . import frame as framing

    pixels = framing.read_sdr(clip, min(max(at, 0.0), max(clip.duration - 0.05, 0.0)), side=max(size))
    if pixels is None:
        return blank(size), None
    image = Image.fromarray((np.clip(pixels, 0, 1) * 255 + 0.5).astype(np.uint8), "RGB")
    return (image if image.size == size else image.resize(size)), pixels


def measure_picture(pixels: np.ndarray | None) -> dict[str, Any]:
    """What a still can say in numbers: how bright, how sharp, and where the largest face sits. None when no face is found."""
    if pixels is None:
        return {"brightness": None, "sharpness": None, "faces": 0, "face_height": None, "eye_line": None, "face_x": None}
    import cv2

    from . import frame as framing

    height, width = pixels.shape[:2]
    luma = pixels[..., 0] * 0.2126 + pixels[..., 1] * 0.7152 + pixels[..., 2] * 0.0722
    grey = (np.clip(luma, 0, 1) * 255).astype(np.uint8)
    found = framing.detect_faces(pixels)
    face = found[0] if found else None
    return {
        "brightness": round(float(luma.mean()), 3),
        "sharpness": round(float(cv2.Laplacian(grey, cv2.CV_64F).var()), 1),
        "faces": len(found),
        "face_height": round(face.h / height, 3) if face else None,
        "eye_line": round(face.eye_y / height, 3) if face else None,
        "face_x": round(face.center[0] / width, 3) if face else None,
    }


def level_db(samples: np.ndarray | None, rate: int, start: float, end: float) -> tuple[float | None, float | None]:
    """RMS and peak of one stretch, in dB below full scale."""
    if samples is None:
        return None, None
    part = samples[int(max(start, 0) * rate) : int(max(end, 0) * rate)]
    if not len(part):
        return None, None
    rms, peak = float(np.sqrt(np.mean(np.square(part)))), float(np.max(np.abs(part)))
    return (round(20 * np.log10(max(rms, 1e-6)), 1), round(20 * np.log10(max(peak, 1e-6)), 1))


def clip_samples(clip: Clip, scratch: Path) -> np.ndarray | None:
    if not clip.has_audio:
        return None
    try:
        return ffmpeg.read_wav(ffmpeg.extract_wav(clip.path, scratch / f"{clip.id}.wav", rate=LEVEL_RATE))[0]
    except AltocueError:
        return None


def kept_marks(project: Project) -> dict[str, list[tuple[float, float]]]:
    """Which source ranges the current cut plays, so a line can be marked as in or out. Empty before any cut."""
    if not project.edl_path.exists():
        return {}
    marks: dict[str, list[tuple[float, float]]] = {}
    for segment in project.edl().segments:
        marks.setdefault(segment.clip, []).append((segment.start, segment.end))
    return marks


def in_cut(marks: dict[str, list[tuple[float, float]]], clip: str, start: float, end: float) -> str:
    if not marks:
        return ""
    middle = (start + end) / 2
    return "in" if any(a <= middle <= b for a, b in marks.get(clip, [])) else "out"


def watch_sources(project: Project, dest: Path, *, only: str | None = None, start: float | None = None, end: float | None = None) -> dict[str, Any]:
    from .cut import script

    lines = script.all_lines(project)
    clips = {clip.id: clip for clip in project.clips()}
    if only and only not in clips:
        raise AltocueError(f"No clip named {only}.", fix=f"Use one of: {', '.join(clips)}.")
    words: dict[str, list[Word]] = {name: project.words(name) for name in clips}
    marks = kept_marks(project)
    chosen = []
    for line in lines:
        if line.silent or (only and line.clip != only):
            continue
        first = words[line.clip][line.first].start
        if (start is not None and first < start) or (end is not None and first >= end):
            continue
        chosen.append(line)
    if not chosen:
        raise AltocueError("No spoken lines to look at in that range.", fix="Run `altocue transcribe` first, or widen --from and --to.")
    if len(chosen) > MOST_LINES:
        raise AltocueError(
            f"{len(chosen)} lines is too many to look at in one go.",
            fix=f"Narrow it with --clip, --from and --to, {MOST_LINES} lines or fewer at a time. `altocue clips find` reads a long recording by its words first.",
        )
    rows: list[dict[str, Any]] = []
    written: list[dict[str, Any]] = []
    with tempfile.TemporaryDirectory(prefix="altocue-watch-") as temp:
        scratch = Path(temp)
        for name, clip in clips.items():
            mine = [line for line in chosen if line.clip == name]
            if not mine:
                continue
            size = frame_size(clip)
            samples = clip_samples(clip, scratch)
            tiles = []
            previous_end = 0.0
            for line in mine:
                said = words[name][line.first : line.last + 1]
                first, last = said[0].start, said[-1].end
                seconds = max(last - first, 0.01)
                times = [first + min(EDGE, seconds / 4), (first + last) / 2, last - min(EDGE, seconds / 4)]
                shots = [source_frame(clip, at, size) for at in times]
                rms, peak = level_db(samples, LEVEL_RATE, first, last)
                picture = measure_picture(shots[1][1])
                state = in_cut(marks, name, first, last)
                text = " ".join(word.text for word in said)
                label = f"{line.tag}  {stamp(first)}  {seconds:.1f}s" + (f"  {state.upper()}" if state else "")
                tiles.append(tile([shot[0] for shot in shots], label, text, text_lines=2))
                rows.append(
                    {
                        "line": line.tag,
                        "clip": name,
                        "start": round(first, 3),
                        "end": round(last, 3),
                        "seconds": round(seconds, 3),
                        "words": len(said),
                        "words_per_second": round(len(said) / seconds, 2),
                        "pause_before": round(max(first - previous_end, 0.0), 3),
                        "level_db": rms,
                        "peak_db": peak,
                        "cut": state,
                        "text": text,
                        **picture,
                    }
                )
                previous_end = last
            for path in sheets_of(tiles, dest, f"sources-{name}"):
                written.append({"file": path.name, "shows": f"Lines of {name}: the start, middle and end of each, with its words", "pass": "selects"})
    write_json(dest / "sources.json", {"note": NOTE_SOURCES, "lines": rows})
    return {"kind": "sources", "sheets": written, "data": "sources.json", "lines": len(rows)}


NOTE_SOURCES = (
    "Measured from three frames and the sound of each line. brightness runs 0 to 1. sharpness is the variance of the Laplacian, "
    "only comparable between lines of one clip. face_height, eye_line and face_x are shares of the frame for the largest face, "
    "and null means no face was found in the middle frame. level_db and peak_db are dB below full scale. Numbers rank nothing: look at the frames."
)


# A render


def newest_render(project: Project) -> Path:
    found = [path for path in (project.out / "final.mp4", project.out / "draft.mp4") if path.exists()]
    if not found:
        raise AltocueError("Nothing has been rendered yet.", fix="Run `altocue render --draft` first. For the recordings themselves, `altocue watch --sources`.")
    return max(found, key=lambda path: path.stat().st_mtime)


def render_frame(file: Path, at: float, scratch: Path, name: str, width: int) -> Image.Image | None:
    from .qa import gates

    got = gates.grab(file, max(at, 0.0), scratch / f"{name}.jpg", width=width)
    if not got:
        return None
    with Image.open(got) as image:
        return image.convert("RGB")


def tile_width(clip: Clip, across: int) -> int:
    """Frame width so `across` frames fill a sheet's width, never larger than the film."""
    width, height = clip.display_size
    wanted = SHEET // across
    if height > width:
        wanted = min(wanted, 300)
    return max(2, min(wanted, width) // 2 * 2)


def describe(item: dict[str, Any]) -> str:
    for key in ("text", "headline", "title", "value", "label"):
        value = item.get(key)
        if isinstance(value, str) and value.strip():
            return value.replace("\n", " / ").replace("*", "")
    return ""


def watch_render(project: Project, file: Path, dest: Path, *, every: float = 0.0) -> dict[str, Any]:
    from .qa import gates

    stage = "draft" if "draft" in file.name else "final"
    context = gates.Context.open(project, file, stage)
    clip, fps = context.clip, context.fps
    duration = clip.duration
    timeline = context.timeline or {}
    cuts = [round(shot.start, 3) for shot in context.shots[1:]]
    written: list[dict[str, Any]] = []

    def keep(paths: list[Path], shows: str, which: str) -> None:
        written.extend({"file": path.name, "shows": shows, "pass": which} for path in paths)

    with tempfile.TemporaryDirectory(prefix="altocue-watch-") as temp:
        scratch = Path(temp)
        wide = tile_width(clip, 4)
        opening = []
        for index, at in enumerate(t for t in OPENING if t < duration):
            image = render_frame(file, at, scratch, f"o{index}", wide)
            if image:
                opening.append(tile([image], stamp(at)))
        keep(sheets_of(opening, dest, "opening"), "The first seconds, frame by frame: what a stranger sees before deciding to stay", "opening")

        half = tile_width(clip, 6)
        pairs = []
        for index, at in enumerate(cuts):
            before = render_frame(file, at - 1 / fps - 0.001, scratch, f"a{index}", half)
            after = render_frame(file, at - 0.001 + 1 / fps / 2, scratch, f"b{index}", half)
            if before and after:
                pairs.append(tile([before, after], f"cut {index + 1} at {stamp(at)}"))
        keep(sheets_of(pairs, dest, "cuts"), "Each cut: the last frame before it and the first frame after", "joins")

        graphics = []
        for index, item in enumerate(timeline.get("items", [])):
            start, end = float(item.get("start", 0)), float(item.get("end", 0))
            image = render_frame(file, min(start + (end - start) * SETTLED, duration - 1 / fps), scratch, f"g{index}", wide)
            if image:
                graphics.append(tile([image], f"{item.get('type', 'graphic')} {stamp(start)} to {stamp(end)}", describe(item), text_lines=2))
        keep(sheets_of(graphics, dest, "graphics"), "Each graphic once it has settled, with its copy underneath", "text")

        inserts = []
        for index, item in enumerate(timeline.get("inserts", [])):
            start, end = float(item.get("start", 0)), float(item.get("end", 0))
            first = render_frame(file, start + 1 / fps / 2, scratch, f"i{index}a", half)
            last = render_frame(file, end - 1 / fps - 0.001, scratch, f"i{index}b", half)
            if first and last:
                inserts.append(tile([first, last], f"insert {stamp(start)} to {stamp(end)}", Path(str(item.get("file", ""))).name, text_lines=1))
        keep(sheets_of(inserts, dest, "inserts"), "Each insert's first and last frame", "picture")

        ending = []
        for index, at in enumerate(sorted({max(duration - 2.0, 0.0), max(duration - 1.0, 0.0), max(duration - 0.5, 0.0), max(duration - 1 / fps - 0.002, 0.0)})):
            image = render_frame(file, at, scratch, f"e{index}", wide)
            if image:
                ending.append(tile([image], stamp(at)))
        keep(sheets_of(ending, dest, "ending"), "The last two seconds: where the film leaves the viewer", "ending")

        if every > 0:
            from . import sheets

            made = sheets.contact_sheets(file, dest / "every", every=every)
            written.extend({"file": f"every/{path.name}", "shows": f"One frame every {every:g} s", "pass": "picture"} for path in made)
    return {"kind": "render", "file": str(file), "duration": round(duration, 3), "cuts": cuts, "sheets": written}


def watch_moments(file: Path, dest: Path, times: list[float]) -> dict[str, Any]:
    """Full-size frames at exact seconds, for a closer look at one moment."""
    from .qa import gates

    written = []
    for at in times:
        name = f"at-{at:08.3f}".replace(".", "_") + ".jpg"
        if gates.grab(file, max(at, 0.0), dest / name):
            written.append({"file": name, "shows": f"The frame at {stamp(at)}, full size", "pass": "moment"})
    if not written:
        raise AltocueError(f"No frame could be read from {file} at those times.", fix="Check the times are inside the film.")
    return {"kind": "moments", "file": str(file), "sheets": written}


PASSES = {
    "selects": "Choose takes by eye as well as by ear: eyes on the lens, energy, a clean first and last frame.",
    "opening": "Does frame 0 show the subject, do words arrive in the first second, is the promise readable by three seconds?",
    "joins": "At each cut: no closed eyes on the first frame, the eyes stay in place across a punch-in, nothing stray.",
    "text": "Each graphic: readable at phone size, clear of the face and the captions, saying what the speaker says.",
    "picture": "Skin, exposure and framing hold from shot to shot. Each insert shows what is being said.",
    "ending": "The last word has finished, the end card is short, and the final frame is one worth leaving on.",
    "moment": "Say what is in the frame before changing anything.",
}


def index_text(found: dict[str, Any]) -> str:
    lines = ["# What to look at", "", "View each file below with your own file-reading tool. Looking at stills is not watching: say so in your report.", ""]
    if found.get("file"):
        lines += [f"Film: `{Path(found['file']).name}`" + (f", {found['duration']:g} s, {len(found.get('cuts', []))} cuts" if "duration" in found else ""), ""]
    seen: list[str] = []
    for row in found["sheets"]:
        if row["pass"] not in seen:
            seen.append(row["pass"])
    for which in seen:
        lines += [f"## {which.capitalize()}", "", PASSES[which], ""]
        lines += [f"- `{row['file']}`: {row['shows']}" for row in found["sheets"] if row["pass"] == which]
        lines.append("")
    if found.get("data"):
        lines += [f"Numbers for every line are in `{found['data']}`. They rank nothing. Use them to find lines worth a closer look.", ""]
    return "\n".join(lines)


def run(args: argparse.Namespace) -> int:
    import json

    project = Project.open(args.project)
    dest = project.work / "watch"
    if args.sources:
        folder = dest / "sources"
        shutil.rmtree(folder, ignore_errors=True)
        folder.mkdir(parents=True)
        found = watch_sources(project, folder, only=args.clip, start=args.start, end=args.end)
    else:
        file = Path(args.file).expanduser().resolve() if args.file else newest_render(project)
        if not file.is_file():
            raise AltocueError(f"No file at {file}.", fix="Check the path, or render with `altocue render --draft`.")
        folder = dest / ("moments" if args.at else "render")
        if not args.at:
            shutil.rmtree(folder, ignore_errors=True)
        folder.mkdir(parents=True, exist_ok=True)
        found = watch_moments(file, folder, args.at) if args.at else watch_render(project, file, folder, every=args.every)
    found["folder"] = str(folder)
    (folder / "index.md").write_text(index_text(found))
    write_json(folder / "index.json", found)
    if args.json:
        print(json.dumps(found, indent=2))
        return 0
    print(f"{len(found['sheets'])} sheets in {folder}")
    for row in found["sheets"]:
        print(f"  {row['file']}: {row['shows']}")
    print(f"Read {folder / 'index.md'} for what to check in each. View the sheets with your own file-reading tool.")
    return 0
