"""Where the faces are in a frame, through the same YuNet model the frame stage uses."""

from __future__ import annotations

import importlib
from pathlib import Path

import numpy as np
from PIL import Image


def model_path() -> Path | None:
    try:
        path = Path(importlib.import_module("altocue.frame").MODEL_PATH)
    except (ImportError, AttributeError):
        return None
    return path if path.exists() else None


def find(image: Path) -> list[list[float]]:
    """Face boxes as x, y, w, h from the top left, 0 to 1."""
    frame = importlib.import_module("altocue.frame")
    with Image.open(image) as picture:
        pixels = np.asarray(picture.convert("RGB"))
    height, width = pixels.shape[:2]
    return [[f.x / width, f.y / height, f.w / width, f.h / height] for f in frame.detect_faces(pixels)]
