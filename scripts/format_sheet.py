#!/usr/bin/env python3
"""Render every format's example and tile the frames, so each one gets looked at. Dev tool, needs the renderer."""

from __future__ import annotations

import argparse
import json
import math
import shutil
import subprocess
from pathlib import Path

from PIL import Image

from altocue import brand as brands
from altocue.brand import data_path
from altocue.motion import formats
from altocue.motion.html import compose
from altocue.motion.hyperframes import binary, quiet_env

SIZES = {"tall": (1080, 1920), "wide": (1920, 1080)}
BACKDROP = '<div style="position:absolute;inset:0;background:linear-gradient(160deg,#2a3f4f,#6a5a4a 55%,#1e2a33)"></div>'
GAP = 0.25


def timeline_for(picked: list[formats.Format], size: tuple[int, int]) -> tuple[dict, list[tuple[float, float]]]:
    items, spans, at = [], [], 0.0
    for fmt in picked:
        end = at + fmt.seconds
        items.append({**fmt.example, "type": fmt.id, "start": at, "end": end})
        spans.append((at, end))
        at = end + GAP
    return {"width": size[0], "height": size[1], "fps": 30, "duration": at, "tail": 0, "total": at, "captions": [], "items": items}, spans


def stills(stage: Path, times: list[float], out: Path) -> list[Path]:
    out.mkdir(parents=True, exist_ok=True)
    moments = ",".join(f"{t:.3f}" for t in times)
    cmd = [str(binary()), "snapshot", str(stage), "--at", moments, "--output", str(out), "--no-end", "--keep"]
    done = subprocess.run(cmd, capture_output=True, text=True, env=quiet_env(), check=False)
    if done.returncode:
        raise SystemExit(done.stdout + done.stderr)
    return [out / f"at-{f'{t:.3f}'.rstrip('0').rstrip('.')}s.png" for t in times]


def main() -> None:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--group", help="One group")
    parser.add_argument("--id", nargs="+", help="Some formats")
    parser.add_argument("--aspect", choices=SIZES, default="tall")
    parser.add_argument("--out", default="/tmp/altocue-sheet", help="Folder for the sheet")
    parser.add_argument("--phases", type=int, default=3, help="Frames per format")
    args = parser.parse_args()
    picked = [f for f in formats.load().values() if (not args.group or f.group == args.group) and (not args.id or f.id in args.id)]
    if not picked:
        raise SystemExit("No formats matched.")
    out = Path(args.out)
    stage = out / "stage"
    shutil.rmtree(stage, ignore_errors=True)
    stage.mkdir(parents=True)
    brand = brands.Brand(json.loads(data_path("brands", "default.json").read_text()), data_path("brands"))
    shutil.copy(brand.font_file, stage / brand.font_file.name)
    size = SIZES[args.aspect]
    timeline, spans = timeline_for(picked, size)
    for font in formats.fonts_for(timeline["items"]):
        shutil.copy(data_path("fonts", formats.FONT_FILES[font]), stage / formats.FONT_FILES[font])
    page = compose(timeline, brand)
    opening = page.index('<div id="root"')
    opening = page.index(">\n", opening) + 2
    (stage / "index.html").write_text(page[:opening] + BACKDROP + "\n" + page[opening:])
    shares = [0.3, 0.62, 0.9][: args.phases] if args.phases > 1 else [0.8]
    times = [round(start + (end - start) * share, 3) for start, end in spans for share in shares]
    frames = stills(stage, times, out / "frames")
    shrink = 300 if args.aspect == "tall" else 420
    tall = round(shrink * size[1] / size[0])
    columns = min(len(shares) * 4, 12)
    rows = math.ceil(len(frames) / columns)
    sheet = Image.new("RGB", (columns * shrink, rows * tall), (10, 10, 10))
    for index, path in enumerate(frames):
        with Image.open(path) as image:
            sheet.paste(image.convert("RGB").resize((shrink, tall)), ((index % columns) * shrink, (index // columns) * tall))
    target = out / f"sheet-{args.group or 'all'}-{args.aspect}.jpg"
    sheet.save(target, quality=86)
    print(target, f"{len(picked)} formats")


if __name__ == "__main__":
    main()
