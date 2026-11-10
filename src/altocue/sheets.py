"""Timestamped contact sheets of a finished render, for looking at every beat before anyone else does.

A passing check is not a review. These sheets put a frame every `every` seconds side by side, each stamped
with its time, so a reviewer can see a flash of the face, a card off the edge or text under a graphic,
and name the second it happens. The stamps are drawn with Pillow, not ffmpeg's drawtext, which some
ffmpeg builds leave out.
"""

from __future__ import annotations

import subprocess
import tempfile
from pathlib import Path

from PIL import Image, ImageDraw, ImageFont

from .brand import data_path
from .errors import AltocueError
from .ffmpeg import require

FONT = ("fonts", "Geist-Variable.ttf")
EVERY = 1.36  # seconds between frames: a 60 s Reel fits on two sheets
COLUMNS, ROWS = 8, 3
TILE_WIDTH = 360


def stamp(seconds: float) -> str:
    minutes, rest = divmod(seconds, 60)
    return f"{int(minutes)}:{rest:05.2f}"


def contact_sheets(render: Path, out: Path, every: float = EVERY, columns: int = COLUMNS, rows: int = ROWS) -> list[Path]:
    """Write sheet-1.jpg, sheet-2.jpg and on into `out`, one frame every `every` seconds. Returns the sheets."""
    if not render.exists():
        raise AltocueError(f"No render at {render}.", fix="Render the Reel first, or pass --render.")
    if every <= 0:
        raise AltocueError("The gap between frames must be above zero.", fix="Pass --every 1.36, for example.")
    out.mkdir(parents=True, exist_ok=True)
    for old in out.glob("sheet-*.jpg"):
        old.unlink()
    with tempfile.TemporaryDirectory(prefix="altocue-sheets-") as scratch:
        pattern = Path(scratch) / "f-%05d.png"
        command = [require("ffmpeg"), "-v", "error", "-y", "-i", str(render), "-vf", f"fps=1/{every},scale={TILE_WIDTH}:-2", "-start_number", "0", str(pattern)]
        done = subprocess.run(command, capture_output=True, text=True, check=False)
        if done.returncode:
            raise AltocueError(f"ffmpeg could not read frames from {render}: {done.stderr.strip()[-300:]}")
        frames = sorted(Path(scratch).glob("f-*.png"))
        if not frames:
            raise AltocueError(f"{render} gave no frames.", fix="Check the render plays.")
        font = ImageFont.truetype(str(data_path(*FONT)), 26)
        per = columns * rows
        sheets = []
        for first in range(0, len(frames), per):
            batch = frames[first : first + per]
            tiles = [Image.open(path).convert("RGB") for path in batch]
            width, height = tiles[0].size
            used = (len(tiles) + columns - 1) // columns
            sheet = Image.new("RGB", (columns * width, used * height), (12, 16, 20))
            draw = ImageDraw.Draw(sheet)
            for index, tile in enumerate(tiles):
                x, y = index % columns * width, index // columns * height
                sheet.paste(tile, (x, y))
                label = stamp((first + index) * every)
                box = draw.textbbox((x + 8, y + 8), label, font=font)
                draw.rectangle((box[0] - 4, box[1] - 4, box[2] + 4, box[3] + 4), fill=(0, 0, 0))
                draw.text((x + 8, y + 8), label, font=font, fill=(255, 255, 255))
                tile.close()
            path = out / f"sheet-{len(sheets) + 1}.jpg"
            sheet.save(path, quality=86)
            sheets.append(path)
    return sheets
