"""`altocue watch`: frames for the editor's own eyes, of the recordings and of a render."""

from __future__ import annotations

import json

import pytest
from canaries import Case, Recipe, build
from conftest import make_clip
from PIL import Image
from test_cut import timed

from altocue import ffmpeg, watch
from altocue.cli import main
from altocue.errors import AltocueError
from altocue.project import Project

TALK = "First we plan the work. Then we film the idea. Next we cut the pauses. Finally we ship the thing."


@pytest.fixture
def recorded(tmp_path):
    source = make_clip(tmp_path / "take.mp4", seconds=7.0, size="320x568")
    project = Project.create(tmp_path / "video")
    project.save_clips([ffmpeg.probe_clip(source, clip_id="take")])
    project.save_words("take", timed(TALK), "test")
    return project


@pytest.fixture(scope="module")
def film(tmp_path_factory):
    return build(Case("watch", "", Recipe(seconds=3.0, labels=["", "", ""], face=False)), tmp_path_factory.mktemp("watch"))


def test_every_line_of_a_recording_becomes_a_tile_with_its_words(recorded):
    assert main(["watch", "--sources", "-p", str(recorded.root)]) == 0
    folder = recorded.work / "watch" / "sources"
    sheets = sorted(folder.glob("sources-take-*.jpg"))
    assert len(sheets) == 1
    with Image.open(sheets[0]) as sheet:
        assert max(sheet.size) <= watch.SHEET
    rows = json.loads((folder / "sources.json").read_text())["lines"]
    assert [row["line"] for row in rows] == ["L0001", "L0002", "L0003", "L0004"]
    assert rows[1]["text"] == "Then we film the idea."
    assert rows[0]["words"] == 5 and rows[0]["seconds"] == pytest.approx(1.5, abs=0.01)
    assert isinstance(rows[0]["level_db"], float) and rows[0]["brightness"] is not None
    assert rows[0]["cut"] == "", "there is no cut yet, so no line is marked in or out"
    assert "sources-take-01.jpg" in (folder / "index.md").read_text()


def test_lines_say_whether_the_current_cut_plays_them(recorded):
    from altocue.models import Edl, Segment

    recorded.save_edl(Edl(fps=30, segments=[Segment(id="s001", clip="take", start=0.0, end=1.6)]))
    main(["watch", "--sources", "-p", str(recorded.root)])
    rows = json.loads((recorded.work / "watch" / "sources" / "sources.json").read_text())["lines"]
    assert [row["cut"] for row in rows] == ["in", "out", "out", "out"]


def test_a_range_narrows_what_is_looked_at(recorded):
    main(["watch", "--sources", "--from", "1.4", "--to", "3.1", "-p", str(recorded.root)])
    rows = json.loads((recorded.work / "watch" / "sources" / "sources.json").read_text())["lines"]
    assert [row["line"] for row in rows] == ["L0002", "L0003"]


def test_too_many_lines_asks_for_a_narrower_look(recorded, monkeypatch):
    monkeypatch.setattr(watch, "MOST_LINES", 3)
    with pytest.raises(AltocueError, match="too many"):
        watch.watch_sources(recorded, recorded.work)


def test_an_unknown_clip_names_the_real_ones(recorded):
    with pytest.raises(AltocueError, match="No clip named b"):
        watch.watch_sources(recorded, recorded.work, only="b")


def test_a_render_gives_its_opening_its_cuts_and_its_ending(film):
    assert main(["watch", "-p", str(film.project.root)]) == 0
    folder = film.project.work / "watch" / "render"
    found = json.loads((folder / "index.json").read_text())
    passes = {row["pass"] for row in found["sheets"]}
    assert {"opening", "joins", "ending"} <= passes
    assert len(found["cuts"]) == 2
    for row in found["sheets"]:
        assert (folder / row["file"]).is_file()
    text = (folder / "index.md").read_text()
    assert "## Joins" in text and "cuts-01.jpg" in text


def test_exact_moments_come_back_full_size(film):
    assert main(["watch", "--at", "1.0", "2.0", "-p", str(film.project.root)]) == 0
    folder = film.project.work / "watch" / "moments"
    frames = sorted(folder.glob("at-*.jpg"))
    assert len(frames) == 2
    with Image.open(frames[0]) as frame:
        assert frame.size == (film.project.size[0], film.project.size[1]) or frame.width >= 320


def test_with_nothing_rendered_it_says_what_to_run(recorded):
    with pytest.raises(AltocueError, match="Nothing has been rendered"):
        watch.newest_render(recorded)


def test_long_words_are_cut_with_dots_and_never_dropped_silently():
    rows = watch.fit("one two three four five six seven eight nine ten", watch._font(15), 80, 2)
    assert len(rows) == 2 and rows[-1].endswith("...")
