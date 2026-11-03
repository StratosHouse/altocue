from __future__ import annotations

import subprocess
import sys
import time
from contextlib import contextmanager

from altocue import caption as captions
from altocue import ffmpeg, frame, locks, mix, motion, plate, render
from altocue import timeline as timelines
from altocue.models import Clip, Edl, Segment
from altocue.motion.layer import Layer
from altocue.project import Project

HOLD = """
import sys
from altocue import locks
with locks.heavy("a test"):
    print("held", flush=True)
    sys.stdin.readline()
"""


def test_a_second_heavy_job_waits_its_turn(tmp_path, monkeypatch, capsys):
    monkeypatch.setenv("HOME", str(tmp_path))
    monkeypatch.delenv("ALTOCUE_HEAVY_JOBS", raising=False)  # a render PC sets more slots for every job it runs
    holder = subprocess.Popen([sys.executable, "-c", HOLD], stdin=subprocess.PIPE, stdout=subprocess.PIPE, text=True)
    assert holder.stdout.readline().strip() == "held"
    second = subprocess.Popen(
        [sys.executable, "-c", 'from altocue import locks\nwith locks.heavy("the second job"):\n    print("got it")'],
        stdout=subprocess.PIPE,
        text=True,
    )
    time.sleep(0.6)
    assert second.poll() is None
    holder.communicate("\n")
    out, _ = second.communicate(timeout=10)
    assert "Waiting for another Altocue job" in out and "got it" in out


def test_a_machine_can_allow_several_heavy_jobs_at_once(tmp_path, monkeypatch):
    """A render PC with room for more sets ALTOCUE_HEAVY_JOBS; the third job still waits for one of the two."""
    monkeypatch.setenv("HOME", str(tmp_path))
    monkeypatch.setenv("ALTOCUE_HEAVY_JOBS", "2")
    first = subprocess.Popen([sys.executable, "-c", HOLD], stdin=subprocess.PIPE, stdout=subprocess.PIPE, text=True)
    second = subprocess.Popen([sys.executable, "-c", HOLD], stdin=subprocess.PIPE, stdout=subprocess.PIPE, text=True)
    assert first.stdout.readline().strip() == "held"
    assert second.stdout.readline().strip() == "held"
    third = subprocess.Popen(
        [sys.executable, "-c", 'from altocue import locks\nwith locks.heavy("the third job"):\n    print("got it")'],
        stdout=subprocess.PIPE,
        text=True,
    )
    time.sleep(0.8)
    assert third.poll() is None
    first.communicate("\n")
    out, _ = third.communicate(timeout=10)
    second.communicate("\n")
    assert "Waiting for another Altocue job" in out and "got it" in out


def test_one_heavy_job_is_the_default(monkeypatch):
    monkeypatch.delenv("ALTOCUE_HEAVY_JOBS", raising=False)
    assert locks.heavy_jobs() == 1
    monkeypatch.setenv("ALTOCUE_HEAVY_JOBS", "nonsense")
    assert locks.heavy_jobs() == 1
    monkeypatch.setenv("ALTOCUE_HEAVY_JOBS", "4")
    assert locks.heavy_jobs() == 4


NESTED = """
from altocue import locks
with locks.heavy("the outer job"):
    with locks.heavy("the inner job"):
        print("nested", flush=True)
"""


def test_a_job_that_holds_the_lock_can_call_another_heavy_step(tmp_path, monkeypatch):
    monkeypatch.setenv("HOME", str(tmp_path))
    done = subprocess.run([sys.executable, "-c", NESTED], capture_output=True, text=True, timeout=10)
    assert done.stdout.strip() == "nested"


def counted_lock(monkeypatch):
    state = {"depth": 0, "labels": []}

    @contextmanager
    def fake(label):
        state["depth"] += 1
        state["labels"].append(label)
        try:
            yield
        finally:
            state["depth"] -= 1

    monkeypatch.setattr(locks, "heavy", fake)
    return state


def small_project(tmp_path):
    project = Project.create(tmp_path / "video")
    project.save_clips([Clip(id="a", path=str(tmp_path / "a.mp4"), duration=2.0, fps=30, width=1080, height=1920)])
    project.save_edl(Edl(fps=30, segments=[Segment(id="s001", clip="a", start=0.0, end=1.0)]))
    return project


def test_the_plate_encodes_under_the_heavy_lock(tmp_path, monkeypatch):
    state = counted_lock(monkeypatch)
    seen = []
    project = small_project(tmp_path)
    monkeypatch.setattr(plate, "build_segment", lambda *args, **kwargs: seen.append(state["depth"]) or tmp_path / "s.mp4")
    monkeypatch.setattr(plate.ffmpeg, "run", lambda args: seen.append(state["depth"]))
    monkeypatch.setattr(plate.mix, "mix_voice", lambda *args: None)
    plate.build(project)
    assert seen and all(depth > 0 for depth in seen)


def test_the_composite_runs_under_the_heavy_lock(tmp_path, monkeypatch):
    state = counted_lock(monkeypatch)
    seen = []
    project = small_project(tmp_path)
    monkeypatch.setattr(frame, "frame_project", lambda project: None)
    monkeypatch.setattr(plate, "build", lambda project, draft: (tmp_path / "p.mp4", tmp_path / "v.wav"))
    monkeypatch.setattr(captions, "build", lambda project: [])
    monkeypatch.setattr(timelines, "build", lambda project, **_: {"tail": 0, "items": [], "inserts": [], "total": 1.0, "sfx": False})
    monkeypatch.setattr(motion, "render_layers", lambda project, renderer, draft: Layer(tmp_path / "l.mov", "basic"))
    monkeypatch.setattr(mix, "mix_bed", lambda *args, **kwargs: None)
    monkeypatch.setattr(render, "compose_args", lambda *args: [])
    monkeypatch.setattr(ffmpeg, "run", lambda args: seen.append(state["depth"]))
    render.build(project)
    assert seen == [1]


def test_workers_follow_memory(monkeypatch):
    monkeypatch.delenv("ALTOCUE_RENDER_WORKERS", raising=False)
    monkeypatch.setattr(locks, "memory_gb", lambda: 16.0)
    assert locks.render_workers() == 3
    monkeypatch.setattr(locks, "memory_gb", lambda: 8.0)
    assert locks.render_workers() == 1
    monkeypatch.setattr(locks, "memory_gb", lambda: 64.0)
    assert locks.render_workers() == 4


def test_workers_can_be_set_by_hand(monkeypatch):
    monkeypatch.setenv("ALTOCUE_RENDER_WORKERS", "2")
    assert locks.render_workers() == 2
