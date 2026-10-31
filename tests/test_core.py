from __future__ import annotations

import argparse
import json

import numpy as np
import pytest

from altocue import ffmpeg
from altocue.cli import main
from altocue.errors import AltocueError
from altocue.models import Edl, Removed, Segment
from altocue.project import Project, nearest_rate


def test_new_project_probes_clip(tmp_path, clip, capsys):
    folder = tmp_path / "video"
    assert main(["new", str(folder), "--input", str(clip), "--brief", "A test"]) == 0
    project = Project.open(folder)
    found = project.clips()
    assert [c.id for c in found] == ["take-one"]
    assert found[0].has_audio and found[0].fps == 30
    assert project.fps == 30
    assert project.size == (1080, 1920)
    assert "A test" in (folder / "brief.md").read_text()
    assert "Project ready" in capsys.readouterr().out


def test_new_refuses_existing_project(tmp_path, clip):
    folder = tmp_path / "video"
    main(["new", str(folder), "--input", str(clip)])
    assert main(["new", str(folder), "--input", str(clip)]) == 1


def test_missing_input_is_reported(tmp_path, capsys):
    assert main(["new", str(tmp_path / "v"), "--input", str(tmp_path / "nope.mp4")]) == 1
    assert "Not found" in capsys.readouterr().err


def test_edl_round_trips():
    edl = Edl(fps=30, segments=[Segment(id="s1", clip="a", start=1, end=2.5)], removed=[Removed(clip="a", start=0, end=1, kind="silence", evidence="gap")])
    again = Edl.from_dict(json.loads(json.dumps(edl.to_dict())))
    assert again == edl and again.duration == 1.5


def test_nearest_rate_snaps_camera_rates():
    assert nearest_rate(23.976) == 24 and nearest_rate(29.97) == 30 and nearest_rate(59.94) == 60


def test_wav_round_trip(tmp_path):
    samples = np.sin(np.linspace(0, 100, 48000)).astype(np.float32) * 0.5
    path = ffmpeg.write_wav(tmp_path / "a.wav", samples, 48000)
    back, rate = ffmpeg.read_wav(path)
    assert rate == 48000 and np.allclose(back, samples, atol=1e-3)


def test_loudness_measures(tmp_path, clip):
    wav = ffmpeg.extract_wav(clip, tmp_path / "a.wav")
    measured = ffmpeg.loudness(wav)
    assert set(measured) == {"integrated", "range", "true_peak"}


def test_probe_rejects_non_media(tmp_path):
    bad = tmp_path / "bad.mp4"
    bad.write_text("not video")
    with pytest.raises(AltocueError):
        ffmpeg.probe_clip(bad)


def test_filters_list_has_what_we_rely_on():
    for name in ("lut3d", "loudnorm", "overlay", "afftdn", "alimiter", "sidechaincompress"):
        assert ffmpeg.has_filter(name), name


def test_run_can_reach_the_review_stage(tmp_path, monkeypatch):
    from altocue import review, run

    project = Project.create(tmp_path / "video")
    seen = {}
    monkeypatch.setattr(review, "run", lambda args: seen.update(vars(args)) or 0)
    run.stages(project, argparse.Namespace())["review"]()
    assert {"project", "file", "reviewer", "ingest", "upload", "paid_key", "editor_vendor", "reset_rounds"} <= set(seen)


def test_run_stops_at_a_stage_that_fails(tmp_path, monkeypatch):
    from altocue import run

    project = Project.create(tmp_path / "video")
    ran = []
    table = {name: (lambda n=name: ran.append(n) or (1 if n == "qa" else None)) for name in run.ORDER}
    monkeypatch.setattr(run, "stages", lambda project, args: table)
    args = argparse.Namespace(project=str(project.root), start="master", end="review", draft=False)
    assert run.run(args) == 1
    assert ran == ["master", "qa"]


def test_run_keeps_an_existing_cut_and_its_rulings(tmp_path, capsys):
    from altocue import run

    project = Project.create(tmp_path / "video")
    project.save_edl(Edl(fps=30, segments=[Segment(id="s1", clip="a", start=0, end=1)]))
    run.stages(project, argparse.Namespace(start="ingest"))["cut"]()
    assert "Keeping the cut" in capsys.readouterr().out
    assert project.edl().segments[0].id == "s1"


def test_a_fresh_cut_saves_the_old_one(tmp_path, monkeypatch, capsys):
    from altocue import cut

    project = Project.create(tmp_path / "video")
    project.save_edl(Edl(fps=30, segments=[Segment(id="s1", clip="a", start=0, end=1)]))
    monkeypatch.setattr(cut, "propose", lambda project: project.edl())
    monkeypatch.setattr(cut, "refuse", lambda project, edl: None)
    monkeypatch.setattr(cut, "write_report", lambda project, edl: "r")
    monkeypatch.setattr(cut, "write_review", lambda project, edl: "v")
    assert cut.run(argparse.Namespace(project=str(project.root), remove=None, apply_review=None, reason="")) == 0
    assert (project.work / "edl-previous.json").exists()
    assert "earlier cut" in capsys.readouterr().out
