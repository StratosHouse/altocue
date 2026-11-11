from __future__ import annotations

import pytest

from altocue.errors import AltocueError
from altocue.models import Clip, Segment
from altocue.plate import Canvas, fit_crop, push_filter, segment_filter, segment_frames, zoomed

CANVAS = Canvas(1080, 1920, 30, 16, "medium")


def clip(width=1920, height=1080, rotation=0):
    return Clip(id="a", path="/a.mov", duration=10, fps=30, width=width, height=height, rotation=rotation)


def test_fit_crop_centres_the_target_aspect():
    x, y, width, height = fit_crop((1920, 1080), (1080, 1920), None)
    assert (x, y) == ((1920 - 607.5) / 2, 0.0) and height == 1080 and round(width, 1) == 607.5


def test_tall_source_for_square_output_keeps_the_top_third():
    _, y, _, height = fit_crop((1080, 1920), (1080, 1080), None)
    assert height == 1080 and 0 < y < (1920 - 1080) / 2


def test_given_crop_is_respected():
    assert fit_crop((1920, 1080), (1080, 1920), [10, 20, 225, 400]) == (10.0, 20.0, 225.0, 400.0)


def test_a_crop_from_an_older_aspect_is_not_stretched():
    assert fit_crop((1920, 1080), (1920, 1080), [10, 20, 225, 400]) == (0.0, 0.0, 1920.0, 1080.0)


def test_zoom_narrows_the_crop():
    _, _, width, height = zoomed((0, 0, 1000, 2000), 1.25)
    assert width == 800 and height == 1600


def test_segment_filter_uses_even_dimensions_and_ends_tagged():
    chain = segment_filter(clip(), Segment(id="s", clip="a", start=0, end=2), CANVAS, None)
    crop = chain.split("crop=")[1].split(":")
    assert int(crop[0]) % 2 == 0 and int(crop[1]) % 2 == 0
    assert "scale=1080:1920" in chain and "fps=30" in chain


def test_push_uses_sub_pixel_filter_only_when_moving():
    still = segment_filter(clip(), Segment(id="s", clip="a", start=0, end=2, zoom_from=1.1, zoom_to=1.1), CANVAS, None)
    moving = segment_filter(clip(), Segment(id="s", clip="a", start=0, end=2, zoom_from=1.0, zoom_to=1.06), CANVAS, None)
    assert "perspective" not in still and "perspective" in moving
    assert "eval=frame" in push_filter(CANVAS, 60, 1.0, 1.06)


def test_an_odd_source_never_asks_for_more_than_it_has():
    clip = Clip(id="c", path="c.mp4", duration=2.0, fps=30.0, width=1079, height=1919)
    chain = segment_filter(clip, Segment(id="s", clip="c", start=0, end=1), Canvas(1080, 1920, 30, 16, "medium"), None)
    assert "crop=1078:" in chain


def test_a_shot_of_no_frames_is_refused_and_never_padded_to_one():
    """The plate used to make one frame of it while the duration gate and the timeline counted none."""
    assert segment_frames(Segment(id="s1", clip="a", start=1.0, end=2.0), 30) == 30
    assert segment_frames(Segment(id="s1", clip="a", start=1.0, end=1.0 + 1 / 30), 30) == 1
    with pytest.raises(AltocueError, match="shorter than one frame") as refused:
        segment_frames(Segment(id="s1", clip="a", start=1.0, end=1.01), 30)
    assert "edl.json" in refused.value.fix


def test_the_picture_of_a_clip_ends_where_its_video_stream_does():
    old = Clip.from_dict({"id": "a", "path": "a.mov", "duration": 10.3, "fps": 30, "width": 64, "height": 64})  # a media.json from before the field
    assert old.video_duration == 0 and old.picture_duration == 10.3
    assert Clip(id="a", path="a.mov", duration=10.3, fps=30, width=64, height=64, video_duration=10.0).picture_duration == 10.0
    assert Clip(id="a", path="a.mov", duration=10.0, fps=30, width=64, height=64, video_duration=10.02).picture_duration == 10.0
    from altocue.ffmpeg import picture_seconds

    assert picture_seconds({"duration": "10.000000", "start_time": "0.000000"}, 30.0) == 10.0
    assert picture_seconds({"tags": {"DURATION": "00:00:10.500000000"}}, 30.0) == 10.5  # Matroska keeps it in a tag
    assert picture_seconds({"nb_frames": "300"}, 30.0) == 10.0 and picture_seconds({}, 30.0) == 0.0
    assert picture_seconds({"duration": "10.0", "start_time": "1.4"}, 30.0, 1.4) == 10.0  # a seek counts from where the file starts
    assert picture_seconds({"duration": "10.0", "start_time": "0.5"}, 30.0, 0.0) == 10.5  # picture that starts late ends late


def test_a_shot_that_ends_after_the_picture_does_holds_the_last_frame(tmp_path):
    """Sound often outlasts picture on a phone. The file's length said there were frames, none came, and every later shot arrived early."""
    from altocue import ffmpeg, plate
    from altocue.project import Project

    source = tmp_path / "phone.mp4"
    picture, sound = ["-f", "lavfi", "-i", "testsrc=s=320x180:r=30:d=1"], ["-f", "lavfi", "-i", "sine=f=300:r=48000:d=1.3"]
    ffmpeg.run([*picture, *sound, "-c:v", "libx264", "-preset", "ultrafast", "-pix_fmt", "yuv420p", "-c:a", "aac", str(source)])
    found = ffmpeg.probe_clip(source, clip_id="a")
    assert found.duration == pytest.approx(1.3, abs=0.03) and found.video_duration == pytest.approx(1.0, abs=0.01)
    project = Project.create(tmp_path / "video")
    folder = tmp_path / "parts"
    folder.mkdir()
    canvas = Canvas(180, 320, 30, 30, "ultrafast")
    part = plate.build_segment(project, found, Segment(id="s1", clip="a", start=0.6, end=1.2), canvas, folder)
    assert int(probe(part, "-count_packets", "-select_streams", "v:0", "-show_entries", "stream=nb_read_packets")) == 18
    blind = Clip.from_dict({**found.to_dict(), "video_duration": 0.0})  # what an older media.json says: only the file's length
    short = plate.build_segment(project, blind, Segment(id="s2", clip="a", start=0.6, end=1.2), canvas, folder)
    assert int(probe(short, "-count_packets", "-select_streams", "v:0", "-show_entries", "stream=nb_read_packets")) < 18, "the fixture no longer shows the fault"


def probe(path, *fields: str) -> str:
    import subprocess

    return subprocess.run(["ffprobe", "-v", "error", *fields, "-of", "csv=p=0", str(path)], capture_output=True, text=True, check=True).stdout


def test_parts_join_with_no_gap_when_the_source_has_a_chapter_track(tmp_path):
    """Zoom files carry a chapter track that starts before the picture. Each part must hold picture only, or every join adds a frame."""
    from altocue import ffmpeg, plate
    from altocue.project import Project

    chapters = tmp_path / "chapters.txt"
    chapters.write_text(";FFMETADATA1\n[CHAPTER]\nTIMEBASE=1/1000\nSTART=0\nEND=6000\ntitle=Recording\n")
    source = tmp_path / "call.mov"
    ffmpeg.run(
        [
            "-f",
            "lavfi",
            "-i",
            "testsrc=s=320x180:r=25:d=6",
            "-i",
            str(chapters),
            "-map",
            "0:v",
            "-map_chapters",
            "1",
            "-c:v",
            "libx264",
            "-preset",
            "ultrafast",
            str(source),
        ]
    )
    assert len(probe(source, "-show_entries", "stream=codec_type").split()) > 1, "the test source should carry a chapter track, as Zoom's do"
    project = Project.create(tmp_path / "video")
    source_clip = Clip(id="a", path=str(source), duration=6.0, fps=25, width=320, height=180, has_audio=False)
    folder = tmp_path / "parts"
    folder.mkdir()
    canvas = Canvas(180, 320, 25, 30, "veryfast")  # B-frames, as the default preset makes: they start the picture a frame in
    parts = [
        plate.build_segment(project, source_clip, Segment(id=f"s{n}", clip="a", start=start, end=start + 1.0), canvas, folder) for n, start in enumerate((0.5, 2.0, 4.0))
    ]
    for part in parts:
        assert probe(part, "-show_entries", "stream=codec_type").split() == ["video"]
    joined = tmp_path / "joined.mp4"
    ffmpeg.run(["-f", "concat", "-safe", "0", "-i", str(ffmpeg.concat_list(tmp_path / "list.ffconcat", parts)), "-c", "copy", str(joined)])
    times = [float(line.strip(",")) for line in probe(joined, "-select_streams", "v:0", "-show_entries", "frame=pts_time").split()]
    assert len(times) == 75
    assert max(b - a for a, b in zip(times, times[1:], strict=False)) < 0.05
