"""Turn a photo into a slow push-in, so a still can fill a B-roll spot. Faces stay in frame and nothing stretches."""

from __future__ import annotations

import tempfile
from pathlib import Path

import numpy as np
from PIL import Image, ImageOps, UnidentifiedImageError

from .. import ffmpeg, frame, grade, plate
from ..errors import AltocueError

PUSH = 1.06  # slow enough to read as a held shot


def require_picture(path: Path) -> None:
    """A downloaded file must really be a picture before anything reads it."""
    try:
        with Image.open(path) as image:
            image.verify()
    except (UnidentifiedImageError, OSError, SyntaxError, ValueError, Image.DecompressionBombError) as error:
        path.unlink(missing_ok=True)
        raise AltocueError("That download is not a picture Altocue can read.", fix="Pick another option.") from error


def upright(source: Path) -> Image.Image:
    """Phones and cameras store some photos on their side and say so in a tag."""
    with Image.open(source) as image:
        return ImageOps.exif_transpose(image).convert("RGB")


def subject_of(image: Image.Image, crop: tuple[int, int]) -> frame.Subject | None:
    pixels = np.asarray(image)
    tracks = frame.find_tracks([frame.detect_faces(pixels)])
    return frame.choose_subject(tracks, *crop)


def push_in(source: str | Path, dest: str | Path, size: tuple[int, int], fps: int, frames: int) -> Path:
    """Crop the photo to the canvas around its faces, then zoom it slowly in place."""
    image = upright(Path(source))
    width, height = size
    crop_size = frame.crop_size(image.size, size)
    subject = subject_of(image, crop_size)
    crop = frame.place_crop(image.size, crop_size, subject)
    x, y, wide, tall = (int(value) for value in crop)
    framed = image.crop((x, y, x + wide, y + tall)).resize(size, Image.Resampling.LANCZOS)
    zoom = max(frame.safe_zoom(crop, subject, PUSH), 1.0 + 1e-3)
    canvas = plate.Canvas(width, height, fps, 17, "medium")
    move = plate.push_filter(canvas, frames, 1.0, round(zoom, 4))
    chain = f"scale=out_color_matrix=bt709:out_range=tv,format=yuv444p,{move},format=yuv420p,{grade.TAGS},setsar=1"
    with tempfile.TemporaryDirectory(prefix="altocue-still-") as folder:
        still = Path(folder) / "still.png"
        framed.save(still)
        args = ["-loop", "1", "-framerate", str(fps), "-i", str(still), "-vf", chain, "-frames:v", str(frames)]
        ffmpeg.run([*args, "-c:v", "libx264", "-crf", "17", "-preset", "medium", "-pix_fmt", "yuv420p", str(dest)])
    return Path(dest)
