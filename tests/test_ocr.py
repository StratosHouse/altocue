"""The frames QA reads for text come from one decode of the film, and are the frames five passes wrote."""

from __future__ import annotations

import subprocess

import pytest
from conftest import make_clip

from altocue import ffmpeg
from altocue.qa import ocr


def five_passes(file, fps, folder):
    """How the whole frames and tiles were written before: one pass over the film for each. Kept here as the reference."""
    base = ["ffmpeg", "-hide_banner", "-nostdin", "-y", "-v", "error", "-i", str(file)]
    subprocess.run([*base, "-vf", f"fps={fps}", "-q:v", "2", str(folder / "full_%05d.jpg")], check=True)
    for name, crop in ocr.TILES.items():
        subprocess.run([*base, "-vf", f"fps={fps},crop={crop},scale=iw*2:ih*2:flags=lanczos", "-q:v", "2", str(folder / f"{name}_%05d.jpg")], check=True)
    return {path.name: path.read_bytes() for path in folder.glob("*.jpg")}


@pytest.mark.parametrize("size", ["320x568", "322x240"])  # a tile of the second has an odd side
def test_one_pass_writes_the_same_images_as_five(tmp_path, monkeypatch, size):
    film = make_clip(tmp_path / "film.mp4", seconds=3.0, size=size)
    before, after = tmp_path / "before", tmp_path / "after"
    before.mkdir()
    after.mkdir()
    wanted = five_passes(film, 2.0, before)
    calls = []
    real = ffmpeg.run
    monkeypatch.setattr(ffmpeg, "run", lambda args: calls.append(args) or real(args))
    times = ocr.sample(film, 2.0, [1.0, 2.5], after)
    cuts = {"cut_00000.jpg": 1.0, "cut_00001.jpg": 2.5}
    assert len(wanted) == 30 and set(times) == set(wanted) | set(cuts)
    assert {name: (after / name).read_bytes() for name in wanted} == wanted  # byte for byte
    assert times["full_00001.jpg"] == 0.0 and times["br_00004.jpg"] == 1.5 and all(times[name] == at for name, at in cuts.items())
    assert all((after / name).stat().st_size > 0 for name in cuts)
    assert len(calls) == 1 + len(cuts)  # the film is decoded whole once, and each cut is a seek


@pytest.mark.skipif(ocr.reader()[0] is None, reason="no text reader on this machine")
def test_text_in_a_film_is_still_read_from_whole_frames_tiles_and_cuts(tmp_path):
    from canaries import text_plate
    from PIL import Image

    still = tmp_path / "frame.png"
    base = Image.new("RGBA", (1080, 1920), (127, 140, 153, 255))
    base.alpha_composite(text_plate("HELLO WORLD", 0.1))
    base.convert("RGB").save(still)
    film = tmp_path / "film.mp4"
    encode = ["-c:v", "libx264", "-preset", "ultrafast", "-pix_fmt", "yuv420p"]
    subprocess.run(["ffmpeg", "-v", "error", "-y", "-loop", "1", "-framerate", "30", "-t", "1", "-i", str(still), *encode, str(film)], check=True)
    frames = tmp_path / "frames"
    frames.mkdir()
    hits = [record for record in ocr.read_video(film, 2.0, [0.5], frames) if "HELLO" in record["text"].upper()]
    kinds = {record["file"].rsplit("/", 1)[-1].split("_")[0] for record in hits}
    assert {"full", "cut"} <= kinds and kinds & {"tl", "tr"}, kinds
    assert {record["time"] for record in hits} == {0.0, 0.5}
    assert all(0.05 < ocr.frame_box(record)[1] < 0.15 for record in hits), hits
