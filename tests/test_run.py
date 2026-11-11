"""`altocue run` says where its time went."""

from __future__ import annotations

import argparse
import json
from datetime import datetime

import pytest

from altocue import run
from altocue.errors import AltocueError
from altocue.project import Project


def stub_stages(monkeypatch, *, qa=lambda project, file: []):
    from altocue import master
    from altocue import qa as gates

    monkeypatch.setattr(master, "run", lambda args: 0)
    monkeypatch.setattr(gates, "run_gates", qa)


def test_run_records_the_time_of_each_stage(tmp_path, monkeypatch, capsys):
    project = Project.create(tmp_path / "video")
    stub_stages(monkeypatch)
    ticks = iter([10.0, 12.5, 20.0, 20.25])
    monkeypatch.setattr(run.time, "perf_counter", lambda: next(ticks))
    args = argparse.Namespace(project=str(project.root), start="master", end="qa", draft=False, renderer="auto")
    assert run.run(args) == 0
    record = json.loads((project.work / "timings.json").read_text())
    assert [(row["stage"], row["seconds"]) for row in record["stages"]] == [("master", 2.5), ("qa", 0.25)]
    assert record["total"] == 2.75
    assert all(datetime.fromisoformat(row["started"]).tzinfo for row in record["stages"])
    lines = capsys.readouterr().out.splitlines()
    assert lines[-1] == "Time: 2.8 s. master 2.5 s, qa 0.2 s. Kept in work/timings.json."


def test_a_run_that_stops_or_fails_still_says_where_its_time_went(tmp_path, monkeypatch, capsys):
    from altocue.qa import GateResult

    project = Project.create(tmp_path / "video")
    stub_stages(monkeypatch, qa=lambda project, file: [GateResult(id="frozen", passed=False)])
    args = argparse.Namespace(project=str(project.root), start="master", end="qa", draft=False, renderer="auto")
    assert run.run(args) == 1
    assert [row["stage"] for row in json.loads((project.work / "timings.json").read_text())["stages"]] == ["master", "qa"]
    assert capsys.readouterr().out.splitlines()[-1].startswith("Time: ")

    def broken(project, file):
        raise AltocueError("The render is missing.", fix="Run `altocue render`.")

    stub_stages(monkeypatch, qa=broken)
    again = argparse.Namespace(project=str(project.root), start="qa", end="qa", draft=False, renderer="auto")
    with pytest.raises(AltocueError):
        run.run(again)
    assert [row["stage"] for row in json.loads((project.work / "timings.json").read_text())["stages"]] == ["qa"]
    assert capsys.readouterr().out.splitlines()[-1].startswith("Time: ")


def test_long_stages_read_in_minutes_and_the_slowest_comes_first():
    spent = [{"stage": "ingest", "seconds": 1.04}, {"stage": "render", "seconds": 125.4}]
    assert run.time_line(spent) == "Time: 2 min 6 s. render 2 min 5 s, ingest 1.0 s. Kept in work/timings.json."
