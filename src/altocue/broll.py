"""Find the spots that want B-roll, then fill them from your own clips, stock or generation."""

from __future__ import annotations

import argparse
import hashlib
import json
import os
import re
import shutil
from pathlib import Path
from typing import Any

from PIL import Image, ImageDraw

from . import cut, ffmpeg, grade, stock
from . import timeline as timelines
from .cut.snap import frame_count
from .errors import AltocueError
from .generate import http
from .project import Project, write_json
from .stock import archive as archive_source
from .stock import base as stock_base
from .stock import credits as asset_credits
from .stock import pexels as pexels_source
from .stock import pixabay as pixabay_source
from .stock import stills

SPOTS_FILE = "broll/spots.json"
WALL_FILE = "broll/wall.json"
WALL_FOLDER = "broll/wall"
WALL_COUNT = 12
CHOSEN_FILE = "broll.json"
VIDEO_TYPES = {".mp4", ".mov", ".m4v", ".webm"}

SHORTEST = 1.6
LONGEST = 3.2
AFTER_HOOK = 3.0  # the viewer meets the speaker first
BEFORE_END = 1.2
MIN_GAP = 3.0
STATIC_AFTER = 3.5  # seconds with no cut before a stretch reads as static
DEFAULT_SHARE = 0.3
PHRASE_GAP = 0.35
OPTIONS_PER_SPOT = 4
TRUE_COLOUR = frozenset({"screen", "screencast", "screenshot", "screenrecording", "ui"})  # tags of a picture that must keep its own colour

PEXELS_SEARCH = pexels_source.VIDEOS
PIXABAY_SEARCH = pixabay_source.VIDEOS
PEXELS_LICENCE = pexels_source.LICENCE
PIXABAY_LICENCE = pixabay_source.LICENCE
SEARCHES = ("nasa", "wikimedia", "met", "cleveland", "pixabay", "pexels", "archive", "auto")
KEY_HELP = {
    "pexels": "Pexels has paused new keys, so use --search pixabay if you have none.",
    "pixabay": "Get a free key at https://pixabay.com/api, then set PIXABAY_API_KEY.",
}
SPOT_ID = re.compile(r"b\d{1,4}")
OPTION_ID = re.compile(r"c\d{1,4}")
UNTRUSTED_NOTE = stock_base.UNTRUSTED_NOTE

COMMON = frozenset(
    """about after again also always anything because been before being better could doing does done down each even every
    everyone everything first from going gonna good great have here into just know like little look lots made make many might
    more most much must need never next nothing only other over part people really right said same should since some someone
    something still such sure take than that their them then there these they thing things think this those through time today
    together under until very want well were what when where which while will with within without would your yours you're
    it's i'm we're that's there's here's""".split()
)


def add_arguments(name: str, parser: argparse.ArgumentParser) -> None:
    parser.add_argument("--project", "-p", default=".", help="Project folder")
    parser.add_argument("--library", nargs="+", default=None, metavar="FOLDER", help="Your own clips to search first")
    parser.add_argument("--search", choices=SEARCHES, default=None, help="Search stock. Sends the search words, never footage. auto needs no key")
    parser.add_argument("--kind", choices=["video", "photo"], default=None, help="Only video or only photos. By default video comes first")
    parser.add_argument("--rights-checked", action="store_true", help="You have checked the rights of an Internet Archive pick yourself")
    parser.add_argument("--pick", nargs="+", default=None, metavar="SPOT=CHOICE", help="b1=c2, b2=skip, b3=shot:shot-1 or b4=file:clip.mp4")
    parser.add_argument("--share", type=float, default=None, help="Most of the runtime B-roll may cover, such as 0.3")
    parser.add_argument("--wall", metavar="WORDS", default=None, help="Search stock for these words and keep a frame of each result with its licence, for a film")
    parser.add_argument("--count", type=int, default=WALL_COUNT, help="With --wall: how many results to keep")


def keywords(text: str) -> list[str]:
    """The words worth searching on, most specific first, each once."""
    seen: dict[str, None] = {}
    for token in re.findall(r"[a-z][a-z'-]+", text.lower()):
        if len(token) >= 4 and token not in COMMON:
            seen.setdefault(token)
    return sorted(seen, key=len, reverse=True)


def phrase_ends(words: list[dict[str, Any]]) -> set[int]:
    """The words a phrase stops on, by punctuation or by a breath."""
    last = len(words) - 1
    return {i for i, word in enumerate(words) if i == last or word["text"].rstrip()[-1:] in ".?!;:," or words[i + 1]["start"] - word["end"] > PHRASE_GAP}


def windows(words: list[dict[str, Any]]) -> list[tuple[int, int]]:
    """Every run of whole words that lasts a sensible time for one insert. Long sentences get windows too."""
    found = []
    for first, opening in enumerate(words):
        for last in range(first, len(words)):
            length = words[last]["end"] - opening["start"]
            if length > LONGEST:
                break
            if length >= SHORTEST:
                found.append((first, last))
    return found


def cut_points(project: Project) -> list[float]:
    edl = project.edl()
    points, at = [], 0.0
    for segment in edl.segments[:-1]:
        at += frame_count(segment.start, segment.end, edl.fps) / edl.fps
        points.append(at)
    return points


def busy_spans(project: Project, words: list[dict[str, Any]], duration: float) -> tuple[list[tuple[float, float]], float]:
    """Graphics that carry meaning stay on the speaker, and hand-placed inserts keep their place. Chips can sit over B-roll."""
    path = project.root / "motion.json"
    motion = json.loads(path.read_text()) if path.exists() else {}
    items, _ = timelines.resolve_items(motion.get("items", []), words, duration)
    placed = [(i["start"], i["end"]) for i in timelines.resolve_inserts(project, motion.get("inserts", []), words)]
    return [(item["start"], item["end"]) for item in items if item["type"] != "chip"] + placed, sum(b - a for a, b in placed)


def score(window: list[dict[str, Any]], whole: bool, cuts: list[float], last_change: float) -> tuple[float, list[str]]:
    start, end = window[0]["start"], window[-1]["end"]
    terms = keywords(" ".join(word["text"] for word in window))
    value, reasons = min(len(terms), 4) + (0.5 if whole else 0.0), []
    if terms:
        reasons.append("names something a viewer could see")
    covered = [point for point in cuts if start + 0.2 < point < end - 0.2]
    if covered:
        value += 1.5
        reasons.append("hides a jump cut")
    if start - last_change >= STATIC_AFTER:
        value += 1.0
        reasons.append("a long stretch with no change on screen")
    return value, reasons


def find_spots(project: Project, share: float | None = None) -> list[dict[str, Any]]:
    """Rank every usable stretch and keep the best that fit the budget. Code proposes, a person or agent decides."""
    edl = project.edl()
    words = [word for word in cut.map_words(project, edl) if word.get("kind", "word") == "word"]
    if not words:
        return []
    duration = round(sum(frame_count(s.start, s.end, edl.fps) for s in edl.segments) / edl.fps, 6)
    cuts = cut_points(project)
    busy, placed = busy_spans(project, words, duration)
    budget = duration * float(share if share is not None else project.setting("broll", "share", DEFAULT_SHARE)) - placed

    ends = phrase_ends(words)
    ranked = []
    for first, last in windows(words):
        window = words[first : last + 1]
        start, end = window[0]["start"], window[-1]["end"]
        if start < AFTER_HOOK or end > duration - BEFORE_END or any(start < b and a < end for a, b in busy):
            continue
        last_change = max((point for point in cuts if point <= start), default=0.0)
        value, reasons = score(window, first - 1 in ends and last in ends, cuts, last_change)
        if reasons:
            ranked.append((value, window, reasons))
    ranked.sort(key=lambda item: (-item[0], item[1][0]["start"]))

    chosen: list[dict[str, Any]] = []
    used = 0.0
    for value, window, reasons in ranked:
        start, end = round(window[0]["start"] * edl.fps) / edl.fps, round(window[-1]["end"] * edl.fps) / edl.fps
        if used + end - start > budget or any(start < s["end"] + MIN_GAP and s["start"] < end + MIN_GAP for s in chosen):
            continue
        text = " ".join(word["text"] for word in window)
        chosen.append({"start": round(start, 6), "end": round(end, 6), "phrase": text, "query": " ".join(keywords(text)[:3]), "why": reasons, "score": value})
        used += end - start
    chosen.sort(key=lambda spot: spot["start"])
    return [{"id": f"b{index}", **spot, "options": []} for index, spot in enumerate(chosen, 1)]


def library_clips(folders: list[str]) -> list[dict[str, Any]]:
    """Index your own clips by name, folder and an optional .txt note beside each file."""
    clips = []
    for folder in folders:
        root = Path(folder).expanduser()
        if not root.is_dir():
            raise AltocueError(f"No folder at {root}.", fix="Pass a folder of video clips to --library.")
        for path in sorted(root.rglob("*")):
            if path.suffix.lower() not in VIDEO_TYPES or path.name.startswith("."):
                continue
            note = path.with_suffix(".txt")
            written = note.read_text(errors="ignore") if note.exists() else ""
            words = f"{path.stem} {path.parent.name} {written}".replace("_", " ").replace("-", " ")
            clips.append({"path": str(path.resolve()), "terms": set(re.findall(r"[a-z]{3,}", words.lower())), "tags": colour_tags(words)})
    return clips


def colour_tags(text: str) -> list[str]:
    """What your own names and notes say about a clip's colour. A stock title is never read this way: "phone screen" is a subject."""
    return sorted(TRUE_COLOUR & set(re.findall(r"[a-z]+", text.lower().replace("_", " ").replace("-", " "))))


def takes_look(spot: dict[str, Any], option: dict[str, Any]) -> bool:
    """`"look": false` on the spot or the option wins. A screen recording or an interface keeps its own colour unless told otherwise."""
    for holder in (spot, option):
        if isinstance(holder.get("look"), bool):
            return holder["look"]
    return not TRUE_COLOUR & {str(tag).lower() for tag in [*option.get("tags", []), option.get("kind", "")]}


def match_library(spot: dict[str, Any], clips: list[dict[str, Any]]) -> list[dict[str, Any]]:
    wanted = set(spot["query"].lower().split()) | set(keywords(spot["phrase"]))
    hits = sorted(((len(wanted & clip["terms"]), clip) for clip in clips), key=lambda item: -item[0])
    found = [{"source": "library", "file": clip["path"], "title": Path(clip["path"]).name, "tags": clip.get("tags", [])} for overlap, clip in hits if overlap]
    return [{key: value for key, value in option.items() if value != []} for option in found][:OPTIONS_PER_SPOT]


def orientation(project: Project) -> str:
    width, height = project.size
    return "portrait" if height > width else "landscape" if width > height else "square"


def pexels(query: str, shape: str, key: str) -> list[dict[str, Any]]:
    return [stock.option(found) for found in pexels_source.search(query, kind="video", shape=shape, limit=OPTIONS_PER_SPOT, key=key, endpoint=PEXELS_SEARCH)]


def pixabay(query: str, shape: str, key: str) -> list[dict[str, Any]]:
    return [stock.option(found) for found in pixabay_source.search(query, kind="video", shape=shape, limit=OPTIONS_PER_SPOT, key=key, endpoint=PIXABAY_SEARCH)]


def narrowed(query: str) -> list[str]:
    """Draft search words are often too specific, so fewer words get a second chance."""
    words = query.split()
    return list(dict.fromkeys(" ".join(words[:count]) for count in (len(words), 2, 1) if count <= len(words)))


def gather(steps: list[tuple[str, str]], query: str, shape: str, *, forgiving: bool, dead: set[str]) -> list[dict[str, Any]]:
    """Ask each source in turn until the spot has its options. Only `auto` moves past a failure."""
    options: list[dict[str, Any]] = []
    for name, kind in steps:
        if name in dead or len(options) >= OPTIONS_PER_SPOT:
            continue
        try:
            found = stock.search(name, query, kind=kind, shape=shape, limit=OPTIONS_PER_SPOT - len(options))
        except AltocueError as error:
            if not forgiving:
                raise
            dead.add(name)
            print(f"Skipped {name} for this run: {str(error).splitlines()[0]}")
            continue
        options += [stock.option(item) for item in found]
    return options


def search(project: Project, spots: list[dict[str, Any]], provider: str, kind: str | None = None) -> None:
    env = stock.SOURCES.get(provider, ((), ""))[1]
    if env and not os.environ.get(env):
        raise AltocueError(f"{env} is not set.", fix=f"{KEY_HELP[provider]} Or use --search auto, --library or generated shots.")
    if provider == "archive":
        print(f"WARNING: {archive_source.WARNING}")
    steps = stock.plan(provider, kind)
    names = {name for name, _ in steps}
    print(f"Searching {', '.join(sorted(names))}. Only the search words are sent, never your footage.")
    shape = orientation(project)
    dead: set[str] = set()
    for spot in spots:
        if not spot["query"]:
            continue
        found: list[dict[str, Any]] = []
        for words in narrowed(spot["query"]):
            found = gather(steps, words, shape, forgiving=provider == "auto", dead=dead)
            if found or len(dead) == len(names):
                break
        spot["options"] = [option for option in spot["options"] if option["source"] not in names] + found
    if dead and len(dead) == len(names):
        raise AltocueError("No stock source answered.", fix="Check the network, or use --library or generated shots.")


def number_options(spots: list[dict[str, Any]]) -> None:
    for spot in spots:
        for index, option in enumerate(spot["options"], 1):
            option["id"] = f"c{index}"


def sheet(project: Project, spots: list[dict[str, Any]]) -> Path | None:
    """One picture of every option, so an agent or a person can choose by eye."""
    tiles: list[tuple[str, Image.Image]] = []
    folder = project.work / "broll" / "previews"
    shutil.rmtree(folder, ignore_errors=True)
    folder.mkdir(parents=True)
    for spot in spots:
        for option in spot["options"]:
            still = folder / f"{spot['id']}-{option['id']}.jpg"
            try:
                if option.get("file"):
                    clip = ffmpeg.probe_clip(option["file"])
                    ffmpeg.frame(option["file"], clip.duration / 2, still, width=320)
                elif option.get("preview"):
                    http.download(option["preview"], still, timeout=60)
                with Image.open(still) as image:
                    tiles.append((f"{spot['id']} {option['id']}", image.convert("RGB").copy()))
            except (AltocueError, OSError):
                continue
    if not tiles:
        return None
    columns = min(4, len(tiles))
    rows = (len(tiles) + columns - 1) // columns
    canvas = Image.new("RGB", (columns * 244 + 4, rows * 260 + 4), "#0c141b")
    draw = ImageDraw.Draw(canvas)
    for index, (label, image) in enumerate(tiles):
        image.thumbnail((240, 236))
        x, y = 4 + (index % columns) * 244, 4 + (index // columns) * 260
        canvas.paste(image, (x, y + 20))
        draw.text((x + 4, y + 4), label, fill="#b5f4d9")
    path = project.work / "broll" / "sheet.jpg"
    canvas.save(path, quality=86)
    return path


plain = stock_base.plain


def option_line(option: dict[str, Any]) -> str:
    credit = f" by {plain(option['author'])}" if option.get("author") else ""
    page = f", {option['page']}" if str(option.get("page", "")).startswith("https://") else ""
    terms = ""
    if option.get("licence"):
        length = f", {option['duration']:.0f} s" if option.get("duration") else ""
        owed = "credit owed" if option.get("credit") else "no credit owed"
        terms = f", {plain(option.get('kind', 'video'), 8)}{length}, {plain(option['licence'], 40)}, {owed}"
    notes = "".join(f" Note: {stock_base.credit_field(note, 160)}" for note in option.get("warnings", []) if note != stock_base.PEOPLE_WARNING)
    return f"- **{option['id']}:** {option['source']}{terms}, {plain(option['title'])}{credit}{page}{notes}"


def write_review(project: Project, spots: list[dict[str, Any]]) -> Path:
    lines = ["# B-roll spots", "", UNTRUSTED_NOTE, "", "Choose with `altocue broll --pick b1=c2 b2=skip`."]
    lines += ["To look for something else, edit `query` in work/broll/spots.json, then run `--search` or `--library` again.", ""]
    if (project.work / "broll" / "sheet.jpg").exists():
        lines += ["Look at every option in `work/broll/sheet.jpg` before choosing.", ""]
    for spot in spots:
        lines += [f"## {spot['id']}: {spot['start']:.2f} to {spot['end']:.2f} s", "", f"- **Under:** {spot['phrase']}"]
        lines += [f"- **Why here:** {', '.join(spot['why'])}", f"- **Search words:** {spot['query'] or '(none)'}"]
        lines += [option_line(option) for option in spot["options"]] or ["- **No options yet.**"]
        lines.append("")
    path = project.work / "broll-review.md"
    path.write_text("\n".join(lines))
    return path


def load_spots(project: Project) -> list[dict[str, Any]]:
    path = project.work / SPOTS_FILE
    if not path.exists():
        raise AltocueError("No B-roll spots yet.", fix="Run `altocue broll` first.")
    saved = json.loads(path.read_text())
    if saved.get("cut") != project.cut_mark():
        raise AltocueError("The cut changed after these spots were found.", fix="Run `altocue broll` again.")
    spots = saved["spots"]
    for spot in spots:
        ids = [option.get("id", "") for option in spot["options"]]
        if not SPOT_ID.fullmatch(str(spot["id"])) or not all(OPTION_ID.fullmatch(str(value)) for value in ids):
            raise AltocueError(f"work/{SPOTS_FILE} has a bad id: {plain(spot['id'])}.", fix="Run `altocue broll` again.")
    return spots


def resolve_choice(project: Project, spot: dict[str, Any], choice: str) -> dict[str, Any]:
    if choice.startswith("file:"):
        path = Path(choice[5:]).expanduser()
        path = path if path.is_absolute() else project.root / path
        return {"source": "file", "file": str(path), "title": path.name, "tags": colour_tags(f"{path.stem} {path.parent.name}")}
    if choice.startswith("shot:"):
        from . import generate

        asset = generate.generated_asset(project, choice[5:])
        return {"source": "generated", "file": str(asset), "title": choice[5:]}
    for option in spot["options"]:
        if option["id"] == choice:
            return option
    raise AltocueError(f"{spot['id']} has no option {choice}.", fix="See work/broll-review.md.")


def require_video(path: Path) -> None:
    """A downloaded file must really be a video, because ffmpeg would follow a playlist hidden inside one."""
    with path.open("rb") as handle:
        head = handle.read(12)
    if head[4:8] != b"ftyp" and head[:4] not in {b"\x1a\x45\xdf\xa3", b"OggS"}:
        path.unlink()
        raise AltocueError("That download is not a video file.", fix="Pick another option.")


def is_still(path: Path, option: dict[str, Any]) -> bool:
    return option.get("kind") == "photo" or path.suffix.lower() in timelines.STILL_TYPES


def source_path(project: Project, option: dict[str, Any]) -> Path:
    if option.get("file"):
        return Path(option["file"])
    name = hashlib.sha256(option["url"].encode()).hexdigest()[:16]
    return project.work / "broll" / "sources" / f"{name}{http.suffix_of(option['url'], 'photo' if option.get('kind') == 'photo' else 'video')}"


def require_clear(option: dict[str, Any], rights_checked: bool) -> None:
    """A stock file reaches a project only under a licence this code can name, and never from a source it does not know."""
    title = plain(option.get("title", "That option"))
    if option["source"] not in stock.SOURCES or not stock_base.clear(option.get("licence", "")):
        raise AltocueError(f"{title} has no clear licence, so it cannot join a project.", fix="Pick another option.")
    if option["licence"] == stock_base.UNVERIFIED and not rights_checked:
        raise AltocueError(f"{title}: {archive_source.WARNING}", fix="Pick another option, or check the rights yourself and add --rights-checked.")


def fetch_option(project: Project, option: dict[str, Any], source: Path) -> None:
    """Download a stock pick once, prove what it is, and write what it owes."""
    if not source.exists():
        http.download(option["url"], source)
        (stills.require_picture if option.get("kind") == "photo" else require_video)(source)
    asset_credits.record(
        project,
        file=str(source.relative_to(project.root)),
        source=option["source"],
        source_url=option.get("page", ""),
        licence=option["licence"],
        licence_url=option.get("licence_url", ""),
        credit=option.get("credit", ""),
        kind=option.get("kind", "video"),
        title=option.get("title", ""),
        warnings=option.get("warnings", ()),
    )


def prepare(project: Project, spot: dict[str, Any], option: dict[str, Any]) -> Path:
    """Cut the insert to length and to the canvas, so the final render only has to lay it in."""
    source = source_path(project, option)
    if option.get("url"):
        fetch_option(project, option, source)
    if not source.exists():
        raise AltocueError(f"No clip at {source}.", fix="Check the path, or pick another option.")
    return encode_insert(project, spot["id"], spot["end"] - spot["start"], source, still=is_still(source, option), look=takes_look(spot, option))


def encode_insert(project: Project, spot_id: str, length: float, source: Path, *, still: bool, look: bool) -> Path:
    """The insert as the render lays it in: on the canvas, the right length, and in the project's look unless it keeps its own colour."""
    folder = project.work / "broll"
    if still:
        lut = grade.insert_lut(project, None, folder / f"{spot_id}.cube", look=look)
        return stills.push_in(source, folder / f"{spot_id}.mp4", project.size, project.fps, max(1, round(length * project.fps)), lut=lut)
    clip = ffmpeg.probe_clip(source)
    if clip.duration < length:
        raise AltocueError(f"{clip.id} lasts {clip.duration:.1f} s, and {spot_id} needs {length:.1f} s.", fix="Pick a longer clip.")
    width, height = project.size
    fps = project.fps
    lut = grade.insert_lut(project, clip, folder / f"{spot_id}.cube", look=look)  # HDR comes down to SDR first, and the look goes on that
    fill = f"scale={width}:{height}:force_original_aspect_ratio=increase:flags=lanczos,crop={width}:{height}"
    chain = f"{grade.video_filter(clip, lut, geometry=fill)},fps={fps},setsar=1"
    begin = max(0.0, (clip.duration - length) / 2)
    dest = folder / f"{spot_id}.mp4"
    frames = max(1, round(length * fps))
    encode = ["-c:v", "libx264", "-crf", "17", "-preset", "medium", "-pix_fmt", "yuv420p"]
    ffmpeg.run(["-ss", f"{begin:.3f}", "-i", str(source), "-an", "-vf", chain, "-frames:v", str(frames), *encode, str(dest)])
    video = next((s for s in ffmpeg.probe_json(dest).get("streams", []) if s.get("codec_type") == "video"), {})
    if int(video.get("nb_frames", 0)) < frames:  # sound can outlast picture, so the file's length proves nothing
        dest.unlink()
        raise AltocueError(f"{clip.id} has too little picture for {spot_id}.", fix="Pick a longer clip.")
    return dest


def match_look(project: Project) -> list[str]:
    """Prepare again every chosen insert that was made under another look, so the film is one picture. Returns the spots redone.

    Each pick records the look it was prepared in. That mark is the key: a new look, or none, makes the insert again from its source.
    """
    path = project.root / CHOSEN_FILE
    chosen = json.loads(path.read_text()) if path.exists() else {}
    if not chosen.get("inserts") or chosen.get("cut") != project.cut_mark():
        return []  # nothing chosen, or chosen for another cut, which the timeline refuses in its own words
    mark = grade.look_mark(project)
    redone, unknown = [], []
    for insert in chosen["inserts"]:
        if insert.get("look") is False or insert.get("look") == mark:
            continue
        if "look" not in insert or not insert.get("prepared_from"):
            unknown += [insert["id"]] if mark else []
            continue
        source = project.root / insert["prepared_from"]  # an absolute path stands as it is
        if not source.exists():
            fix = f"Pick it again: `altocue broll --pick {insert['id']}=...`."
            raise AltocueError(f"{insert['id']} was prepared under another look, and its source {source} is gone.", fix=fix)
        encode_insert(project, insert["id"], insert["end"] - insert["start"], source, still=bool(insert.get("still")), look=True)
        insert["look"] = mark
        redone.append(insert["id"])
    if redone:
        write_json(path, chosen)
        print(f"The look changed, so {', '.join(redone)} {'was' if len(redone) == 1 else 'were'} prepared again in it.")
    if unknown:
        print(f"{', '.join(unknown)}: picked before inserts took the project's look. Pick again with `altocue broll --pick` to give it the look.")
    return redone


def apply_picks(project: Project, picks: list[str], rights_checked: bool = False) -> list[dict[str, Any]]:
    spots = {spot["id"]: spot for spot in load_spots(project)}
    chosen_path = project.root / CHOSEN_FILE
    mark = project.cut_mark()
    earlier = json.loads(chosen_path.read_text()) if chosen_path.exists() else {}
    chosen = {item["id"]: item for item in earlier.get("inserts", [])} if earlier.get("cut") == mark else {}
    for pick in picks:
        if "=" not in pick:
            raise AltocueError(f"Cannot read {pick}.", fix="Use the form b1=c2, b2=skip, b3=shot:shot-1 or b4=file:clip.mp4.")
        spot_id, choice = pick.split("=", 1)
        if spot_id not in spots:
            raise AltocueError(f"No spot named {spot_id}.", fix="See work/broll-review.md.")
        if choice == "skip":
            chosen.pop(spot_id, None)
            continue
        spot, option = spots[spot_id], resolve_choice(project, spots[spot_id], choice)
        if option.get("url"):
            require_clear(option, rights_checked)
        file = prepare(project, spot, option)
        credit = {key: option[key] for key in ("source", "title", "page", "author", "licence", "licence_url", "credit") if option.get(key)}
        source = source_path(project, option)
        if option.get("url"):
            credit["source_file"] = str(source.relative_to(project.root))
        inside = source.resolve().is_relative_to(project.root.resolve())
        credit["prepared_from"] = str(source.resolve().relative_to(project.root.resolve()) if inside else source.resolve())
        credit |= {"still": is_still(source, option), "look": grade.look_mark(project) if takes_look(spot, option) else False}
        chosen[spot_id] = {"id": spot_id, "file": str(file.relative_to(project.root)), "start": spot["start"], "end": spot["end"], "under": spot["phrase"], **credit}
    inserts = sorted(chosen.values(), key=lambda item: item["start"])
    write_json(chosen_path, {"cut": mark, "inserts": inserts})
    write_credits(project, inserts)
    return inserts


def drop_stale_picks(project: Project, spots: list[dict[str, Any]]) -> None:
    """New spots replace the old ones, so a pick stays only if its spot is still there."""
    path = project.root / CHOSEN_FILE
    if not path.exists():
        return
    chosen = json.loads(path.read_text())
    mark = project.cut_mark()
    current = {(spot["id"], spot["start"], spot["end"]) for spot in spots}
    inserts = chosen.get("inserts", [])
    kept = [i for i in inserts if (i["id"], i["start"], i["end"]) in current] if chosen.get("cut") == mark else []
    if len(kept) != len(inserts):
        print(f"{len(inserts) - len(kept)} earlier picks no longer match a spot, so they were dropped.")
    write_json(path, {"cut": mark, "inserts": kept})


def wall(project: Project, query: str, provider: str, count: int) -> list[dict[str, Any]]:
    """Real results for one search, each saved as a frame inside the project with its licence recorded, for a film to show the search.

    Every frame goes through the same record a download does, so `altocue credits` owes what it shows. Only the search words are sent.
    """
    words = " ".join(query.split())
    if not words:
        raise AltocueError("A wall needs words to search for.", fix='Pass them to --wall, such as --wall "ocean drone".')
    env = stock.SOURCES.get(provider, ((), ""))[1]
    if env and not os.environ.get(env):
        raise AltocueError(f"{env} is not set.", fix=KEY_HELP.get(provider, "Use --search auto."))
    steps = stock.plan(provider, None)
    print(f"Searching {', '.join(dict.fromkeys(name for name, _ in steps))} for {words!r}. Only the search words are sent.")
    found: list[stock_base.Result] = []
    for name, kind in steps:
        if len(found) >= count:
            break
        try:
            found += [item for item in stock.search(name, words, kind=kind, shape=orientation(project), limit=count - len(found)) if item.preview]
        except AltocueError as error:
            if provider != "auto":
                raise
            print(f"Skipped {name}: {str(error).splitlines()[0]}")
    folder = project.root / WALL_FOLDER
    folder.mkdir(parents=True, exist_ok=True)
    rows = []
    for item in found[:count]:
        relative = f"{WALL_FOLDER}/{item.source}-{stock_base.slug(item.id)}.jpg"
        try:
            http.download(item.preview, project.root / relative, timeout=60)
            with Image.open(project.root / relative) as picture:
                picture.verify()
        except (AltocueError, OSError):
            (project.root / relative).unlink(missing_ok=True)
            continue
        asset_credits.record(
            project, file=relative, source=item.source, source_url=item.source_url, licence=item.licence, licence_url=item.licence_url,
            credit=item.credit, kind=item.kind, title=item.title, warnings=item.warnings,
        )  # fmt: skip
        chip = stock_base.short_licence(item.licence)
        rows.append({"image": relative, "text": chip, "title": plain(item.title), "source": item.source, "licence": item.licence, "page": item.source_url})
    if not rows:
        raise AltocueError(f"No stock result for {words!r} had a picture to show.", fix="Try plainer words, or another source.")
    write_json(project.root / WALL_FILE, {"query": words, "items": rows})
    return rows


def write_credits(project: Project, inserts: list[dict[str, Any]]) -> None:
    found = [item for item in inserts if item.get("source") in stock.SOURCES]
    if not found:
        return
    lines = ["# B-roll credits", "", "Where no credit is owed it is still a kind thing to give. `altocue credits` prints what this film owes.", ""]
    for item in found:
        owed = f". Credit owed: {' '.join(item['credit'].splitlines())}" if item.get("credit") else ""
        lines.append(f"- {item['title']} by {item.get('author', 'unknown')}, {item.get('page', '')}, licence {item.get('licence', '')}{owed}")
    (project.out / "broll-credits.md").write_text("\n".join(lines) + "\n")


def run(args: argparse.Namespace) -> int:
    project = Project.open(args.project)
    if getattr(args, "wall", None) is not None:
        rows = wall(project, args.wall, args.search or "auto", max(int(args.count), 1))
        print(UNTRUSTED_NOTE)
        for row in rows:
            print(f"- {row['image']}: {row['source']}, {row['licence']}, {row['page']}")
        print(f"{len(rows)} frames in {WALL_FOLDER}, listed in {WALL_FILE} with the licence for each. Use its items in a library_scroll.")
        return 0
    if args.pick:
        inserts = apply_picks(project, args.pick, getattr(args, "rights_checked", False))
        print(f"B-roll chosen for {len(inserts)} of {len(load_spots(project))} spots. It lands in the next `altocue render`.")
        return 0
    saved = project.work / SPOTS_FILE
    earlier = json.loads(saved.read_text()) if saved.exists() else {}
    keep = (args.search or args.library) and args.share is None and earlier.get("cut") == project.cut_mark()
    spots = load_spots(project) if keep else find_spots(project, args.share)
    if not keep:
        drop_stale_picks(project, spots)
        queries = {spot["phrase"]: spot["query"] for spot in earlier.get("spots", [])}  # a re-cut keeps the search words you wrote
        for spot in spots:
            spot["query"] = queries.get(spot["phrase"], spot["query"])
    folders = args.library or list(project.setting("broll", "library", [])) or earlier.get("library", [])
    if folders:
        clips = library_clips(folders)
        for spot in spots:
            spot["options"] = [o for o in spot["options"] if o["source"] != "library"] + match_library(spot, clips)
        print(f"Searched {len(clips)} of your clips.")
    if args.search:
        search(project, spots, args.search, getattr(args, "kind", None))
    number_options(spots)
    write_json(saved, {"cut": project.cut_mark(), "library": folders, "spots": spots})
    picture = sheet(project, spots)
    review = write_review(project, spots)
    for spot in spots:
        print(f"{spot['id']}: {spot['start']:.1f} to {spot['end']:.1f} s, {len(spot['options'])} options, {', '.join(spot['why'])}")
    print(f"{len(spots)} spots. Review: {review}" + (f"\nOptions: {picture}" if picture else ""))
    if not any(spot["options"] for spot in spots) and folders:
        print("None of your clips matched. Clips match on file name, folder and a .txt note beside them. Any file works with b1=file:clip.mp4.")
    elif not any(spot["options"] for spot in spots):
        print("No options yet. Add --library <folder>, --search auto, or generate a shot and pick it with b1=shot:<id>.")
    return 0
