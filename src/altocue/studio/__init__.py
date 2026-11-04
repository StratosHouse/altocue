"""Optional Studio direction stage for the normal Altocue render."""

from __future__ import annotations

import argparse
import bisect
import json
import subprocess
from pathlib import Path

import numpy as np
from PIL import Image, ImageOps

from .. import ffmpeg
from ..errors import AltocueError
from ..project import write_json
from .plan import check_speech, load, need
from .scene import draw_scene, header, image


def frame(plan, n):
    rows = plan["scenes"]
    idx = bisect.bisect_right([s["start"] for s in rows], n) - 1
    out = Image.new("RGBA", (720, 1280))
    if idx >= 0 and n < rows[idx]["end"]:
        scene = draw_scene(plan, rows[idx], n - rows[idx]["start"])
        out.paste(scene, (0, 0))
    return out


def key_audio(plan, work):
    """Start a single licensed transient on each glyph. Never let a key sound past the next glyph or shot."""
    cues = []
    work.mkdir(exist_ok=True, parents=True)
    for i, row in enumerate(plan["scenes"]):
        if row["recipe"] != "typing" or not row.get("sound"):
            continue
        src = ffmpeg.extract_wav(Path(plan["_assets"][row["sound"]]), work / f"source-{i}.wav")
        samples, rate = ffmpeg.read_wav(src)
        samples = np.asarray(samples, dtype=float)
        envelope = np.abs(samples)
        peak = float(envelope.max(initial=0))
        need(peak > 0, "The supplied key sound is silent.")
        hit = int(np.flatnonzero(envelope > 0.17 * peak)[0])
        keys = row["keys"]
        for j, k in enumerate(keys):
            next_at = keys[j + 1] if j + 1 < len(keys) else row["end"] - row["start"]
            size = min(round(0.041 * rate), (next_at - k) * rate // plan["fps"], len(samples) - hit)
            tap = samples[hit : hit + size].copy()
            nf = min(size, round(0.011 * rate))
            tap[-nf:] *= np.linspace(1, 0, nf)
            path = work / f"key-{i}-{j}.wav"
            ffmpeg.write_wav(path, tap, rate)
            cues.append({"file": str(path), "at": (row["start"] + k) / plan["fps"], "align": "start", "under_db": 12, "min_gap_ms": 0})
    return cues


def layer(plan, path, count, size=(720, 1280)):
    """Encode a transparent, exact-frame overlay. Stream frames, never an unbounded list of images."""
    path.parent.mkdir(parents=True, exist_ok=True)
    tmp = path.with_name(path.stem + ".partial.mov")
    args = [
        "ffmpeg",
        "-v",
        "error",
        "-y",
        "-f",
        "rawvideo",
        "-pixel_format",
        "rgba",
        "-video_size",
        f"{size[0]}x{size[1]}",
        "-framerate",
        str(plan["fps"]),
        "-i",
        "-",
        "-c:v",
        "qtrle",
        "-pix_fmt",
        "argb",
        str(tmp),
    ]
    try:
        with subprocess.Popen(args, stdin=subprocess.PIPE) as proc:
            assert proc.stdin
            try:
                for n in range(count):
                    im = frame(plan, n)
                    im = im if im.size == size else im.resize(size, Image.Resampling.LANCZOS)
                    proc.stdin.write(im.tobytes())
            finally:
                proc.stdin.close()
            if proc.wait():
                raise AltocueError("Could not encode the Studio layer.", fix="Check ffmpeg and available disk space.")
        tmp.replace(path)
    except (OSError, BrokenPipeError) as e:
        raise AltocueError("Could not write the Studio layer.", fix="Check ffmpeg, disk space and local asset files.") from e
    finally:
        tmp.unlink(missing_ok=True)


def attach(project, timeline, canvas, *, draft=False):
    if not (project.root / "studio.json").is_file():
        return timeline
    need(canvas.width * 16 == canvas.height * 9, "Studio presenter compositions require a 9:16 project.")
    n = round(timeline["total"] * canvas.fps)
    plan = load(project.root, total=n, fps=canvas.fps)
    work = project.work / "studio"
    path = work / ("draft.mov" if draft else "full.mov")
    layer(plan, path, n, (360, 640) if draft else (720, 1280))
    cues = key_audio(plan, work / "keys")
    write_json(work / "receipt.json", {"fps": canvas.fps, "frames": n, "scene_count": len(plan["scenes"]), "keys": [q["at"] for q in cues]})
    return {**timeline, "studio_layer": str(path), "cues": [*timeline.get("cues", []), *cues]}


def cover(plan, scene_index, portrait, dest):
    """Compose a portable face + product cover; write all four centred-crop proofs."""
    sc = plan["scenes"][scene_index]
    im = Image.new("RGB", (720, 1280), "#152d30")
    header(im, plan)
    from .canvas import txt

    txt(im, (53, 355), plan["product"], 59, "b", limit=405)
    txt(im, (54, 440), sc.get("title", sc.get("keyword", "")), 45, "s", limit=420)
    art = image(plan, sc.get("asset", sc.get("items", [{}])[0].get("asset", plan["mark"])), 130)
    im.paste(art, (452, 355), art)
    with Image.open(portrait) as src:
        photo = ImageOps.fit(src.convert("RGB"), (680, 616), centering=(0.5, 0.46))
    im.paste(photo, (20, 526))
    dest.parent.mkdir(parents=True, exist_ok=True)
    im.resize((1080, 1920), Image.Resampling.LANCZOS).save(dest)
    for tag, h in (("3x4", 960), ("4x5", 900), ("square", 720)):
        y = (1280 - h) // 2
        im.crop((0, y, 720, y + h)).save(dest.with_name(f"{dest.stem}-{tag}.jpg"))


def add_arguments(name, p):
    p.add_argument("action", choices=["demo", "catalog", "example", "validate", "layer", "frame", "cover", "check-speech", "dialogue"], nargs="?", default="catalog")
    p.add_argument("--project", "-p", default=".")
    p.add_argument("--out", type=Path)
    p.add_argument("--frame", type=int, default=0)
    p.add_argument("--scene", type=int, default=0)
    p.add_argument("--portrait", type=Path)
    p.add_argument("--words", type=Path)


def run(args: argparse.Namespace):
    if args.action == "demo":
        from .demo import create

        print(create(Path(args.project).resolve()))
        return 0
    if args.action == "catalog":
        print(Path(__file__).with_name("collection.json").read_text())
        return 0
    if args.action == "example":
        print((Path(__file__).with_name("example.json")).read_text())
        return 0
    root = Path(args.project).resolve()
    if args.action == "dialogue":
        from .dialogue import conform

        need(args.out is not None, "Choose a new output WAV with --out.")
        conform(root, args.out)
        return 0
    plan = load(root)
    if args.action == "validate":
        print(f"{len(plan['scenes'])} Studio scenes validated.")
        return 0
    if args.action == "check-speech":
        need(args.words and args.words.is_file(), "Supply independent final word timestamps with --words.")
        rules = plan.get("speech_checks", [])
        need(bool(rules), "There are no final speech checks in this manifest.")
        words = json.loads(args.words.read_text())
        r = check_speech(words["words"], rules, plan["fps"])
        print(json.dumps(r, indent=2))
        return int(not all(x["passed"] for x in r))
    need(args.out is not None, "Choose an output file with --out.")
    if args.action == "frame":
        frame(plan, args.frame).save(args.out)
    elif args.action == "layer":
        layer(plan, args.out, plan["scenes"][-1]["end"])
    else:
        need(args.portrait and args.portrait.is_file() and 0 <= args.scene < len(plan["scenes"]), "Choose a clean, cropped face still and an existing scene.")
        cover(plan, args.scene, args.portrait, args.out)
    return 0
