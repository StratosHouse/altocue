"""Emphasis punch-ins: a quick eased zoom on a key line that holds to the cut, then the next shot is the master frame."""

from __future__ import annotations

import subprocess
from pathlib import Path

import numpy as np
import pytest

from altocue import plate
from altocue.errors import AltocueError
from altocue.models import Clip, Segment

CANVAS = plate.Canvas(180, 320, 30, 16, "medium")


def take(path: Path) -> Clip:
    subprocess.run(["ffmpeg", "-v", "error", "-y", "-f", "lavfi", "-i", "testsrc2=size=180x320:rate=30:duration=3", "-c:v", "libx264", "-pix_fmt", "yuv420p", str(path)], check=True)
    return Clip(id="a", path=str(path), duration=3, fps=30, width=180, height=320)


def frames(clip: Clip, segment: Segment) -> np.ndarray:
    chain = plate.segment_filter(clip, segment, CANVAS, None)
    raw = subprocess.run(["ffmpeg", "-v", "error", "-ss", str(segment.start), "-i", clip.path, "-vf", chain, "-frames:v", "60", "-f", "rawvideo", "-pix_fmt", "gray", "-"],
                         capture_output=True, check=True).stdout
    return np.frombuffer(raw, np.uint8).reshape(-1, 320, 180).astype(int)


def test_a_punch_holds_still_until_its_word_then_zooms_in_and_holds(tmp_path):
    clip = take(tmp_path / "a.mp4")
    plain = frames(clip, Segment(id="s1", clip="a", start=0.0, end=2.0))
    punched = frames(clip, Segment(id="s1", clip="a", start=0.0, end=2.0, punch={"at": 0.8, "to": 1.25, "over": 0.4}))
    differ = [np.abs(a - b).mean() for a, b in zip(plain, punched)]
    assert max(differ[:22]) < 1.5  # before the word, the master frame
    assert min(differ[40:]) > 6  # after it, tighter on every frame
    middle = np.abs(punched[30] - plain[30]).mean()
    assert 0 < middle < min(differ[40:])  # on the way in, part of the way


def test_a_punch_can_ease_back_out(tmp_path):
    clip = take(tmp_path / "a.mp4")
    plain = frames(clip, Segment(id="s1", clip="a", start=0.0, end=2.0))
    out = frames(clip, Segment(id="s1", clip="a", start=0.0, end=2.0, punch={"at": 0.3, "to": 1.25, "over": 0.3, "back": 1.2}))
    assert np.abs(out[25] - plain[25]).mean() > 6 and np.abs(out[55] - plain[55]).mean() < 1.5


def test_a_punch_that_cannot_play_is_refused():
    clip = Clip(id="a", path="/a.mov", duration=10, fps=30, width=180, height=320)
    with pytest.raises(AltocueError, match="outside the segment"):
        plate.segment_filter(clip, Segment(id="s1", clip="a", start=1.0, end=2.0, punch={"at": 3.0, "to": 1.1}), CANVAS, None)
    with pytest.raises(AltocueError, match="outside 1 to 1.6"):
        plate.segment_filter(clip, Segment(id="s1", clip="a", start=1.0, end=2.0, punch={"at": 1.2, "to": 2.5}), CANVAS, None)
    with pytest.raises(AltocueError, match="both a slow push and a punch"):
        plate.segment_filter(clip, Segment(id="s1", clip="a", start=1.0, end=2.0, zoom_from=1.0, zoom_to=1.1, punch={"at": 1.2, "to": 1.1}), CANVAS, None)


def test_a_punch_survives_the_cut_file():
    segment = Segment.from_dict({"id": "s1", "clip": "a", "start": 0.0, "end": 2.0, "punch": {"at": 0.5, "to": 1.12}})
    assert segment.punch == {"at": 0.5, "to": 1.12} and segment.to_dict()["punch"] == {"at": 0.5, "to": 1.12}
