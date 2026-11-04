"""Conform revised dialogue to the locked picture using real, explicitly bounded source PCM."""

from __future__ import annotations

import json
import tempfile
from pathlib import Path

import numpy as np

from .. import ffmpeg
from ..errors import AltocueError
from .plan import integer, local, need


def conform(root, dest):
    """Source in/out are samples at 48 kHz; output is a frame index. Preserve the caller's final running time."""
    try:
        spec = json.loads((root / "studio-dialogue.json").read_text())
    except (OSError, ValueError) as e:
        raise AltocueError(str(e), fix="Provide studio-dialogue.json with a base, fps, frames, replacements and inserts.") from e
    need(
        isinstance(spec, dict) and isinstance(spec.get("pieces"), list) and isinstance(spec.get("replace"), list),
        "Provide dialogue source pieces and replacement ranges.",
    )
    sources = [spec.get("base"), *(row.get("source") for row in spec["pieces"] if isinstance(row, dict))]
    need(all(dest.resolve() != local(root, rel) for rel in sources), "The new dialogue output must not overwrite any original source.")
    fps = spec.get("fps")
    frames = spec.get("frames")
    need(integer(fps) and 12 <= fps <= 120 and integer(frames) and frames > 0, "Invalid dialogue output timebase.")
    with tempfile.TemporaryDirectory(prefix="altocue-studio-speech-") as td:
        folder = Path(td)
        cache = {}

        def samples(rel):
            if rel not in cache:
                path = local(root, rel)
                wav = ffmpeg.extract_wav(path, folder / f"{len(cache)}.wav")
                arr, rate = ffmpeg.read_wav(wav)
                need(rate == 48000, "The dialogue decoder did not supply 48 kHz PCM.")
                cache[rel] = arr
            return cache[rel]

        total = round(frames * 48000 / fps)
        base = samples(spec["base"])
        out = np.zeros(total, np.float32)
        n = min(len(base), total)
        out[:n] = base[:n]
        ranges = []
        prev = 0
        for span in spec["replace"]:
            a, b = span
            need(integer(a) and integer(b) and 0 <= a < b <= frames and a >= prev, "Declare ordered replacement output frame ranges.")
            lo, hi = round(a * 48000 / fps), round(b * 48000 / fps)
            out[lo:hi] = 0
            ranges.append((lo, hi))
            prev = b
        last = 0
        receipt = []
        for row in spec["pieces"]:
            a, b, at = row["in_sample"], row["out_sample"], row["at_frame"]
            need(all(integer(v) for v in (a, b, at)), "Source sample and output frame positions must be integers.")
            src = samples(row["source"])
            start = round(at * 48000 / fps)
            end = start + b - a
            need(
                0 <= a < b <= len(src) and start >= last and any(lo <= start < end <= hi for lo, hi in ranges),
                "Source piece crosses its source or output replacement bounds.",
            )
            f = row.get("fade_samples", 432)
            need(integer(f) and 0 <= 2 * f <= b - a, "Invalid dialogue handle.")
            piece = src[a:b].copy()
            if f:
                ramp = np.linspace(0, 1, f)
                piece[:f] *= ramp
                piece[-f:] *= 1 - ramp
            out[start:end] = piece
            last = end
            receipt.append({"source": row["source"], "source_samples": [a, b], "output_samples": [start, end]})
        dest.parent.mkdir(parents=True, exist_ok=True)
        ffmpeg.write_wav(dest, out, 48000)
        (dest.with_suffix(".sources.json")).write_text(json.dumps({"sample_rate": 48000, "pieces": receipt}, indent=2))
