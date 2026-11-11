"""Shorts Studio runs on a real take: the folder Shorts Studio reads, and the gates on what comes back."""

from __future__ import annotations

import json
import shutil
import subprocess
from pathlib import Path

import pytest

from altocue import shorts
from altocue.cli import main
from altocue.errors import AltocueError

needs_ffmpeg = pytest.mark.skipif(not shutil.which("ffmpeg"), reason="needs ffmpeg")

WORDS = [
    {"text": "Ask", "start": 0.0, "end": 0.3},
    {"text": "five", "start": 0.3, "end": 0.6},
    {"text": "questions", "start": 0.6, "end": 1.1},
    {"text": "first.", "start": 1.1, "end": 1.5},
    {"text": "Then", "start": 2.0, "end": 2.3},
    {"text": "scope", "start": 2.3, "end": 2.7},
    {"text": "it.", "start": 2.7, "end": 3.0},
]


def take(path: Path, seconds: float = 3.0) -> Path:
    path.parent.mkdir(parents=True, exist_ok=True)
    command = ["ffmpeg", "-y", "-loglevel", "error", "-f", "lavfi", "-i", f"testsrc2=s=216x384:r=30:d={seconds}"]
    command += ["-f", "lavfi", "-i", f"sine=f=220:d={seconds}", "-shortest", "-c:v", "libx264", "-pix_fmt", "yuv420p", "-c:a", "aac", str(path)]
    subprocess.run(command, check=True)
    return path


def tone(path: Path, freq: int) -> Path:
    path.parent.mkdir(parents=True, exist_ok=True)
    subprocess.run(["ffmpeg", "-y", "-loglevel", "error", "-f", "lavfi", "-i", f"sine=f={freq}:d=0.3", str(path)], check=True)
    return path


def framed(words: list[dict], fps: int = 30) -> list[dict]:
    return [{**w, "start_frame": round(w["start"] * fps), "end_frame": round(w["end"] * fps)} for w in words]


def test_the_plan_cues_every_sentence_and_every_look_away_word_for_word():
    takes = shorts.lines_of([90], framed(WORDS), 30)
    plan = shorts.plan_lines(takes, [{"start": 2.1, "end": 2.9, "reason": "eyes down"}], 3)
    assert 'Cue: take-01 "Ask five questions first."' in plan
    assert 'Cue: take-01 "Then scope it."' in plan
    assert 'Cue: take-01 "scope it."' in plan
    assert 'data-cover="cover-01"' in plan
    assert plan.rstrip().endswith("Captions: 3 words")
    for line in plan.splitlines():
        if line.startswith("Cue:"):
            assert line.split('"')[1] in takes[0]["dialogue"]


def test_each_recorded_line_is_its_own_take_cut_on_the_joins():
    # Two segments: "Ask five questions first." then, after a join at 1.8 s, "Then scope it."
    takes = shorts.lines_of([54, 36], framed(WORDS), 30)
    assert [(t["id"], t["first_frame"], t["last_frame"]) for t in takes] == [("take-01", 0, 54), ("take-02", 54, 90)]
    assert [t["dialogue"] for t in takes] == ["Ask five questions first.", "Then scope it."]
    assert takes[1]["words"][0]["start"] == pytest.approx(0.2)  # in the take's own seconds
    plan = shorts.plan_lines(takes, [{"start": 2.1, "end": 2.9, "reason": "eyes down"}], 3)
    assert 'Cue: take-02 "Then scope it."' in plan and 'Cue: take-02 "scope it."' in plan
    assert "from 0.30 s into take-02" in plan


def test_a_one_word_line_or_a_line_with_no_words_joins_its_neighbour():
    words = framed([{"text": "First,", "start": 0.05, "end": 0.35}, *[{**w, "start": w["start"] + 1.0, "end": w["end"] + 1.0} for w in WORDS]])
    # "First," alone in 0.4 s, a 0.1 s breath with no words, then two real lines
    takes = shorts.lines_of([12, 3, 60, 45], words, 30)
    assert [t["dialogue"] for t in takes] == ["First, Ask five questions first.", "Then scope it."]
    assert takes[0]["first_frame"] == 0 and takes[1]["last_frame"] == 120
    assert shorts.lines_of([80, 10], framed(WORDS), 30)[-1]["last_frame"] == 90  # a short last line joins the one before


def test_covers_map_through_the_take_they_sit_in():
    timeline = {"takes": [{"id": "take-01", "source_from": 0.1, "timeline_start": 0.0}, {"id": "take-02", "source_from": 0.15, "timeline_start": 1.6}]}
    placed = [{"id": "take-01", "from": 0.0, "to": 1.8}, {"id": "take-02", "from": 1.8, "to": 3.0}]
    assert shorts.to_timeline(timeline, 2.1, placed) == pytest.approx(2.1 - 1.8 - 0.15 + 1.6)
    assert shorts.to_timeline(timeline, 1.8, placed, end=True) == pytest.approx(1.7)  # the end of take-01, not the start of take-02
    assert shorts.to_timeline(timeline, 2.1) == pytest.approx(2.0)  # a run from before: one take, the whole cut


@needs_ffmpeg
def test_a_take_is_cut_on_exact_frames(tmp_path):
    plate = take(tmp_path / "plate.mp4", 3.0)
    voice = tmp_path / "voice.wav"
    subprocess.run(["ffmpeg", "-y", "-loglevel", "error", "-f", "lavfi", "-i", "sine=f=220:d=3", str(voice)], check=True)
    out = tmp_path / "take-02.mp4"
    shorts.cut_take(plate, voice, out, 54, 90, 30)
    frames = subprocess.run(
        ["ffprobe", "-v", "error", "-count_frames", "-select_streams", "v:0", "-show_entries", "stream=nb_read_frames", "-of", "csv=p=0", str(out)],
        capture_output=True,
        text=True,
        check=True,
    ).stdout.strip()
    assert int(frames) == 36
    assert shorts.probe_duration(out) == pytest.approx(1.2, abs=0.05)


def test_spans_read_a_gaze_tools_output(tmp_path):
    path = tmp_path / "gaze.json"
    path.write_text(json.dumps({"away": [{"start": 4, "end": 5.5, "reason": "looked down"}, {"start": 1, "end": 1}]}))
    assert shorts.load_spans(path) == [{"start": 4.0, "end": 5.5, "reason": "looked down"}]


@needs_ffmpeg
def test_prepare_writes_a_run_with_a_real_take_and_only_the_pack(tmp_path):
    pack = tmp_path / "pack"
    tone(pack / "whoosh" / "air.wav", 600)
    (tmp_path / "script.txt").write_text("Ask five questions first.\nThen scope it.\n")
    kit = tmp_path / "kit"
    kit.mkdir()
    (kit / "shorts_kit.py").write_text("CSS = ''\n")
    (kit / "grain.png").write_bytes(b"png")
    video = take(tmp_path / "raw.mp4")
    out = tmp_path / "run"
    command = ["shorts", "prepare", "--take", str(video), "--script", str(tmp_path / "script.txt"), "--out", str(out), "--sfx-dir", str(pack)]
    assert main([*command, "--kit", str(kit)]) == 0
    assert (out / "shorts_kit.py").exists() and (out / "media" / "grain.png").exists()
    assert "shorts_kit.py" in (out / "HOUSE.md").read_text()
    reel = json.loads((out / "reel.json").read_text())
    assert reel["takes"][0]["job_id"] is None
    assert reel["takes"][0]["dialogue"] == "Ask five questions first. Then scope it."
    assert reel["media"][0]["role"] == "host-source"
    assert (out / "media" / "sfx" / "whoosh" / "air.wav").exists()
    assert json.loads((out / "altocue-shorts.json").read_text())["sfx_only"] is True
    assert chr(0x2014) not in (out / "HOUSE.md").read_text()
    assert (tmp_path / "run.zip").exists()
    with pytest.raises(AltocueError):
        shorts.prepare(shorts_args(video, tmp_path / "script.txt", out))
    (pack / "whoosh" / "air.wav").unlink()
    tone(pack / "pop.wav", 800)
    assert main(["shorts", "prepare", "--take", str(video), "--script", str(tmp_path / "script.txt"), "--out", str(out), "--sfx-dir", str(pack), "--force"]) == 0
    assert not (out / "media" / "sfx" / "whoosh").exists()
    assert (out / "media" / "sfx" / "pop.wav").exists()


def shorts_args(video: Path, script: Path, out: Path):
    from altocue.cli import Parser

    parser = Parser()
    shorts.add_arguments("shorts", parser)
    return parser.parse_args(["prepare", "--take", str(video), "--script", str(script), "--out", str(out)])


def finished_run(tmp_path: Path, *, sound: Path, cover_start: float, page_text: str = "Ask first.") -> Path:
    run = tmp_path / "done"
    (run / "composition" / "media").mkdir(parents=True)
    shutil.copy(sound, run / "composition" / "media" / sound.name)
    pack_sound = tone(tmp_path / "pack" / "air.wav", 600)
    record = {"covers": [{"start": 2.1, "end": 2.9}], "sfx_only": True, "sounds": [{"file": "media/sfx/air.wav", "name": "air", "sha256": shorts.sha256(pack_sound)}]}
    (run / "altocue-shorts.json").write_text(json.dumps(record))
    (run / "reel.json").write_text(json.dumps({"takes": [{"id": "take-01", "job_id": None}]}))
    timeline = {"fps": 30, "master_duration": 3.0, "takes": [{"id": "take-01", "source_from": 0.1, "timeline_start": 0.0}]}
    (run / "timeline.json").write_text(json.dumps(timeline))
    page = f"""<div id="root"><audio id="voice" src="media/voice-master.wav" data-start="0"></audio>
<audio id="sfx-1" src="media/{sound.name}" data-start="1"></audio>
<div class="card" data-cover="cover-01" data-start="{cover_start}" data-duration="1.0"><p>{page_text}</p></div></div>"""
    (run / "composition" / "index.html").write_text(page)
    take(run / "renders" / "final.mp4")
    return run


@needs_ffmpeg
def test_a_clean_run_passes(tmp_path):
    run = finished_run(tmp_path, sound=tone(tmp_path / "pack" / "air.wav", 600), cover_start=1.95)
    assert shorts.check(run, None) == []


@needs_ffmpeg
def test_a_foreign_sound_an_open_look_away_and_a_dash_all_fail(tmp_path):
    tone(tmp_path / "pack" / "air.wav", 600)
    run = finished_run(tmp_path, sound=tone(tmp_path / "library" / "swish.wav", 900), cover_start=2.5, page_text=f"Ask {chr(0x2014)} first")
    problems = shorts.check(run, None)
    assert any("not from the run's sound pack" in p for p in problems)
    assert any("cover-01" in p for p in problems)
    assert any("em dash" in p for p in problems)


@needs_ffmpeg
def test_a_hidden_scene_covers_a_look_away_and_a_video_tag_fails(tmp_path):
    run = finished_run(tmp_path, sound=tone(tmp_path / "pack" / "air.wav", 600), cover_start=2.5)
    page = run / "composition" / "index.html"
    html = page.read_text().replace('<div class="card"', '<section data-layout="hidden" data-start="1.9" data-duration="1.2"></section><div class="card"')
    page.write_text(html)
    assert shorts.check(run, None) == []
    page.write_text(html.replace("</div></div>", '<video src="media/take.mp4"></video></div></div>'))
    assert any("<video>" in p for p in shorts.check(run, None))


@needs_ffmpeg
def test_a_generated_presenter_fails(tmp_path):
    run = finished_run(tmp_path, sound=tone(tmp_path / "pack" / "air.wav", 600), cover_start=1.95)
    (run / "reel.json").write_text(json.dumps({"takes": [{"id": "take-01", "job_id": "abc"}]}))
    assert any("real take" in p for p in shorts.check(run, None))


def test_a_missing_render_is_not_a_pass(tmp_path):
    assert "skipped check is not a pass" in shorts.check_length(tmp_path / "none.mp4", {"master_duration": 3})[0]


def test_a_preview_keeps_only_the_covers_inside_it():
    spans = [{"start": 5.0, "end": 6.0, "reason": "a"}, {"start": 19.5, "end": 21.0, "reason": "b"}, {"start": 31.6, "end": 32.0, "reason": "c"}]
    assert shorts.inside_take(spans, 19.8) == [{"start": 5.0, "end": 6.0, "reason": "a"}, {"start": 19.5, "end": 19.8, "reason": "b"}]


SPOKEN = {"fps": 30, "master_duration": 6.0, "caption_words": [{"start": 0.0, "end": 1.0}, {"start": 1.1, "end": 2.0}, {"start": 4.0, "end": 6.0}]}


def scene(layout: str, start: float, seconds: float) -> str:
    return f'<section class="scene" data-layout="{layout}" data-start="{start}" data-duration="{seconds}"></section>'


def test_the_face_never_shows_without_speech():
    house = dict(shorts.HOUSE_DEFAULTS)
    assert any("2.00 to 4.00 s with nothing said" in p for p in shorts.check_face(scene("base", 0, 6), SPOKEN, house))
    assert shorts.check_face(scene("base", 0, 2) + scene("hidden", 2, 2) + scene("base", 4, 2), SPOKEN, house) == []


def test_a_flash_of_the_face_between_graphics_fails():
    page = scene("hidden", 0, 2.0) + scene("base", 2.0, 0.3) + scene("hidden", 2.3, 3.7)
    problems = shorts.check_face(page, SPOKEN, dict(shorts.HOUSE_DEFAULTS))
    assert any("only 0.30 s" in p for p in problems)
    assert any("flash cut" in p for p in problems)


def test_heavy_or_jumping_grain_fails():
    house = dict(shorts.HOUSE_DEFAULTS)
    assert shorts.check_grain("<style>.grain{opacity:.03}</style>", house) == []
    assert any("opacity 0.07" in p for p in shorts.check_grain("<style>.grain{background:url(g.png);opacity:.07}</style>", house))
    jumping = "const gr = $(host, '.grain'); if (gr) { const f = k.frameAt(t); gr.style.transform = `translate(${f}px)`; }"
    assert any("every frame" in p for p in shorts.check_grain(jumping, house))


def test_the_house_end_card_layouts_and_faces_hold():
    house = {**shorts.HOUSE_DEFAULTS, "outro": "house-card", "layouts": {"cutout": [0.4, 1.0]}, "fonts": ["Geist"]}
    page = "<style>.t{font-family:'Inter',sans-serif}</style>" + scene("base", 0, 6)
    scenes = shorts.scenes_in(page)
    problems = shorts.check_house(page, {"outro": {"id": "cta"}}, scenes, 6.0, house)
    assert any("house-card" in p for p in problems)
    assert any("cutout layout fills 0%" in p for p in problems)
    assert any("inter" in p for p in problems)
    good = "<style>.t{font-family:'Geist',sans-serif}</style>" + scene("cutout", 0, 6)
    assert shorts.check_house(good, {"outro": {"id": "house-card"}}, shorts.scenes_in(good), 6.0, house) == []


def test_each_graphic_holds_long_enough_to_read():
    house = dict(shorts.HOUSE_DEFAULTS)
    wordy = '<section data-layout="hidden" data-start="1" data-duration="1.2"><h2>Write the answers first, and the demo changes</h2></section>'
    assert any("8 words" in p for p in shorts.check_pacing(wordy, house))
    calm = wordy.replace('data-duration="1.2"', 'data-duration="3.0"')
    assert shorts.check_pacing(calm, house) == []


def test_too_many_changes_or_sounds_at_once_fail():
    house = dict(shorts.HOUSE_DEFAULTS)
    rushed = scene("base", 0, 1) + scene("hidden", 1, 1) + scene("base", 2, 1) + scene("hidden", 3, 3)
    assert any("3 scene changes" in p for p in shorts.check_pacing(rushed, house))
    busy = "".join(f'<audio id="sfx-{n}" src="media/sfx/a.wav" data-start="{1 + n * 0.2}"></audio>' for n in range(3))
    assert any("3 sound effects" in p for p in shorts.check_pacing(busy, house))


def test_a_subtitle_cue_too_short_to_read_fails(tmp_path):
    (tmp_path / "renders").mkdir()
    (tmp_path / "renders" / "subtitles.srt").write_text("1\n00:00:01,000 --> 00:00:02,000\nFine\n\n2\n00:00:02,000 --> 00:00:02,001\nGone\n")
    timeline = {"caption_words": [{"start": 1.0, "end": 1.3}, {"start": 2.0, "end": 2.0}]}
    problems = shorts.check_cues(tmp_path, timeline)
    assert any("0.001 s cue at 2.00 s" in p for p in problems)
    assert any("1 caption words have no length" in p for p in problems)


@needs_ffmpeg
def test_prepare_from_a_project_gives_one_take_per_line(tmp_path):
    from conftest import make_clip

    from altocue import ffmpeg, render
    from altocue.ingest import ingest
    from altocue.models import Edl, Segment, Word
    from altocue.project import Project

    source = make_clip(tmp_path / "take.mp4", seconds=4.0, size="320x568")
    project = Project.create(tmp_path / "video", aspect="9:16")
    (project.sources / "take.mp4").symlink_to(source)
    clip = ingest(project)[0]
    said = [("Ask", 0.1), ("five", 0.4), ("questions", 0.7), ("first.", 1.2), ("Then", 2.4), ("scope", 2.8), ("it.", 3.2)]
    project.save_words(clip.id, [Word(text, at, round(at + 0.25, 3)) for text, at in said], "test")
    ffmpeg.extract_wav(source, project.audio_dir / "take.wav", rate=48000)
    project.save_edl(Edl(fps=30, segments=[Segment(id="s001", clip=clip.id, start=0.0, end=2.0), Segment(id="s002", clip=clip.id, start=2.2, end=3.7)]))
    render.build(project, renderer="basic")
    out = tmp_path / "run"
    assert main(["shorts", "prepare", "--project", str(project.root), "--out", str(out)]) == 0
    reel = json.loads((out / "reel.json").read_text())
    assert [(t["id"], t["dialogue"]) for t in reel["takes"]] == [("take-01", "Ask five questions first."), ("take-02", "Then scope it.")]
    assert [t["duration"] for t in reel["takes"]] == pytest.approx([2.0, 1.5], abs=0.05)
    assert all(t["job_id"] is None for t in reel["takes"]) and len(reel["media"]) == 2
    record = json.loads((out / "altocue-shorts.json").read_text())
    assert record["takes"] == [{"id": "take-01", "from": 0.0, "to": 2.0}, {"id": "take-02", "from": 2.0, "to": 3.5}]
    assert 'Cue: take-02 "Then scope it."' in (out / "edit-plan.md").read_text()


JOINED = {
    "fps": 30,
    "master_duration": 10.0,
    "takes": [{"id": "take-01", "timeline_start": 0.0}, {"id": "take-02", "timeline_start": 4.0}, {"id": "take-03", "timeline_start": 7.0}],
}


def test_a_full_graphic_near_a_join_lands_on_it():
    house = dict(shorts.HOUSE_DEFAULTS)
    late = scene("hidden", 4.4, 2.0)
    assert any("starts 0.40 s from a join" in p for p in shorts.check_joins(late, JOINED, {}, house))
    early_end = scene("hidden", 4.0, 2.6)
    assert any("ends 0.40 s from a join" in p for p in shorts.check_joins(early_end, JOINED, {}, house))
    assert shorts.check_joins(scene("hidden", 4.0, 3.0), JOINED, {}, house) == []
    assert shorts.check_joins(scene("hidden", 5.0, 1.0), JOINED, {}, house) == []  # no join near


def test_a_scene_over_ten_seconds_is_split_on_a_word():
    house = dict(shorts.HOUSE_DEFAULTS)
    timeline = {"fps": 30, "master_duration": 14.0, "caption_words": [{"start": float(t), "end": t + 0.4} for t in range(14)]}
    problems = shorts.check_timing(scene("hidden", 0.0, 12.0) + scene("base", 12.0, 1.5), timeline, house)
    assert len(problems) == 1 and "runs 12.0 s" in problems[0] and "near 6.00 s" in problems[0]
    assert shorts.check_timing(scene("cutout", 0.0, 12.0), timeline, house) == []  # the face carries a long line


def test_a_graphic_never_snaps_onto_a_join_inside_another_look_away():
    house = dict(shorts.HOUSE_DEFAULTS)
    takes = [{"id": "take-01", "from": 0.0, "to": 4.0}, {"id": "take-02", "from": 4.0, "to": 7.0}, {"id": "take-03", "from": 7.0, "to": 10.0}]
    record = {"covers": [{"start": 3.6, "end": 4.3}], "takes": takes}
    timeline = {**JOINED, "takes": [{**t, "source_from": 0.0} for t in JOINED["takes"]]}
    assert shorts.check_joins(scene("hidden", 4.4, 1.6), timeline, record, house) == []


def sound(path: Path, parts: list[tuple[float, float]], seconds: float) -> Path:
    """A voice stand-in: a tone during each (start, end) part and quiet elsewhere."""
    path.parent.mkdir(parents=True, exist_ok=True)
    loud = "+".join(f"between(t,{a},{b})" for a, b in parts)
    expr = f"0.3*sin(2*PI*220*t)*({loud})"
    subprocess.run(["ffmpeg", "-y", "-loglevel", "error", "-f", "lavfi", "-i", f"aevalsrc='{expr}':s=16000:d={seconds}", str(path)], check=True)
    return path


@needs_ffmpeg
def test_a_pause_on_paper_with_the_voice_running_through_is_not_a_silent_face(tmp_path):
    house = dict(shorts.HOUSE_DEFAULTS)
    talking = sound(tmp_path / "talking.wav", [(0.0, 6.0)], 6.0)
    assert shorts.check_face(scene("base", 0, 6), SPOKEN, house, talking) == []
    paused = sound(tmp_path / "paused.wav", [(0.0, 2.0), (4.0, 6.0)], 6.0)
    assert any("2.00 to 4.00 s with nothing said" in p for p in shorts.check_face(scene("base", 0, 6), SPOKEN, house, paused))


@needs_ffmpeg
def test_fix_words_times_a_missed_first_word_and_closes_a_false_pause(tmp_path):
    run = tmp_path / "run"
    sound(run / "media" / "takes" / "take-01.wav", [(0.12, 1.0), (1.0, 2.6)], 3.0)
    words = [
        {"word": "One,", "start": 0.5, "end": 0.5, "interpolated": True},
        {"word": "clips,", "start": 0.5, "end": 0.9},
        {"word": "songs", "start": 1.6, "end": 2.0},
        {"word": "first.", "start": 2.0, "end": 2.5},
    ]
    measured = run / "takes" / "take-01.json"
    measured.parent.mkdir(parents=True)
    measured.write_text(json.dumps({"caption_words": words, "source_from": 0.0, "source": "media/takes/take-01.wav"}))
    (run / "transcripts").mkdir()
    (run / "transcripts" / "take-01.json").write_text(json.dumps({"provider": "whisper", "words": []}))
    assert shorts.fix_words(run, dry=True) and measured.exists()
    lines = shorts.fix_words(run)
    assert "'One,' at the start" in lines[0] and "after 'clips,' closed" in lines[0]
    fixed = json.loads((run / "transcripts" / "take-01.json").read_text())
    assert fixed["provider"] == "whisper+sound-fix"
    assert fixed["words"][0]["start"] == pytest.approx(0.12, abs=0.03) and fixed["words"][0]["end"] <= fixed["words"][1]["start"] + 1e-6
    assert fixed["words"][1]["end"] == pytest.approx(1.58)
    assert not measured.exists()


@needs_ffmpeg
def test_fix_words_lets_a_takes_edge_words_follow_the_sound(tmp_path):
    """A Reel played "doll" for "dollars": the recognizer ended the take's last word 0.4 s before the sound did."""
    run = tmp_path / "run"
    sound(run / "media" / "takes" / "take-01.wav", [(0.1, 2.4)], 3.0)
    words = [{"word": "Anyone", "start": 0.4, "end": 0.8}, {"word": "dollars.", "start": 1.5, "end": 2.0}]
    measured = run / "takes" / "take-01.json"
    measured.parent.mkdir(parents=True)
    measured.write_text(json.dumps({"caption_words": words, "source_from": 0.0, "source": "media/takes/take-01.wav"}))
    lines = shorts.fix_words(run)
    assert "'Anyone' starts" in lines[0] and "'dollars.' ends" in lines[0]
    fixed = json.loads((run / "transcripts" / "take-01.json").read_text())
    assert fixed["words"][0]["start"] == pytest.approx(0.1, abs=0.03)
    assert fixed["words"][1]["end"] == pytest.approx(2.4, abs=0.04)


def test_a_beat_whose_cue_is_no_longer_said_is_named(tmp_path):
    """After a re-cut, "Booking in the app?" became "Bookings and hours," and the beat cued on it quietly moved."""
    run = tmp_path / "run"
    (run / "transcripts").mkdir(parents=True)
    (run / "transcripts" / "take-01.json").write_text(json.dumps({"words": [{"word": w} for w in " Bookings and hours, sorted.".split(" ") if w]}))
    (run / "edit-plan.md").write_text('Beat: line-01\nCue: take-01 "Bookings and hours,"\n\nBeat: line-02\nCue: take-01 "Booking in the app?"\n')
    problems = shorts.check_plan_cues(run)
    assert len(problems) == 1 and "line-02" in problems[0]


def test_frames_writes_timestamped_contact_sheets(tmp_path):
    from PIL import Image

    run = tmp_path / "run"
    take(run / "renders" / "final.mp4", 4.0)
    assert main(["shorts", "frames", str(run), "--every", "0.5"]) == 0
    sheets = sorted((run / "review").glob("sheet-*.jpg"))
    assert [s.name for s in sheets] == ["sheet-1.jpg"]
    with Image.open(sheets[0]) as sheet:
        assert sheet.width == 8 * 360  # 8 frames to a row
    assert shorts_frames_stamp(61.36) == "1:01.36"


def shorts_frames_stamp(seconds: float) -> str:
    from altocue.sheets import stamp

    return stamp(seconds)
