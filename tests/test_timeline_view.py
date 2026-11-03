"""`altocue timeline`: the real stage, opened in the pinned HyperFrames studio and nowhere else."""

from __future__ import annotations

import json
import re
import stat
import subprocess
from html.parser import HTMLParser
from pathlib import Path

import pytest
from conftest import make_clip

from altocue import brand as brands
from altocue import ffmpeg, timeline_view
from altocue.cli import main
from altocue.ingest import ingest
from altocue.models import Edl, Segment, Word
from altocue.motion.html import compose
from altocue.project import Project, write_json

ADDRESS = "http://127.0.0.1:3911/#project/video"
STARTED = {"schemaVersion": 1, "operation": "start", "ok": True, "result": {"state": "started", "port": 3911, "studioUrl": ADDRESS, "ready": True}}


class Clips(HTMLParser):
    """Every timed element on the page, as HyperFrames will read it."""

    def __init__(self) -> None:
        super().__init__()
        self.found: list[dict[str, str]] = []

    def handle_starttag(self, tag: str, attrs: list[tuple[str, str | None]]) -> None:
        values = {key: value or "" for key, value in attrs}
        if "data-track-index" in values:
            self.found.append({"tag": tag, **values})


def clips_of(page: str) -> list[dict[str, str]]:
    parser = Clips()
    parser.feed(page)
    return parser.found


def stage_of(project: Project) -> Path:
    return project.work / "timeline" / "video"


def page_of(project: Project) -> str:
    return (stage_of(project) / "index.html").read_text()


def footage(page: str) -> list[dict[str, str]]:
    return [clip for clip in clips_of(page) if clip.get("id", "").startswith("shot-")]


@pytest.fixture
def project(tmp_path: Path) -> Project:
    source = make_clip(tmp_path / "take.mp4", seconds=4.0, size="320x568")
    made = Project.create(tmp_path / "video", aspect="9:16")
    (made.sources / "take.mp4").symlink_to(source)
    clip = ingest(made)[0]
    made.save_words(clip.id, [Word("Hello", 0.2, 0.6), Word("there", 0.6, 1.0), Word("friend.", 2.4, 2.9)], "test")
    ffmpeg.extract_wav(source, made.audio_dir / "take.wav", rate=48000)
    made.save_edl(Edl(fps=30, segments=[Segment(id="s001", clip=clip.id, start=0.0, end=2.0), Segment(id="s002", clip=clip.id, start=2.2, end=3.7)]))
    return made


@pytest.fixture
def layered(project: Project) -> Project:
    (project.root / "broll").mkdir()
    make_clip(project.root / "broll" / "city.mp4", seconds=1.0, size="320x180", audio=False)
    ffmpeg.run(["-f", "lavfi", "-i", "color=red:size=64x64:duration=1", "-frames:v", "1", str(project.root / "broll" / "map.png")])
    ffmpeg.run(["-f", "lavfi", "-i", "sine=frequency=880:sample_rate=48000:duration=8", str(project.root / "bed.wav")])
    write_json(
        project.root / "motion.json",
        {
            "music": {"path": "bed.wav"},
            "items": [
                {"type": "chip", "text": "Hello", "start": 0.2, "duration": 1.0},
                {"type": "lower_third", "title": "Name", "start": 1.0, "duration": 1.0},
                {"type": "endcard", "headline": "Bye", "duration": 1.0},
            ],
            "inserts": [{"file": "broll/city.mp4", "start": 0.5, "end": 2.5}, {"file": "broll/map.png", "start": 2.8, "end": 3.2}],
        },
    )
    return project


@pytest.fixture
def studio(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> dict[str, Path]:
    """A stand-in for the pinned CLI, which records exactly how it was called and answers as the real one does."""
    folder = tmp_path / "renderer" / "node_modules" / ".bin"
    folder.mkdir(parents=True)
    calls, env, reply = tmp_path / "calls.txt", tmp_path / "env.txt", tmp_path / "reply.json"
    reply.write_text(json.dumps(STARTED))
    script = folder / "hyperframes"
    script.write_text(f'#!/bin/sh\nprintf "%s\\n" "$@" > "{calls}"\nenv > "{env}"\ncat "{reply}"\n')
    script.chmod(script.stat().st_mode | stat.S_IXUSR)
    monkeypatch.setenv("ALTOCUE_RENDERER_DIR", str(tmp_path / "renderer"))
    monkeypatch.setattr(timeline_view.HyperFramesRenderer, "available", lambda self: (True, ""))
    return {"calls": calls, "env": env, "reply": reply, "script": script}


# The stage


def test_the_stage_is_the_page_the_renderer_uses(layered):
    timeline_view.write_stage(layered)
    timeline = json.loads(layered.timeline_path.read_text())
    plain = compose(timeline, brands.load(layered))
    page = page_of(layered)
    assert all(line in page for line in plain.splitlines())
    kinds = {clip.get("class", "") for clip in clips_of(page)}
    assert any("cap" in kind for kind in kinds) and any("chip" in kind for kind in kinds) and any("lt" in kind for kind in kinds) and any("end" in kind for kind in kinds)


def test_the_stage_says_it_animates_without_a_timeline_script(project):
    timeline_view.write_stage(project)
    assert re.search(r'<div id="root"[^>]* data-no-timeline', page_of(project))


def test_the_stage_carries_what_the_renderer_needs(layered):
    timeline_view.write_stage(layered)
    assert (stage_of(layered) / brands.load(layered).font_file.name).exists()


def test_every_shot_is_its_own_clip_at_its_place_in_the_film(project):
    timeline_view.write_stage(project)
    first, second = footage(page_of(project))
    assert [(c["data-start"], c["data-duration"], c["data-media-start"], c["data-track-index"]) for c in (first, second)] == [
        ("0.000", "2.000", "0.000", "0"),
        ("2.000", "1.500", "2.000", "0"),
    ]
    assert first["tag"] == "video" and "muted" in first and "playsinline" in first


def test_the_footage_is_the_plate_not_a_copy(project):
    timeline_view.write_stage(project)
    linked = stage_of(project) / footage(page_of(project))[0]["src"]
    assert linked.is_symlink() and linked.resolve() == (project.work / "plate-draft.mp4").resolve()


def test_a_full_plate_is_used_when_there_is_one(project):
    from altocue import plate

    plate.build(project)
    timeline_view.write_stage(project)
    linked = stage_of(project) / footage(page_of(project))[0]["src"]
    assert linked.resolve() == (project.work / "plate.mp4").resolve()


def test_inserts_sit_over_the_footage_on_their_real_files(layered):
    timeline_view.write_stage(layered)
    page = page_of(layered)
    video, still = [clip for clip in clips_of(page) if clip.get("id", "").startswith("insert-")]
    assert video["tag"] == "video" and video["data-start"] == "0.500" and "muted" in video and "playsinline" in video
    assert still["tag"] == "img" and (still["data-start"], still["data-duration"]) == ("2.800", "0.400")
    assert (stage_of(layered) / video["src"]).resolve() == (layered.root / "broll" / "city.mp4").resolve()
    assert (stage_of(layered) / still["src"]).resolve() == (layered.root / "broll" / "map.png").resolve()
    assert page.index('id="shot-s001"') < page.index('id="insert-1"') < page.index('class="clip cap')  # later on the page paints on top


def test_an_insert_stops_when_its_file_does(layered):
    timeline_view.write_stage(layered)
    insert = next(clip for clip in clips_of(page_of(layered)) if clip.get("id") == "insert-1")
    assert insert["data-duration"] == "1.000"  # the slot is 2 s and the file holds 1 s


def test_an_insert_with_a_transition_shows_the_renders_own_blend(layered):
    motion = json.loads((layered.root / "motion.json").read_text())
    motion["inserts"][0]["in"] = {"type": "dissolve", "duration": 0.3}
    write_json(layered.root / "motion.json", motion)
    timeline_view.write_stage(layered)
    insert = next(clip for clip in clips_of(page_of(layered)) if clip.get("id") == "insert-1")
    baked = (stage_of(layered) / insert["src"]).resolve()
    assert baked.parent == (layered.work / "inserts-draft").resolve() and baked.name.startswith("insert-")
    assert insert["data-duration"] == "2.000"  # the render's clip fills the whole span, so nothing is cut short


def test_the_sound_arrives_as_voice_music_and_effects(layered):
    timeline_view.write_stage(layered)
    sounds = {clip["id"]: clip for clip in clips_of(page_of(layered)) if clip["tag"] == "audio"}
    assert set(sounds) == {"sound-voice", "sound-music", "sound-effects"}
    for clip in sounds.values():
        assert (stage_of(layered) / clip["src"]).exists() and clip["data-start"] == "0.000" and clip["data-duration"] == "4.500"


def test_a_silent_stem_stays_off_the_timeline(project):
    timeline_view.write_stage(project)
    sounds = {clip["id"] for clip in clips_of(page_of(project)) if clip["tag"] == "audio"}
    assert sounds == {"sound-voice"}
    assert not (stage_of(project) / "music.wav").exists()


def test_no_two_clips_overlap_on_a_track(layered):
    motion = json.loads((layered.root / "motion.json").read_text())
    motion["inserts"].append({"file": "broll/map.png", "start": 0.6, "end": 1.0})  # over the city shot
    write_json(layered.root / "motion.json", motion)
    timeline_view.write_stage(layered)
    tracks: dict[str, list[tuple[float, float]]] = {}
    for clip in clips_of(page_of(layered)):
        start = float(clip["data-start"])
        tracks.setdefault(clip["data-track-index"], []).append((start, start + float(clip["data-duration"])))
    for spans in tracks.values():
        spans.sort()
        assert all(a[1] <= b[0] + 1e-6 for a, b in zip(spans, spans[1:], strict=False))


def test_the_last_frame_holds_under_the_end_card(layered):
    timeline_view.write_stage(layered)
    hold = next(clip for clip in clips_of(page_of(layered)) if clip.get("id") == "hold")
    assert hold["tag"] == "img" and (hold["data-start"], hold["data-duration"]) == ("3.500", "1.000") and hold["data-track-index"] == "0"
    assert (stage_of(layered) / hold["src"]).stat().st_size > 0


def test_no_end_card_means_no_hold(project):
    timeline_view.write_stage(project)
    assert not [clip for clip in clips_of(page_of(project)) if clip.get("id") == "hold"]


def test_page_lengths_match_the_timeline(layered):
    timeline_view.write_stage(layered)
    assert re.search(r'<div id="root"[^>]* data-duration="4\.500"', page_of(layered))


def test_writing_the_stage_changes_neither_the_cut_nor_the_graphics(layered):
    motion = (layered.root / "motion.json").read_bytes()
    cuts = layered.cut_mark()
    timeline_view.write_stage(layered)
    assert (layered.root / "motion.json").read_bytes() == motion and layered.cut_mark() == cuts


def test_a_changed_cut_reaches_the_page(project):
    timeline_view.write_stage(project)
    edl = project.edl()
    edl.segments[1].end = 3.5
    project.save_edl(edl)
    timeline_view.write_stage(project)
    assert footage(page_of(project))[1]["data-duration"] == "1.300"


# The studio, and nothing else


def test_the_studio_never_opens_anything(project, studio):
    assert main(["timeline", "-p", str(project.root)]) == 0
    args = studio["calls"].read_text().split()
    assert args[0] == "preview" and args[1] == str(stage_of(project))
    assert {"--no-open", "--background", "--json"} <= set(args)
    assert "--open" not in args and "--browser-path" not in args and "--remote-debugging-port" not in args


def test_telemetry_stays_off(project, studio):
    main(["timeline", "-p", str(project.root)])
    env = studio["env"].read_text().splitlines()
    assert {"HYPERFRAMES_NO_TELEMETRY=1", "HYPERFRAMES_NO_UPDATE_CHECK=1", "DO_NOT_TRACK=1"} <= set(env)


def test_the_address_is_printed(project, studio, capsys):
    main(["timeline", "-p", str(project.root)])
    assert ADDRESS in capsys.readouterr().out


def test_a_port_can_be_chosen(project, studio):
    main(["timeline", "-p", str(project.root), "--port", "4567"])
    args = studio["calls"].read_text().split()
    assert args[args.index("--port") + 1] == "4567"


def test_the_command_says_plainly_that_the_studio_is_a_view(project, studio, capsys):
    main(["timeline", "-p", str(project.root)])
    out = capsys.readouterr().out
    assert "view" in out and "edl.json" in out and "motion.json" in out


def test_overlays_are_named_as_not_drawn(project, studio, capsys):
    write_json(project.root / "motion.json", {"overlay": [{"type": "grain", "amount": 0.2}, {"type": "vignette", "amount": 0.3}]})
    main(["timeline", "-p", str(project.root)])
    out = capsys.readouterr().out
    assert "grain and vignette" in out and "not drawn" in out


def test_no_overlays_means_no_overlay_line(project, studio, capsys):
    main(["timeline", "-p", str(project.root)])
    assert "not drawn" not in capsys.readouterr().out


def test_a_studio_that_will_not_start_is_reported(project, studio, capsys):
    studio["reply"].write_text(json.dumps({"schemaVersion": 1, "operation": "start", "ok": False, "error": {"message": "port in use"}}))
    assert main(["timeline", "-p", str(project.root)]) == 1
    assert "port in use" in capsys.readouterr().err


def test_output_that_is_not_json_is_reported(project, studio, capsys):
    studio["reply"].write_text("Something went wrong")
    assert main(["timeline", "-p", str(project.root)]) == 1
    assert "Something went wrong" in capsys.readouterr().err


def test_no_renderer_says_how_to_get_one(project, studio, monkeypatch, capsys):
    monkeypatch.setattr(timeline_view.HyperFramesRenderer, "available", lambda self: (False, "the renderer is not installed."))
    assert main(["timeline", "-p", str(project.root)]) == 1
    assert "altocue setup" in capsys.readouterr().err
    assert not stage_of(project).exists()


def test_stop_stops_the_studio_and_builds_nothing(project, studio):
    studio["reply"].write_text(json.dumps({"schemaVersion": 1, "operation": "stop", "ok": True, "result": {"state": "stopped"}}))
    assert main(["timeline", "-p", str(project.root), "--stop"]) == 0
    args = studio["calls"].read_text().split()
    assert args[:2] == ["preview", str(stage_of(project))] and "--stop" in args
    assert not (stage_of(project) / "index.html").exists()


def test_an_empty_cut_is_refused_before_the_studio_starts(project, studio, capsys):
    project.save_edl(Edl(fps=30, segments=[]))
    assert main(["timeline", "-p", str(project.root)]) == 1
    assert "altocue cut" in capsys.readouterr().err and not studio["calls"].exists()


# A studio edit never quietly diverges


def test_a_studio_edit_is_found_kept_and_named(project, studio, capsys):
    main(["timeline", "-p", str(project.root)])
    page = stage_of(project) / "index.html"
    edited = page.read_text().replace('data-duration="1.500"', 'data-duration="1.200"')
    page.write_text(edited)
    capsys.readouterr()
    assert main(["timeline", "-p", str(project.root)]) == 0
    out = capsys.readouterr().out
    kept = list((project.work / "timeline").glob("studio-edit-*.html"))
    assert len(kept) == 1 and kept[0].read_text() == edited and kept[0].name in out
    assert "not in edl.json" in out
    assert page.read_text() != edited


def test_stopping_reports_a_studio_edit_too(project, studio, capsys):
    main(["timeline", "-p", str(project.root)])
    page = stage_of(project) / "index.html"
    page.write_text(page.read_text() + "<!-- moved -->")
    studio["reply"].write_text(json.dumps({"schemaVersion": 1, "operation": "stop", "ok": True, "result": {"state": "stopped"}}))
    capsys.readouterr()
    main(["timeline", "-p", str(project.root), "--stop"])
    assert "not in edl.json" in capsys.readouterr().out


def test_the_ids_the_studio_stamps_as_it_starts_are_not_an_edit(project, studio, capsys):
    main(["timeline", "-p", str(project.root)])
    page = stage_of(project) / "index.html"
    page.write_text(page.read_text().replace('<video id="shot-s001"', '<video data-hf-id="hf-1" id="shot-s001"'))
    capsys.readouterr()
    main(["timeline", "-p", str(project.root)])
    assert "not in edl.json" not in capsys.readouterr().out
    assert not list((project.work / "timeline").glob("studio-edit-*.html"))


def test_an_untouched_page_raises_nothing(project, studio, capsys):
    main(["timeline", "-p", str(project.root)])
    capsys.readouterr()
    main(["timeline", "-p", str(project.root)])
    assert "not in edl.json" not in capsys.readouterr().out
    assert not list((project.work / "timeline").glob("studio-edit-*.html"))


def test_the_real_cli_is_pinned_to_the_preview_flags_it_needs():
    """Runs only where the pinned CLI is installed. `--no-open` is what keeps a browser from opening."""
    from altocue.motion.hyperframes import binary, quiet_env

    if not binary().exists():
        pytest.skip("The renderer is not installed here.")
    done = subprocess.run([str(binary()), "preview", "--help"], capture_output=True, text=True, env=quiet_env(), timeout=60)
    for flag in ("--no-open", "--background", "--json", "--stop", "--port"):
        assert flag in done.stdout
