"""A long encode says how far it is, and saying so changes nothing about the encode."""

from __future__ import annotations

import json
import re

import pytest

from altocue import ffmpeg
from altocue.errors import AltocueError

PICTURE = ["-f", "lavfi", "-i", "testsrc2=size=160x120:rate=30:duration=1"]
SOUND = ["-f", "lavfi", "-i", "sine=frequency=220:sample_rate=48000:duration=1"]
ENCODE = ["-c:v", "libx264", "-preset", "ultrafast", "-pix_fmt", "yuv420p"]


@pytest.fixture
def chatty(monkeypatch):
    """No waiting: every report becomes a line, so a one second encode can show what a long one prints."""
    monkeypatch.setattr(ffmpeg, "PROGRESS_AFTER", 0.0)
    monkeypatch.setattr(ffmpeg, "PROGRESS_EVERY", 0.0)


def clock(monkeypatch, *times):
    ticks = iter(times)
    monkeypatch.setattr(ffmpeg.time, "monotonic", lambda: next(ticks))


def test_progress_changes_nothing_about_the_encode(tmp_path, chatty, capsys):
    """Same file, same result, and the log is still there for whoever reads it."""
    args = [*PICTURE, *SOUND, "-af", "volumedetect", *ENCODE, "-c:a", "aac"]
    plain = ffmpeg.run([*args, str(tmp_path / "plain.mp4")])
    assert capsys.readouterr().err == ""
    with ffmpeg.progress("Compositing", 1.0, tmp_path / "work" / "progress.json"):
        watched = ffmpeg.run([*args, str(tmp_path / "watched.mp4")])
    assert (tmp_path / "watched.mp4").read_bytes() == (tmp_path / "plain.mp4").read_bytes()
    assert (watched.returncode, watched.stdout) == (plain.returncode, plain.stdout) == (0, "")
    assert [arg.replace("watched", "plain") for arg in watched.args] == plain.args
    assert "mean_volume" in plain.stderr and "mean_volume" in watched.stderr
    said = capsys.readouterr()
    assert said.out == ""  # stdout belongs to `altocue mcp` and to pipes
    lines = said.err.splitlines()
    assert lines and all(re.fullmatch(r"Compositing: \d+% after \d+:\d\d", line) for line in lines)
    assert lines[-1].startswith("Compositing: 9") or lines[-1].startswith("Compositing: 100%")
    record = json.loads((tmp_path / "work" / "progress.json").read_text())
    assert record["label"] == "Compositing" and record["running"] is False and record["seconds"] == 1.0 and record["percent"] >= 90


def test_a_short_job_says_nothing(tmp_path, capsys):
    with ffmpeg.progress("Compositing", 1.0, tmp_path / "progress.json"):
        ffmpeg.run([*PICTURE, *ENCODE, str(tmp_path / "short.mp4")])
    said = capsys.readouterr()
    assert said.out == "" and said.err == ""
    assert not (tmp_path / "progress.json").exists()


def test_a_long_job_speaks_after_a_few_seconds_and_then_every_few(monkeypatch, tmp_path, capsys):
    clock(monkeypatch, 100.0, 101.0, 106.0, 108.0, 111.5, 171.5, 172.0)
    job = ffmpeg.Progress("Building the picture", 200.0, tmp_path / "progress.json")
    job.at(10.0)  # one second in
    assert capsys.readouterr().err == "" and not (tmp_path / "progress.json").exists()
    job.at(50.0)  # six seconds in
    job.at(60.0)  # two seconds after that line
    job.at(90.0)
    job.at(500.0)  # past the end: a transition encodes a few frames twice
    assert capsys.readouterr().err.splitlines() == [
        "Building the picture: 25% after 0:06",
        "Building the picture: 45% after 0:11",
        "Building the picture: 100% after 1:11",
    ]
    running = json.loads((tmp_path / "progress.json").read_text())
    assert running == {"label": "Building the picture", "percent": 100, "encoded": 200.0, "seconds": 200.0, "elapsed": 71.5, "running": True}
    job.close()
    assert json.loads((tmp_path / "progress.json").read_text())["running"] is False


def test_the_calls_of_one_job_add_up(tmp_path, chatty, capsys):
    with ffmpeg.progress("Building the picture", 2.0):
        ffmpeg.run([*PICTURE, *ENCODE, str(tmp_path / "one.mp4")])
        first = [int(found) for found in re.findall(r"(\d+)%", capsys.readouterr().err)]
        ffmpeg.run([*PICTURE, *ENCODE, str(tmp_path / "two.mp4")])
        second = [int(found) for found in re.findall(r"(\d+)%", capsys.readouterr().err)]
    assert first and max(first) <= 50
    assert second and min(second) >= 45 and max(second) >= 90


def test_sound_and_stream_copies_do_not_move_a_picture(tmp_path, chatty, capsys):
    ffmpeg.run([*PICTURE, *ENCODE, str(tmp_path / "a.mp4")])
    with ffmpeg.progress("Building the picture", 1.0, tmp_path / "progress.json"):
        ffmpeg.run([*SOUND, str(tmp_path / "voice.wav")])
        ffmpeg.run(["-i", str(tmp_path / "a.mp4"), "-c", "copy", str(tmp_path / "copy.mp4")])
        measured = ffmpeg.loudness(tmp_path / "voice.wav")  # writes to "-", so its output is left alone
    assert capsys.readouterr().err == "" and not (tmp_path / "progress.json").exists()
    assert (tmp_path / "copy.mp4").stat().st_size > 0 and measured["integrated"] < 0


def test_a_failure_inside_a_job_still_carries_the_log(tmp_path, chatty):
    with pytest.raises(AltocueError, match="No such file or directory"), ffmpeg.progress("Compositing", 1.0):
        ffmpeg.run(["-i", str(tmp_path / "missing.mp4"), *ENCODE, str(tmp_path / "out.mp4")])


def test_nothing_is_reported_outside_a_job_or_with_no_length(tmp_path, chatty, capsys):
    with ffmpeg.progress("Compositing", 0.0, tmp_path / "progress.json"):
        ffmpeg.run([*PICTURE, *ENCODE, str(tmp_path / "a.mp4")])
    ffmpeg.run([*PICTURE, *ENCODE, str(tmp_path / "b.mp4")])
    assert capsys.readouterr().err == "" and not (tmp_path / "progress.json").exists()


def test_a_render_reports_the_picture_and_the_composite(tmp_path, monkeypatch):
    """The two long encodes of a render each run as a job, measured against the film, with numbers an agent can poll."""
    from test_locks import small_project

    from altocue import render

    monkeypatch.setenv("HOME", str(tmp_path))
    project = small_project(tmp_path)
    seen = []

    def job():
        current = ffmpeg._JOB.get()
        return (current.label, current.seconds, current.file) if current else None

    monkeypatch.setattr(render.frame, "frame_project", lambda project: None)
    monkeypatch.setattr(render.plate, "build", lambda project, draft: seen.append(job()) or (tmp_path / "p.mp4", tmp_path / "v.wav"))
    monkeypatch.setattr(render.caption, "build", lambda project: seen.append(job()))
    monkeypatch.setattr(render.timelines, "build", lambda project, **_: {"tail": 0, "items": [], "inserts": [], "total": 1.5, "sfx": False})
    monkeypatch.setattr(render.motion, "render_layers", lambda project, renderer, draft, timeline: None)
    monkeypatch.setattr(render.mix, "mix_bed", lambda *args, **kwargs: None)
    monkeypatch.setattr(render, "compose_args", lambda *args: [])
    monkeypatch.setattr(render.ffmpeg, "run", lambda args: seen.append(job()))
    render.build(project)
    said = project.work / "progress.json"
    assert seen == [("Building the picture", 1.0, said), None, ("Compositing", 1.5, said)]
    assert ffmpeg._JOB.get() is None


def test_a_real_render_reports_both_jobs_and_still_makes_the_film(tmp_path, monkeypatch, chatty, capsys):
    """The plate is many encodes, a sound mix and a stream copy. Its lines never pass 100% and the film comes out whole."""
    from conftest import make_clip

    from altocue import render
    from altocue.ingest import ingest
    from altocue.models import Edl, Segment
    from altocue.project import Project

    monkeypatch.setenv("HOME", str(tmp_path))
    source = make_clip(tmp_path / "take.mp4", seconds=4.0, size="320x568")
    project = Project.create(tmp_path / "video", aspect="9:16")
    (project.sources / "take.mp4").symlink_to(source)
    clip = ingest(project)[0]
    project.save_words(clip.id, [], "test")
    ffmpeg.extract_wav(source, project.audio_dir / "take.wav", rate=48000)
    project.save_edl(Edl(fps=30, segments=[Segment(id="s001", clip=clip.id, start=0.0, end=2.0), Segment(id="s002", clip=clip.id, start=2.2, end=3.7)]))
    capsys.readouterr()
    film = render.build(project, renderer="basic")
    said = capsys.readouterr().err
    picture = [int(found) for found in re.findall(r"^Building the picture: (\d+)%", said, re.MULTILINE)]
    composite = [int(found) for found in re.findall(r"^Compositing: (\d+)%", said, re.MULTILINE)]
    assert picture and picture == sorted(picture) and 90 <= picture[-1] <= 100
    assert composite and composite == sorted(composite) and 90 <= composite[-1] <= 100
    assert ffmpeg.probe_clip(film).duration == pytest.approx(3.5, abs=0.1)
    record = json.loads((project.work / "progress.json").read_text())
    assert record["label"] == "Compositing" and record["running"] is False
