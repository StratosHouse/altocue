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


def test_the_plan_cues_every_sentence_and_every_look_away_word_for_word():
    dialogue = shorts.dialogue_of(WORDS)
    plan = shorts.plan_lines(WORDS, dialogue, [{"start": 2.1, "end": 2.9, "reason": "eyes down"}], 3)
    assert 'Cue: take-01 "Ask five questions first."' in plan
    assert 'Cue: take-01 "Then scope it."' in plan
    assert 'Cue: take-01 "scope it."' in plan
    assert 'data-cover="cover-01"' in plan
    assert plan.rstrip().endswith("Captions: 3 words")
    for line in plan.splitlines():
        if line.startswith("Cue:"):
            assert line.split('"')[1] in dialogue


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
