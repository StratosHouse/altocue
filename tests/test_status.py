"""`altocue status`: what is done, what is stale, and the one command to run next."""

from __future__ import annotations

import json
import os
import time
from pathlib import Path

import pytest
from conftest import make_clip

from altocue import frame, grade, qa, render, status
from altocue.cli import main
from altocue.ingest import ingest
from altocue.models import Edl, Removed, Segment, Word
from altocue.project import Project, write_json

BASE = int(time.time()) - 1000
LATER = BASE + 900


def states(project: Project) -> dict[str, str]:
    return {stage.name: stage.state for stage in status.collect(project)}


@pytest.fixture
def complete(tmp_path: Path) -> Project:
    """Every stage's real output on disk, oldest first, so each test only breaks one link."""
    source = make_clip(tmp_path / "take.mp4", seconds=1.0, size="160x284")
    os.utime(source, (1000, 1000))
    project = Project.create(tmp_path / "video")
    (project.sources / "take.mp4").symlink_to(source)
    ingest(project)
    project.save_words("take", [Word("hello", 0.1, 0.4)], "test")
    (project.audio_dir / "take.wav").write_bytes(b"sound")
    project.save_edl(Edl(fps=30, segments=[Segment(id="s001", clip="take", start=0.0, end=0.9)]))
    grade.grade_project(project)
    frame.frame_project(project, force=True)
    (project.out / "render.mp4").write_bytes(b"picture")
    write_json(project.work / "render.json", {"cut": project.cut_mark()})
    (project.out / "final.mp4").write_bytes(b"finished")
    digest = qa.sha256(project.out / "final.mp4")
    write_json(project.review_dir / "qa.json", {"sha256": digest, "passed": True, "gates": []})
    write_json(project.review_dir / "report.json", {"file_sha256": digest, "verdict": "SHIP"})
    write_json(project.root / "deliver" / "deliver.json", {"final_sha256": digest})
    order = [project.work / "words" / "take.json", project.audio_dir / "take.wav", project.edl_path, *project.lut_dir.glob("*"), project.work / "frame.json"]
    order += [project.out / "render.mp4", project.work / "render.json", project.out / "final.mp4"]
    for step, path in enumerate(order):
        os.utime(path, (BASE + step, BASE + step))
    return project


def test_a_new_project_starts_at_ingest(tmp_path, capsys):
    project = Project.create(tmp_path / "video")
    assert set(states(project).values()) == {"not run"}
    assert status.next_command(status.collect(project), ".") == "altocue ingest -p ."


def test_a_finished_project_is_all_done(complete):
    assert set(states(complete).values()) == {"done"}
    assert status.next_command(status.collect(complete), ".") is None


def test_a_changed_source_makes_the_words_stale_and_says_force(complete):
    source = Path(complete.clips()[0].path)
    os.utime(source, (LATER, LATER))
    found = states(complete)
    assert found["ingest"] == "stale" or found["transcribe"] == "stale"
    (complete.media_path).touch()
    os.utime(complete.media_path, (LATER + 1, LATER + 1))
    assert status.next_command(status.collect(complete), ".") == "altocue transcribe -p . --force"


def test_a_missing_transcript_is_not_run(complete):
    (complete.words_dir / "take.json").unlink()
    assert states(complete)["transcribe"] == "not run"


def test_a_new_cut_makes_the_framing_and_render_stale_by_the_cut_mark(complete):
    edl = complete.edl()
    edl.segments[0].end = 0.8
    complete.save_edl(edl)
    found = states(complete)
    assert found["frame"] == "stale" and found["render"] == "stale"
    assert "cut changed" in next(s.detail for s in status.collect(complete) if s.name == "render")
    assert status.next_command(status.collect(complete), "video") == "altocue grade -p video"


def test_a_zoom_edit_does_not_count_as_a_new_cut_but_still_needs_a_render(complete):
    edl = complete.edl()
    edl.segments[0].zoom_from = edl.segments[0].zoom_to = 1.1
    complete.save_edl(edl)
    found = states(complete)
    assert found["frame"] == "done" and found["render"] == "stale"
    assert "cut changed" not in next(s.detail for s in status.collect(complete) if s.name == "render")


def test_a_changed_motion_file_makes_the_render_stale(complete):
    (complete.root / "motion.json").write_text("{}")
    os.utime(complete.root / "motion.json", (LATER, LATER))
    assert states(complete)["render"] == "stale"
    assert status.next_command(status.collect(complete), ".") == "altocue render -p ."


def test_a_newer_render_makes_the_master_stale(complete):
    os.utime(complete.out / "render.mp4", (LATER, LATER))
    assert states(complete)["master"] == "stale"


def test_qa_review_and_deliver_go_stale_by_the_films_real_content(complete):
    (complete.out / "final.mp4").write_bytes(b"a different film")
    os.utime(complete.out / "final.mp4", (BASE + 7, BASE + 7))
    found = states(complete)
    assert found["qa"] == "stale" and found["review"] == "stale" and found["deliver"] == "stale"


def test_touching_the_final_without_changing_it_is_not_stale(complete):
    os.utime(complete.out / "final.mp4", (BASE + 9, BASE + 9))
    assert states(complete)["qa"] == "done"


def test_a_stale_stage_takes_everything_after_it_with_it(complete):
    os.utime(complete.out / "render.mp4", (LATER, LATER))
    stages = {s.name: s for s in status.collect(complete)}
    assert stages["qa"].state == "stale" and "master" in stages["qa"].detail


def test_a_failed_qa_is_reported_in_the_detail(complete):
    digest = qa.sha256(complete.out / "final.mp4")
    gates = [{"id": "loudness", "passed": False, "skipped": False}, {"id": "words", "passed": False, "skipped": True}]
    write_json(complete.review_dir / "qa.json", {"sha256": digest, "passed": False, "gates": gates})
    detail = next(s.detail for s in status.collect(complete) if s.name == "qa")
    assert "failed: loudness" in detail and "not checked: words" in detail


def test_a_verdict_shows_beside_the_review(complete):
    assert "SHIP" in next(s.detail for s in status.collect(complete) if s.name == "review")


def test_a_pack_waiting_for_a_reviewer_says_how_to_finish(complete):
    (complete.review_dir / "report.json").unlink()
    (complete.review_dir / "PACK.md").write_text("pack")
    assert status.next_command(status.collect(complete), ".") == "altocue review -p . --ingest review/verdict.json"


def test_rulings_still_waiting_are_named_on_the_cut(complete):
    edl = complete.edl()
    edl.removed.append(Removed("take", 0.5, 0.6, "retake", "a guess", "review"))
    complete.save_edl(edl)
    os.utime(complete.edl_path, (BASE + 2, BASE + 2))
    assert "1 item waits" in next(s.detail for s in status.collect(complete) if s.name == "cut")


def test_stale_b_roll_shows_only_when_there_is_b_roll(complete):
    assert "broll" not in states(complete)
    write_json(complete.root / "broll.json", {"cut": "older", "inserts": [{"id": "b1"}]})
    assert states(complete)["broll"] == "stale"
    write_json(complete.root / "broll.json", {"cut": complete.cut_mark(), "inserts": [{"id": "b1"}]})
    os.utime(complete.root / "broll.json", (BASE + 4, BASE + 4))
    assert states(complete)["broll"] == "done"


def test_a_project_that_is_not_ingested_does_not_crash(tmp_path):
    project = Project.create(tmp_path / "video")
    (project.out / "final.mp4").write_bytes(b"x")
    assert status.collect(project)


def test_the_command_lists_every_stage_and_one_next_line(complete, capsys):
    os.utime(complete.out / "render.mp4", (LATER, LATER))
    assert main(["status", "-p", str(complete.root)]) == 0
    out = capsys.readouterr().out
    lines = [line for line in out.splitlines() if line.strip()]
    assert any(line.startswith("master") and "stale" in line for line in lines)
    assert sum(line.startswith("Next:") for line in lines) == 1
    assert f"Next: altocue master -p {complete.root}" in out


def test_render_records_the_cut_it_was_made_from(tmp_path, monkeypatch):
    monkeypatch.setenv("HOME", str(tmp_path))
    from test_locks import small_project

    project = small_project(tmp_path)
    monkeypatch.setattr(render.frame, "frame_project", lambda project: None)
    monkeypatch.setattr(render.plate, "build", lambda project, draft: (tmp_path / "p.mp4", tmp_path / "v.wav"))
    monkeypatch.setattr(render.caption, "build", lambda project: [])
    monkeypatch.setattr(render.timelines, "build", lambda project, **_: {"tail": 0, "items": [], "inserts": [], "total": 1.0, "sfx": False})
    monkeypatch.setattr(render.motion, "render_layers", lambda project, renderer, draft: None)
    monkeypatch.setattr(render.mix, "mix_bed", lambda *args, **kwargs: None)
    monkeypatch.setattr(render, "compose_args", lambda *args: [])
    monkeypatch.setattr(render.ffmpeg, "run", lambda args: None)
    render.build(project)
    assert json.loads((project.work / "render.json").read_text())["cut"] == project.cut_mark()


def test_a_changed_brief_or_rules_file_makes_qa_stale(complete):
    for name in ("brief.md", "rules.json"):
        (complete.root / name).write_text("{}")
        os.utime(complete.root / name, (time.time() + 50, time.time() + 50))
        assert states(complete)["qa"] == "stale"
        assert f"{name} changed" in next(s.detail for s in status.collect(complete) if s.name == "qa")
        (complete.root / name).unlink()


def test_a_changed_brief_makes_the_review_stale(complete):
    (complete.root / "brief.md").write_text("new")
    os.utime(complete.root / "brief.md", (time.time() + 50, time.time() + 50))
    write_json(complete.review_dir / "qa.json", json.loads((complete.review_dir / "qa.json").read_text()))
    assert states(complete)["review"] == "stale"


def shots(project: Project, *names: str) -> None:
    write_json(project.root / "shots.json", {"shots": [{"id": name} for name in names]})


def test_generated_shots_show_only_when_there_is_a_shot_list(complete):
    assert "generate" not in states(complete)
    write_json(complete.root / "shots.json", {"shots": []})
    assert "generate" not in states(complete)


def test_a_shot_still_to_generate_is_named_and_comes_next(complete):
    shots(complete, "a", "b")
    found = {s.name: s for s in status.collect(complete)}
    assert found["generate"].state == "not run" and "a, b" in found["generate"].detail
    assert status.next_command(status.collect(complete), ".") == "altocue generate -p ."


def test_a_shot_that_failed_its_check_is_stale(complete):
    from altocue.generate import store

    shots(complete, "a")
    asset = complete.root / "a.mp4"
    asset.write_bytes(b"x")
    store.atomic_json(store.manifest_path(complete), {"shots": {"a": {"asset": "a.mp4", "check_passed": False}}})
    found = {s.name: s for s in status.collect(complete)}
    assert found["generate"].state == "stale" and "failed its check" in found["generate"].detail


def test_generated_shots_all_on_disk_are_done(complete):
    from altocue.generate import store

    shots(complete, "a")
    (complete.root / "a.mp4").write_bytes(b"x")
    store.atomic_json(store.manifest_path(complete), {"shots": {"a": {"asset": "a.mp4", "check_passed": True}}})
    assert states(complete)["generate"] == "done"


def page_for(project: Project, digest: str) -> Path:
    target = project.review_dir / "page.html"
    target.write_text(f'<!doctype html><head><meta name="altocue-film" content="{digest}"></head>' + "x" * 100)
    return target


def test_the_page_shows_only_once_one_was_written(complete):
    assert "page" not in states(complete)


def test_a_page_for_this_film_is_done(complete):
    page_for(complete, qa.sha256(complete.out / "final.mp4"))
    assert states(complete)["page"] == "done"


def test_a_page_for_another_film_is_stale_and_says_how_to_redo_it(complete):
    page_for(complete, "0" * 64)
    found = {s.name: s for s in status.collect(complete)}
    assert found["page"].state == "stale" and "film changed" in found["page"].detail
    assert status.next_command(status.collect(complete), ".") == "altocue review -p . --page"


def test_a_page_older_than_the_verdict_is_stale(complete):
    target = page_for(complete, qa.sha256(complete.out / "final.mp4"))
    os.utime(target, (BASE, BASE))
    assert states(complete)["page"] == "stale"
