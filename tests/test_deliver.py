"""Deliverables from one finished film. The master is only ever read."""

from __future__ import annotations

import hashlib
import json
import os
import re
import subprocess
from pathlib import Path

import pytest
from conftest import make_clip

from altocue import cut, deliver, ffmpeg, frame, master, platforms, render
from altocue.cli import main
from altocue.errors import AltocueError
from altocue.ingest import ingest
from altocue.models import Edl, Segment, Word
from altocue.project import Project

OPENING = "Most films lose people in the first three seconds."
CLOSING = "So we cut the slow start and kept going."


def timed_words(text: str, start: float, step: float) -> list[Word]:
    return [Word(token, round(start + i * step, 3), round(start + (i + 1) * step - 0.02, 3)) for i, token in enumerate(text.split())]


@pytest.fixture
def finished(tmp_path: Path) -> Project:
    """A two-shot film, rendered with the basic renderer and mastered, with no transcriber involved."""
    source = make_clip(tmp_path / "take.mp4", seconds=4.0, size="320x568")
    project = Project.create(tmp_path / "video", aspect="9:16")
    (project.sources / "take.mp4").symlink_to(source)
    clip = ingest(project)[0]
    project.save_words(clip.id, timed_words(OPENING, 0.1, 0.2) + timed_words(CLOSING, 2.3, 0.15), "test")
    ffmpeg.extract_wav(source, project.audio_dir / "take.wav", rate=48000)
    project.save_edl(Edl(fps=30, segments=[Segment(id="s001", clip=clip.id, start=0.0, end=2.0), Segment(id="s002", clip=clip.id, start=2.2, end=3.7)]))
    render.build(project, renderer="basic")
    master.master_file(project.out / "render.mp4", project.out / "final.mp4")
    return project


def fingerprint(project: Project) -> dict[str, str]:
    """Every file outside deliver/ and the version workspace, by content and time."""
    found = {}
    for path in sorted(project.root.rglob("*")):
        parts = path.relative_to(project.root).parts
        if not path.is_file() or path.is_symlink() or parts[0] == "deliver" or parts[:2] == ("work", "versions"):
            continue
        found[str(path)] = f"{hashlib.sha256(path.read_bytes()).hexdigest()}:{path.stat().st_mtime_ns}"
    return found


def streams(path: Path) -> list[dict]:
    return ffmpeg.probe_json(path)["streams"]


def chapters_of(path: Path) -> list[dict]:
    done = subprocess.run(["ffprobe", "-v", "error", "-show_chapters", "-of", "json", str(path)], capture_output=True, text=True, check=True)
    return json.loads(done.stdout)["chapters"]


def stamps(text: str) -> list[tuple[float, float]]:
    pattern = r"(\d\d):(\d\d):(\d\d)[,.](\d{3}) --> (\d\d):(\d\d):(\d\d)[,.](\d{3})"
    found = []
    for groups in re.findall(pattern, text):
        n = [int(v) for v in groups]
        found.append((n[0] * 3600 + n[1] * 60 + n[2] + n[3] / 1000, n[4] * 3600 + n[5] * 60 + n[6] + n[7] / 1000))
    return found


# Subtitles and the soft track


def test_deliver_writes_subtitles_a_soft_track_and_a_report(finished, capsys):
    assert main(["deliver", "-p", str(finished.root)]) == 0
    folder = finished.root / "deliver"
    srt, vtt = (folder / "subtitles.srt").read_text(), (folder / "subtitles.vtt").read_text()
    assert srt.startswith("1\n00:00:00,100 --> ") and "Most films lose people" in srt
    assert vtt.startswith("WEBVTT\n\n00:00:00.100 --> ")
    subtitle = next(s for s in streams(folder / "film.mp4") if s["codec_type"] == "subtitle")
    assert subtitle["codec_name"] == "mov_text" and subtitle["tags"]["language"] == "eng"
    assert subtitle["disposition"]["default"] == 0  # the captions are burned in, so a player must not double them
    report = json.loads((folder / "deliver.json").read_text())
    assert report["subtitles"]["reading"]["cues"] >= 2 and report["film"]["file"] == "film.mp4"
    assert "subtitles.srt" in capsys.readouterr().out


def test_no_cue_crosses_a_cut_in_a_real_project(finished):
    main(["deliver", "-p", str(finished.root)])
    cues = stamps((finished.root / "deliver" / "subtitles.srt").read_text())
    cut_at = cut.segment_ends(finished.edl())["s001"]
    assert any(end <= cut_at for _, end in cues) and any(start >= cut_at for start, _ in cues)
    assert not any(start < cut_at < end for start, end in cues)


def test_the_soft_copy_keeps_the_masters_picture_and_sound(finished):
    main(["deliver", "-p", str(finished.root)])
    def media_md5(path, stream):
        done = subprocess.run(["ffmpeg", "-v", "error", "-i", str(path), "-map", stream, "-c", "copy", "-f", "md5", "-"], capture_output=True, text=True, check=True)
        return done.stdout.strip()
    film, final = finished.root / "deliver" / "film.mp4", finished.out / "final.mp4"
    assert media_md5(film, "0:v") == media_md5(final, "0:v")
    assert media_md5(film, "0:a") == media_md5(final, "0:a")


def test_a_project_with_no_transcript_still_delivers_and_says_why(tmp_path, finished, capsys):
    (finished.words_dir / "take.json").unlink()
    assert main(["deliver", "-p", str(finished.root)]) == 0
    assert not (finished.root / "deliver" / "subtitles.srt").exists()
    assert "No subtitles" in capsys.readouterr().out


# Chapters


def chapter_rows():
    return [{"title": "Open", "start": 0}, {"title": "Middle", "start": 12.5}, {"title": "End", "start": 30}]


def test_chapters_come_from_the_project_list(tmp_path):
    project = Project.create(tmp_path / "video")
    (project.root / "chapters.json").write_text(json.dumps(chapter_rows()))
    found, source = deliver.chapter_list(project, [], 45.0)
    assert source == "chapters.json"
    assert [(c["title"], c["start"], c["end"]) for c in found] == [("Open", 0.0, 12.5), ("Middle", 12.5, 30.0), ("End", 30.0, 45.0)]


def test_chapters_can_come_from_the_directors_beats(tmp_path):
    project = Project.create(tmp_path / "video")
    (project.root / "beats.json").write_text(json.dumps({"beats": [{"name": "Hook", "start": 0}, {"name": "Turn", "start": {"word": "turn"}}]}))
    words = [{"text": "turn,", "start": 15.0, "end": 15.4}]
    found, source = deliver.chapter_list(project, words, 30.0)
    assert source == "beats.json" and [c["start"] for c in found] == [0.0, 15.0]


def test_a_list_wins_over_beats(tmp_path):
    project = Project.create(tmp_path / "video")
    (project.root / "chapters.json").write_text(json.dumps(chapter_rows()))
    (project.root / "beats.json").write_text(json.dumps({"beats": [{"name": "Hook", "start": 0}]}))
    assert deliver.chapter_list(project, [], 45.0)[1] == "chapters.json"


def test_no_list_means_no_chapters(tmp_path):
    assert deliver.chapter_list(Project.create(tmp_path / "video"), [], 45.0) == ([], "")


def test_the_first_chapter_always_opens_the_film(tmp_path):
    project = Project.create(tmp_path / "video")
    (project.root / "chapters.json").write_text(json.dumps([{"title": "Late", "start": 4}, {"title": "Next", "start": 20}]))
    assert deliver.chapter_list(project, [], 45.0)[0][0]["start"] == 0.0


@pytest.mark.parametrize(
    ("rows", "text"),
    [
        ([{"title": "No time"}], "start"),
        ([{"start": 2}], "title"),
        ([{"title": "Past", "start": 90}], "past the end"),
        ([{"title": "One", "start": 5}, {"title": "Two", "start": 5}], "same moment"),
    ],
)
def test_bad_chapters_are_refused(tmp_path, rows, text):
    project = Project.create(tmp_path / "video")
    (project.root / "chapters.json").write_text(json.dumps(rows))
    with pytest.raises(AltocueError, match=text):
        deliver.chapter_list(project, [], 45.0)


def test_the_youtube_block_follows_youtubes_rules():
    rows = [{"title": "Open", "start": 0.0, "end": 12.5}, {"title": "Middle", "start": 12.5, "end": 75.0}, {"title": "End", "start": 75.0, "end": 3700.0}]
    assert deliver.youtube_problems(rows) == []
    assert deliver.youtube_block(rows) == "00:00:00 Open\n00:00:12 Middle\n00:01:15 End\n"
    assert deliver.youtube_block(rows[:2]) == "00:00 Open\n00:12 Middle\n"


def test_a_list_youtube_would_ignore_says_why():
    short = [{"title": "A", "start": 0.0, "end": 12.0}, {"title": "B", "start": 12.0, "end": 16.0}, {"title": "C", "start": 16.0, "end": 40.0}]
    assert any("10 seconds" in problem for problem in deliver.youtube_problems(short))
    assert any("three" in problem for problem in deliver.youtube_problems(short[:2]))


def test_chapter_titles_are_escaped_for_ffmpeg():
    text = deliver.ffmetadata([{"title": "Q=A; #1", "start": 0.0, "end": 1.5}])
    slash = chr(92)
    assert text.startswith(";FFMETADATA1\n") and f"START=0\nEND=1500\ntitle=Q{slash}=A{slash}; {slash}#1\n" in text


def test_chapters_reach_the_mp4_and_the_description_block(finished):
    rows = [{"title": "Open", "start": 0}, {"title": "Turn", "start": 1.5}, {"title": "End", "start": 2.5}]
    (finished.root / "chapters.json").write_text(json.dumps(rows))
    assert main(["deliver", "-p", str(finished.root)]) == 0
    marks = chapters_of(finished.root / "deliver" / "film.mp4")
    assert [c["tags"]["title"] for c in marks] == ["Open", "Turn", "End"]
    assert abs(float(marks[1]["start_time"]) - 1.5) < 0.01
    report = json.loads((finished.root / "deliver" / "deliver.json").read_text())
    assert report["chapters"]["youtube"]["problems"]  # these run under 10 seconds, so YouTube would ignore them
    assert not (finished.root / "deliver" / "chapters.txt").exists()


def test_a_valid_list_writes_the_description_block(finished, monkeypatch):
    rows = [{"title": "Open", "start": 0}, {"title": "Turn", "start": 1.5}, {"title": "End", "start": 2.5}]
    (finished.root / "chapters.json").write_text(json.dumps(rows))
    monkeypatch.setattr(deliver, "youtube_problems", lambda chapters: [])
    assert main(["deliver", "-p", str(finished.root)]) == 0
    assert (finished.root / "deliver" / "chapters.txt").read_text().startswith("00:00 Open\n")


# Thumbnails and loudness


def test_thumbnails_and_a_contact_sheet_land_in_deliver(finished):
    main(["deliver", "-p", str(finished.root)])
    folder = finished.root / "deliver" / "thumbnails"
    assert (folder / "contact-sheet.jpg").exists()
    report = json.loads((finished.root / "deliver" / "deliver.json").read_text())
    assert all((folder / c["file"]).exists() for c in report["thumbnails"]["candidates"])


def test_the_report_gives_loudness_as_measured_with_its_source(finished):
    main(["deliver", "-p", str(finished.root)])
    report = json.loads((finished.root / "deliver" / "deliver.json").read_text())
    loud = report["film"]["loudness"]
    assert loud["target"] == -14.0 and abs(loud["measured"]["integrated"] + 14) <= 0.5 and loud["passed"] is True
    assert "no primary source" in loud["source"].lower()


def test_a_named_target_remasters_the_copy_and_never_the_final(finished):
    before = fingerprint(finished)
    assert main(["deliver", "-p", str(finished.root), "--loudness", "podcast"]) == 0
    measured = ffmpeg.loudness(finished.root / "deliver" / "film.mp4")
    assert abs(measured["integrated"] + 16) <= 0.5 and measured["true_peak"] < -1
    report = json.loads((finished.root / "deliver" / "deliver.json").read_text())
    assert report["film"]["loudness"]["name"] == "podcast" and "Apple Podcasts" in report["film"]["loudness"]["source"]
    assert fingerprint(finished) == before


# Platform versions


def test_one_edit_becomes_a_file_per_platform_and_the_master_is_untouched(finished):
    before = fingerprint(finished)
    assert main(["deliver", "-p", str(finished.root), "--for", "square,youtube", "--renderer", "basic"]) in (0, 1)
    assert fingerprint(finished) == before
    folder = finished.root / "deliver"
    sizes = {name: next(s for s in streams(folder / f"{name}.mp4") if s["codec_type"] == "video") for name in ("square", "youtube")}
    assert (sizes["square"]["width"], sizes["square"]["height"]) == (1080, 1080)
    assert (sizes["youtube"]["width"], sizes["youtube"]["height"]) == (1920, 1080)
    versions = {v["platform"]: v for v in json.loads((folder / "deliver.json").read_text())["versions"]}
    assert versions["youtube"]["size"] == [1920, 1080]
    assert "estimate" in versions["youtube"]["zone_source"].lower()
    assert {"loudness", "true_peak", "duration", "safe_zone", "face_clear", "graphics_clear", "format"} <= {g["id"] for g in versions["square"]["gates"]}
    assert abs(versions["square"]["loudness"]["measured"]["integrated"] + 14) <= 0.5


def test_a_version_uses_the_frame_stage_not_a_blind_crop(finished, monkeypatch):
    seen = []
    real = frame.frame_project

    def spy(project, force=False):
        seen.append(project.size)
        return real(project, force=force)

    monkeypatch.setattr(frame, "frame_project", spy)
    main(["deliver", "-p", str(finished.root), "--for", "square", "--renderer", "basic"])
    assert (1080, 1080) in seen


def test_a_version_runs_under_the_heavy_lock_one_at_a_time(finished, monkeypatch):
    labels, depth = [], {"now": 0, "most": 0}
    real = deliver.locks.heavy

    from contextlib import contextmanager

    @contextmanager
    def counted(label):
        labels.append(label)
        depth["now"] += 1
        depth["most"] = max(depth["most"], depth["now"])
        with real(label):
            yield
        depth["now"] -= 1

    monkeypatch.setattr(deliver.locks, "heavy", counted)
    monkeypatch.setattr(deliver, "render_version", lambda version, renderer: finished.out / "render.mp4")
    main(["deliver", "-p", str(finished.root), "--for", "square,youtube", "--renderer", "basic"])
    assert sum("square" in label for label in labels) == 1 and sum("youtube" in label for label in labels) == 1
    assert depth["most"] == 1


def test_a_film_over_the_limit_is_not_rendered_and_says_so(finished, monkeypatch, capsys):
    short = platforms.Platform("reels", "9:16", platforms.PLATFORMS["reels"].zone, "Estimate.", 1.0, None, "A test limit.")
    monkeypatch.setitem(platforms.PLATFORMS, "reels", short)
    monkeypatch.setattr(deliver, "render_version", lambda version, renderer: pytest.fail("no render for a film that cannot post"))
    assert main(["deliver", "-p", str(finished.root), "--for", "reels", "--renderer", "basic"]) == 1
    assert "over the" in capsys.readouterr().out
    assert not (finished.root / "deliver" / "reels.mp4").exists()


def test_a_film_under_the_minimum_says_so():
    linked = platforms.PLATFORMS["linkedin"]
    assert "under" in deliver.length_problem(linked, 2.0)
    assert deliver.length_problem(linked, 60.0) is None
    assert "over" in deliver.length_problem(linked, 901.0)
    assert deliver.length_problem(platforms.PLATFORMS["square"], 99999.0) is None


def test_a_matching_platform_reuses_the_picture(finished, monkeypatch):
    finished.config["output"]["platform"] = "reels"
    (finished.root / "altocue.toml").write_text((finished.root / "altocue.toml").read_text().replace('aspect = "9:16"', 'aspect = "9:16"\nplatform = "reels"'))
    render.build(Project.open(finished.root), renderer="basic")
    master.master_file(finished.out / "render.mp4", finished.out / "final.mp4")
    monkeypatch.setattr(deliver, "render_version", lambda version, renderer: pytest.fail("the master already fits, so nothing needs rendering"))
    assert main(["deliver", "-p", str(finished.root), "--for", "reels", "--renderer", "basic"]) in (0, 1)
    assert (finished.root / "deliver" / "reels.mp4").exists()


def test_a_tall_version_lifts_its_text_inside_the_zone(finished, monkeypatch):
    seen = {}
    real = render.build

    def spy(project, **kwargs):
        done = real(project, **kwargs)
        seen["zone"] = json.loads(project.timeline_path.read_text())["zone"]
        return done

    monkeypatch.setattr(deliver.render, "build", spy)
    main(["deliver", "-p", str(finished.root), "--for", "shorts", "--renderer", "basic"])
    assert seen["zone"] == platforms.PLATFORMS["shorts"].zone


def test_a_failed_version_keeps_its_workspace_and_a_passed_one_clears_it(finished, monkeypatch):
    main(["deliver", "-p", str(finished.root), "--for", "square", "--renderer", "basic"])
    report = json.loads((finished.root / "deliver" / "deliver.json").read_text())["versions"][0]
    leftover = (finished.work / "versions" / "square").exists()
    assert leftover == (not report["passed"])


# The guard rails


def test_deliver_needs_a_finished_film(tmp_path, capsys):
    project = Project.create(tmp_path / "video")
    assert main(["deliver", "-p", str(project.root)]) == 1
    assert "altocue master" in capsys.readouterr().err


def test_deliver_refuses_a_film_older_than_the_cut(finished, capsys):
    os.utime(finished.out / "final.mp4", (1, 1))
    assert main(["deliver", "-p", str(finished.root)]) == 1
    assert "older than the cut" in capsys.readouterr().err


def test_an_unknown_platform_stops_before_any_work(finished, capsys):
    assert main(["deliver", "-p", str(finished.root), "--for", "myspace"]) == 1
    assert "myspace" in capsys.readouterr().err
    assert not (finished.root / "deliver" / "deliver.json").exists()


def test_a_later_run_keeps_the_versions_it_did_not_touch(finished, monkeypatch):
    monkeypatch.setattr(deliver, "render_version", lambda version, renderer: finished.out / "render.mp4")
    main(["deliver", "-p", str(finished.root), "--for", "square", "--renderer", "basic"])
    main(["deliver", "-p", str(finished.root), "--for", "youtube", "--renderer", "basic"])
    main(["deliver", "-p", str(finished.root)])
    report = json.loads((finished.root / "deliver" / "deliver.json").read_text())
    assert [v["platform"] for v in report["versions"]] == ["square", "youtube"]


def test_a_new_film_drops_the_old_versions_from_the_report(finished, monkeypatch):
    monkeypatch.setattr(deliver, "render_version", lambda version, renderer: finished.out / "render.mp4")
    main(["deliver", "-p", str(finished.root), "--for", "square", "--renderer", "basic"])
    master.master_file(finished.out / "render.mp4", finished.out / "final.mp4", -15.0)
    main(["deliver", "-p", str(finished.root)])
    assert json.loads((finished.root / "deliver" / "deliver.json").read_text())["versions"] == []


def test_a_second_run_replaces_the_first(finished):
    main(["deliver", "-p", str(finished.root)])
    main(["deliver", "-p", str(finished.root)])
    assert (finished.root / "deliver" / "film.mp4").exists()
